"""BMC 设备状态巡检主入口（``-status-query``）。

复用版本查询的 ``RedfishChannel`` / ``RedfishReader`` 通道与目标收集骨架，
通过 ``DeviceStatusParser`` 产出"设备状态巡检表"（每 BMC 一行：在线 /
BMC 健康 / 电源状态 / 功率 / UID / 型号 / 序列号 / 事件告警 / 固件），
对应 PoDM ``/UI/Rest/Maintenance/DoManageInfo`` 中可由 BMC Redfish 直接
获取的部分；不涉及 RackD 私有纳管字段（纳管状态 / 告警计数 / OS 域）。

两个入口：

- ``status_query_execute(args)``：CLI 编排入口，经 ``TargetContext`` 收集
  CPU / NPU BMC 目标（按 host 去重）后批量巡检，结果写入 results.json /
  results.csv（逻辑与版本查询一致）。
- ``collect_status(targets, ...)``：对给定目标列表直接巡检并返回结果行，
  供 Streamlit 页面等已有目标的调用方复用。

成功判定：某目标连接 / 认证失败（通道级错误）或全部核心状态资源不可达时
判为失败（离线）；解析层"尽力而为"，单资源缺失只留空字段不影响整体。
"""

from sprinkler.base.log import RunLog
from sprinkler.connect.targets import ConnectionTarget, TargetContext
from sprinkler.utils.utils import positive_int_or_default

from sprinkler.checker.status.parsers import DeviceStatusParser
from sprinkler.checker.version.query.channels.redfish import RedfishChannel
from sprinkler.checker.version.query.registry import bmc_identification_patterns

# 检查项标识：-status-query 的 RunLog 默认 check_id（日志目录与记录命名用）。
CHECK_ID = "status_query"

# 默认并行度：-parallel 未指定时的线程池并发数（与版本查询一致）。
DEFAULT_PARALLEL = 8

# 巡检的 BMC 目标类型（CPU / NPU，按 host 去重）。
_STATUS_TARGET_TYPES = ("cpu_bmc", "npu_bmc")


def _collect_targets_dedup(target_ctx, target_types: tuple = _STATUS_TARGET_TYPES) -> list:
    """按多个目标类型收集目标并按 host 去重；单类型失败时跳过语义返回。

    与版本查询 RedfishQueryStrategy 的去重规则一致：CPU BMC 与 NPU BMC 可能
    共享同一 BMC IP（整机管理口），同 host 只保留第一个。
    """
    collected, seen_hosts = [], set()
    for target_type in target_types:
        try:
            for target in target_ctx.get_targets(target_type):
                host = getattr(target, "host", None) or ""
                if host and host in seen_hosts:
                    continue
                seen_hosts.add(host)
                collected.append(target)
        except Exception:
            continue
    return collected


def collect_status(targets: list, timeout: int = 30, parallel: int = 8,
                   run_log: RunLog = None) -> list:
    """对给定 BMC 目标列表执行设备状态巡检，返回结果行列表（每目标一行）。

    复用 ``RedfishChannel`` 批量采集（建会话 + 识别 CPU/NPU + 产 RawResult），
    逐目标交给 ``DeviceStatusParser`` 取值，并在任务结束后释放每个会话。

    参数：
        targets: ``ConnectionTarget`` 或 dict 目标列表（须含 host / user /
            password，可经 ``_target_creds`` 兼容）。
        timeout: 单次 Redfish 请求超时秒数。
        parallel: 批量巡检并发数（>1 时使用线程池并行）。
        run_log: 日志对象；可为 None（不记日志）。

    返回：
        结果行列表（字典），字段见 ``DeviceStatusInfo``；每个失败目标都有
        ``ok=False`` 与 ``reason``，不会中断整批巡检。
    """
    channel = RedfishChannel(bmc_patterns=bmc_identification_patterns(),
                             timeout=timeout, parallel=parallel)
    parser = DeviceStatusParser()
    raw_results = channel.collect(targets, args=None, run_log=run_log, timeout=timeout)
    rows = []
    for raw in raw_results:
        rows.append(_render_row(raw, parser))
        _close_session(raw)
    return rows


def _render_row(raw, parser: DeviceStatusParser) -> dict:
    """渲染单个 RawResult 为巡检结果行。

    通道级错误（连接 / 认证失败）直接出失败行；否则交解析器，按其返回的
    (字段, ok, reason) 组装结果行（target / host / ip / type / ok / reason
    为通用字段，inline 进解析产生的状态字段）。

    目标可能是 ``ConnectionTarget`` 或 dict，统一经 ``from_value`` 归一化后
    读取通用字段（与 ``QueryStrategy._build_row`` 行为一致）。
    """
    raw.target = ConnectionTarget.from_value(raw.target)
    target = raw.target
    row = {
        "target": target.label(),
        "host": target.host,
        "ip": target.host,
        "type": raw.target_type or "",
        "ok": False,
        "reason": "",
    }
    if raw.error:
        row["reason"] = raw.error
        return row
    fields, ok, reason = parser.parse(raw)
    row.update(fields or {})
    row["ok"] = ok
    row["reason"] = reason
    return row


def _close_session(raw):
    """释放 Redfish 会话；会话对象缺失或关闭异常时静默返回。"""
    reader = getattr(raw, "data", None)
    if reader is not None and hasattr(reader, "close"):
        try:
            reader.close()
        except Exception:
            pass


def status_query_execute(args) -> int:
    """``-status-query`` 的 CLI 入口。全部目标成功返回 0，否则返回 1。

    流程：解析运行期参数 → 经 TargetContext 收集 CPU / NPU BMC 目标（按
    host 去重）→ 批量巡检 → 写 results.json / results.csv → 按失败数返回。
    """
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")

    timeout = positive_int_or_default(getattr(args, "query_timeout", 30), 30, "-query-timeout")
    parallel = positive_int_or_default(getattr(args, "parallel", DEFAULT_PARALLEL),
                                       DEFAULT_PARALLEL, "-parallel")

    target_ctx = TargetContext(args)
    targets = _collect_targets_dedup(target_ctx)
    if not targets:
        run_log.warn("no BMC targets selected for status query")
        return 1

    run_log.info(f"status targets={len(targets)} parallel={parallel}")
    rows = collect_status(targets, timeout=timeout, parallel=parallel, run_log=run_log)
    run_log.write_results(rows)

    fail_count = sum(1 for r in rows if not r.get("ok"))
    if fail_count > 0:
        run_log.fail(f"status_query completed with {fail_count} failures")
        return 1
    run_log.ok(f"status_query completed successfully for all {len(rows)} targets")
    return 0
