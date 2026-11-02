import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import streamlit as st

from sprinkler.auto.runtime.check.check_env import (
    CheckEnv, resolve_env,
)
from sprinkler.auto.runtime.check.check_runner import CheckRunner

from sprinkler.base.log import RunLog
from sprinkler.connect.base import BaseConnector
from views.utils._theme import inject as _inject_warm_theme, inject_terminal_css
from views.utils._components import (
    StatusCollector, render_target_selector, inject_terminal_height_css,
    make_terminal_fragment,
)


ENV_OPTIONS = {
    "CPU": "cpu1213",
    "NPU": "npu1213",
    "SFU": "sfu1260",
}

_ENV_TO_MODE = {
    "cpu1213": "cpu_1213",
    "npu1213": "npu_1213",
    "sfu1260": "sfu_com",
}

_CHECK_ITEM_LABELS = {
    "card": "子卡注册", "config": "配置信息", "port": "端口状态", "link": "建链状态",
    "credit": "信用证", "disk_cpu_os": "os磁盘", "disk_cpu_1213": "1213磁盘",
    "disk_cpu_bmc": "bmc磁盘", "disk_npu_1213": "1213磁盘", "disk_npu_bmc": "bmc磁盘",
    "disk_sfu_bmc": "bmc磁盘", "disk_sfu_1260": "1260磁盘", "ubm_conn": "1213连接数",
}

_ENV_CHECK_ITEMS = {
    "cpu1213": ["card", "config", "port", "link", "credit", "disk_cpu_os", "disk_cpu_1213", "disk_cpu_bmc", "ubm_conn"],
    "npu1213": ["card", "config", "port", "link", "credit", "disk_npu_1213", "disk_npu_bmc", "ubm_conn"],
    "sfu1260": ["card", "config", "port", "link", "credit", "disk_sfu_bmc", "disk_sfu_1260", "ubm_conn"],
}

_ENV_DEVICE_MAP = {
    "cpu1213": {"device_type": "cpu", "host_key": "cpu_bmc_host", "builder_name": "cpu_1213"},
    "npu1213": {"device_type": "npu", "host_key": "bmc_host", "builder_name": "npu_1213"},
    "sfu1260": {"device_type": "sfu", "host_key": "com_host", "builder_name": "sfu_com"},
}


class _TeeRunLog:
    def __init__(self, run_log, collector):
        self._run_log = run_log
        self._collector = collector

    def info(self, message):
        self._run_log.info(message)
        self._collector.info(message)

    def fail(self, message):
        self._run_log.fail(message)
        self._collector.fail(message)

    def ok(self, message):
        self._run_log.ok(message)
        self._collector.ok(message)

    def __getattr__(self, name):
        return getattr(self._run_log, name)


