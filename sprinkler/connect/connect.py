from sprinkler.arguments import get_args
from sprinkler.base.log import RunLog
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.targets import TargetContext

CONNECTION_MODES = {
    "cpu_os": {"connection_type": "ssh_os", "target_type": "cpu_os"},
    "cpu_bmc": {"connection_type": "ssh_bmc", "target_type": "cpu_bmc"},
    "cpu_1213": {"connection_type": "cpu_1213", "target_type": "cpu_1213"},
    "npu_bmc": {"connection_type": "ssh_bmc", "target_type": "npu_bmc"},
    "npu_1213": {"connection_type": "npu_1213", "target_type": "npu_1213"},
    "sfu_bmc": {"connection_type": "ssh_bmc", "target_type": "sfu_bmc"},
    "sfu_com": {"connection_type": "telnet_com", "target_type": "sfu_com"},
    "roce_bmc": {"connection_type": "ssh_bmc", "target_type": "roce_bmc"},
    "roce_com": {"connection_type": "telnet_com", "target_type": "roce_com"},
    "smn_os": {"connection_type": "ssh_os", "target_type": "smn_os"},
    "smn_bmc": {"connection_type": "ssh_os", "target_type": "smn_bmc"},
    # SMN serial port run commands (handled directly via smn_1213_bmc_execute)
    "cpu_1213_smn": {"connection_type": "smn_1213_bmc"},
    "npu_1213_smn": {"connection_type": "smn_1213_bmc"},
    "sfu_com_smn": {"connection_type": "smn_1213_bmc"},
    "cpu_bmc_smn": {"connection_type": "smn_1213_bmc"},
    "npu_bmc_smn": {"connection_type": "smn_1213_bmc"},
    "sfu_bmc_smn": {"connection_type": "smn_1213_bmc"},
}


def _run_mode(args, mode):
    config = CONNECTION_MODES[mode]

    if config.get("connection_type") == "smn_1213_bmc":
        from sprinkler.connect.smn import smn_1213_bmc_execute
        return smn_1213_bmc_execute(args)

    run_log = RunLog(args, default_check_id=mode)
    run_log.info(f"log_dir={run_log.path or 'disabled'}")
    try:
        target_ctx = TargetContext(args)
        targets = target_ctx.get_targets(config["target_type"])
        runner = create_runner(args=args, run_log=run_log, connection_type=config["connection_type"])
        results = runner.run(targets)
    except Exception as exc:
        return run_log.fail_error(mode, exc)

    return run_log.finish_results(mode, results)


def connect_exec():
    args = get_args()

    for mode in CONNECTION_MODES:
        if getattr(args, mode, False):
            return _run_mode(args, mode)
