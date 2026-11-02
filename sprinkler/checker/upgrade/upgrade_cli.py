"""升级功能 CLI 入口 —— 由 auto_exec() 调用。

命令分发：
- -upgrade-init  → scenario="initial"
- -upgrade-run   → scenario="daily"
- -upgrade-batch → scenario="batch"
"""

import datetime
from pathlib import Path

from sprinkler.arguments import get_args
from sprinkler.base.log import RunLog
from sprinkler.db.database import init_db
from sprinkler.db import dao


def upgrade_exec() -> int:
    """升级功能 CLI 入口；无升级参数时返回 0。"""
    args = get_args()
    if not (getattr(args, "upgrade_init", False)
            or getattr(args, "upgrade_run", False)
            or getattr(args, "upgrade_batch", False)):
        return 0

    scenario = _detect_scenario(args)
    return _run_upgrade(args, scenario)


def _detect_scenario(args) -> str:
    if getattr(args, "upgrade_init", False):
        return "initial"
    if getattr(args, "upgrade_run", False):
        return "daily"
    return "batch"


def _run_upgrade(args, scenario: str) -> int:
    """统一升级执行入口。"""
    run_log = RunLog(args, default_check_id=f"upgrade_{scenario}")
    conn = init_db()
    try:
        # 1. 解析目标
        targets = _resolve_targets(args, conn, scenario, run_log)
        if not targets:
            print("未选择升级目标")
            return 1

        # 2. 解析固件包
        packages = _resolve_packages(args, conn, scenario)
        if not packages:
            print("未指定固件包")
            return 1

        # 3. dry-run
        if getattr(args, "upgrade_dry_run", False):
            return _print_plan(targets, packages, scenario, args)

        # 4. 创建升级任务
        task_id = dao.create_upgrade_task(
            conn, name=_task_name(args, scenario), scenario=scenario,
            cluster_id=getattr(args, "cluster_id", None),
            strategy="auto",
            parallel=getattr(args, "parallel", 8),
        )

        # 5. 执行升级
        from sprinkler.checker.upgrade.orchestrator import UpgradeOrchestrator
        orchestrator = UpgradeOrchestrator(
            conn, task_id, run_log=run_log,
            parallel=getattr(args, "parallel", 8),
        )
        results = orchestrator.run(
            targets, packages, scenario,
            strategy="auto",
            timeout=getattr(args, "upgrade_timeout", 1800),
        )

        # 6. 输出结果摘要
        succeeded = sum(1 for r in results if r.ok)
        failed = sum(1 for r in results if not r.ok)
        print(f"\n升级完成: 成功 {succeeded} / 失败 {failed} / 总计 {len(results)}")
        for r in results:
            status = "✅" if r.ok else "❌"
            ver = f" {r.version_before}→{r.version_after}" if r.version_after else ""
            print(f"  {status} [{r.host}] {r.component} via {r.channel}{ver}"
                  + (f" — {r.error}" if r.error else ""))

        return 1 if failed else 0

    finally:
        conn.close()


def _resolve_targets(args, conn, scenario: str, run_log) -> list:
    """解析升级目标列表。

    优先级：
    1. -cluster-id → 从数据库 blade 表查询
    2. -sp/-ca/-node/-ip → 从 TargetContext 解析（复用现有逻辑）
    """
    cluster_id = getattr(args, "cluster_id", None)
    if cluster_id is not None:
        return _targets_from_cluster(conn, cluster_id, run_log)

    # 复用 TargetContext
    from sprinkler.connect.targets import TargetContext
    target_ctx = TargetContext(args)
    # 批量场景默认查询 cpu_bmc + npu_bmc
    target_types = ["cpu_bmc", "npu_bmc", "roce_bmc"]
    try:
        return target_ctx.get_targets_by_types(target_types)
    except (ValueError, FileNotFoundError):
        try:
            return target_ctx.get_targets("cpu_bmc")
        except (ValueError, FileNotFoundError):
            return []


def _targets_from_cluster(conn, cluster_id: int, run_log) -> list:
    """从数据库 cluster → cabinet → blade 查询升级目标。"""
    from sprinkler.checker.upgrade.credentials import blade_to_connection_target
    blades = []
    cabinets = dao.list_cabinets(conn, cluster_id)
    for cab in cabinets:
        for blade in dao.list_blades(conn, cab["id"]):
            if blade.get("bmc_ip"):
                kind = f"{blade['device_type'].lower()}_bmc" \
                    if blade["device_type"] in ("cpu", "npu", "sfu", "RoCE") else "cpu_bmc"
                blades.append(blade_to_connection_target(blade, kind=kind))
    return blades


def _resolve_packages(args, conn, scenario: str) -> dict:
    """解析固件包映射 {component: path}。

    逻辑：
    1. -upgrade-firmware-dir 指定时 → 本地模式，从目录扫描固件包文件
    2. 否则 → 从 firmware_package 表查询已登记的固件包
    """
    components_str = getattr(args, "upgrade_components", "") or ""
    components = ([c.strip() for c in components_str.split(",") if c.strip()]
                  if components_str else
                  ["ibmc", "bios", "cpld", "npu_firmware"])

    fw_dir = getattr(args, "upgrade_firmware_dir", None)

    packages = {}
    if fw_dir:
        from sprinkler.checker.upgrade.firmware_repo import FIRMWARE_DIR
        # 从数据库查找该目录下已登记的包
        for comp in components:
            pkgs = dao.list_firmware_packages(conn, component=comp)
            for pkg in pkgs:
                pkg_path = Path(pkg["file_path"])
                if not pkg_path.is_absolute():
                    pkg_path = FIRMWARE_DIR.parent / pkg_path
                if str(pkg_path.parent).startswith(str(Path(fw_dir).expanduser())):
                    packages[comp] = str(pkg_path)
                    break
        return packages

    # 默认：从数据库查找已登记固件包
    from sprinkler.checker.upgrade.firmware_repo import resolve_firmware_path
    for comp in components:
        pkgs = dao.list_firmware_packages(conn, component=comp)
        if pkgs:
            try:
                packages[comp] = resolve_firmware_path(conn, pkgs[0]["id"])
            except ValueError:
                pass
    return packages


def _print_plan(targets, packages, scenario: str, args) -> int:
    """打印升级计划（dry-run）。"""
    print("=== 升级计划 (dry-run) ===")
    print(f"场景: {scenario}")
    print(f"目标数: {len(targets)}")
    print(f"固件包: {len(packages)} 个组件")
    for comp, path in packages.items():
        print(f"  {comp}: {path}")
    print("\n目标列表:")
    for t in targets:
        host = getattr(t, "host", "") or str(t.get("host", ""))
        kind = (getattr(t, "meta", {}) or {}).get("kind", "")
        print(f"  {host} ({kind})")
    return 0


def _task_name(args, scenario: str) -> str:
    """生成任务名称。"""
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"upgrade_{scenario}_{ts}"
