# -*- coding: utf-8 -*-
"""故障模式库匹配：在日志收集目录中检索已知故障模式。

用法（入口见 sprinkler.plog.plog.fault_match_entry）：
    sprinkler -fault-match -log-dir <目录> [-fault-library <路径>] [-output-format table|json|csv]

故障模式库不上代码仓，默认与 superpod table 同目录：
    <sprinkler_config>/plog_fault_library.csv（见 utils.utils.default_config_dir）

处理流程：
    1. _load_fault_library()  一次性加载并规范化故障模式库（驻留内存复用）
    2. _scan_directory()      递归扫描目录中的 .log/.txt 及无扩展名文件（含路径边界校验）
    3. _match_single_log()    逐文件流式逐行匹配
    4. match_directory()      聚合全部命中并按 (故障码, 文件, 行号) 去重
    5. format_results()       按 table/json/csv 格式化命中清单
"""
import csv
import io
import json
import os
import re
from collections import deque

import pandas as pd

from sprinkler.utils.utils import default_config_dir

DEFAULT_LIBRARY_PATH = str(default_config_dir() / "plog_fault_library.csv")
DEFAULT_RESULT_DIRNAME = "fault_match_result"
SUPPORTED_LOG_SUFFIXES = (".log", ".txt")
OUTPUT_FORMATS = ("table", "json", "csv")

# 模式库表头别名：统一映射到内部字段名，兼容新旧两版 CSV 表头
_COLUMN_ALIASES = {
    "故障类型": "fault_type",
    "故障名称": "fault_name",
    "模式编号": "error_code",
    "关键日志": "key_log",
    "故障原因": "error_cause",
    "解决方法": "resolution",
    "env_class": "fault_type",
    "pattern_name": "fault_name",
}

_NULL_TOKENS = ("", "nan", "na", "none")


