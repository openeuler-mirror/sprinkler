"""Format generic result fields; this module does not write files or execute actions."""

import json
import re

class ResultFormatter:
    PASS_WORDS = {"ok", "pass", "passed", "success", "successful", "true", "yes", "y"}
    FAIL_WORDS = {"fail", "failed", "false", "no", "n", "error", "err"}
    TABLE_FIRST_COLUMNS = ["ok", "rc", "target", "host", "backend", "cmd", "error_type", "reason", "elapsed"]

    def __init__(self, use_color=False):
        self.use_color = use_color

    def result_ok(self, result: dict):
        value = result.get("ok")
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0

        text = str(value or "").strip().lower()
        if text:
            words = set(re.split(r"[^a-z0-9]+", text))
            if words & self.FAIL_WORDS:
                return False
            if words & self.PASS_WORDS:
                return True

        return self.rc_ok(result.get("rc"))

    def rc_ok(self, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return int(value) == 0
        text = str(value or "").strip()
        return text in {"0", "0.0"}

    def color_level(self, level: str):
        text = str(level)
        if not self.use_color:
            return text
        colors = {"PASS": "\033[32m", "OK": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m",
                  "INFO": "\033[36m"}
        color = colors.get(text.upper())
        return f"{color}{text}\033[0m" if color else text

    def elapsed_text(self, value):
        try:
            return f"{float(value or 0):.3f}"
        except (TypeError, ValueError):
            return "0.000"

    def target_log_entry(self, result: dict, now_text: str):
        item = self.normalized_result(result)
        detail = ("target={target} host={host} port={port} user={user} "
                  "backend={backend} rc={rc} ok={ok} "
                  "error_type={error_type} elapsed={elapsed}s").format(**item)
        lines = [f"[{now_text}] $ {item['cmd']}", detail]
        if item["stdout"]:
            lines.extend(["", "--- stdout ---", item["stdout"].rstrip()])
        if item["stderr"]:
            lines.extend(["", "--- stderr ---", item["stderr"].rstrip()])
        lines.append("")
        return "\n".join(lines) + "\n"

    def normalized_result(self, result: dict):
        item = dict(result or {})
        return {"target": item.get("target", ""), "host": item.get("host", ""), "port": item.get("port", ""),
                "user": item.get("user", ""), "backend": item.get("backend", ""), "cmd": item.get("cmd", ""),
                "ok": item.get("ok", ""), "rc": item.get("rc", ""),
                "error_type": item.get("error_type", ""), "elapsed": item.get("elapsed", ""),
                "stdout": self.text(item.get("stdout")), "stderr": self.text(item.get("stderr"))}

    def text(self, value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        if value is None:
            return ""
        return str(value)

    def safe_name(self, value: str):
        text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
        return text.strip("._") or "target"

    def first_lines(self, text: str, limit: int = 3):
        lines = [line for line in str(text or "").strip().splitlines() if line.strip()]
        return "\n".join(lines[:limit])

    def table_columns(self, results):
        columns = []
        keys = set()
        for result in results:
            if isinstance(result, dict):
                keys.update(result)

        for name in self.TABLE_FIRST_COLUMNS:
            if name in keys:
                columns.append(name)

        for result in results:
            if not isinstance(result, dict):
                continue
            for name in result:
                if name not in columns:
                    columns.append(name)
        return columns

    def table_value(self, value):
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value).replace("\r\n", "\n").replace("\r", "\n")

class CommandResultView:
    def __init__(self, formatter):
        self.formatter = formatter

    def target(self, result: dict):
        return str(result.get("target", "") or "target")

    def summary(self, result: dict, level: str):
        target = self.target(result)
        ok = self.formatter.result_ok(result)
        parts = [f"target={target}", f"host={result.get('host', '')}",
                 f"backend={result.get('backend', '')}", f"rc={result.get('rc', '')}"]
        if not ok and result.get("error_type"):
            parts.append(f"error_type={result.get('error_type')}")
        parts.append(f"elapsed={self.formatter.elapsed_text(result.get('elapsed'))}s")
        parts.append(f"cmd={result.get('cmd', '')}")
        return " ".join(parts)

    def output_blocks(self, result: dict, ok: bool, show_output: bool):
        target = self.target(result)
        blocks = []
        if result.get("stdout"):
            blocks.append(("INFO", f"[{target}] ", result["stdout"], show_output))
        if result.get("stderr"):
            blocks.append(("INFO", f"[{target}][stderr] ", result["stderr"], show_output))
        if not ok and not show_output:
            excerpt = self.formatter.first_lines(result.get("stderr") or result.get("stdout") or "")
            if excerpt:
                blocks.append(("INFO", f"[{target}][detail] ", excerpt, True))
        return blocks
