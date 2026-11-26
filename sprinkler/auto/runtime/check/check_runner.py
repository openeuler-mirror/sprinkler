from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from sprinkler.base.log import RunLog
from sprinkler.connect.connection_runner import ConnectionRunner, create_runner
from sprinkler.connect.targets import TargetContext, ConnectionTarget
from sprinkler.connect.base import BaseConnector
from sprinkler.auto.runtime.check.check_env import CheckEnv, ENV_LABEL, resolve_env_from_args
from sprinkler.auto.runtime.check.items.check_item_base import CheckItemBase
from sprinkler.auto.runtime.check.items.check_card_register import CheckCardRegister
from sprinkler.auto.runtime.check.items.check_config import CheckConfig
from sprinkler.auto.runtime.check.items.check_port_status import (
    CheckPortStatus, CheckPort5808, CheckLane5808,
)
from sprinkler.auto.runtime.check.items.check_link_status import CheckLinkStatus
from sprinkler.auto.runtime.check.items.check_credit_status import CheckCreditStatus, CheckCredit5808
from sprinkler.auto.runtime.check.items.check_disk import CheckDisk, CheckDisk1213, CheckDiskBMC, CheckDiskNPU1213, CheckDiskNPUBMC, CheckDiskSFUBMC, CheckDiskSFU1260
from sprinkler.auto.runtime.check.items.check_ubm_conn import CheckUbmConn
from sprinkler.utils.utils import positive_int_or_default

_CHECK_ITEM_MAP: dict[str, type[CheckItemBase]] = {
    "card": CheckCardRegister,
    "config": CheckConfig,
    "port": CheckPortStatus,
    "link": CheckLinkStatus,
    "credit": CheckCreditStatus,
    "disk_cpu_os": CheckDisk,
    "disk_cpu_1213": CheckDisk1213,
    "disk_cpu_bmc": CheckDiskBMC,
    "disk_npu_1213": CheckDiskNPU1213,
    "disk_npu_bmc": CheckDiskNPUBMC,
    "disk_sfu_bmc": CheckDiskSFUBMC,
    "disk_sfu_1260": CheckDiskSFU1260,
    "ubm_conn": CheckUbmConn,
}

_DISK_ALIAS = ["disk_cpu_os", "disk_cpu_1213", "disk_cpu_bmc"]
_NPU_DISK_ALIAS = ["disk_npu_1213", "disk_npu_bmc"]
_SFU_DISK_ALIAS = ["disk_sfu_bmc", "disk_sfu_1260"]

# 环境专属检查项覆盖（同名检查项在不同环境使用不同实现）。
_CHECK_ITEM_MAP_BY_ENV: dict[str, dict[str, type[CheckItemBase]]] = {
    "5808": {
        "port": CheckPort5808,
        "credit": CheckCredit5808,
        "lane": CheckLane5808,
    },
}

ALL_CHECK_ITEM_NAMES: list[str] = list(_CHECK_ITEM_MAP.keys())


def _item_map_for_env(env_name: str = "") -> dict[str, type[CheckItemBase]]:
    return _CHECK_ITEM_MAP_BY_ENV.get(env_name, _CHECK_ITEM_MAP)


def all_check_item_names(env_name: str = "") -> list[str]:
    return list(_item_map_for_env(env_name).keys())


def parse_check_items(check_item_str: str | None, env_name: str = "") -> list[str]:
    item_map = _item_map_for_env(env_name)
    if check_item_str is None or check_item_str.strip() == "all":
        return list(item_map)
    items = [item.strip() for item in check_item_str.split(",") if item.strip()]
    expanded = []
    for item in items:
        if item == "disk":
            if env_name == "npu1213":
                expanded.extend(_NPU_DISK_ALIAS)
            elif env_name == "sfu1260":
                expanded.extend(_SFU_DISK_ALIAS)
            else:
                expanded.extend(_DISK_ALIAS)
        elif item == "npu_disk":
            expanded.extend(_NPU_DISK_ALIAS)
        elif item == "sfu_disk":
            expanded.extend(_SFU_DISK_ALIAS)
        else:
            expanded.append(item)
    invalid = [item for item in expanded if item not in item_map]
    if invalid:
        raise ValueError(f"不支持的检查项: {invalid}，可选: {list(item_map)}")
    return expanded


def resolve_check_items(item_names: list[str], env_name: str = "") -> list[type[CheckItemBase]]:
    item_map = _item_map_for_env(env_name)
    return [item_map[name] for name in item_names if name in item_map]


