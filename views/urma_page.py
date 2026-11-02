import os
import shlex
import threading
import time
from types import SimpleNamespace

import streamlit as st

from sprinkler.connect.connect import CONNECTION_MODES
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.base import BaseConnector
from views.utils._theme import inject as _inject_warm_theme, inject_terminal_css
from views.utils._components import (
    StatusCollector, render_target_selector, render_peer_target_selector, inject_terminal_height_css,
    make_terminal_fragment,
)

_OP_NPU_BASH = "免密破解"
_OP_NOPASS = "免密配置"
_OP_BOARD_BW = "板内打流"
_OP_CROSS_BW = "跨板打流"

_BOARD_MODE_OPTIONS = {
    "h2d": "h2d (主机到设备)",
    "d2h": "d2h (设备到主机)",
    "d2d": "d2d (设备到设备)",
    "all": "all (全部)",
}

_CROSS_MODE_OPTIONS = {
    "xd2d": "xd2d (跨板设备到设备)",
    "ubg": "ubg (UBG)",
}

_URMA_PERFTEST_MODE_OPTIONS = {
    "write_bw": "write_bw",
    "read_bw": "read_bw",
}


_D2D_MODE_OPTIONS = {
    "many-to-one": "多打一",
    "one-to-many": "一打多",
}

_LINK_MODE_OPTIONS = {
    "all": "全部链路",
    "l0": "L0链路",
    "l1": "L1链路",
}

_RANK_OPTIONS = list(range(8))


class _NpuBashLogAdapter:
    def __init__(self, collector):
        self._collector = collector

    def write_target_results(self, target_name, results):
        self._collector.write_target_results(target_name, results)

    def info(self, message):
        self._collector.info(message)

    def ok(self, message):
        self._collector.ok(message)

    def fail(self, message):
        self._collector.fail(message)


def _run_task(targets, commands, connect_timeout=4, command_timeout=120, parallel=4, status_log=None):
    config = CONNECTION_MODES["cpu_os"]
    args = SimpleNamespace(
        connect_timeout=connect_timeout,
        timeout=command_timeout,
        parallel=parallel,
        cmd=commands,
        file=None,
    )
    runner = create_runner(args=args, run_log=status_log, connection_type=config["connection_type"])
    try:
        results_all = runner.run(targets, commands=commands)
    except Exception as exc:
        results_all = [
            {
                "target": BaseConnector.target_label(t),
                "host": t.host, "port": t.port, "user": t.user,
                "cmd": "; ".join(commands),
                "ok": False, "rc": 255, "error_type": "exception",
                "stdout": "", "stderr": str(exc), "elapsed": 0.0,
                "backend": config["connection_type"],
            }
            for t in targets
        ]
    return results_all


def _run_npu_bash_task(targets, sol_ports, connect_timeout=10, command_timeout=60, status_log=None):
    from sprinkler.auto.runtime.general.npu_sol import NpuSolSession, NpuSolRunner, bmc_ssh_target, parse_sol_ports
    from sprinkler.connect.connection_runner import normalize_commands
    from sprinkler.connect.base import ResultProcessor

    npu_bmc_targets = [bmc_ssh_target(t) for t in targets]

    cmd_file = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "sprinkler", "auto", "runtime", "general", "npu_sol_cmds.txt")
    args = SimpleNamespace(
        connect_timeout=connect_timeout,
        timeout=command_timeout,
        cmd=None,
        file=cmd_file,
        sol_ports=sol_ports,
        parallel=1,
    )

    commands = normalize_commands(args)
    ports = parse_sol_ports(getattr(args, "sol_ports", "3,5,7,9,11,13,15,17"))
    processor = ResultProcessor()
    adapter = _NpuBashLogAdapter(status_log) if status_log else None

    results = []
    for target in npu_bmc_targets:
        session = NpuSolSession(target, args, processor)
        try:
            target_error = target.validate()
            if target_error:
                target_results = [session.result(i, p, cmd, False, "", target_error, time.time(), "target")
                                  for i, p in enumerate(ports) for cmd in commands]
            else:
                try:
                    session.connect()
                except Exception as exc:
                    target_results = [session.result(i, p, cmd, False, "", str(exc), time.time(),
                                                      session.connect_error_type(exc))
                                      for i, p in enumerate(ports) for cmd in commands]
                else:
                    target_results = []
                    for idx, port in enumerate(ports):
                        if adapter:
                            adapter.info(f"[{BaseConnector.target_label(target)}] 切换到 SOL 端口 {port} (NPU{idx})...")
                        ok, output = session.enter_sol(port)
                        if not ok:
                            target_results.extend(
                                session.result(idx, port, cmd, False, "", output, time.time(), "sol")
                                for cmd in commands
                            )
                            if adapter:
                                adapter.fail(f"[{BaseConnector.target_label(target)}] SOL 端口 {port} (NPU{idx}) 进入失败")
                            session.exit_sol()
                            continue
                        for cmd in commands:
                            target_results.append(session.run_command(idx, port, cmd))
                        if adapter:
                            adapter.ok(f"[{BaseConnector.target_label(target)}] SOL 端口 {port} (NPU{idx}) 执行完成")
                        session.exit_sol()
        except Exception as exc:
            target_results = [{
                "target": BaseConnector.target_label(target), "host": target.host, "port": target.port,
                "user": target.user, "cmd": "sprinkler -npu-bash", "ok": False, "rc": 255,
                "error_type": "exception", "stdout": "", "stderr": str(exc), "elapsed": 0.0, "backend": "npu_sol",
            }]
        finally:
            session.close()
        if adapter:
            adapter.write_target_results(BaseConnector.target_label(target), target_results)
        results.extend(target_results)

    return results


