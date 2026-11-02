"""Display and persist command results; this module does not execute actions."""

import csv
import json
import os
import sys
import time
from pathlib import Path
from threading import RLock

from sprinkler.base.results import CommandResultView, ResultFormatter
from sprinkler.utils.utils import default_log_root

class RunLog:
    def __init__(self, args, default_check_id: str):
        self.check_id = str(getattr(args, "check_id", "") or default_check_id)
        self.show_output = bool(getattr(args, "show_output", False))
        self.formatter = ResultFormatter(use_color=sys.stdout.isatty())
        self.path = self._make_log_dir(args)
        self._write_lock = RLock()

    def log(self, level: str, message: str):
        self._write_run_line(level, message, show=True)

    def info(self, message: str):
        self.log("INFO", message)

    def ok(self, message: str):
        self.log("PASS", message)
    def fail(self, message: str):
        self.log("FAIL", message)

    def fail_error(self, label: str, exc: Exception):
        self.fail(f"error: {exc}")
        self.write_results([])
        self.auto_details(label, 0, 1)
        self.finish_log_dir()
        return 2

    def write_results(self, results: list[dict]):
        self.write_json("results.json", results)
        self.write_results_table(results)

    def write_json(self, name: str, data):
        if self.path is None:
            return
        (self.path / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8", errors="replace")

    def write_results_table(self, results: list[dict]):
        if self.path is None:
            return
        columns = self.formatter.table_columns(results)
        with (self.path / "results.csv").open("w", encoding="utf-8-sig", errors="replace", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=columns, extrasaction="ignore")
            if columns:
                writer.writeheader()
                for result in results:
                    if isinstance(result, dict):
                        writer.writerow({name: self.formatter.table_value(result.get(name)) for name in columns})

    def target_log_path(self, target_name: str):
        if self.path is None:
            return None
        return self.path / f"{self.formatter.safe_name(target_name)}.log"

    def write_target_results(self, target_name: str, results: list[dict]):
        path = self.target_log_path(target_name)
        if path is None:
            return
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", errors="replace") as fp:
                for result in results:
                    fp.write(self.formatter.target_log_entry(result, self.now_text()))

    def auto_details(self, label: str, passed: int, total: int):
        self.info(">>>AUTO_DETAILS_START<<<")
        self.info(f"{label} run_pass={passed} run_total={total} bw_pass={passed} bw_total={total}")
        self.info(">>>AUTO_DETAILS_END<<<")

    def finish_results(self, label: str, results: list[dict], result_view=None, result_writer=None):
        view = result_view or CommandResultView(self.formatter)
        self.write_results(results)
        if result_writer is not None:
            result_writer.write(self.path)
        for result in results:
            self._log_result(result, view)
        passed = sum(1 for result in results if self.formatter.result_ok(result))
        total = len(results)
        self.auto_details(label, passed, total)
        self.finish_log_dir()
        return 0 if total > 0 and passed == total else 1

    def finish_log_dir(self):
        self.info(f"log_dir={self.path if self.path is not None else 'stdout'}")

    def _make_log_dir(self, args):
        if os.environ.get("SPRINKLER_STDOUT_LOG_ONLY") == "1":
            return None
        root_arg = str(getattr(args, "log_root", "") or "").strip()
        root = Path(root_arg).expanduser() if root_arg else default_log_root()
        base = root / self.check_id / self.run_timestamp()
        for index in range(1000):
            path = base if index == 0 else Path(f"{base}_{index}")
            try:
                path.mkdir(parents=True, exist_ok=False)
                return path
            except FileExistsError:
                continue
        raise FileExistsError(str(base))

    def _log_result(self, result: dict, result_view):
        with self._write_lock:
            ok = self.formatter.result_ok(result)
            log_level_fn = getattr(result_view, "log_level", None)
            level = log_level_fn(result, ok) if log_level_fn else ("PASS" if ok else "FAIL")
            self.log(level, result_view.summary(result, level))
            for block_level, prefix, text, show in result_view.output_blocks(result, ok, self.show_output):
                self._log_output_block(block_level, prefix, text, show=show)

    def _log_output_block(self, level: str, prefix: str, text: str, show: bool):
        for line in str(text or "").rstrip("\n").splitlines():
            message = f"{prefix}{line}"
            if show:
                self.log(level, message)
            else:
                self._write(level, message)

    def _write(self, level: str, message: str):
        self._write_run_line(level, message, show=False)

    def _write_run_line(self, level: str, message: str, show: bool):
        with self._write_lock:
            now = self.now_text()
            line = f"{now} | {level} | {self.check_id} | {message}"
            if show:
                print(f"{now} | {self.formatter.color_level(level)} | {self.check_id} | {message}", flush=True)
            if self.path is not None:
                with (self.path / "run.log").open("a", encoding="utf-8", errors="replace") as fp:
                    fp.write(line + "\n")

    @staticmethod
    def now_text():
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    @staticmethod
    def run_timestamp():
        return time.strftime("%Y%m%d_%H%M%S", time.localtime())