class CheckRunner:
    """检查执行器：接收 CheckEnv + check_items，按检查项过滤命令后执行。"""

    def __init__(self, env: CheckEnv, args=None, run_log: RunLog = None,
                 check_items: list[str] | None = None):
        self.env = env
        self.args = args
        self.run_log = run_log
        self.check_items = check_items or all_check_item_names(env.name)
        self._item_classes = resolve_check_items(self.check_items, env_name=env.name)
        self._target_ctx: TargetContext | None = None
        self._runner: ConnectionRunner | None = None

    def _item_check_name(self, item_cls: type[CheckItemBase]) -> str:
        """解析检查项显示名；含 {label} 的模板按入口（L1/L2）填充。"""
        check_name = getattr(item_cls, 'CHECK_NAME', item_cls.__name__)
        if "{label}" not in check_name:
            return check_name
        env_type = resolve_env_from_args(self.args)
        label = ENV_LABEL.get(env_type, "") if env_type else ""
        return check_name.format(label=label or self.env.name)

    @property
    def target_ctx(self) -> TargetContext:
        if self._target_ctx is None:
            self._target_ctx = TargetContext(self.args)
        return self._target_ctx

    def get_slot_id(self, node_index: int = 0) -> int:
        return self.env.slot_map.get(node_index, 0)

    def _get_runner(self, connection_type: str = None) -> ConnectionRunner:
        if connection_type and connection_type != self.env.connection_type:
            return create_runner(args=self.args, run_log=self.run_log, connection_type=connection_type)
        if self._runner is None:
            self._runner = create_runner(
                args=self.args, run_log=self.run_log, connection_type=self.env.connection_type
            )
        return self._runner

    def _get_targets(self, target_type: str = None) -> list[ConnectionTarget]:
        return self.target_ctx.get_targets(target_type or self.env.target_type)

    def get_commands(self, slot_id: int = 0) -> list[str]:
        commands = []
        for item_cls in self._item_classes:
            commands.extend(item_cls.get_commands(
                chip_count=self.env.chip_count, slot_id=slot_id, credit_after_lines=self.env.credit_after_lines
            ))
        return commands

    def execute_and_log(self, targets: list[ConnectionTarget] = None, slot_id: int = 0):
        runner = self._get_runner()
        target_list = targets or self._get_targets()
        commands = self.get_commands(slot_id=slot_id)
        return runner.run(target_list, commands, on_result=self._handle_realtime_result)
    
    def _handle_realtime_result(self, result: dict):
        cmd = result.get("cmd", "")
        stdout = result.get("stdout", "")
        target = result.get("target", "")
        
        for item_cls in self._item_classes:
            if hasattr(item_cls, 'analyze_single_command'):
                item_cls.analyze_single_command(
                    cmd=cmd,
                    stdout=stdout,
                    target=target,
                    run_log=self.run_log
                )

    def _group_items_by_connection(self) -> dict[tuple[str, str], list[type[CheckItemBase]]]:
        groups: dict[tuple[str, str], list[type[CheckItemBase]]] = defaultdict(list)
        for item_cls in self._item_classes:
            conn = item_cls.CONNECTION_TYPE or self.env.connection_type
            tgt = item_cls.TARGET_TYPE or self.env.target_type
            groups[(conn, tgt)].append(item_cls)
        return dict(groups)

    def analyze_log(self, log_path: str | dict[str, str]) -> tuple[bool, dict[str, list[str]], dict]:
        if isinstance(log_path, dict):
            item_log_map = log_path
        else:
            item_log_map = None
        results = {}
        anomalies = {}
        item_status = {}
        for item_cls in self._item_classes:
            name = item_cls.__name__
            check_name = self._item_check_name(item_cls)
            item_log = item_log_map.get(name, log_path) if item_log_map else log_path
            item = item_cls(item_log, run_log=self.run_log)
            item.CHECK_NAME = check_name
            if hasattr(item, 'ALLOWED_DOWN_PATTERN'):
                item.ALLOWED_DOWN_PATTERN = self.env.allowed_down_pattern
            if hasattr(item, 'CHIP_COUNT'):
                item.CHIP_COUNT = self.env.chip_count
            if hasattr(item, 'EXPECTED_CARD_COUNT'):
                item.EXPECTED_CARD_COUNT = self.env.expected_card_count
            ok = item.analyze_log()
            results[name] = ok
            item_status[name] = (check_name, ok, item.anomalies)
            if item.anomalies:
                anomalies[name] = item.anomalies
        all_pass = all(results.values())
        return all_pass, anomalies, item_status

    def _resolve_target_for_group(self, target: ConnectionTarget, tgt_type: str) -> ConnectionTarget:
        if tgt_type == self.env.target_type:
            return target
        node_num = target.meta.get("node", 0) if target.meta else 0
        targets = self._get_targets(tgt_type)
        for t in targets:
            t_node = t.meta.get("node", 0) if t.meta else 0
            if t_node == node_num:
                return t
        return target

    def check_single_node(
        self, target: ConnectionTarget, log_path: str, slot_id: int = 0
    ) -> tuple[bool, dict[str, list[str]], dict]:
        groups = self._group_items_by_connection()
        primary_log_path = log_path
        item_log_map: dict[str, str] = {}
        for (conn_type, tgt_type), item_classes in groups.items():
            runner = self._get_runner(conn_type)
            group_target = self._resolve_target_for_group(target, tgt_type)
            if tgt_type != self.env.target_type:
                group_log_path = self._get_log_path(group_target)
            else:
                group_log_path = primary_log_path
            for item_cls in item_classes:
                item_log_map[item_cls.__name__] = group_log_path
            commands = []
            for item_cls in item_classes:
                commands.extend(item_cls.get_commands(
                    chip_count=self.env.chip_count, slot_id=slot_id, credit_after_lines=self.env.credit_after_lines
                ))
            # 部分检查项（如 5808 credit）需要先解析首轮结果，再生成后续命令。
            def deferred_provider(results):
                deferred_commands = []
                for item_cls in item_classes:
                    deferred_commands.extend(item_cls.get_deferred_commands(
                        results=results,
                        chip_count=self.env.chip_count,
                        slot_id=slot_id,
                        credit_after_lines=self.env.credit_after_lines,
                    ))
                return deferred_commands

            results = runner.run(
                [group_target], commands,
                on_result=self._handle_realtime_result,
                deferred_provider=deferred_provider,
            )
            if not results:
                return False, {"ExecuteFail": ["命令执行无结果"]}, {}
            failed_cmds = [r.get("cmd", "?") for r in results if isinstance(r, dict) and not r.get("ok") and not r.get("stdout")]
            # 默认保持原行为：任一命令失败即返回；5808 允许部分失败后继续分析。
            if failed_cmds and not (
                self.env.continue_on_partial_failure and len(failed_cmds) < len(results)
            ):
                detail = failed_cmds
                if self.env.continue_on_partial_failure and len(failed_cmds) > 5:
                    detail = failed_cmds[:5] + [f"... 共 {len(failed_cmds)} 条命令执行失败"]
                return False, {"ExecuteFail": detail}, {}

        return self.analyze_log(item_log_map)

    def _get_log_path(self, target: ConnectionTarget) -> str:
        if self.run_log is not None:
            return str(self.run_log.target_log_path(BaseConnector.target_label(target)))
        return ""

    def _resolve_primary_target_type(self) -> str:
        groups = self._group_items_by_connection()
        target_types = set(tgt for (_, tgt) in groups)
        if len(target_types) == 1:
            return target_types.pop()
        return self.env.target_type

    DEFAULT_PARALLEL = 8

    def check_batch(self) -> dict[str, tuple[bool, dict, dict]]:
        primary_tgt_type = self._resolve_primary_target_type()
        targets = self._get_targets(primary_tgt_type)
        max_workers = self._resolve_parallel(len(targets))
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                node_results = list(pool.map(self._check_one, targets))
        else:
            node_results = [self._check_one(t) for t in targets]
        results = {}
        for target, (all_pass, anomalies, item_status) in zip(targets, node_results):
            results[BaseConnector.target_label(target)] = (all_pass, anomalies, item_status)
        return results

    def _check_one(self, target: ConnectionTarget) -> tuple[bool, dict[str, list[str]]]:
        log_path = self._get_log_path(target)
        node_num = target.meta.get("node", 0) if target.meta else 0
        slot_id = self.get_slot_id(node_num)
        return self.check_single_node(target, log_path, slot_id=slot_id)

    def _resolve_parallel(self, target_count: int) -> int:
        return min(positive_int_or_default(getattr(self.args, "parallel", self.DEFAULT_PARALLEL), self.DEFAULT_PARALLEL, "-parallel"), target_count)
