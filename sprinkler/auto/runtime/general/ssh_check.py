from concurrent.futures import ThreadPoolExecutor

from sprinkler.base.log import RunLog
from sprinkler.connect.base import ResultProcessor
from sprinkler.connect.ssh.bmc import SSHBMCConnector
from sprinkler.connect.ssh.os import SSHOSConnector
from sprinkler.connect.targets import TargetContext
from sprinkler.connect.telnet.com import TelnetCOMConnector
from sprinkler.utils.utils import positive_int_or_default


CHECK_ID = "ssh_check"


class SSHCheckResultView:
    def __init__(self, formatter):
        self.formatter = formatter

    def target(self, result: dict):
        return str(result.get("target") or "target")

    def summary(self, result: dict, level: str):
        parts = [
            f"sheet={result.get('sheet', '')}",
            f"subsystem={result.get('subsystem', '')}",
            f"target={self.target(result)}",
            f"host={result.get('host', '')}",
            f"category={result.get('category') or result.get('error_type') or 'unknown'}",
            f"reason={result.get('reason', '')}",
        ]
        return " ".join(parts)

    def output_blocks(self, result: dict, ok: bool, show_output: bool):
        return []


class SSHCheckTargetSelector:
    TARGET_TYPES = ("cpu_os", "cpu_bmc", "npu_bmc", "sfu_bmc", "sfu_com",
                    "roce_bmc", "roce_com")
    GROUP_TYPES = {
        "bmc": ("cpu_bmc", "sfu_bmc", "roce_bmc"),
        "sfu": ("sfu_bmc", "sfu_com"),
        "roce": ("roce_bmc", "roce_com"),
    }

    def __init__(self, args, target_ctx=None):
        self.args = args
        self.target_ctx = target_ctx or TargetContext(args)

    def targets(self):
        types = self.target_types()
        if len(types) == 1:
            return self.target_ctx.get_targets(types[0])
        return self.target_ctx.get_targets_by_types(types)

    def target_types(self):
        target_type = str(getattr(self.args, "target_type", "cpu-os") or "cpu-os").strip().replace("-", "_").lower()
        if target_type == "all":
            return self.TARGET_TYPES
        if target_type in self.GROUP_TYPES:
            return self.GROUP_TYPES[target_type]
        if target_type not in self.TARGET_TYPES:
            raise ValueError(f"-target-type {target_type.replace('_', '-')} is not supported")
        return (target_type,)


class SSHCheckRunner:
    CHECK_MARKER = "__SPRINKLER_SSH_OK__"
    CONNECTOR_BY_KIND = {
        "cpu_os": SSHOSConnector,
        "cpu_bmc": SSHBMCConnector,
        "npu_bmc": SSHBMCConnector,
        "sfu_bmc": SSHBMCConnector,
        "sfu_com": TelnetCOMConnector,
        "roce_bmc": SSHBMCConnector,
        "roce_com": TelnetCOMConnector,
    }
    ASSET_CATEGORY_BY_KIND = {
        "cpu_os": "cpu",
        "cpu_bmc": "cpu",
        "npu_bmc": "npu",
        "sfu_bmc": "sfu",
        "sfu_com": "sfu",
        "roce_bmc": "RoCE",
        "roce_com": "RoCE",
    }
    ENDPOINT_BY_KIND = {
        "cpu_os": "os",
        "cpu_bmc": "bmc",
        "npu_bmc": "bmc",
        "sfu_bmc": "bmc",
        "sfu_com": "com",
        "roce_bmc": "bmc",
        "roce_com": "com",
    }

    def __init__(self, args=None, run_log=None):
        self.args = args
        self.run_log = run_log
        self.result_processor = ResultProcessor()
        self.connectors = {}

    def run(self, targets):
        target_list = list(targets or [])
        if not target_list:
            raise ValueError("targets cannot be empty")

        for target in target_list:
            self.connector_for(target)

        parallel = min(positive_int_or_default(getattr(self.args, "parallel", 8), 8, "-parallel"), len(target_list))
        if parallel > 1:
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                checked = list(pool.map(self._check_target, target_list))
        else:
            checked = [self._check_target(target) for target in target_list]

        results = []
        for target, raw_results, check_results in checked:
            if self.run_log is not None:
                self.run_log.write_target_results(target.label(), raw_results)
                self.run_log.write_target_results(target.label(), check_results)
            results.extend(check_results)
        return results

    def _check_target(self, target):
        connector = self.connector_for(target)
        raw_results = connector.run_target(target, [self.check_command(target)])
        check_results = [self._check_result(target, result) for result in raw_results]
        return target, raw_results, check_results

    def connector_for(self, target):
        kind = str((target.meta or {}).get("kind") or "cpu_os")
        connector_class = self.CONNECTOR_BY_KIND.get(kind)
        if connector_class is None:
            raise ValueError(f"no connector for target type: {kind}")
        if kind not in self.connectors:
            self.connectors[kind] = connector_class(args=self.args, result_processor=self.result_processor)
        return self.connectors[kind]

    def check_command(self, target):
        return f"echo {self.CHECK_MARKER}"

    def _check_result(self, target, result: dict):
        item = dict(result)
        meta = dict(target.meta or {})
        kind = str(meta.get("kind") or "cpu_os")
        item["target"] = item.get("target") or target.label()
        item["host"] = item.get("host") or target.host
        item["port"] = item.get("port") or target.port
        item["user"] = item.get("user") or target.user
        ok = bool(result.get("ok")) and self.CHECK_MARKER in str(result.get("stdout") or "")
        category = "ok" if ok else str(result.get("error_type") or "unknown")
        reason = "login ok" if ok else self.result_processor.first_reason(result)
        if result.get("ok") and not ok:
            category = "command"
            reason = "check marker missing"

        item["ok"] = ok
        item["category"] = category
        item["reason"] = reason
        item["sheet"] = meta.get("sheet") or meta.get("cabinet_name") or ""
        item["subsystem"] = meta.get("subsystem") or kind
        item["asset_category"] = meta.get("asset_category") or self.ASSET_CATEGORY_BY_KIND.get(kind, "")
        item["endpoint"] = meta.get("endpoint") or self.ENDPOINT_BY_KIND.get(kind, "")
        item["tcp_ok"] = None
        item["ssh_ok"] = bool(result.get("ok"))
        return item


def execute(args):
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
    try:
        targets = SSHCheckTargetSelector(args).targets()
        results = SSHCheckRunner(args=args, run_log=run_log).run(targets)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    return run_log.finish_results(CHECK_ID, results, SSHCheckResultView(run_log.formatter))