def _run_check_task(env: CheckEnv, check_items: list[str], args, targets, run_log=None, status_collector=None):
    checker = CheckRunner(env=env, args=args, run_log=run_log, check_items=check_items)
    results = {}
    exec_logs = {}
    max_workers = getattr(args, "parallel", 8) or 8

    def _check_and_collect(target):
        label = BaseConnector.target_label(target)
        if status_collector:
            status_collector.info(f"开始检查 {label} ...")

        log_path = checker._get_log_path(target)
        node_num = target.meta.get("node", 0) if target.meta else 0
        slot_id = checker.get_slot_id(node_num)

        groups = checker._group_items_by_connection()
        item_log_map = {}
        all_cmd_results = []

        for (conn_type, tgt_type), item_classes in groups.items():
            runner = checker._get_runner(conn_type)
            group_target = checker._resolve_target_for_group(target, tgt_type)
            group_log_path = checker._get_log_path(group_target) if tgt_type != env.target_type else log_path
            for item_cls in item_classes:
                item_log_map[item_cls.__name__] = group_log_path
            commands = []
            for item_cls in item_classes:
                commands.extend(item_cls.get_commands(
                    chip_count=env.chip_count, slot_id=slot_id, credit_after_lines=env.credit_after_lines
                ))

            def _on_result(result, _label=label):
                checker._handle_realtime_result(result)
                if status_collector and isinstance(result, dict):
                    status_collector.log_cmd_result(_label, result)

            cmd_results = runner.run([group_target], commands, on_result=_on_result)
            if cmd_results:
                all_cmd_results.extend(cmd_results)
            if not cmd_results:
                if status_collector:
                    status_collector.fail(f"[{label}] 命令执行无结果")
                return False, {"ExecuteFail": ["命令执行无结果"]}, {}, all_cmd_results
            failed_cmds = [r.get("cmd", "?") for r in cmd_results if isinstance(r, dict) and not r.get("ok") and not r.get("stdout")]
            if failed_cmds:
                if status_collector:
                    status_collector.fail(f"[{label}] 部分命令执行失败: {', '.join(failed_cmds)}")
                return False, {"ExecuteFail": failed_cmds}, {}, all_cmd_results

        all_pass, anomalies, item_status = checker.analyze_log(item_log_map)

        if status_collector:
            method = status_collector.ok if all_pass else status_collector.fail
            method(f"[{label}] 检查{'全部通过' if all_pass else '存在异常'}")
            status_collector.write_target_result(label, all_pass, anomalies, item_status, [])

        return all_pass, anomalies, item_status, all_cmd_results

    if max_workers > 1 and len(targets) > 1:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as pool:
            node_results = list(pool.map(_check_and_collect, targets))
    else:
        node_results = [_check_and_collect(t) for t in targets]

    for target, result in zip(targets, node_results):
        label = BaseConnector.target_label(target)
        if isinstance(result, tuple) and len(result) == 4:
            all_pass, anomalies, item_status, cmd_results = result
            results[label] = (all_pass, anomalies, item_status)
            exec_logs[label] = cmd_results
        else:
            results[label] = (False, {"Error": ["节点执行异常"]}, {})
            exec_logs[label] = []

    return results, exec_logs


_render_terminal_right = make_terminal_fragment("check", "等待执行检查任务...")


def render():
    _inject_warm_theme()
    inject_terminal_css()
    inject_terminal_height_css()

    col_left, col_right = st.columns([1, 2], gap="medium", vertical_alignment="top")

    with col_left:
        with st.container(border=True):
            st.markdown("**检查配置**")
            env_label = st.selectbox("环境类型", list(ENV_OPTIONS.keys()), index=0)
            env_name = ENV_OPTIONS[env_label]
            env = resolve_env(env_name)

        targets, parallel = render_target_selector(_ENV_TO_MODE[env_name], default_parallel=8)

        with st.container(border=True):
            st.markdown("**检查项**")
            env_items = _ENV_CHECK_ITEMS.get(env_name, [])
            item_labels = {k: _CHECK_ITEM_LABELS.get(k, k) for k in env_items}
            selected_labels = st.multiselect(
                "选择检查项",
                list(item_labels.values()),
                default=[item_labels[k] for k in env_items[:1]] if env_items else [],
                label_visibility="collapsed",
            )
            label_to_key = {v: k for k, v in item_labels.items()}
            selected_items = [label_to_key[l] for l in selected_labels]

        exec_disabled = not selected_items or not targets
        if st.button("🚀 执行检查", disabled=exec_disabled, type="primary", use_container_width=True):
            collector = StatusCollector(result_mode="dict")
            collector.progress = (0, len(targets))
            st.session_state.update({
                "check_collector": collector,
                "check_running": True,
                "check_results": None,
                "check_exec_logs": None,
            })

            def _run_in_thread():
                try:
                    args = SimpleNamespace(
                        connect_timeout=4,
                        timeout=30,
                        parallel=parallel,
                        cmd=[],
                        file=None,
                        check_id=f"check_{env.name}",
                    )
                    run_log = RunLog(args, default_check_id=f"check_{env.name}")
                    tee_log = _TeeRunLog(run_log, collector)
                    _run_check_task(
                        env, list(selected_items), args, list(targets),
                        run_log=tee_log, status_collector=collector,
                    )
                except Exception as exc:
                    collector.fail(f"检查执行异常: {exc}")
                finally:
                    collector.finished = True

            threading.Thread(target=_run_in_thread, daemon=True).start()
            st.rerun()

    with col_right:
        _render_terminal_right()
