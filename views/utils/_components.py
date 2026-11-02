import io
import re
import threading
from dataclasses import asdict

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text):
    return _ANSI_RE.sub("", text)

import pandas as pd
import streamlit as st

from sprinkler.connect.connect import CONNECTION_MODES
from sprinkler.connect.targets import TargetBuilder, CabinetRef, TargetContext, ConnectionTarget
from sprinkler.config.superpod_info import preprocess_column_names
from sprinkler.utils.utils import default_config_dir


_HOST_FIELDS = {
    "cpu": ("host", "cpu_bmc_host"),
    "npu": ("bmc_host",),
    "sfu": ("bmc_host", "com_host"),
    "smn": ("host", "bmc_host"),
}


class StatusCollector:
    def __init__(self, *, result_mode="list"):
        self._messages = []
        self._results = [] if result_mode == "list" else {}
        self._exec_logs = {}
        self._extra = {}
        self._done_count = 0
        self._total_count = 0
        self._finished = False
        self._lock = threading.Lock()
        self._result_mode = result_mode

    def _append(self, level, message):
        with self._lock:
            self._messages.append((level, message))

    def info(self, message):
        self._append("info", message)

    def fail(self, message):
        self._append("fail", message)

    def ok(self, message):
        self._append("ok", message)

    def log(self, level, message):
        self._append(level.lower(), message)

    def write_target_results(self, target_name, results):
        with self._lock:
            if self._result_mode == "list":
                self._results.extend(results if results else [])
            self._done_count += 1

    def log_cmd_result(self, target_label, cmd_result):
        with self._lock:
            self._exec_logs.setdefault(target_label, []).append(cmd_result)

    def write_target_result(self, target_label, all_pass, anomalies, item_status, cmd_results):
        with self._lock:
            self._results[target_label] = (all_pass, anomalies, item_status)
            if cmd_results:
                self._exec_logs.setdefault(target_label, []).extend(cmd_results)
            self._done_count += 1

    @property
    def messages(self):
        with self._lock:
            return list(self._messages)

    @property
    def results(self):
        with self._lock:
            return list(self._results) if self._result_mode == "list" else dict(self._results)

    @property
    def exec_logs(self):
        with self._lock:
            return dict(self._exec_logs)

    @property
    def progress(self):
        with self._lock:
            return self._done_count, self._total_count

    @progress.setter
    def progress(self, value):
        with self._lock:
            self._done_count, self._total_count = value

    def set_extra(self, key, value):
        with self._lock:
            self._extra[key] = value

    @property
    def extra(self):
        with self._lock:
            return dict(self._extra)

    @property
    def finished(self):
        with self._lock:
            return self._finished

    @finished.setter
    def finished(self, value):
        with self._lock:
            self._finished = value


def html_escape(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def status_icon(level):
    if level in ("ok", "pass"):
        return "✅"
    if level in ("fail",):
        return "❌"
    return "⏳"


def render_terminal_body(html_content):
    st.markdown(
        f'<div class="cmd-terminal">{html_content}</div>',
        unsafe_allow_html=True,
    )


def render_status_terminal(messages, is_running=False, collector=None, wait_text="等待执行任务..."):
    if not messages and not is_running:
        st.markdown(
            f'<div class="cmd-terminal cmd-terminal-status"><span class="cmd-info">{html_escape(wait_text)}</span></div>',
            unsafe_allow_html=True,
        )
        return

    lines = []
    for level, message in messages:
        css_class = "cmd-ok" if level in ("ok", "pass") else "cmd-fail" if level == "fail" else "cmd-info"
        icon = status_icon(level)
        lines.append(f'<span class="{css_class}">{icon} {html_escape(_strip_ansi(message))}</span>')

    if is_running:
        lines.append('<span class="cmd-refresh-anim">█</span>')

    scroll_id = "status-scroll-anchor"
    st.markdown(
        f'<div class="cmd-terminal cmd-terminal-status">{"<br>".join(lines)}<span id="{scroll_id}"></span></div>',
        unsafe_allow_html=True,
    )
    st.html(
        f'''<script>setTimeout(function(){{var p=window.parent;var e=p.document.getElementById("{scroll_id}");if(!e)return;var c=e.parentElement;if(!p._statusAutoScroll)p._statusAutoScroll=true;if(!p._statusScrollBound){{p._statusScrollBound=true;c.addEventListener('scroll',function(){{p._statusAutoScroll=c.scrollHeight-c.scrollTop-c.clientHeight<50;}});}}if(p._statusAutoScroll)c.scrollTop=e.offsetTop;}},50)</script>'''
    )


def build_terminal_rows(group):
    lines = []
    for row in group:
        if not isinstance(row, dict):
            continue
        cmd = row.get("cmd", "")
        if cmd:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f'<span class="cmd-prompt">$</span> <span class="cmd-cmd">{html_escape(cmd)}</span>')
        stdout = row.get("stdout", "")
        stderr = row.get("stderr", "")
        if stdout:
            escaped = html_escape(_strip_ansi(stdout.rstrip())).replace("\n", "<br>")
            lines.append(f'<span class="cmd-stdout">{escaped}</span>')
        if stderr:
            escaped = html_escape(_strip_ansi(stderr.rstrip())).replace("\n", "<br>")
            lines.append(f'<span class="cmd-stderr">{escaped}</span>')
    return "<br>".join(lines)


