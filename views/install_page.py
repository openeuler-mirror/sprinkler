import threading

from types import SimpleNamespace

import streamlit as st

from sprinkler.connect.connect import CONNECTION_MODES
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.base import BaseConnector
from views.utils._theme import inject as _inject_warm_theme, inject_terminal_css
from views.utils._components import (
    StatusCollector, render_target_selector, inject_terminal_height_css, make_terminal_fragment,
)


_GIT_REPO_URL = "szv-open.codehub.huawei.com/innersource/LingquInfra_G/Sprinkler.git"
_HOSTS_ENTRY = "141.3.140.45 szv-open.codehub.huawei.com"

_URL_ESCAPE_MAP = {
    "!": "%21", "#": "%23", "$": "%24", "&": "%26", "'": "%27",
    "(": "%28", ")": "%29", "*": "%2A", "+": "%2B", ",": "%2C",
    "/": "%2F", ":": "%3A", ";": "%3B", "=": "%3D", "?": "%3F",
    "@": "%40", "[": "%5B", "]": "%5D",
    " ": "%20", '"': "%22", "%": "%25", "-": "%2D", ".": "%2E",
    "<": "%3C", ">": "%3E", "\\": "%5C", "^": "%5E", "_": "%5F",
    "`": "%60", "{": "%7B", "|": "%7C", "}": "%7D", "~": "%7E",
}


def _escape_password(password):
    return "".join(_URL_ESCAPE_MAP.get(ch, ch) for ch in password)


def _build_install_commands(http_proxy, https_proxy, git_user, git_password):
    escaped_pwd = _escape_password(git_password)
    clone_url = f"https://{git_user}:{escaped_pwd}@{_GIT_REPO_URL}"
    script_lines = [
        "#!/bin/bash",
        "if command -v sprinkler >/dev/null 2>&1; then",
        "  EXISTING_PATH=$(sprinkler 2>/dev/null | grep -oP '(?<=Sprinkler Path is ).*' | head -1)",
        '  if [ -n "$EXISTING_PATH" ] && [[ "$EXISTING_PATH" == /home/healthy_check/* ]]; then',
        '    echo "ALREADY_INSTALLED:$EXISTING_PATH"',
        "    exit 0",
        "  fi",
        "fi",
        f"export http_proxy={http_proxy}",
        f"export https_proxy={https_proxy}",
        "export no_proxy=127.0.0.1,.huawei.com,localhost,local,.local",
        "export GIT_SSL_NO_VERIFY=1",
        "git config --global http.sslVerify false",
        f"git config --global https.proxy {https_proxy}",
        f"git config --global http.proxy {http_proxy}",
        f"grep -q '{_HOSTS_ENTRY}' /etc/hosts || echo '{_HOSTS_ENTRY}' >> /etc/hosts",
        f"if [ -d /home/healthy_check/Sprinkler/.git ]; then cd /home/healthy_check/Sprinkler && git pull; else mkdir -p /home/healthy_check && cd /home/healthy_check && git clone {clone_url}; fi",
        "pip3 config set global.index-url http://mirrors.aliyun.com/pypi/simple/",
        "pip3 config set global.trusted-host mirrors.aliyun.com",
        "cd /home/healthy_check/Sprinkler && pip install -e .",
    ]
    script_content = "\n".join(script_lines)
    heredoc_cmd = f"cat > /home/healthy_check/sprinkler_install.sh << 'SPRINKLER_EOF'\n{script_content}\nSPRINKLER_EOF"
    return [
        heredoc_cmd,
        "bash /home/healthy_check/sprinkler_install.sh",
        "rm -f /home/healthy_check/sprinkler_install.sh",
        "sprinkler",
    ]


def _run_install_task(targets, commands, connect_timeout=10, command_timeout=300, parallel=4, status_log=None):
    config = CONNECTION_MODES["cpu_os"]
    args = SimpleNamespace(
        connect_timeout=connect_timeout,
        timeout=command_timeout,
        parallel=parallel,
        cmd=commands,
        file=None,
    )
    runner = create_runner(args=args, run_log=status_log, connection_type=config["connection_type"])

    def _on_result(result):
        if status_log is not None and isinstance(result, dict):
            target_name = result.get("target", "")
            cmd = result.get("cmd", "")
            ok = result.get("ok", False)
            short_cmd = cmd if len(cmd) <= 60 else cmd[:57] + "..."
            if ok:
                status_log.ok(f"[{target_name}] 完成: {short_cmd}")
            else:
                stderr = result.get("stderr", "")
                reason = stderr.strip().split("\n")[0] if stderr.strip() else result.get("error_type", "失败")
                status_log.fail(f"[{target_name}] 失败: {short_cmd} ({reason})")

    try:
        results_all = runner.run(targets, commands=commands, on_result=_on_result)
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


_render_terminal_right = make_terminal_fragment("install", "等待执行安装任务...")


def render():
    _inject_warm_theme()
    inject_terminal_css()
    inject_terminal_height_css()

    col_left, col_right = st.columns([1, 2], gap="medium", vertical_alignment="top")

    with col_left:
        with st.container(border=True):
            st.markdown("**连接配置**")
            col_proxy, col_git_user, col_git_pwd = st.columns(3)
            with col_proxy:
                proxy_ip = st.text_input("代理IP", placeholder="如：10.0.0.1")
            with col_git_user:
                git_user = st.text_input("CodeHub用户名")
            with col_git_pwd:
                git_password = st.text_input("CodeHub密码", type="password")

            command_timeout = 300
            connect_timeout = 10

        targets, parallel = render_target_selector("cpu_os", default_parallel=8)

        _proxy_prefix = "http://p_atlas:proxy%40123@"
        _proxy_suffix = ":8080"
        http_proxy = f"{_proxy_prefix}{proxy_ip.strip()}{_proxy_suffix}" if proxy_ip.strip() else ""
        https_proxy = http_proxy

        config_complete = proxy_ip.strip() and git_user.strip() and git_password.strip() and targets
        if not config_complete:
            missing = []
            if not proxy_ip.strip():
                missing.append("代理IP")
            if not git_user.strip():
                missing.append("CodeHub用户名")
            if not git_password.strip():
                missing.append("CodeHub密码")
            if not targets:
                missing.append("目标节点")
            st.info(f"请完善配置：{', '.join(missing)}")

        if st.button("🚀 开始安装", disabled=not config_complete, type="primary", use_container_width=True):
            commands = _build_install_commands(http_proxy.strip(), https_proxy.strip(), git_user.strip(), git_password.strip())
            collector = StatusCollector()
            collector.progress = (0, len(targets))
            st.session_state.update({
                "install_collector": collector,
                "install_running": True,
                "install_results": None,
                "install_status_log": None,
            })

            def _run_in_thread():
                try:
                    collector.info(f"开始在 {len(targets)} 个节点上安装（脚本会自动检测是否已安装）...")
                    _run_install_task(
                        targets, commands,
                        connect_timeout=connect_timeout,
                        command_timeout=command_timeout,
                        parallel=parallel,
                        status_log=collector,
                    )
                    collector.ok("安装任务执行完毕")
                except Exception as exc:
                    collector.fail(f"安装执行异常: {exc}")
                finally:
                    collector.finished = True

            threading.Thread(target=_run_in_thread, daemon=True).start()
            st.rerun()

    with col_right:
        _render_terminal_right()
