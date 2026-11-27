"""``-bios-inspect`` 命令行入口：批量只读巡检 CPU BMC 的 BIOS 高性能配置。"""

import getpass
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import List, Optional

from sprinkler.base.log import RunLog
from sprinkler.checker.bios.inspector import (
    BiosInspectError,
    BiosInspector,
    build_reader,
    build_rows,
)
from sprinkler.checker.bios.whitelist import load_whitelist
from sprinkler.connect.targets import NodeIpSpec, TargetContext
from sprinkler.utils.utils import positive_int_or_default

CHECK_ID = "bios_inspect"
DEFAULT_PARALLEL = 8
DEFAULT_TIMEOUT = 20
DEFAULT_REDFISH_PORT = 443


def validate_target_options(args) -> None:
    """校验 BIOS 巡检的目标/凭据组合。

    使用 ``-sp/-ca/-xlsx`` 或结构化 ``-node`` 时，BMC 账号密码来自配置表格，
    再传 ``-user/-pwd`` 会被静默忽略，因此这里直接报错。``-ip`` 与
    ``-node`` 的 IP 规格（``ip;user;pwd``）继续支持逐台/共享凭据。
    """
    if not getattr(args, "bios_inspect", False):
        return
    node_text = str(getattr(args, "node", "") or "").strip()
    structured_node = bool(node_text) and not NodeIpSpec.is_ip_spec(node_text)
    cluster_source = any(str(getattr(args, name, "") or "").strip()
                         for name in ("sp", "ca", "xlsx")) or structured_node
    if not cluster_source:
        return
    user = str(getattr(args, "user", "root") or "root")
    password = str(getattr(args, "pwd", "") or "")
    if user != "root" or password:
        raise ValueError(
            "-bios-inspect 使用 -sp/-ca/-xlsx 或结构化 -node 时，"
            "BMC 账号密码来自配置表格，不能再传 -user/-pwd"
        )


def prompt_missing_passwords(targets):
    """单台且未提供密码时，用标准 Linux 隐藏输入提示一次密码。

    - 多台不逐个提示：直接报错，要求使用 ``-pwd``/``-ip`` 凭据或 xlsx；
    - 非交互终端（CI、管道、cron）不提示，避免卡住或回显密码；
    - ``ConnectionTarget`` 是 frozen dataclass，用 ``replace`` 生成新对象。
    """
    missing = [target for target in targets if not target.password]
    if not missing:
        return targets
    if len(targets) != 1:
        raise ValueError(
            f"{len(missing)} 台 BMC 未提供密码；多台巡检请使用 -pwd、-ip 凭据或 xlsx，"
            "交互输入密码仅支持单台"
        )
    target = targets[0]
    if not sys.stdin.isatty():
        raise ValueError(
            f"未提供 {target.user}@{target.host} 的 BMC 密码，且当前不是交互终端；"
            "请使用 -pwd 传入密码"
        )
    try:
        password = getpass.getpass(f"请输入 BMC 密码 ({target.user}@{target.host}): ")
    except (KeyboardInterrupt, EOFError):
        raise ValueError("已取消密码输入")
    if not password:
        raise ValueError("密码为空，已取消")
    return [replace(target, password=password)]


def _target_label(target) -> str:
    return str(getattr(target, "name", "") or getattr(target, "host", "") or target)