def _build_nopass_commands(npu_user, npu_password):
    conf_cmd = (
        f"mkdir -p /home/healthy_check/sprinkler_config && "
        f"echo -e 'POD_NPU_USER={npu_user}\\nPOD_NPU_PASSWORD={npu_password}' "
        f"> /home/healthy_check/sprinkler_config/config.conf"
    )
    nopass_cmd = "sprinkler -no-pass"
    return [conf_cmd, nopass_cmd]


def _build_board_bw_command(mode, duration, urma_mode, d2d_main, d2d_mode, rank=None, urma_opts=""):
    cmd_parts = ["sprinkler", "-urma-bandwidth"]
    if mode != "all":
        cmd_parts.extend(["-mode", mode])
    if duration != 30:
        cmd_parts.extend(["-duration", str(duration)])
    if urma_mode != "write_bw":
        cmd_parts.extend(["-urma-mode", urma_mode])
    if urma_opts:
        cmd_parts.extend(["-urma-opts", urma_opts])
    if rank:
        cmd_parts.extend(["-rank", rank])
    if mode == "d2d" and d2d_main is not None:
        cmd_parts.extend(["-d2d-main", str(d2d_main)])
    if mode == "d2d":
        cmd_parts.extend(["-d2d-mode", d2d_mode])
    return " ".join(cmd_parts)


def _build_cross_bw_command(mode, duration, urma_mode, link_mode, same_plane, all2all,
                            server_rank, rank=None, urma_opts="", node=""):
    cmd_parts = ["sprinkler", "-cluster-bw"]
    if mode != "all":
        cmd_parts.extend(["-mode", mode])
    if duration != 30:
        cmd_parts.extend(["-duration", str(duration)])
    if urma_mode != "write_bw":
        cmd_parts.extend(["-urma-mode", urma_mode])
    if urma_opts:
        cmd_parts.extend(["-urma-opts", urma_opts])
    if link_mode != "all" and mode != "ubg":
        cmd_parts.extend(["-link-mode", link_mode])
    if same_plane:
        cmd_parts.append("-same-plane")
    if all2all:
        cmd_parts.append("-all2all")
    if server_rank:
        cmd_parts.extend(["-server-rank", server_rank])
    if rank:
        cmd_parts.extend(["-rank", rank])
    if node:
        cmd_parts.extend(["-node", node])
    return " ".join(cmd_parts)


def _render_nopass_config():
    c1, c2 = st.columns(2)
    with c1:
        npu_user = st.text_input("NPU用户名", value="root")
    with c2:
        npu_password = st.text_input("NPU密码", type="password")
    return {"npu_user": npu_user, "npu_password": npu_password}


