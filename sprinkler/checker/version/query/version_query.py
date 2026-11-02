"""``-version-query`` 命令入口。

按 ``-query-mode`` 编排跨 BMC（Redfish）、OS（SSH）、前台交换机
（switch，C 平面 SSH 直连）三个通道的批量版本采集。先经 ``TargetContext``
解析目标列表，再通过 ``build_channel_strategies`` 从注册表组装各通道的
策略集合并串联执行，结果写入 results.json / results.csv，构建 HTML 报告，
并可选执行合规检查。仅当所有目标成功且合规通过时返回 0。

支持的模式与选项：
- ``-query-mode``：redfish / ssh / frontend / hybrid（默认，全部通道执行）
- ``-component-filter``：按组件组裁剪通道（如只查 BMC、BIOS）
- ``-parallel``：Redfish 批量查询的并发数
- ``-dry-run``：只打印查询计划，不连接任何设备
- ``-compliance-only``：跳过设备查询，仅基于已有 results.json 做合规比对
"""

import json
from pathlib import Path

from sprinkler.base.log import RunLog
from sprinkler.connect.targets import TargetContext
from sprinkler.utils.utils import positive_int_or_default
from sprinkler.checker.version.query.factory import build_channel_strategies
from sprinkler.checker.version.query.report import VersionReportBuilder, ComplianceChecker
from sprinkler.checker.version.query.registry import (
    CHANNELS, DEVICES, target_types_by_channel,
    device_for_target_type, devices_in_channel, merge_field_map,
)
from sprinkler.checker.version.query.switch_targets import collect_switch_targets

# 检查项标识：-version-query 的 RunLog 默认 check_id（日志目录与记录命名用）。
CHECK_ID = "version_query"

# 默认并行度：-parallel 未指定时的线程池并发数。
DEFAULT_PARALLEL = 8

# 组件 / 目标类型 / 合并规则全部来源于 registry（单一事实来源）。
# 新增通道 = 添加 ChannelSpec 并在 factory.py 注册通道类；
# 新增设备/字段 = 在 registry 中添加 DeviceSpec/FieldSpec。
def _mode_applies(query_mode: str, channel) -> bool:
    """判断某通道是否在指定查询模式下启用。

    通道在其专属模式（``query_mode == channel.mode_key``）或混合模式
    （``query_mode == "hybrid"``）下启用。
    """
    return query_mode in (channel.mode_key, "hybrid")


def version_query_execute(args) -> int:
    """``-version-query`` 的 CLI 入口。全部成功返回 0，否则返回 1。

    执行流水线：
    1. 解析运行期参数（query_mode / component_filter / timeout / parallel）
    2. 特殊模式分流：dry-run（仅打印计划）、compliance-only（仅合规比对）
    3. 解析目标上下文（TargetContext，含 Excel / ``-ip`` 目标源）
    4. 遍历注册表 CHANNELS，对启用的通道调用 ``build_channel_strategies``
       组装策略并串联执行，收集全部结果行
    5. hybrid 模式下按 IP 合并各通道结果（BMC + OS + 前台）
    6. 写结果文件（results.json / results.csv）、生成 HTML 报告
    7. 可选合规检查；任一目标失败或合规不通过时返回 1
    """
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")

    query_mode = str(getattr(args, "query_mode", "hybrid") or "hybrid").lower()
    component_filter = _parse_component_filter(getattr(args, "component_filter", "") or "")
    timeout = positive_int_or_default(getattr(args, "query_timeout", 30), 30, "-query-timeout")
    parallel = positive_int_or_default(getattr(args, "parallel", DEFAULT_PARALLEL), DEFAULT_PARALLEL, "-parallel")

    if getattr(args, "dry_run", False):
        return _dry_run(args, run_log, query_mode, component_filter)
    if getattr(args, "compliance_only", False):
        return _compliance_only(args, run_log)

    target_ctx = TargetContext(args)
    results = []

    # 通过 registry 分发所有适用通道（redfish/ssh/frontend）。
    # 每个通道都经工厂组装出策略集合，再串联执行（Redfish 通道一次识别全类型，
    # 命令通道按设备目标类型各自执行）。
    for channel in CHANNELS:
        if not _mode_applies(query_mode, channel):
            continue
        if _component_filtered(run_log, component_filter, set(channel.components), channel.label):
            continue
        strategies = build_channel_strategies(
            channel.mode_key, args=args, run_log=run_log,
            timeout=timeout, parallel=parallel)
        if not strategies:
            run_log.warn(f"no strategy registered for channel {channel.mode_key!r}, skipping")
            continue
        run_log.info(f"channel {channel.mode_key}: strategies={len(strategies)} parallel={parallel}")
        for strategy in strategies:
            results.extend(strategy.query_all(target_ctx=target_ctx))

    if query_mode == "hybrid":
        results = _merge_all_results(results)

    run_log.write_results(results)
    report_path = _build_report(args, run_log, results)
    if report_path:
        run_log.info(f"report={report_path}")

    compliance_ok = _run_compliance(args, run_log, results)

    fail_count = sum(1 for r in results if not r.get("ok"))
    if fail_count > 0:
        run_log.fail(f"version_query completed with {fail_count} failures")
        return 1
    if not compliance_ok:
        run_log.fail("version_query completed but compliance check failed")
        return 1
    run_log.ok(f"version_query completed successfully for all {len(results)} targets")
    return 0


