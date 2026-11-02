import json
import threading
from types import SimpleNamespace

import streamlit as st

from sprinkler.auto.bandwidth.topology.inventory import ClusterInventoryBuilder, local_xboard_pairs_from_inventory
from sprinkler.auto.bandwidth.common import (
    display_link, display_reason, has_link_distinction, DEFAULT_ROOTINFO,
)
from sprinkler.auto.bandwidth.planning.eid_pairs import host_device_eid_pairs
from sprinkler.auto.bandwidth.topology.rootinfo import RootInfoTopology
from sprinkler.config.host_device_pairs import parse_host_device_pairs_text
from sprinkler.connect.base import BaseConnector, ResultProcessor
from sprinkler.connect.ssh.os import SSHOSConnector
from sprinkler.auto.bandwidth.common import unique_ints
from views.utils._theme import inject as _inject_warm_theme, inject_terminal_css
from views.utils._components import (
    StatusCollector, render_target_selector, inject_terminal_height_css,
    html_escape, render_status_terminal, render_terminal_body,
)

_MODE_OPTIONS = {
    "h2d": "h2d (主机到设备)",
    "d2h": "d2h (设备到主机)",
    "d2d": "d2d (设备到设备)",
}


def _run_link_query(targets, mode, d2d_mode, d2d_main, rank, link_mode, same_plane, all2all, server_rank,
                    connect_timeout=4, status_log=None):
    args = SimpleNamespace(
        connect_timeout=connect_timeout,
        timeout=10,
        parallel=1,
        cmd=[],
        file=None,
    )
    manager = SSHOSConnector(args=args, result_processor=ResultProcessor(), run_log=status_log or None)

    all_pairs = []
    for target in targets:
        label = BaseConnector.target_label(target)
        if status_log:
            status_log.info(f"正在连接 {label}...")
        try:
            client = manager.connect_client(target)
        except Exception as exc:
            if status_log:
                status_log.fail(f"连接 {label} 失败: {exc}")
            continue
        if status_log:
            status_log.ok(f"已连接 {label}，正在读取远程配置...")

        try:
            rootinfo_text = _read_remote_file(manager, client, target, DEFAULT_ROOTINFO)
            root_data = json.loads(str(rootinfo_text or "{}"))

            topo_path = RootInfoTopology.topo_file_path(root_data)
            topo_text = ""
            if topo_path:
                if not topo_path.startswith("/"):
                    import posixpath
                    topo_path = posixpath.join(posixpath.dirname(DEFAULT_ROOTINFO), topo_path)
                topo_text = _read_remote_file(manager, client, target, topo_path)

            topology = RootInfoTopology.from_data(root_data, topo_data=json.loads(topo_text) if topo_text.strip() else None)

            device_ids = unique_ints(item.get("device_id") for item in root_data.get("rank_list") or []
                                     if isinstance(item, dict))
            host_device_pairs = _read_remote_host_device_pairs(manager, client, target, device_ids)

            if status_log:
                status_log.ok(f"{label} 配置读取完成，正在计算链路...")

            inventory = ClusterInventoryBuilder().from_text(target, rootinfo_text, topo_text=topo_text,
                                                            host_device_pairs=host_device_pairs)
            ranks = topology.ranks()
            pairs = []

            if mode in ("h2d", "d2h"):
                pairs += host_device_eid_pairs(mode, ranks, host_device_pairs)
            elif mode == "d2d":
                pairs += local_xboard_pairs_from_inventory(
                    topology, inventory, ranks, link_mode=link_mode,
                    d2d_mode=d2d_mode, master_rank=d2d_main,
                )
            else:
                pairs += host_device_eid_pairs("h2d", ranks, host_device_pairs)
                pairs += host_device_eid_pairs("d2h", ranks, host_device_pairs)
                pairs += local_xboard_pairs_from_inventory(
                    topology, inventory, ranks, link_mode=link_mode,
                    d2d_mode=d2d_mode, master_rank=d2d_main,
                )

            for pair in pairs:
                pair["target_host"] = target.host
                pair["target_label"] = label

            all_pairs.extend(pairs)
            if status_log:
                status_log.ok(f"{label} 链路计算完成，共 {len(pairs)} 条链路")
        except Exception as exc:
            if status_log:
                status_log.fail(f"{label} 查询链路失败: {exc}")
        finally:
            try:
                client.close()
            except Exception:
                pass

    return all_pairs


def _read_remote_file(manager, client, target, path):
    import shlex
    command = f"cat {shlex.quote(path)}"
    result = manager.run_command(client, target, command)
    if not result.get("ok"):
        error = result.get("stderr") or result.get("stdout") or "unknown error"
        raise RuntimeError(f"读取远程文件失败 {path}: {error}")
    return result.get("stdout") or ""


def _read_remote_host_device_pairs(manager, client, target, device_ids):
    devices = " ".join(str(device) for device in device_ids)
    command = (f"for dev in {devices}; do "
               "echo $dev > /proc/asdrv_ub/dev_id 2>/dev/null || continue; "
               "echo __SPRINKLER_DEV__ $dev; "
               "cat /proc/asdrv_ub/pair_info 2>/dev/null || true; done; true")
    result = manager.run_command(client, target, command)
    return parse_host_device_pairs_text(result.get("stdout")) if result.get("ok") else {}


def _deduplicate_d2d_pairs(pairs):
    seen = set()
    result = []
    for pair in pairs:
        kind = pair.get("kind", "")
        if kind in ("intra_d2d", "xboard_d2d"):
            cr = pair.get("client_rank", "")
            sr = pair.get("server_rank", "")
            key = (kind, tuple(sorted([cr, sr])))
            if key in seen:
                continue
            seen.add(key)
        result.append(pair)
    return result