def _render_npu_bash_config():
    from sprinkler.auto.runtime.general.npu_sol import DEFAULT_SOL_PORTS
    from sprinkler.base.ranges import parse_range
    default_ports = ",".join(str(p) for p in parse_range(DEFAULT_SOL_PORTS))
    sol_ports = st.text_input("SOL端口", value=default_ports,
                              placeholder="如: 3,5,7,9,11,13,15,17")
    return {"sol_ports": sol_ports}


def _render_board_bw_d2d_config():
    d2d_mode = st.selectbox("D2D模式", list(_D2D_MODE_OPTIONS.keys()), index=0,
                            format_func=lambda k: _D2D_MODE_OPTIONS[k])
    d2d_main = None
    rank = None
    if d2d_mode == "many-to-one":
        c7, c8 = st.columns(2)
        with c8:
            server_rank = st.selectbox("Server rank", _RANK_OPTIONS, index=0,
                                       format_func=lambda r: f"NPU{r}")
        with c7:
            client_options = [r for r in _RANK_OPTIONS if r != server_rank]
            default_clients = [r for r in range(1, 8) if r != server_rank]
            client_ranks = st.multiselect("Client rank", client_options, default=default_clients,
                                          format_func=lambda r: f"NPU{r}")
        if client_ranks:
            rank = ",".join(str(r) for r in client_ranks) + f",{server_rank}"
        d2d_main = server_rank
    else:
        c7, c8 = st.columns(2)
        with c7:
            client_rank = st.selectbox("Client rank", _RANK_OPTIONS, index=0,
                                       format_func=lambda r: f"NPU{r}")
        with c8:
            server_options = [r for r in _RANK_OPTIONS if r != client_rank]
            default_servers = [r for r in range(1, 8) if r != client_rank]
            server_ranks = st.multiselect("Server rank", server_options, default=default_servers,
                                          format_func=lambda r: f"NPU{r}")
        if server_ranks:
            rank = f"{client_rank}," + ",".join(str(r) for r in server_ranks)
        d2d_main = client_rank

    return {"d2d_mode": d2d_mode, "d2d_main": d2d_main, "rank": rank}


def _render_board_bw_advanced(mode):
    c1, c2 = st.columns(2)
    with c1:
        duration = st.number_input("持续时长(秒)", min_value=1, max_value=600, value=30)
    with c2:
        urma_mode = st.selectbox("perftest模式", list(_URMA_PERFTEST_MODE_OPTIONS.keys()), index=0,
                                 format_func=lambda k: _URMA_PERFTEST_MODE_OPTIONS[k])

    urma_opts = st.text_input("urma_perftest附加选项",
                              placeholder="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 ...")

    return {
        "duration": duration,
        "urma_mode": urma_mode,
        "urma_opts": urma_opts.strip(),
    }


def _render_cross_bw_main_config():
    c1, c2 = st.columns(2)
    with c1:
        mode = st.selectbox("模式", list(_CROSS_MODE_OPTIONS.keys()), index=0,
                            format_func=lambda k: _CROSS_MODE_OPTIONS[k])
    with c2:
        if mode == "ubg":
            link_mode = "all"
        else:
            link_mode = st.selectbox("链路选择", list(_LINK_MODE_OPTIONS.keys()), index=0,
                                     format_func=lambda k: _LINK_MODE_OPTIONS[k])

    c5, c6 = st.columns(2)
    with c5:
        def _on_same_plane_change():
            if st.session_state.get("urma_same_plane", False):
                st.session_state["urma_all2all"] = False
        same_plane = st.checkbox("同平面rank映射", value=True, key="urma_same_plane",
                                 on_change=_on_same_plane_change,
                                 help="每个客户端只打与自己相同编号的服务端（NPU0→NPU0, NPU1→NPU1...），是一对一映射")
    with c6:
        def _on_all2all_change():
            if st.session_state.get("urma_all2all", False):
                st.session_state["urma_same_plane"] = False
        all2all = st.checkbox("全互联", value=False, key="urma_all2all",
                              on_change=_on_all2all_change,
                              help="所有 client rank 对选中的 server rank 都打流，即每个客户端打所有服务端（NPU0→NPU0~7, NPU1→NPU0~7...），是全组合")

    rank = None
    server_rank = None

    if not same_plane:
        c_left, c_right = st.columns(2)
        with c_right:
            server_rank_val = st.selectbox("Server rank", _RANK_OPTIONS, index=0,
                                           format_func=lambda r: f"NPU{r}")
        with c_left:
            client_options = [r for r in _RANK_OPTIONS if r != server_rank_val]
            default_clients = [r for r in range(1, 8) if r != server_rank_val]
            client_ranks = st.multiselect("Client rank", client_options, default=default_clients,
                                          format_func=lambda r: f"NPU{r}")
        if client_ranks:
            rank = ",".join(str(r) for r in client_ranks) + f",{server_rank_val}"
        server_rank = str(server_rank_val)

    return {
        "mode": mode,
        "link_mode": link_mode,
        "rank": rank,
        "server_rank": server_rank,
        "same_plane": same_plane,
        "all2all": all2all,
    }