class FaultMatcher(object):
    """故障模式匹配器：加载模式库一次，对目录下全部日志文件执行匹配。"""

    def __init__(self, library_path=None, output_format="table", context_before=3):
        if output_format not in OUTPUT_FORMATS:
            raise ValueError("非法的输出格式: {}，可选: {}".format(output_format, "/".join(OUTPUT_FORMATS)))
        self.library_path = library_path or DEFAULT_LIBRARY_PATH
        self.output_format = output_format
        # 逐行匹配时连同当前行之前的 context_before 行组成上下文，兼容关键词跨行场景
        self.context_before = max(0, int(context_before))
        self.patterns = []
        self.scanned_files = []
        self._load_fault_library()

    @staticmethod
    def _normalize_cell(value):
        text = "" if value is None else str(value).strip()
        return "" if text.lower() in _NULL_TOKENS else text

    # printf 风格占位符（%s/%u/%d/%zu 等），真实日志中会被实际值替换，需按占位符切分关键词
    _PRINTF_PLACEHOLDER = re.compile(r"%(?:[-+ #0]*\d*(?:\.\d+)?[hljztL]*[diuoxXfFeEgGaAcspn])")

    @classmethod
    def _split_keywords(cls, raw_key_log):
        """拆分并规范化关键词：按 & 拆分、去首尾成对引号、按 printf 占位符再切分，
        统一转小写并丢弃过短片段（避免 '有'/'无' 这类关键词全量误报）。"""
        keywords = []
        for part in str(raw_key_log).split("&"):
            part = part.strip().strip("\"'“”‘’").strip()
            for frag in cls._PRINTF_PLACEHOLDER.split(part):
                frag = re.sub(r"\s+", " ", frag).strip()
                if len(frag) >= 4 and frag.lower() not in _NULL_TOKENS:
                    keywords.append(frag.lower())
        return keywords

    def _load_fault_library(self):
        """读取 CSV 并规范化：模式编号去空白，关键日志经 _split_keywords 规范化拆分，剔除无效行。"""
        if not os.path.isfile(self.library_path):
            raise FileNotFoundError(
                "故障模式库文件未找到: {}（请将 plog_fault_library.csv 放到 sprinkler_config 目录，"
                "或通过 -fault-library 指定路径）".format(self.library_path))
        try:
            df = pd.read_csv(self.library_path, dtype=str, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(self.library_path, dtype=str, encoding="gbk")
        except Exception as e:
            raise ValueError("加载故障模式库失败: {}: {}".format(self.library_path, e))
        df = df.fillna("")
        df.columns = [_COLUMN_ALIASES.get(str(c).strip(), str(c).strip()) for c in df.columns]

        patterns = []
        for _, row in df.iterrows():
            row = row.to_dict()
            error_code = self._normalize_cell(row.get("error_code", ""))
            # 关键日志为匹配依据；兼容旧库单独的 error_keywords 列
            raw_key_log = row.get("error_keywords", "") or row.get("key_log", "")
            keywords = self._split_keywords(raw_key_log)
            if not error_code and not keywords:
                continue  # 故障码与关键词均为空的无效行
            # 纯数字模式编号（如 507018）视为日志中真实出现的错误码，参与与匹配；
            # 含字母/下划线的编号（如 AISW_CANN_ACL_02）是模式 ID，日志中不会出现，仅用于展示
            ec_matchable = bool(error_code) and error_code.isdigit()
            pattern = {
                "error_code": error_code,
                "keywords": keywords,
                "ec_regex": re.compile(r"\b" + re.escape(error_code) + r"\b") if ec_matchable else None,
                "fault_type": self._normalize_cell(row.get("fault_type", "")),
                "fault_name": self._normalize_cell(row.get("fault_name", "")),
                "key_log": self._normalize_cell(row.get("key_log", "")) or self._normalize_cell(raw_key_log),
                "error_cause": self._normalize_cell(row.get("error_cause", "")),
                "resolution": self._normalize_cell(row.get("resolution", "")),
            }
            patterns.append(pattern)
        self.patterns = patterns
        return patterns

    @staticmethod
    def _is_log_file(name):
        """判定待扫描文件：.log/.txt 结尾，或无扩展名（如 npu0_messages）。"""
        if name.lower().endswith(SUPPORTED_LOG_SUFFIXES):
            return True
        return "." not in name

    def _scan_directory(self, log_dir):
        """递归收集目录下全部 .log/.txt 及无扩展名文件，校验路径并拒绝逃逸出根目录的符号链接。"""
        root = os.path.realpath(log_dir)
        if not os.path.isdir(root):
            raise NotADirectoryError("日志目录不存在或不是目录: {}".format(log_dir))
        files = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames
                if d != DEFAULT_RESULT_DIRNAME
                and os.path.realpath(os.path.join(dirpath, d)).startswith(root + os.sep)
            )
            for name in sorted(filenames):
                if not self._is_log_file(name):
                    continue
                path = os.path.join(dirpath, name)
                real = os.path.realpath(path)
                if not real.startswith(root + os.sep):
                    continue
                files.append(path)
        return sorted(files)

    @staticmethod
    def _match_pattern(pattern, context, context_lower):
        """单模式匹配规则：可匹配故障码（纯数字）全词精确匹配、关键词全部命中、两者兼具时取与；
        模式编号为模式 ID（不可匹配）时仅以关键词判定。"""
        ec_active = pattern["ec_regex"] is not None
        keywords = pattern["keywords"]
        ec_ok = ec_active and bool(pattern["ec_regex"].search(context))
        kw_ok = bool(keywords) and all(kw in context_lower for kw in keywords)
        if ec_active and keywords:
            return ec_ok and kw_ok
        if ec_active:
            return ec_ok
        return kw_ok

    def _match_single_log(self, file_path):
        """流式逐行读取单个日志文件，返回全部命中项。"""
        hits = []
        window = deque(maxlen=self.context_before + 1)
        last_hit_line = {}  # pattern_index -> line_no，抑制上下文窗口内的相邻重复命中
        try:
            stream = open(file_path, "r", encoding="utf-8", errors="replace")
        except OSError as e:
            print("[Warn] 跳过无法读取的文件: {}: {}".format(file_path, e))
            return hits
        with stream:
            for line_no, raw_line in enumerate(stream, 1):
                window.append(raw_line.rstrip("\n"))
                context = "\n".join(window)
                context_lower = context.lower()
                for idx, pattern in enumerate(self.patterns):
                    if not self._match_pattern(pattern, context, context_lower):
                        continue
                    prev_line = last_hit_line.get(idx)
                    if prev_line is not None and line_no - prev_line <= self.context_before:
                        continue
                    last_hit_line[idx] = line_no
                    hits.append({
                        "error_code": pattern["error_code"],
                        "fault_name": pattern["fault_name"],
                        "fault_type": pattern["fault_type"],
                        "key_log": pattern["key_log"],
                        "error_cause": pattern["error_cause"],
                        "resolution": pattern["resolution"],
                        "file": file_path,
                        "line": line_no,
                    })
        return hits

    def match_directory(self, log_dir):
        """扫描目录并聚合全部命中，按 (故障码, 文件, 行号) 去重。"""
        self.scanned_files = self._scan_directory(log_dir)
        total = len(self.scanned_files)
        print("[Info] 共发现 {} 个待扫描日志文件".format(total))
        all_hits = []
        for i, file_path in enumerate(self.scanned_files, 1):
            print("[Info] 正在扫描 ({}/{}): {}".format(i, total, file_path))
            file_hits = self._match_single_log(file_path)
            if file_hits:
                print("[Info]   命中 {} 个故障模式".format(len(file_hits)))
            all_hits.extend(file_hits)
        seen = set()
        unique_hits = []
        for hit in all_hits:
            key = (hit["error_code"] or hit["fault_name"], hit["file"], hit["line"])
            if key in seen:
                continue
            seen.add(key)
            unique_hits.append(hit)
        return unique_hits

    @staticmethod
    def _hit_to_row(hit):
        return {
            "模式编号": hit["error_code"],
            "故障名称": hit["fault_name"],
            "故障类型": hit["fault_type"],
            "文件": hit["file"],
            "行号": hit["line"],
            "关键日志": hit["key_log"],
            "故障原因": hit["error_cause"],
            "解决方法": hit["resolution"],
        }

    def format_results(self, hits):
        """按用户指定格式（table/json/csv）格式化命中清单。"""
        rows = [self._hit_to_row(h) for h in hits]
        if self.output_format == "json":
            return json.dumps({
                "total": len(rows),
                "scanned_files": len(self.scanned_files),
                "matches": rows,
            }, ensure_ascii=False, indent=2)
        if self.output_format == "csv":
            return self._format_csv(rows)
        return self._format_table(rows)

    def _format_csv(self, rows):
        buffer = io.StringIO()
        headers = ["模式编号", "故障名称", "故障类型", "文件", "行号", "关键日志", "故障原因", "解决方法"]
        writer = csv.DictWriter(buffer, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue().rstrip("\r\n")

    @staticmethod
    def _clip(text, width):
        text = str(text).replace("\n", " ")
        return text if len(text) <= width else text[:width - 1] + "…"

    def _format_table(self, rows):
        if not rows:
            return "未找到匹配的故障模式（共扫描 {} 个日志文件）".format(len(self.scanned_files))
        headers = ["序号", "模式编号", "故障名称", "故障类型", "文件", "行号", "关键日志", "故障原因", "解决方法"]
        limits = [6, 12, 20, 10, 40, 8, 40, 40, 40]
        table_rows = []
        for i, row in enumerate(rows, 1):
            table_rows.append([
                str(i), row["模式编号"], row["故障名称"], row["故障类型"],
                row["文件"], str(row["行号"]), row["关键日志"], row["故障原因"], row["解决方法"],
            ])
        table_rows = [[self._clip(cell, limit) for cell, limit in zip(r, limits)] for r in table_rows]
        widths = [max(len(headers[i]), max(len(r[i]) for r in table_rows)) for i in range(len(headers))]
        sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

        def fmt_row(cells):
            return "|" + "|".join(" {} ".format(c).ljust(w + 2) for c, w in zip(cells, widths)) + "|"

        lines = ["命中 {} 个故障模式（共扫描 {} 个日志文件）".format(len(rows), len(self.scanned_files)),
                 sep, fmt_row(headers), sep]
        lines.extend(fmt_row(r) for r in table_rows)
        lines.append(sep)
        return "\n".join(lines)
