from sprinkler.base.log import RunLog
from sprinkler.connect.base import ResultProcessor
from sprinkler.connect.ssh.bmc import SSHBMCConnector
from sprinkler.connect.connection_runner import ConnectionRunner
from sprinkler.connect.targets import TargetContext
 
CHECK_ID = "check_version"
VERSION_COMMAND = "source /etc/profile; /opt/bmc/skynet/lua /opt/bmc/apps/cli/service/ipmcget.lua -d version"
BMC_TYPES = ["cpu-bmc", "npu-bmc", "sfu-bmc", "roce-bmc"]
BMC_TYPE_TO_TARGET_TYPE = {
    "cpu-bmc": "cpu_bmc",
    "npu-bmc": "npu_bmc",
    "sfu-bmc": "sfu_bmc",
    "roce-bmc": "roce_bmc",
}
 
 
def check_version_exec(args) -> int:
    """
    Execute BMC version batch check.
    
    Args:
        args: Command line argument object (argparse.Namespace)
        
    Returns:
        int: 0 for all success, non-0 for failures
    """
    run_log = RunLog(args, default_check_id=CHECK_ID)
    run_log.info(f"log_dir={run_log.path}")
 
    total_success = 0
    total_fail = 0
 
    for bmc_type in BMC_TYPES:
        success, fail = _check_version_for_bmc_type(args, run_log, bmc_type)
        total_success += success
        total_fail += fail
 
    if total_fail > 0:
        run_log.fail(f"check_version completed with {total_fail} failures")
        return 1
    run_log.ok(f"check_version completed successfully for all {total_success} targets")
    return 0
 
 
def _check_version_for_bmc_type(args, run_log, bmc_type: str) -> tuple[int, int]:
    """
    Execute version check for specified BMC type.
    
    Args:
        args: Command line arguments
        run_log: RunLog instance
        bmc_type: BMC type such as "cpu-bmc", "npu-bmc", "sfu-bmc"
        
    Returns:
        tuple[int, int]: (success_count, fail_count)
    """
    target_type = BMC_TYPE_TO_TARGET_TYPE.get(bmc_type)
    if not target_type:
        run_log.fail(f"unsupported BMC type: {bmc_type}")
        return 0, 1
 
    try:
        target_ctx = TargetContext(args)
        targets = target_ctx.get_targets(target_type)
    except Exception as exc:
        run_log.warn(f"no {bmc_type} targets: {exc}")
        return 0, 0
 
    if not targets:
        run_log.warn(f"no {bmc_type} targets found")
        return 0, 0
 
    connector = SSHBMCConnector(args=args, result_processor=ResultProcessor(), run_log=run_log)
    runner = ConnectionRunner(args=args, run_log=run_log, connector=connector)
 
    run_log.info(f"【{bmc_type} version check】")
    success_count = 0
    fail_count = 0
 
    try:
        results = runner.run(targets, [VERSION_COMMAND])
    except Exception as exc:
        run_log.fail_error(CHECK_ID, exc)
        return 0, len(targets)
 
    for result in results:
        target_name = result.get("target", "unknown")
        if result.get("ok"):
            success_count += 1
            stdout = result.get("stdout", "").strip()
            run_log.ok(f"  {target_name}: {stdout}")
        else:
            fail_count += 1
            error_type = result.get("error_type", "unknown")
            reason = result.get("reason", "")
            run_log.fail(f"  {target_name}: FAIL - {error_type} {reason}")
 
    return success_count, fail_count
