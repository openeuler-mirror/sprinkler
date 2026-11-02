"""运行时检查入口：根据 -sfu-check/-npu-check/-cpu-check + -check-item 执行检查。"""

from sprinkler.base.log import RunLog
from sprinkler.auto.runtime.check.check_env import (
    resolve_env_type, resolve_env_from_args, resolve_env, ENV_LABEL,
)
from sprinkler.auto.runtime.check.check_runner import parse_check_items
from sprinkler.auto.runtime.check.check_builder import CheckBuilder


def runtime_check(args):
    """运行时检查统一入口。由 auto.py 调用。"""
    env_type = resolve_env_from_args(args)
    if env_type is None:
        return

    check_item_str = getattr(args, 'check_item', None)
    check_type = resolve_env_type(env_type)
    item_names = parse_check_items(check_item_str, env_name=check_type)
    label = ENV_LABEL.get(env_type, env_type.upper())

    run_log = RunLog(args, default_check_id=f"{env_type}_check")
    run_log.info(f"log_dir={run_log.path or 'disabled'}")
    run_log.info(f"{label} 运行时检查")
    run_log.info(f"检查类型: {check_type}")
    run_log.info(f"检查项: {', '.join(item_names)}")

    env = resolve_env(check_type)
    checker = (CheckBuilder()
               .env(env)
               .args(args)
               .run_log(run_log)
               .check_items(item_names)
               .build())

    try:
        results = checker.check_batch()
    except Exception as exc:
        run_log.fail_error(f"{env_type}_check", exc)
        return

    _print_results(run_log, results, label)


def _print_results(run_log, results: dict, label: str):
    run_log.info(" ")
    run_log.info(f"{label} 检查结果汇总")

    total_pass = True
    for target_label, (all_pass, anomalies, item_status) in results.items():
        if run_log:
            status_str = run_log.formatter.color_level("PASS" if all_pass else "FAIL")
        else:
            status_str = "PASS" if all_pass else "FAIL"
        run_log.info(f"  {target_label}: {status_str}")
        if not all_pass:
            total_pass = False
        if item_status:
            for name, (check_name, ok, anomaly_list) in item_status.items():
                item_status_str = run_log.formatter.color_level("PASS" if ok else "FAIL") if run_log else ("PASS" if ok else "FAIL")
                run_log.info(f"    [{check_name}]: {item_status_str}")
                if anomaly_list:
                    for anomaly in anomaly_list:
                        run_log.fail(f"      - {anomaly}")
        if not item_status and not all_pass:
            for item_name, anomaly_list in anomalies.items():
                run_log.fail(f"    [{item_name}]")
                for anomaly in anomaly_list:
                    run_log.fail(f"      - {anomaly}")

    if run_log:
        status_str = run_log.formatter.color_level("PASS" if total_pass else "FAIL")
    else:
        status_str = "PASS" if total_pass else "FAIL"
    run_log.info(f"  总体结果: {status_str}")