def _build_link_terminal_rows(pairs):
    show_link = has_link_distinction(pairs)
    prev_header = ""
    lines = []
    for pair in pairs:
        kind = pair.get("kind", "")
        link_val = display_link(pair.get("link"))
        header_parts = [f"kind={kind}", f"runner={pair.get('runner', '')}"]
        if show_link and link_val:
            header_parts.append(f"link={link_val}")
        header = " ".join(header_parts)

        if header != prev_header:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f'<span class="cmd-info">--- {html_escape(header)} ---</span>')
            prev_header = header

        reason = display_reason(pair.get("reason"))
        client_eid = pair.get("client_eid32", "")
        server_eid = pair.get("server_eid32", "")
        client_target = pair.get("client_target", "")
        server_target = pair.get("server_target", "")

        if client_target and server_target:
            detail = f"{client_target}→{server_target}"
        else:
            detail = f"NPU{pair.get('client_rank', '?')}→NPU{pair.get('server_rank', '?')}"
        if client_eid:
            detail += f"  eid={client_eid}"
        if server_eid:
            detail += f" → eid={server_eid}"
        if reason:
            detail += f"  reason={reason}"

        ok = pair.get("ok", True)
        css_class = "cmd-ok" if ok else "cmd-fail"
        lines.append(f'<span class="{css_class}">{html_escape(detail)}</span>')

    return "<br>".join(lines)



@st.fragment(run_every="0.3s")
def _render_terminal_right():
    running = st.session_state.get("link_running", False)
    collector = st.session_state.get("link_collector")
    final_messages = st.session_state.get("link_status_log")

    if running and collector:
        messages = collector.messages
        is_running = not collector.finished
        with st.container(border=True):
            st.markdown("**执行过程**")
            render_status_terminal(messages, is_running=is_running, collector=collector if is_running else None, wait_text="等待查询链路...")

        if collector.finished:
            update = {
                "link_results": collector.results,
                "link_status_log": collector.messages,
                "link_running": False,
            }
            if hasattr(collector, "extra") and collector.extra:
                for k, v in collector.extra.items():
                    update[k] = v
            st.session_state.update(update)
            st.session_state.pop("link_collector", None)
            st.rerun()
    elif final_messages:
        with st.container(border=True):
            st.markdown("**执行过程**")
            render_status_terminal(final_messages, is_running=False, wait_text="等待查询链路...")
    else:
        with st.container(border=True):
            st.markdown("**执行过程**")
            render_status_terminal([], is_running=False, wait_text="等待查询链路...")


def render():
    _inject_warm_theme()
    inject_terminal_css()
    inject_terminal_height_css()
    st.markdown("""<style>
        .cmd-terminal:not(.cmd-terminal-status) {
            max-height: 50vh !important;
        }
    </style>""", unsafe_allow_html=True)

    col_left, col_right = st.columns([1, 2], gap="medium", vertical_alignment="top")

    with col_left:
        with st.container(border=True):
            st.markdown("**链路配置**")
            mode = st.selectbox("模式", list(_MODE_OPTIONS.keys()), index=0,
                                format_func=lambda k: _MODE_OPTIONS[k])

        is_intra_d2d = mode == "d2d"
        link_mode = "all"
        same_plane = False
        all2all = False
        d2d_mode = "fullmesh" if is_intra_d2d else "many-to-one"
        d2d_main = None
        rank = ""
        server_rank = ""

        targets, parallel = render_target_selector("cpu_os", default_parallel=4)

        exec_disabled = not targets
        if st.button("🔍 查询链路", disabled=exec_disabled, type="primary", use_container_width=True):
            collector = StatusCollector()
            collector.progress = (0, len(targets))
            st.session_state.update({
                "link_collector": collector,
                "link_running": True,
                "link_results": None,
                "link_status_log": None,
                "link_pairs": [],
                "link_ranks": [],
            })

            task = dict(
                targets=targets, mode=mode,
                d2d_mode=d2d_mode if is_intra_d2d else "many-to-one",
                d2d_main=d2d_main, rank=rank, link_mode=link_mode,
                same_plane=same_plane, all2all=all2all, server_rank=server_rank,
            )

            def _run_in_thread():
                try:
                    pairs = _run_link_query(
                        task["targets"], task["mode"],
                        task["d2d_mode"], task["d2d_main"],
                        task["rank"], task["link_mode"],
                        task["same_plane"], task["all2all"], task["server_rank"],
                        connect_timeout=4, status_log=collector,
                    )
                    collector.set_extra("link_pairs", pairs)
                    collector.set_extra("link_ranks", sorted(set(
                        int(r) for p in pairs
                        for r in [p.get("client_rank", ""), p.get("server_rank", "")]
                        if r != "" and r is not None
                    )))
                    deduped_count = len(_deduplicate_d2d_pairs(pairs))
                    collector.ok(f"链路查询完成，共 {deduped_count} 条链路（去重后）")
                except Exception as exc:
                    collector.fail(f"链路查询异常: {exc}")
                finally:
                    collector.finished = True

            threading.Thread(target=_run_in_thread, daemon=True).start()
            st.rerun()



    with col_right:
        _render_terminal_right()

        pairs = st.session_state.get("link_pairs", [])
        if pairs and not st.session_state.get("link_running", False):
            deduped = _deduplicate_d2d_pairs(pairs)
            with st.container(border=True):
                st.markdown("**链路详情**")
                render_terminal_body(_build_link_terminal_rows(deduped))
