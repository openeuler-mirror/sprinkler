import re
import threading
from types import SimpleNamespace

import streamlit as st

from sprinkler.connect.connect import CONNECTION_MODES
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.base import BaseConnector
from views.utils._theme import inject as _inject_warm_theme, inject_terminal_css
from views.utils._components import (
    StatusCollector, render_target_selector, inject_terminal_height_css,
    make_terminal_fragment,
)


_DANGEROUS_PATTERNS = [
    (re.compile(r'\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\b'), "文件删除(rm -rf)"),
    (re.compile(r'\brm\s+-[a-zA-Z]*r[a-zA-Z]*\b'), "文件删除(rm -r)"),
    (re.compile(r'\brmdir\b'), "目录删除(rmdir)"),
    (re.compile(r'\bmkfs\b'), "磁盘格式化(mkfs)"),
    (re.compile(r'\bdd\s+\b'), "磁盘写入(dd)"),
    (re.compile(r'\bfdisk\b'), "磁盘分区(fdisk)"),
    (re.compile(r'\bformat\b'), "磁盘格式化(format)"),
    (re.compile(r'\breboot\b'), "系统重启(reboot)"),
    (re.compile(r'\bshutdown\b'), "系统关机(shutdown)"),
    (re.compile(r'\bhalt\b'), "系统停机(halt)"),
    (re.compile(r'\bpoweroff\b'), "系统断电(poweroff)"),
    (re.compile(r'\binit\s+[06]\b'), "系统重启/关机(init)"),
    (re.compile(r'\bchmod\s+777\b'), "权限变更(chmod 777)"),
    (re.compile(r'\bchown\s+-R\b'), "权限变更(chown -R)"),
    (re.compile(r'>\s*/dev/sd'), "覆盖磁盘设备"),
    (re.compile(r'\btee\b.*>\s*/(etc|boot|usr|var)/'), "覆盖系统文件"),
    (re.compile(r'\bkill\s+-9\s+1\b'), "强杀init进程(kill -9 1)"),
    (re.compile(r'\bkillall\b'), "批量杀进程(killall)"),
    (re.compile(r'\biptables\s+-F\b'), "清空防火墙规则(iptables -F)"),
    (re.compile(r'\broute\s+del\s+default\b'), "删除默认路由(route del default)"),
]


def _check_dangerous_commands(commands):
    matched = []
    for cmd in commands:
        for pattern, label in _DANGEROUS_PATTERNS:
            if pattern.search(cmd):
                matched.append((cmd, label))
                break
    return matched


def _parse_commands(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def _run_connect_task(mode_key, targets, commands, connect_timeout=4, command_timeout=30, parallel=8, progress_cb=None, status_log=None):
    config = CONNECTION_MODES[mode_key]
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
    if progress_cb:
        progress_cb(len(targets), len(targets))
    return results_all


_render_terminal_right = make_terminal_fragment("connect", "等待执行命令任务...")


def render():
    _inject_warm_theme()
    inject_terminal_css()
    inject_terminal_height_css()

    col_left, col_right = st.columns([1, 2], gap="medium", vertical_alignment="top")

    with col_left:
        with st.container(border=True):
            st.markdown("**连接配置**")
            mode_keys = list(CONNECTION_MODES.keys())
            selected_idx = st.selectbox("连接模式", range(len(mode_keys)), format_func=lambda i: mode_keys[i])
            mode_key = mode_keys[selected_idx]

            command_timeout = 30
            connect_timeout = 4

        targets, parallel = render_target_selector(mode_key, default_parallel=8)

        with st.container(border=True):
            st.markdown("**命令配置**")
            cmd_text = st.text_area("命令配置", placeholder="每行一条命令，如：\nls -la\nuname -a", height=100, label_visibility="collapsed")
            commands = _parse_commands(cmd_text) if cmd_text.strip() else []

        exec_disabled = not targets or not commands
        dangerous_matched = _check_dangerous_commands(commands) if commands else []
        if dangerous_matched:
            exec_disabled = True
            for cmd, label in dangerous_matched:
                st.error(f"⚠️ 高危指令已拦截 [{label}]：`{cmd}`")
            st.warning("包含高危指令，禁止执行")
        if st.button("🚀 执行命令", disabled=exec_disabled, type="primary", use_container_width=True):
            status_collector = StatusCollector()
            status_collector.progress = (0, len(targets))
            st.session_state["connect_collector"] = status_collector
            st.session_state["connect_running"] = True
            st.session_state["connect_results"] = None
            st.session_state["connect_status_log"] = None

            task = dict(
                mode_key=mode_key, targets=targets, commands=commands,
                connect_timeout=connect_timeout, command_timeout=command_timeout, parallel=parallel,
            )

            def _run_in_thread():
                try:
                    _run_connect_task(
                        task["mode_key"], task["targets"], task["commands"],
                        connect_timeout=task["connect_timeout"],
                        command_timeout=task["command_timeout"],
                        parallel=task["parallel"],
                        progress_cb=None,
                        status_log=status_collector,
                    )
                finally:
                    status_collector.finished = True

            thread = threading.Thread(target=_run_in_thread, daemon=True)
            thread.start()
            st.rerun()

    with col_right:
        _render_terminal_right()