def _component_filtered(run_log, component_filter, components, label) -> bool:
    """返回 True 表示该通道应被 -component-filter 跳过。

    当用户给出组件过滤且本通道的组件组与其无交集时跳过该通道（并打日志）。
    组件组取自 registry 的 ChannelSpec.components。
    """
    if component_filter and not (component_filter & components):
        run_log.info(f"component-filter excludes {label} components, skipping {label} query")
        return True
    return False


def _merge_all_results(results: list) -> list:
    """将同一 IP 的 BMC / OS / 前台交换机行合并为单行（hybrid 模式）。

    前台交换机（switch）使用独立于 BMC 的前台管理 IP（SSH 直连），其
    CC/CFG 字段以独立字段名（switch_cc/switch_cfg）保留为单独行。
    OS 行仅在 IP 匹配时合并进 BMC 行；否则 OS 行保持独立。

    合并规则（哪种目标类型合并进基准行、字段如何重命名）由
    ``registry.DEVICES`` 驱动 —— 参见 ``DeviceSpec.field_map``。
    """
    # 非基准（要合并进去的）目标类型：sshd/frontend 通道拥有的类型。
    merge_in_types = {d.target_type for d in DEVICES if d.channel in ("ssh", "frontend")}
    bmc_rows = [r for r in results if r.get("type") not in merge_in_types]
    os_rows = {r.get("ip") or r.get("host") or "": r for r in results
               if r.get("type") == "cpu_os"}
    frontend_rows = [r for r in results if (r.get("type") or "") in {
        d.target_type for d in devices_in_channel("frontend")}]

    merged = []
    for row in bmc_rows:
        ip = row.get("ip") or row.get("host") or ""
        os_row = os_rows.get(ip)
        if os_row:
            for src, dst in merge_field_map("cpu_os"):
                row[dst] = os_row.get(src, "")
            if not row.get("ok") and os_row.get("ok"):
                row["ok"], row["reason"] = True, ""
        merged.append(row)

    bmc_by_ip = {r.get("ip") or r.get("host") or "": r for r in merged}
    standalone_frontend = []
    for fe_row in frontend_rows:
        ip = fe_row.get("ip") or fe_row.get("host") or ""
        fe_type = fe_row.get("type") or ""
        field_map = dict(merge_field_map(fe_type))
        dev = device_for_target_type(fe_type)
        merges_into_bmc = bool(dev and dev.merge_into_bmc)
        bmc_row = bmc_by_ip.get(ip) if merges_into_bmc else None
        if bmc_row and field_map:
            for src, dst in field_map.items():
                bmc_row[dst] = fe_row.get(src, "")
            if not bmc_row.get("ok") and fe_row.get("ok"):
                bmc_row["ok"], bmc_row["reason"] = True, ""
        else:
            # 独立的前台行（无匹配 BMC 基准行，或非合并设备，如 switch）：
            # 就地重命名其内部字段（例如 cc_version -> switch_cc）
            for src, dst in field_map.items():
                fe_row[dst] = fe_row.pop(src, "")
            standalone_frontend.append(fe_row)

    merged_bmc_ips = {r.get("ip") or r.get("host") or "" for r in merged}
    ssh_types = {d.target_type for d in devices_in_channel("ssh")}
    standalone_os = [r for r in results if (r.get("type") or "") in ssh_types
                     and (r.get("ip") or r.get("host") or "") not in merged_bmc_ips]
    return merged + standalone_frontend + standalone_os


def _build_report(args, run_log, results) -> str:
    """按 ``-report-format`` 在日志目录生成报告，返回文件路径；关闭或空结果时返回空串。"""
    if not getattr(args, "report", True) or not results:
        return ""
    report_format = str(getattr(args, "report_format", "html") or "html").lower()
    builder = VersionReportBuilder(report_format=report_format)
    output_path = str(run_log.path / f"report.{report_format}") if run_log.path is not None else None
    return builder.build(results, output_path=output_path)


