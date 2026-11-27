"""PRBS 全量打流流程编排（实现思路 a~g）。

流程：
  a) 确保 NPU 免密可用，并下发 /root/serdestool.bin
  b) NPU 各 macro/lane 开启 prbs31（tx+rx）
  c) 各台 5808 前台按端口映射开启 prbs31
  c2) 开流后读取一轮状态做就绪校验（NPU 每 macro 4 lane、5808 每端口 en=1）
  d) 读取 NPU 侧误码率
  e) 读取各台 5808 侧 prbs 状态
  f) 关闭 NPU 侧 prbs
  g) 关闭 5808 侧 prbs（异常中断时也会执行 f/g 清理）

PRBS 误码率依赖对端发送 PRBS，因此 ``-ip``（5808 前台管理 IP）为必填，
未指定时在预检阶段报错退出；``-ip`` 支持 ``IP:port`` 和逗号分隔的多台
5808，登录参数为 ``-user``/``-pwd``。
开流完成后先做一轮就绪校验，未就绪不进入有效采样。
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Sequence, Tuple

from sprinkler.auto.prbs.serdes import (
    DEFAULT_LANES,
    DEFAULT_TIMEOUT,
    MAX_MACRO_INDEX,
    REMOTE_TOOL_PATH,
    NpuPrbs,
    check_status_ready,
    format_status_report,
    parse_status,
)
from sprinkler.auto.prbs.ssh_env import default_ranks, ensure_no_pass
from sprinkler.auto.prbs.switch_5808 import (
    Switch5808,
    SwitchPort,
    check_prbs_ready,
    command_succeeded,
    format_get_report,
    parse_prbs_output,
)
from sprinkler.auto.prbs.tool import ensure_serdestool
from sprinkler.base.log import RunLog
from sprinkler.base.ranges import parse_range
from sprinkler.checker.serdes.pn import build_local_npu_items
from sprinkler.logging import Colors

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_PASS = 2
EXIT_TOOL = 3
EXIT_RUN = 4


def selected_ranks(args) -> List[int]:
    """-rank 指定范围；未指定时用 host/device pair（兜底 0-7）。"""
    spec = str(getattr(args, "rank", "") or "").strip()
    return parse_range(spec) if spec else default_ranks()


def _parse_macros(args) -> List[int]:
    spec = str(getattr(args, "macros", "") or "").strip()
    macros = parse_range(spec) if spec else list(range(8))
    if not macros:
        raise ValueError("-macros 不能为空")
    if any(macro < 0 for macro in macros):
        raise ValueError("-macros 不支持负数")
    if any(macro > MAX_MACRO_INDEX for macro in macros):
        raise ValueError(f"-macros 超出范围: 合法值 0-{MAX_MACRO_INDEX}")
    return macros


def _parse_limit(args) -> float | None:
    """误码率目标值（-target）；未指定时不做 PASS/FAIL 判定。"""
    value = getattr(args, "target", None)
    if value is None:
        return None
    limit = float(value)
    if not math.isfinite(limit) or limit <= 0:
        raise ValueError("-target 必须是大于 0 的有限数")
    return limit


def _parse_rounds(args) -> int:
    """有效采样轮数；内部额外采集一轮并丢弃，作为每个端口的预热读数。"""
    value = getattr(args, "rounds", 5)
    rounds = int(value) if value is not None else 5
    if rounds < 1:
        raise ValueError("-rounds 至少为 1")
    return rounds


def _parse_interval(args) -> int:
    """相邻两轮读数之间的等待秒数。"""
    value = getattr(args, "interval", 10)
    interval = int(value) if value is not None else 10
    if interval < 0:
        raise ValueError("-interval 不能为负数")
    return interval


def _parse_slots(args, count: int) -> List[int]:
    """slot 唯一标识一台 5808：-slot 按 -ip 顺序一一对应，数量相同且不重复。"""
    raw = getattr(args, "slot", "1")
    spec = "1" if raw is None else str(raw).strip()
    values = [item.strip() for item in spec.split(",")]
    if not spec or any(not item for item in values):
        raise ValueError("-slot 不能为空")
    slots = []
    for value in values:
        try:
            slot = int(value)
        except ValueError:
            raise ValueError(f"-slot 必须是整数: {value}") from None
        if slot < 0:
            raise ValueError("-slot 不支持负数")
        slots.append(slot)
    if len(slots) != count:
        raise ValueError(f"-slot 数量必须与 -ip 数量 {count} 一致，"
                         f"当前 {len(slots)} 个")
    if len(set(slots)) != len(slots):
        raise ValueError("-slot 不能重复：每台 5808 对应唯一 slot")
    return slots


def _build_switches(args) -> List[Switch5808]:
    """按 -ip/-user/-pwd 构造 5808 目标；每个 -ip 对应一台 5808 前台。"""
    if not str(getattr(args, "ip", "") or "").strip():
        raise ValueError("PRBS 误码率测量需要 5808 侧开流，请用 "
                         "-ip <ip>[,<ip>...] 指定 5808 前台管理 IP")
    from sprinkler.connect.targets import TargetContext

    targets = TargetContext(args).get_targets("5808_front")
    if not targets:
        raise ValueError("PRBS 需要至少一个 5808 前台目标")
    slots = _parse_slots(args, len(targets))
    return [
        Switch5808(target.host, user=target.user, password=target.password,
                   port=target.port, slot=slot, args=args, name=target.name)
        for target, slot in zip(targets, slots)
    ]


def _report_npu_commands(label: str, results: Sequence[dict]) -> List[str]:
    """打印 NPU 命令执行结果，返回失败摘要。"""
    failed = [result for result in results if not result.get("ok")]
    print(f"  {label}: {len(results) - len(failed)}/{len(results)} 条命令成功")
    details = []
    for result in failed[:10]:
        reason = _last_line(result.get("stderr") or result.get("stdout")) \
            or f"rc={result.get('rc')}"
        print(f"    {result.get('target')}: {result.get('command')} -> {reason}")
        details.append(f"{label}: {result.get('target')} {reason}")
    if len(failed) > 10:
        print(f"    ... 其余 {len(failed) - 10} 条失败省略")
    return details


def _report_switch_commands(label: str, ports: Sequence,
                            results: Sequence[dict],
                            switch_name: str = "") -> List[str]:
    """打印 5808 命令执行结果，返回失败摘要。"""
    failed = 0
    details = []
    prefix = f"{switch_name} " if switch_name else ""
    for index, port in enumerate(ports):
        result = results[index] if index < len(results) else {}
        if command_succeeded(result):
            continue
        failed += 1
        reason = _last_line(result.get("stderr") or result.get("stdout")) \
            or f"rc={result.get('rc', '?')}"
        print(f"    {prefix}chip{port.chip} port{port.port} "
              f"({port.interface}): {reason}")
        details.append(f"{label}: chip{port.chip} port{port.port} {reason}")
    print(f"  {label}: {len(ports) - failed}/{len(ports)} 个端口成功")
    return details


def _record_port_ber(history: Dict[Tuple[str, int, int, int, int], List[float]],
                     switch_name: str, ports: Sequence,
                     results: Sequence[dict]) -> None:
    """记录本轮每台 5808 每个 lane 的 BER。"""
    for index, port in enumerate(ports):
        result = results[index] if index < len(results) else {}
        if not command_succeeded(result):
            continue
        parsed = parse_prbs_output(result.get("stdout", ""))
        if parsed is None:
            continue
        for lane in parsed.serdes:
            try:
                rate = float(lane["rate"])
            except (KeyError, TypeError, ValueError):
                continue
            key = (switch_name, int(port.chip), int(port.port),
                   int(lane["unit"]), int(lane["serdes"]))
            history.setdefault(key, []).append(rate)


def _record_npu_ber(history: Dict[Tuple[str, int, int, int], List[float]],
                    results: Sequence[dict]) -> None:
    """记录每轮 NPU 侧每个 lane 的 RX BER。"""
    for result in results:
        rows = parse_status(result.get("stdout", ""))
        if not rows:
            continue
        target = result.get("target") or f"NPU{result.get('rank', 0)}"
        for row in rows:
            key = (target, int(row["serdes"]), int(row["macro"]), int(row["lane"]))
            history.setdefault(key, []).append(float(row["ber"]))


def _ber_summary_header(prefix: str, kept_rounds: int,
                        limit: float | None) -> str:
    """汇总标题按实际完成的有效采样轮数生成。"""
    threshold = f"，阈值 {limit:g}" if limit is not None else ""
    if kept_rounds > 0:
        return f"{prefix}（{kept_rounds} 轮有效采样，首轮预热已丢弃{threshold}）:"
    return f"{prefix}（无有效采样轮次{threshold}）:"


def _format_ber_line(prefix: str, worst: float, mean: float,
                     limit: float | None) -> Tuple[str, bool | None]:
    """单条 BER 汇总行；设置阈值时追加 PASS/FAIL 判定。"""
    line = f"{prefix}: max={worst:.6e} mean={mean:.6e}"
    if limit is None:
        return line, None
    passed = worst <= limit
    return f"{line} {'PASS' if passed else 'FAIL'}", passed


def _npu_ber_summary_lines(history: Dict[Tuple[str, int, int, int], List[float]],
                           kept_rounds: int,
                           limit: float | None = None) -> List[Tuple[str, bool | None]]:
    """返回 NPU 侧每个 lane 的 RX BER 汇总行。"""
    rows = []
    for (target, serdes, macro, lane), values in history.items():
        if not values:
            continue
        rows.append(((target, serdes, macro, lane), max(values),
                     sum(values) / len(values)))
    rows.sort(key=lambda item: item[1], reverse=True)

    lines: List[Tuple[str, bool | None]] = [
        (_ber_summary_header("NPU 侧 RX 误码率统计", kept_rounds, limit), None)
    ]
    if not rows:
        lines.append(("  无有效读数", None))
        return lines
    for (target, serdes, macro, lane), worst, mean in rows:
        lines.append(_format_ber_line(
            f"  {target} serdes{serdes} m{macro} L{lane}", worst, mean, limit))
    return lines


def _switch_ber_summary_lines(jobs: Sequence[Tuple[Switch5808, List[SwitchPort]]],
                              history: Dict[Tuple[str, int, int, int, int], List[float]],
                              kept_rounds: int,
                              limit: float | None = None) -> List[Tuple[str, bool | None]]:
    """返回每台 5808 每个 lane 的 RX BER 汇总行（按最大值降序）。"""
    port_info = {}
    for switch, ports in jobs:
        for port in ports:
            port_info[(switch.name, int(port.chip), int(port.port))] = port
    rows = []
    for (switch_name, chip, port_no, unit, serdes), values in history.items():
        if not values:
            continue
        port = port_info.get((switch_name, chip, port_no))
        interface = port.interface if port is not None else "unknown"
        rows.append(((switch_name, chip, port_no, unit, serdes, interface),
                     max(values), sum(values) / len(values)))
    rows.sort(key=lambda item: item[1], reverse=True)

    lines: List[Tuple[str, bool | None]] = [
        (_ber_summary_header("5808 侧 RX 端口误码率统计", kept_rounds, limit), None)
    ]
    for (switch_name, chip, port_no, unit, serdes, interface), worst, mean in rows:
        lines.append(_format_ber_line(
            f"  {switch_name} chip{chip} port{port_no} ({interface}) "
            f"unit{unit} serdes{serdes}", worst, mean, limit))
    seen_ports = {(switch_name, chip, port_no)
                  for (switch_name, chip, port_no, _, _) in history}
    for switch, ports in jobs:
        missing = [port for port in ports
                   if (switch.name, int(port.chip), int(port.port)) not in seen_ports]
        if missing:
            preview = ", ".join(f"chip{port.chip}/port{port.port}"
                                for port in missing[:10])
            suffix = "" if len(missing) <= 10 else f" ... 共 {len(missing)} 个"
            lines.append(
                (f"  {switch.name} 无有效读数端口: {preview}{suffix}", None))
    return lines


def prbs_exec(args) -> int:
    try:
        macros = _parse_macros(args)
        limit = _parse_limit(args)
        rounds = _parse_rounds(args)
        interval = _parse_interval(args)
        switches = _build_switches(args)
    except (ValueError, OSError) as exc:
        print(f"{Colors.RED}ERROR: {exc}{Colors.RESET}")
        return EXIT_USAGE

    run_log = RunLog(args, "PRBS")
    log_file = run_log.path / "run.log" if run_log.path is not None else None
    print(f"PRBS 明细日志: {log_file if log_file is not None else '未启用'}")
    write_detail = run_log.detail

    requested = selected_ranks(args)
    items = build_local_npu_items(args)
    found = {int(item["rank"]) for item in items}
    skipped = [rank for rank in requested if rank not in found]
    if skipped:
        print(f"{Colors.YELLOW}警告: rank {','.join(str(rank) for rank in skipped)} "
              f"没有 host/device pair，已跳过{Colors.RESET}")
    if not items:
        print(f"{Colors.RED}ERROR: 没有可用的 NPU，请检查 -rank 或 /proc/asdrv_ub"
              f"{Colors.RESET}")
        return EXIT_USAGE

    ranks = [int(item["rank"]) for item in items]
    print(f"PRBS 目标 NPU rank: {','.join(str(rank) for rank in ranks)}")
    print("serdestool.bin: NPU 缺失时交互输入 CPU OS 路径（自动写入 SERDES_TOOL_PATH）")
    acquire_rounds = rounds + 1
    print(f"NPU 参数: udie={getattr(args, 'udie', 0)} "
          f"macros={','.join(str(macro) for macro in macros)} lanes={DEFAULT_LANES} "
          f"有效采样={rounds} 轮 + 首轮预热丢弃（共 {acquire_rounds} 轮）")
    if limit is not None:
        print(f"误码率阈值: {limit:g}（超过阈值的 lane 标记 FAIL）")

    if not ensure_no_pass(args, ranks=ranks):
        print("PRBS 中止: NPU 免密不可用")
        return EXIT_NO_PASS

    print("检查 NPU 侧 serdestool.bin ...")
    if not ensure_serdestool(items):
        print("PRBS 中止: serdestool.bin 未就绪")
        return EXIT_TOOL

    npu = NpuPrbs(
        items,
        tool_path=REMOTE_TOOL_PATH,
        udie=int(getattr(args, "udie", 0) or 0),
        macros=macros,
        parallel=int(getattr(args, "parallel", 8) or 8),
        timeout=DEFAULT_TIMEOUT,
    )

    failures: List[str] = []
    jobs: List[Tuple[Switch5808, List[SwitchPort]]] = []
    ber_history: Dict[Tuple[str, int, int, int, int], List[float]] = {}
    npu_ber_history: Dict[Tuple[str, int, int, int], List[float]] = {}
    kept_rounds = 0
    npu_started = False
    interrupted = False
    try:
        print("步骤 b: NPU 开启 prbs31 ...")
        failures += _report_npu_commands("NPU 开流", npu.start())
        npu_started = True

        print("步骤 c: 查询 5808 端口映射 ...")
        for switch in switches:
            try:
                ports = switch.query_ports()
            except Exception as exc:
                failures.append(f"{switch.name} 端口映射查询失败: "
                                f"{type(exc).__name__}: {exc}")
                print(f"{Colors.RED}  {switch.name} 端口映射查询失败: "
                      f"{exc}{Colors.RESET}")
                continue
            jobs.append((switch, ports))
            print(f"  {switch.name} 端口数: {len(ports)}")

        print("步骤 c: 5808 开启 prbs31 ...")
        for switch, ports in jobs:
            try:
                results = switch.set_prbs(ports, True)
            except Exception as exc:
                failures.append(f"{switch.name} 开流异常: "
                                f"{type(exc).__name__}: {exc}")
                print(f"{Colors.RED}  {switch.name} 开流异常: {exc}{Colors.RESET}")
                continue
            failures += _report_switch_commands(
                f"5808 开流({switch.name})", ports, results, switch.name)

        for round_no in range(1, acquire_rounds + 1):
            if round_no > 1 and interval > 0:
                print(f"  等待 {interval}s 后进入第 {round_no}/{acquire_rounds} 轮采集 ...")
                time.sleep(interval)

            print(f"步骤 d: 第 {round_no}/{acquire_rounds} 轮读取 NPU 误码状态 ...")
            status_results = npu.read_status()
            failures += _report_npu_commands(f"NPU 读误码(轮{round_no})", status_results)

            print(f"步骤 e: 第 {round_no}/{acquire_rounds} 轮读取 5808 prbs 状态 ...")
            switch_results: List[Tuple[Switch5808, List[SwitchPort], List[dict]]] = []
            for switch, ports in jobs:
                try:
                    results = switch.get_prbs(ports)
                except Exception as exc:
                    failures.append(f"{switch.name} 读结果异常(轮{round_no}): "
                                    f"{type(exc).__name__}: {exc}")
                    print(f"{Colors.RED}  {switch.name} 读结果异常: "
                          f"{exc}{Colors.RESET}")
                    continue
                failures += _report_switch_commands(
                    f"5808 读结果({switch.name},轮{round_no})",
                    ports, results, switch.name)
                switch_results.append((switch, ports, results))

            if round_no == 1:
                problems = check_status_ready(status_results, DEFAULT_LANES)
                expected_reads = len(items) * len(macros)
                if len(status_results) != expected_reads:
                    problems.append(f"NPU 状态读数数量异常: 期望 {expected_reads} 条，"
                                    f"实际 {len(status_results)} 条")
                for switch, ports, results in switch_results:
                    problems.extend(f"{switch.name} {item}"
                                    for item in check_prbs_ready(ports, results))
                if not jobs:
                    problems.append("没有可用的 5808 端口")
                elif len(switch_results) != len(jobs):
                    problems.append(f"{len(jobs) - len(switch_results)} 台 5808 "
                                    f"未成功读取状态")
                if problems:
                    interrupted = True
                    failures.extend(f"开流未就绪: {item}" for item in problems)
                    print(f"{Colors.RED}  首轮开流就绪校验失败，未开始有效采样"
                          f"（{len(problems)} 项）{Colors.RESET}")
                    for item in problems[:20]:
                        print(f"    {item}")
                    if len(problems) > 20:
                        print(f"    ... 其余 {len(problems) - 20} 项省略（详见日志）")
                    for item in problems:
                        write_detail(f"开流未就绪: {item}")
                    break
                message = (f"首轮开流就绪校验通过（NPU {len(items)} 卡 × "
                           f"{len(macros)} macro、5808 "
                           f"{sum(len(ports) for _, ports, _ in switch_results)} 个端口），"
                           f"开始有效采样")
                print(f"{Colors.GREEN}  {message}{Colors.RESET}")
                write_detail(message)
                continue

            kept = round_no - 1
            details = [f"--- 第 {kept}/{rounds} 轮有效采样结果 ---"]
            details += format_status_report(status_results)
            for switch, ports, results in switch_results:
                details += format_get_report(ports, results, switch.name)
            _record_npu_ber(npu_ber_history, status_results)
            for switch, ports, results in switch_results:
                _record_port_ber(ber_history, switch.name, ports, results)
            kept_rounds = kept
            for line in details:
                write_detail(line)

    except Exception as exc:
        interrupted = True
        failures.append(f"执行异常: {type(exc).__name__}: {exc}")
        print(f"{Colors.RED}PRBS 执行异常: {type(exc).__name__}: {exc}{Colors.RESET}")
    finally:
        # 清理失败也要计入结果，但不能让清理异常掩盖原始错误。
        if npu_started:
            print("步骤 f: 关闭 NPU prbs ...")
            try:
                failures += _report_npu_commands("NPU 关流", npu.stop())
            except Exception as exc:
                failures.append(f"NPU 关流异常: {type(exc).__name__}: {exc}")
                print(f"{Colors.RED}NPU 关流异常: {exc}{Colors.RESET}")
        if jobs:
            print("步骤 g: 关闭 5808 prbs ...")
            for switch, ports in jobs:
                try:
                    results = switch.set_prbs(ports, False)
                except Exception as exc:
                    failures.append(f"{switch.name} 关流异常: "
                                    f"{type(exc).__name__}: {exc}")
                    print(f"{Colors.RED}  {switch.name} 关流异常: "
                          f"{exc}{Colors.RESET}")
                    continue
                failures += _report_switch_commands(
                    f"5808 关流({switch.name})", ports, results, switch.name)

    summary_lines = _npu_ber_summary_lines(npu_ber_history, kept_rounds, limit)
    if jobs:
        summary_lines += _switch_ber_summary_lines(
            jobs, ber_history, kept_rounds, limit)
    ber_failed = sum(1 for _, ok in summary_lines if ok is False)
    for line, ok in summary_lines:
        if ok is True:
            print(f"{Colors.GREEN}{line}{Colors.RESET}")
        elif ok is False:
            print(f"{Colors.RED}{line}{Colors.RESET}")
        else:
            print(line)
        write_detail(line)

    if interrupted:
        message = f"PRBS 中止，失败 {len(failures)} 项"
        print(f"{Colors.RED}{message}{Colors.RESET}")
        write_detail(message)
        return EXIT_RUN
    if failures:
        message = f"PRBS 完成，失败 {len(failures)} 项"
        print(f"{Colors.RED}{message}{Colors.RESET}")
        write_detail(message)
        return EXIT_RUN
    if ber_failed:
        message = f"PRBS 完成，{ber_failed} 个 lane 误码率超过阈值 {limit:g}"
        print(f"{Colors.RED}{message}{Colors.RESET}")
        write_detail(message)
        return EXIT_RUN
    message = "PRBS 完成，全部命令执行成功"
    if limit is not None:
        message += f"，误码率均未超过阈值 {limit:g}"
    print(f"{Colors.GREEN}{message}{Colors.RESET}")
    write_detail(message)
    return EXIT_OK


def _last_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