def render_result_tabs(results, exec_logs=None):
    if exec_logs is None:
        exec_logs = {}

    if not results and not exec_logs:
        return

    if isinstance(results, dict):
        tab_names = list(results.keys())
        if not tab_names:
            return
        tabs = st.tabs(tab_names)
        for tab, label in zip(tabs, tab_names):
            with tab:
                cmd_results = exec_logs.get(label, [])
                render_terminal_body(build_terminal_rows(cmd_results))
    else:
        rows = [
            r if isinstance(r, dict) else (asdict(r) if hasattr(r, "__dataclass_fields__") else dict(r))
            for r in results
        ]
        grouped = {}
        for i, row in enumerate(rows):
            key = row.get("target", f"结果 {i+1}")
            grouped.setdefault(key, []).append(row)
        for label in exec_logs:
            if label not in grouped:
                grouped[label] = []
        tab_names = list(grouped.keys())
        if not tab_names:
            return
        tabs = st.tabs(tab_names)
        for tab, label in zip(tabs, tab_names):
            with tab:
                group = grouped[label]
                log_rows = exec_logs.get(label, [])
                all_rows = log_rows if log_rows else group
                render_terminal_body(build_terminal_rows(all_rows))


def render_unified_terminal(collector=None, final_results=None, final_exec_logs=None, final_messages=None, finished=False, wait_text="等待执行任务..."):
    if collector:
        messages = collector.messages
        results = collector.results
        exec_logs = collector.exec_logs if hasattr(collector, "exec_logs") else None
        is_running = not collector.finished
    else:
        messages = final_messages or []
        results = final_results or []
        exec_logs = final_exec_logs
        is_running = False

    with st.container(border=True):
        st.markdown("**执行过程**")
        render_status_terminal(messages, is_running=is_running, collector=collector if is_running else None, wait_text=wait_text)
    with st.container(border=True):
        st.markdown("**执行结果**")
        render_result_tabs(results, exec_logs=exec_logs)


def inject_terminal_height_css():
    st.markdown("""<style>
        .cmd-terminal-status {
            max-height: 20vh !important;
            overflow-y: auto !important;
        }
        .cmd-terminal:not(.cmd-terminal-status) {
            max-height: 60vh !important;
            overflow-y: auto !important;
        }
    </style>""", unsafe_allow_html=True)


def discover_superpod_files():
    config_dir = default_config_dir()
    if not config_dir.exists():
        return []
    return [
        (m.group(1).lower(), p)
        for p in sorted(config_dir.glob("superpod_*.xlsx"))
        if (m := re.fullmatch(r"superpod_(.+)\.xlsx", p.name, re.IGNORECASE))
    ]


def load_superpod_sheets(file_source):
    with pd.ExcelFile(file_source) as xls:
        return list(xls.sheet_names)


def load_cabinet_from_xlsx(file_source, sheet_name, sp_num, ca_index, default_user="root"):
    with pd.ExcelFile(file_source) as xls:
        df = pd.read_excel(xls, sheet_name=sheet_name)
        df = preprocess_column_names(df)
    from sprinkler.config.cabinet_info import get_cabinet_data
    return get_cabinet_data(df, sheet_name, sp_num, ca_index, default_user=default_user, default_password="")