def _run_compliance(args, run_log, results) -> bool:
    """执行合规检查：读取 -expected-versions 清单并与 results 比对。

    - 未提供清单：直接通过（返回 True）。
    - 清单无效：判失败并返回 False。
    - 有不匹配项：逐个告警、把明细写入 compliance.json，返回 False。
    """
    expected_path = str(getattr(args, "expected_versions", "") or "").strip()
    if not expected_path:
        return True
    try:
        manifest = json.loads(Path(expected_path).expanduser().read_text(encoding="utf-8"))
    except Exception as exc:
        run_log.fail(f"expected-versions file invalid: {exc}")
        return False
    non_compliant = ComplianceChecker().compliance_check(results, manifest)
    if not non_compliant:
        run_log.ok("compliance check passed: all components match expected versions")
        return True
    run_log.fail(f"compliance check failed: {len(non_compliant)} component(s) mismatched")
    for item in non_compliant:
        run_log.fail(f"  {item['target']} ({item['ip']}) {item['component']}: "
                     f"expected={item['expected']} actual={item['actual']}")
    if run_log.path is not None:
        run_log.write_json("compliance.json", non_compliant)
    return False


def _dry_run_list(run_log, label, targets):
    """记录演练模式下某通道的目标数量与每个目标的行。"""
    run_log.info(f"  {label} targets: {len(targets)}")
    for t in targets:
        run_log.info(f"    - {t.label()} ({t.host})")


def _dry_run(args, run_log, query_mode, component_filter) -> int:
    """演练模式：仅打印将查询的目标计划，不连接任何设备，返回 0/1。

    按 registry 的 CHANNELS 遍历适用的通道，受 mode 与组件过滤约束；
    对每个通道列出目标类型与目标清单。
    """
    run_log.info("[dry-run] version query plan:")
    run_log.info(f"  query_mode={query_mode} component_filter={sorted(component_filter) or 'ALL'}")
    try:
        target_ctx = TargetContext(args)
    except Exception as exc:
        run_log.fail(f"target resolution failed: {exc}")
        return 1

    for channel in CHANNELS:
        if not _mode_applies(query_mode, channel):
            continue
        if _component_filtered(run_log, component_filter, set(channel.components), channel.label):
            continue
        if channel.mode_key == "redfish":
            # Redfish 通道需跨目标类型收集并按 host 去重（与 RedfishQueryStrategy 一致）
            _dry_run_list(run_log, "BMC (redfish)", _collect_targets_dedup(
                target_ctx, target_types_by_channel().get("redfish", ()), run_log))
        elif channel.mode_key == "ssh":
            try:
                _dry_run_list(run_log, "OS (ssh)", target_ctx.get_targets("cpu_os"))
            except Exception as exc:
                run_log.warn(f"  OS targets unavailable: {exc}")
        else:
            for tt in channel.target_types:
                try:
                    # switch 目标来源不在 TargetContext 能力范围，使用 checker
                    # 内的独立收集器（从 Excel 的 frontend_host / com_host 收集）
                    if tt == "switch":
                        targets = collect_switch_targets(args)
                        if not targets:
                            run_log.warn("  switch targets unavailable")
                            continue
                        _dry_run_list(run_log, f"{tt} (frontend)", targets)
                        continue
                    _dry_run_list(run_log, f"{tt} (frontend)", target_ctx.get_targets(tt))
                except Exception as exc:
                    run_log.warn(f"  {tt} targets unavailable: {exc}")
    return 0


def _collect_targets_dedup(target_ctx, target_types: tuple, run_log= None) -> list:
    """按多个目标类型收集目标并按 host 去重；单类型失败时跳过并告警。"""
    collected, seen_hosts = [], set()
    for target_type in target_types:
        try:
            for target in target_ctx.get_targets(target_type):
                host = getattr(target, "host", None) or ""
                if host and host in seen_hosts:
                    continue
                seen_hosts.add(host)
                collected.append(target)
        except Exception as exc:
            if run_log is not None:
                run_log.warn(f"skip target type {target_type}: {exc}")
    return collected


def _compliance_only(args, run_log) -> int:
    """跳过设备查询；加载之前的 results.json 仅执行合规检查。"""
    expected_path = str(getattr(args, "expected_versions", "") or "").strip()
    if not expected_path:
        run_log.fail("-compliance-only requires -expected-versions to be set")
        return 1
    if run_log.path is None:
        run_log.fail("-compliance-only requires a run log directory (log_dir) to load results.json")
        return 1
    results_path = run_log.path / "results.json"
    if not results_path.exists():
        run_log.fail(f"results.json not found in {run_log.path}; run a query first without -compliance-only")
        return 1
    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    except Exception as exc:
        run_log.fail(f"results.json invalid: {exc}")
        return 1
    run_log.info(f"loaded {len(results)} result(s) from {results_path} for compliance-only check")
    if not _run_compliance(args, run_log, results):
        run_log.fail("compliance-only check failed")
        return 1
    run_log.ok("compliance-only check passed")
    return 0


def _parse_component_filter(raw: str) -> set:
    """解析 -component-filter 的逗号分隔字符串为全大写组件集合；空则返回空集。"""
    if not raw or not raw.strip():
        return set()
    return {item.strip().upper() for item in raw.split(",") if item.strip()}