def _render_cross_bw_advanced(config):
    c1, c2 = st.columns(2)
    with c1:
        duration = st.number_input("持续时长(秒)", min_value=1, max_value=600, value=30)
    with c2:
        urma_mode = st.selectbox("perftest模式", list(_URMA_PERFTEST_MODE_OPTIONS.keys()), index=0,
                                 format_func=lambda k: _URMA_PERFTEST_MODE_OPTIONS[k])

    urma_opts = st.text_input("urma_perftest附加选项",
                              placeholder="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 ...")

    config.update({
        "duration": duration,
        "urma_mode": urma_mode,
        "urma_opts": urma_opts.strip(),
        "node": "",
    })
    return config


def _build_node_from_peer_targets(peer_targets):
    if not peer_targets:
        return ""
    sp_ca_nodes = {}
    for t in peer_targets:
        meta = t.meta or {}
        sp = str(meta.get("sp", "0"))
        ca = str(meta.get("ca", "0"))
        node = meta.get("node", 0)
        sp_ca_nodes.setdefault(sp, {}).setdefault(ca, []).append(int(node))
    parts = []
    for sp, ca_map in sorted(sp_ca_nodes.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        ca_parts = []
        for ca, nodes in sorted(ca_map.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            node_spec = _compress_node_numbers(sorted(nodes))
            ca_parts.append(f"c{ca}:{node_spec}")
        parts.append(f"s{sp}:[{','.join(ca_parts)}]")
    return "{" + ",".join(parts) + "}"


def _compress_node_numbers(numbers):
    if not numbers:
        return ""
    ranges = []
    start = prev = numbers[0]
    for n in numbers[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


_render_terminal_right = make_terminal_fragment("urma", "等待执行URMA任务...")


def render():
    _inject_warm_theme()
    inject_terminal_css()
    inject_terminal_height_css()

    col_left, col_right = st.columns([1, 2], gap="medium", vertical_alignment="top")

    with col_left:
        with st.container(border=True):
            st.markdown("**操作配置**")
            op_type = st.selectbox("操作类型", [_OP_NPU_BASH, _OP_NOPASS, _OP_BOARD_BW, _OP_CROSS_BW], index=0)

            if op_type == _OP_NOPASS:
                op_config = _render_nopass_config()
            elif op_type == _OP_NPU_BASH:
                op_config = _render_npu_bash_config()
            elif op_type == _OP_BOARD_BW:
                board_mode = st.selectbox("模式", list(_BOARD_MODE_OPTIONS.keys()), index=2,
                                          format_func=lambda k: _BOARD_MODE_OPTIONS[k])
                op_config = {"mode": board_mode}
                if board_mode == "d2d":
                    op_config.update(_render_board_bw_d2d_config())
            else:
                op_config = _render_cross_bw_main_config()

            if op_type == _OP_BOARD_BW:
                with st.expander("高级配置", expanded=False):
                    op_config.update(_render_board_bw_advanced(op_config["mode"]))
            elif op_type == _OP_CROSS_BW:
                with st.expander("高级配置", expanded=False):
                    op_config = _render_cross_bw_advanced(op_config)

            if op_type == _OP_NPU_BASH:
                command_timeout = 60
                connect_timeout = 10
            else:
                command_timeout = 120
                connect_timeout = 4

        target_mode = "npu_bmc" if op_type == _OP_NPU_BASH else "cpu_os"

        peer_targets = []
        if op_type == _OP_CROSS_BW:
            srv_col, cli_col = st.columns(2, gap="small")
            with srv_col:
                targets, parallel = render_target_selector(target_mode, default_parallel=8, label="Server（执行机）")
            with cli_col:
                peer_targets = render_peer_target_selector(target_mode, label="Client（对端）")
        else:
            targets, parallel = render_target_selector(target_mode, default_parallel=8)

        if op_type == _OP_NOPASS:
            config_complete = op_config["npu_user"].strip() and op_config["npu_password"].strip() and targets
            if not config_complete:
                missing = []
                if not op_config["npu_user"].strip():
                    missing.append("NPU用户名")
                if not op_config["npu_password"].strip():
                    missing.append("NPU密码")
                if not targets:
                    missing.append("目标节点")
                st.info(f"请完善配置：{', '.join(missing)}")
        elif op_type == _OP_NPU_BASH:
            config_complete = bool(targets)
            if not config_complete:
                st.info("请选择目标节点")
        elif op_type == _OP_CROSS_BW:
            config_complete = bool(targets) and bool(peer_targets)
            if not config_complete:
                missing = []
                if not targets:
                    missing.append("Server执行机")
                if not peer_targets:
                    missing.append("Client对端")
                st.info(f"请完善配置：{', '.join(missing)}")
        else:
            config_complete = bool(targets)
            if not config_complete:
                st.info("请选择目标节点")

        if st.button("🚀 执行", disabled=not config_complete, type="primary", use_container_width=True):
            if op_type == _OP_NOPASS:
                commands = _build_nopass_commands(op_config["npu_user"].strip(), op_config["npu_password"].strip())
            elif op_type == _OP_NPU_BASH:
                commands = ["sprinkler -npu-bash"]
            elif op_type == _OP_BOARD_BW:
                cmd_str = _build_board_bw_command(
                    op_config["mode"], op_config.get("duration", 30),
                    op_config.get("urma_mode", "write_bw"), op_config.get("d2d_main"),
                    op_config.get("d2d_mode", "many-to-one"), op_config.get("rank"),
                    urma_opts=op_config.get("urma_opts", ""),
                )
                commands = [cmd_str]
            else:
                node_param = _build_node_from_peer_targets(peer_targets)
                cmd_str = _build_cross_bw_command(
                    op_config["mode"], op_config.get("duration", 30),
                    op_config.get("urma_mode", "write_bw"),
                    op_config["link_mode"], op_config.get("same_plane", False),
                    op_config.get("all2all", False), op_config.get("server_rank", ""),
                    rank=op_config.get("rank"),
                    urma_opts=op_config.get("urma_opts", ""),
                    node=node_param,
                )
                commands = [cmd_str]

            collector = StatusCollector()
            collector.progress = (0, len(targets))
            st.session_state.update({
                "urma_collector": collector,
                "urma_running": True,
                "urma_results": None,
                "urma_status_log": None,
            })

            task = dict(
                op_type=op_type, targets=targets, commands=commands,
                connect_timeout=connect_timeout, command_timeout=command_timeout, parallel=parallel,
                sol_ports=op_config.get("sol_ports", "") if op_type == _OP_NPU_BASH else None,
            )

            def _run_in_thread():
                try:
                    if task["op_type"] == _OP_NPU_BASH:
                        _run_npu_bash_task(
                            task["targets"],
                            sol_ports=task["sol_ports"],
                            connect_timeout=task["connect_timeout"],
                            command_timeout=task["command_timeout"],
                            status_log=collector,
                        )
                    else:
                        collector.info(f"在 {len(task['targets'])} 个节点上执行{task['op_type']}...")
                        _run_task(
                            task["targets"], task["commands"],
                            connect_timeout=task["connect_timeout"],
                            command_timeout=task["command_timeout"],
                            parallel=task["parallel"],
                            status_log=collector,
                        )
                    collector.ok(f"{task['op_type']}执行完毕")
                except Exception as exc:
                    collector.fail(f"执行异常: {exc}")
                finally:
                    collector.finished = True

            threading.Thread(target=_run_in_thread, daemon=True).start()
            st.rerun()

    with col_right:
        _render_terminal_right()