def build_targets_from_cabinet(mode_key, cabinet_data, selected_nodes, sp, ca_index, sheet_name):
    config = CONNECTION_MODES[mode_key]
    target_type = config["target_type"]
    specs = TargetContext.TARGET_SPECS
    if target_type not in specs:
        return []
    device_type, builder_name = specs[target_type]
    devices = cabinet_data.get(device_type, [])
    if not devices:
        return []
    device_map = {item["index"]: item for item in devices}
    builder = TargetBuilder("root", "")
    cabinet_ref = CabinetRef(sp=sp, ca=str(ca_index), sheet_name=sheet_name, sheet_index=ca_index)
    builder_func = getattr(builder, builder_name)
    targets = []
    for node_idx in selected_nodes:
        if node_idx in device_map:
            targets.append(builder_func(sp, cabinet_ref, node_idx, device_map[node_idx]))
    return targets


def render_manual_input(mode_key="cpu_os", key_prefix=""):
    config = CONNECTION_MODES.get(mode_key, {})
    kind = config.get("target_type", "")

    raw_text = st.text_area(
        "手动输入目标",
        placeholder="每行一个目标，用空格分割：IP 用户名 密码\n如：192.168.1.1 root mypassword",
        height=120,
        key=f"manual_input_{mode_key}_{key_prefix}" if key_prefix else f"manual_input_{mode_key}",
    )

    targets = []
    if raw_text.strip():
        for i, line in enumerate(raw_text.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) < 1:
                continue
            ip = parts[0]
            user = parts[1] if len(parts) > 1 else "root"
            password = parts[2] if len(parts) > 2 else ""
            target = ConnectionTarget(
                name=ip,
                host=ip,
                port=22,
                user=user,
                password=password,
                meta={
                    "kind": kind,
                    "sp": "0",
                    "ca": "0",
                    "node": i,
                    "cabinet_name": "",
                    "raw_name": ip,
                    "source": "manual",
                },
            )
            targets.append(target)

    if targets:
        st.success(f"已输入 {len(targets)} 个目标")
    return targets


def render_target_selector(mode_key, default_parallel=4, show_parallel=True, label="执行机"):
    with st.container(border=True):
        if show_parallel:
            _radio_col, _par_col = st.columns([3, 1])
            with _radio_col:
                st.markdown(f"**{label}**")
                target_mode = st.radio(" ", ["从超节点文件获取", "手动输入"], horizontal=True, label_visibility="collapsed", key=f"_radio_{label}")
            with _par_col:
                parallel = st.number_input("并行度", min_value=1, max_value=64, value=default_parallel, help="并发执行的机器数量")
        else:
            st.markdown(f"**{label}**")
            parallel = default_parallel
            target_mode = st.radio(" ", ["从超节点文件获取", "手动输入"], horizontal=True, label_visibility="collapsed", key=f"_radio_{label}")
        if target_mode == "从超节点文件获取":
            targets = render_xlsx_selector(mode_key, key_prefix=label)
        else:
            targets = render_manual_input(mode_key, key_prefix=label)
        if not targets:
            st.warning("请配置至少一个目标")
    return targets, parallel


def render_peer_target_selector(mode_key, label="对端（Client）"):
    with st.container(border=True):
        st.markdown(f"**{label}**")
        target_mode = st.radio(" ", ["从超节点文件获取", "手动输入"], horizontal=True, label_visibility="collapsed", key=f"_radio_{label}")
        if target_mode == "从超节点文件获取":
            targets = render_xlsx_selector(mode_key, key_prefix=label)
        else:
            targets = render_manual_input(mode_key, key_prefix=label)
        if not targets:
            st.warning("请配置至少一个对端目标")
    return targets


