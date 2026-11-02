"""版本报告生成器与合规检查器。

本模块输出链路的两块收尾能力：

- ``VersionReportBuilder``：把版本查询结果渲染为自包含的 HTML 报告
  （表头、行、样式内联，无需外部资源），支持自动隐藏全空列。
- ``ComplianceChecker``：把查询结果与预期版本清单（manifest）比对，
  输出不合规明细，供 ``-expected-versions`` 高级用法使用。

设计说明：

- IP / 目标与版本值原样展示（用户提供的数据、本地报告），缺失字段渲染为
  空单元格；遇到格式异常的行绝不抛异常（健壮性优先，避免单行异常拖垮整个
  报告 / 合规流程）。
- 报告列（``REPORT_COLUMNS``）与合规字段均来自 registry（单一事实来源）：
  - ``REPORT_COLUMNS`` 在 ``registry.FIELDS`` 基础上派生，保证"新增字段 →
    报告列自动出现"。
  - ``ComplianceChecker`` 按 manifest 的 key 从结果行取值，新增字段无需改代码
"""

from pathlib import Path

from sprinkler.checker.version.query.registry import REPORT_COLUMNS

# 行内样式串（拼接自多段 CSS 规则），保持报告自包含、无需外部样式文件。
# 关键样式：表头深蓝底白字（sticky 固定）、奇偶行斑马纹、PASS/FAIL 绿/红着色、
# 首列冻结（便于横向滚动时识别目标列）。
_REPORT_CSS = (
    "body{font-family:Consolas,'Courier New',monospace;margin:16px;}"
    "h1{font-size:18px;}.summary{margin:8px 0;font-weight:bold;}"
    "table{border-collapse:collapse;width:100%;font-size:13px;}"
    "th,td{border:1px solid #ddd;padding:6px 10px;text-align:left;white-space:nowrap;}"
    "th{background:#4a6fa5;color:#fff;position:sticky;top:0;}"
    "tr:nth-child(even){background:#f9f9f9;}"
    "tr.pass td:first-child{color:#2e7d32;font-weight:bold;}"
    "tr.fail td:first-child{color:#c62828;font-weight:bold;}"
    "td:first-child{position:sticky;left:0;background:inherit;z-index:1;}"
    "tr:nth-child(even) td:first-child{background:#f9f9f9;}"
)


class VersionReportBuilder:
    """从版本查询结果构建 HTML 版本报告。

    只依赖 registry.REPORT_COLUMNS 与结果行字典，因此对查询逻辑解耦：
    新增字段只需在 registry 注册，报告列自动出现；无字段值/空行自动隐藏列。
    """

    def __init__(self, report_format: str = "html"):
        """构造报告生成器。

        参数：
            report_format: 报告格式，当前仅支持 ``html``
        异常：
            ValueError: 传入不支持的格式时抛出
        """
        if report_format not in ("html",):
            raise ValueError(f"unsupported report_format: {report_format!r} (only 'html')")
        self.report_format = report_format

    def build(self, results: list, output_path: str = None) -> str:
        """将结果渲染为 HTML 报告文件并返回其绝对路径。

        步骤：
        1. 对每行做归一化（回填 ip、将 ok 布尔转为 PASS/FAIL 字符串）
        2. 渲染 HTML 表格（含居中摘要）
        3. 写入 output_path（默认当前目录 report.html），确保父目录存在。

        参数：
            results: 版本查询结果行字典列表
            output_path: 输出文件路径；None 时写 ``report.html``
        返回：
            写入文件的绝对路径字符串
        """
        rows = [self._render_row(r) for r in results]
        html = self._render_html(rows)
        path = Path(output_path).expanduser() if output_path else Path("report.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return str(path.resolve())

    def _render_row(self, result: dict) -> dict:
        """归一化单行：ip 缺失时回退 host，ok 布尔转为 PASS/FAIL 字符串。

        结果行可能来自不同通道 / 合并逻辑，ip 与 host 可能只填其一；此处统一
        回填 ip，并保证 ok 字段一定是"PASS"/"FAIL"字符串而非布尔值
        """
        row = dict(result or {})
        row["ip"] = str(result.get("ip") or result.get("host") or "")
        row["ok"] = "PASS" if result.get("ok") else "FAIL"
        return row

    def _render_html(self, rows: list) -> str:
        """渲染 HTML 表格。

        只保留至少一行有值的列（自动隐藏全空列）；全部为空时兜底显示前 4 列
        行 class 依据 ``ok == "PASS"`` 取 ``pass``/``fail`` 用于 CSS 着色；
        顶部附带 total / pass / fail 计数的摘要行
        """
        # 过滤出至少在一行有值的列；若都空则退回 REPORT_COLUMNS 前 4 列保证有表头
        available = [(k, l) for k, l in REPORT_COLUMNS
                     if any(row.get(k) not in (None, "", []) for row in rows)] or REPORT_COLUMNS[:4]
        head = "".join(f"<th>{l}</th>" for _, l in available)
        body_lines = []
        for row in rows:
            cells = "".join(f"<td>{row.get(k, '') or ''}</td>" for k, _ in available)
            body_lines.append(f'<tr class="{"pass" if row.get("ok") == "PASS" else "fail"}">{cells}</tr>')
        total, passed = len(rows), sum(1 for r in rows if r.get("ok") == "PASS")
        summary = f"total={total} pass={passed} fail={total - passed}"
        return (
            '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            "<title>Sprinkler Version Query Report</title>\n"
            f"<style>\n{_REPORT_CSS}\n</style>\n</head>\n<body>\n"
            "<h1>Sprinkler Version Query Report</h1>\n"
            f'<div class="summary">{summary}</div>\n<table>\n'
            f"<thead><tr>{head}</tr></thead>\n<tbody>\n" + "\n".join(body_lines) + "\n</tbody>\n"
            "</table>\n</body>\n</html>\n"
        )


class ComplianceChecker:
    """将版本查询结果与预期版本清单（manifest）进行比对。

    合规规则：仅比对 ``ok`` 的目标；manifest 中某字段的预期值与实际值均非空且
    不相等时记为一次不合规。空预期或空实际均跳过（无法判定即视为通过）。
    被跳过（失败）目标也不参与合规判定。
    """

    def compliance_check(self, report_results: list, expected_manifest: dict) -> list:
        """执行合规比对。

        参数：
            report_results: 版本查询结果行列表
            expected_manifest: dict，``{component: expected_version}``
        返回：
            不合规明细列表；每项为
            ``{target, ip, component, expected, actual}``。全匹配时返回空列表
        异常：
            ValueError: ``expected_manifest`` 非 dict 时抛出
        """
        if not isinstance(expected_manifest, dict):
            raise ValueError("expected manifest must be a dict mapping component -> version")
        non_compliant = []
        for result in report_results:
            if not result.get("ok"):
                continue  # 失败目标不参与合规判定
            for component, expected in expected_manifest.items():
                actual = str(result.get(component, "") or "")
                expected = str(expected or "")
                # 仅当预期与实际都有值且不一致时才记不合规
                if expected and actual and actual != expected:
                    non_compliant.append({
                        "target": result.get("target", ""),
                        "ip": result.get("ip") or result.get("host") or "",
                        "component": component, "expected": expected, "actual": actual,
                    })
        return non_compliant