def _target_creds(target):
    host = str(getattr(target, "host", "") or "").strip()
    user = str(getattr(target, "user", "") or "Administrator").strip() or "Administrator"
    password = str(getattr(target, "password", "") or "")
    try:
        port = int(getattr(target, "port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    meta = getattr(target, "meta", {}) or {}
    # 只有用户在 -ip/-node 里显式写了 IP:port 才沿用该端口；xlsx 里的 22
    # 是 SSH 默认端口，不能拿来访问 Redfish，未显式指定时统一用 443。
    redfish_port = port if meta.get("port_explicit") and port else DEFAULT_REDFISH_PORT
    return host, user, password, redfish_port


def _inspect_target(target, whitelist, system_id: str, timeout: int) -> dict:
    """采集单个 BMC；异常转换为结果行里的 error，不影响其它节点。"""
    label = _target_label(target)
    result = {"node": label, "rows": [], "missing": [], "warnings": [], "error": ""}
    inspector = None
    try:
        host, user, password, port = _target_creds(target)
        reader = build_reader(host, user, password, timeout, port)
        try:
            inspector = BiosInspector(reader, system_id)
            inspector.load(whitelist.aliases())
            rows, missing = build_rows(whitelist, inspector.current, inspector.registry_meta)
        finally:
            reader.close()
    except (BiosInspectError, ValueError) as exc:
        result["error"] = str(exc)
        return result
    except Exception as exc:  # 单节点失败不阻断集群巡检
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    for row in rows:
        row["Node"] = label
    result.update(rows=rows, missing=missing, warnings=inspector.warnings)
    return result


TABLE_HEADER = "属性名 | 配置项 | 当前值 | 配置项路径"


def _console_row(row: dict) -> str:
    """按固定列顺序输出一行：中文名 | 配置项 | 当前值 | 配置项路径。"""
    return " | ".join([
        str(row.get("ZhName") or "-"),
        str(row.get("Attribute") or "-"),
        str(row.get("CurrentValue") or "-"),
        str(row.get("FullMenuPath") or "-"),
    ])


def _log_rows(run_log: RunLog, rows: List[dict]) -> None:
    for row in rows:
        run_log.info(_console_row(row))


def split_keywords(value: Optional[str]) -> List[str]:
    """把 ``PowerPolicy|ConfigTDP`` 拆成去重（大小写不敏感）的关键字列表。"""
    if not value:
        return []
    keywords: List[str] = []
    seen = set()
    for part in str(value).split("|"):
        keyword = part.strip()
        if not keyword or keyword.lower() in seen:
            continue
        seen.add(keyword.lower())
        keywords.append(keyword)
    return keywords


def bios_inspect_execute(args) -> int:
    """只读巡检入口。返回 0 表示各节点查询均成功。"""
    run_log = RunLog(args, CHECK_ID)
    try:
        validate_target_options(args)
        whitelist = load_whitelist()
        keywords = split_keywords(getattr(args, "bios_whitelist", None))
        whitelist, unmatched = whitelist.select(keywords)
        if unmatched:
            raise ValueError(
                "以下关键字未匹配任何白名单条目: " + "|".join(unmatched)
                + "（按名称/别名精确匹配，不支持子串与分类名）")
        if not whitelist.items:
            raise ValueError("白名单中没有可巡检的条目（请检查 enabled 或关键字）")
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    try:
        targets = TargetContext(args).get_targets("cpu_bmc")
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)
    if not targets:
        return run_log.fail_error(CHECK_ID, ValueError("no cpu_bmc targets selected"))
    try:
        targets = prompt_missing_passwords(targets)
    except Exception as exc:
        return run_log.fail_error(CHECK_ID, exc)

    system_id = str(getattr(args, "bios_system_id", "1") or "1")
    timeout = positive_int_or_default(
        getattr(args, "bios_timeout", DEFAULT_TIMEOUT), DEFAULT_TIMEOUT, "-bios-timeout")
    parallel = positive_int_or_default(
        getattr(args, "parallel", DEFAULT_PARALLEL), DEFAULT_PARALLEL, "-parallel")
    workers = min(parallel, len(targets))

    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_inspect_target, target, whitelist, system_id, timeout): target
            for target in targets
        }
        for future in as_completed(futures):
            results.append(future.result())

    ordered = sorted(results, key=lambda item: item["node"])
    run_log.info(TABLE_HEADER)
    run_log.info("-" * 100)

    errors = 0
    for index, result in enumerate(ordered, start=1):
        node = result["node"]
        run_log.info(f"节点{index} ：{node}      "
                     "BMC 菜单：系统管理 -> BIOS配置 -> BIOS全量配置菜单")
        if result["error"]:
            errors += 1
            run_log.fail(f"{node}: {result['error']}")
            continue
        for warning in result["warnings"]:
            run_log.info(f"{node}: {warning}")
        if result["missing"]:
            run_log.info(f"{node}: 当前 BIOS 未暴露项: {', '.join(result['missing'])}")
        _log_rows(run_log, result["rows"])

    run_log.info(f"bios_summary nodes={len(targets)} errors={errors} "
                 f"attributes={len(whitelist.items)}")
    run_log.finish_log_dir()
    return 0 if not errors else 1