def render_xlsx_selector(mode_key, key_prefix=""):
    sp_bytes = st.session_state.get("_shared_sp_bytes")
    sp_name = st.session_state.get("_shared_sp_name", "")

    if not sp_bytes:
        st.info("请在左侧边栏上传超节点文件（.xlsx）")
        return []

    name_match = re.fullmatch(r"superpod_(.+)\.xlsx", sp_name, re.IGNORECASE)
    sp_num = name_match.group(1).lower() if name_match else "0"
    if not name_match:
        _sp_num_key = f"upload_sp_num_{mode_key}_{key_prefix}" if key_prefix else f"upload_sp_num_{mode_key}"
        sp_num = st.text_input("超节点编号", value="0", key=_sp_num_key)

    file_source = io.BytesIO(sp_bytes)
    sheets = []
    try:
        sheets = load_superpod_sheets(file_source)
    except Exception as exc:
        st.error(f"读取表格失败: {exc}")
        return []
    file_source.seek(0)

    if not sheets:
        st.warning("表格无页签")
        return []

    ca_labels = [f"{hex(i)[2:]}: {name}" for i, name in enumerate(sheets)]
    _ca_key = f"select_ca_{mode_key}_{key_prefix}" if key_prefix else f"select_ca_{mode_key}"
    selected_ca_labels = st.multiselect("选择机柜(页签)", ca_labels, default=ca_labels[:1] if ca_labels else [], key=_ca_key)

    if not selected_ca_labels:
        st.info("请选择至少一个机柜")
        return []

    label_to_idx = {l: i for i, l in enumerate(ca_labels)}
    selected_ca_indices = [(label_to_idx[l], sheets[label_to_idx[l]]) for l in selected_ca_labels]

    all_cabinet_data = {}
    for ca_idx, ca_name in selected_ca_indices:
        try:
            file_source.seek(0)
            data = load_cabinet_from_xlsx(file_source, ca_name, sp_num, ca_idx)
            all_cabinet_data[(ca_idx, ca_name)] = data
        except Exception as exc:
            st.warning(f"读取机柜 {ca_name} 失败: {exc}")

    if not all_cabinet_data:
        st.warning("未加载到任何机柜数据")
        return []

    config = CONNECTION_MODES[mode_key]
    target_type = config["target_type"]
    specs = TargetContext.TARGET_SPECS
    if target_type not in specs:
        st.error(f"不支持的目标类型: {target_type}")
        return []
    device_type, _ = specs[target_type]

    node_options = []
    for (ca_idx, ca_name), data in all_cabinet_data.items():
        devices = data.get(device_type, [])
        for item in devices:
            idx = item["index"]
            raw_name = item.get("raw_name", f"{device_type}{idx}")
            host_info = next((item.get(f, "") for f in _HOST_FIELDS.get(device_type, ()) if item.get(f, "")), "")
            label_text = f"[{ca_name}] {raw_name} ({host_info})" if host_info else f"[{ca_name}] {raw_name}"
            node_options.append({
                "label": label_text,
                "ca_idx": ca_idx,
                "ca_name": ca_name,
                "node_idx": idx,
                "data": data,
            })

    if not node_options:
        st.warning(f"所选机柜中无 {device_type.upper()} 设备")
        return []

    _node_key = f"select_node_{mode_key}_{key_prefix}" if key_prefix else f"select_node_{mode_key}"
    selected_labels = st.multiselect(
        "选择节点",
        [n["label"] for n in node_options],
        default=[node_options[0]["label"]] if node_options else [],
        key=_node_key,
    )

    if not selected_labels:
        st.info("请选择至少一个节点")
        return []

    targets = []
    label_set = set(selected_labels)
    for opt in node_options:
        if opt["label"] in label_set:
            built = build_targets_from_cabinet(
                mode_key, opt["data"], [opt["node_idx"]], sp_num, opt["ca_idx"], opt["ca_name"]
            )
            targets.extend(built)

    if targets:
        st.success(f"已选择 {len(targets)} 个目标")

    return targets


def make_terminal_fragment(prefix, wait_text="等待执行任务..."):
    @st.fragment(run_every="0.3s")
    def _fragment():
        running = st.session_state.get(f"{prefix}_running", False)
        collector = st.session_state.get(f"{prefix}_collector")
        final_results = st.session_state.get(f"{prefix}_results")
        final_exec_logs = st.session_state.get(f"{prefix}_exec_logs")
        final_messages = st.session_state.get(f"{prefix}_status_log")

        if running and collector:
            render_unified_terminal(collector=collector, wait_text=wait_text)

            if collector.finished:
                update = {
                    f"{prefix}_results": collector.results,
                    f"{prefix}_status_log": collector.messages,
                    f"{prefix}_running": False,
                }
                if hasattr(collector, "exec_logs"):
                    update[f"{prefix}_exec_logs"] = collector.exec_logs
                if hasattr(collector, "extra") and collector.extra:
                    for k, v in collector.extra.items():
                        update[k] = v
                st.session_state.update(update)
                st.session_state.pop(f"{prefix}_collector", None)
                st.rerun()
        elif final_results is not None or final_messages:
            render_unified_terminal(
                final_results=final_results,
                final_exec_logs=final_exec_logs,
                final_messages=final_messages,
                wait_text=wait_text,
            )
        else:
            render_unified_terminal(wait_text=wait_text)

    return _fragment
