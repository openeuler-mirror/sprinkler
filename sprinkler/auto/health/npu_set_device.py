"""
NPU设备设置功能
功能: 测试torch.npu.set_device()对每个NPU设备的设置
"""

import os
import sys
import signal
import threading
import time
from typing import Optional, Tuple, List

from sprinkler.base.log import RunLog
from sprinkler.logging import log


def npu_set_device_check(args) -> int:
    """
    NPU设备设置检查主入口
    参数:
        args: 命令行参数对象
    返回:
        退出码 (0表示成功, 1表示失败)
    """
    CHECK_ID = "npu_set_device"
    EXPECTED_DEVICE_COUNT = 8
    DEVICE_TIMEOUT = 10

    run_log = RunLog(args, CHECK_ID)
    log("INFO", CHECK_ID, "开始NPU Set Device测试")

    failed_devices: List[int] = []

    def signal_handler(signum, frame):
        log("WARN", CHECK_ID, f"收到信号 {signum}，正在退出...")
        sys.exit(124)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        import torch
        import torch_npu
    except ImportError as e:
        log("FAIL", CHECK_ID, f"依赖未安装: {e.name}，请执行: pip install {e.name}")
        return 1
    except OSError as e:
        log("FAIL", CHECK_ID, f"CANN库加载失败({e})，请先执行: source /usr/local/Ascend/ascend-toolkit/set_env.sh")
        return 1

    if not (os.environ.get("ASCEND_TOOLKIT_HOME") or os.environ.get("ASCEND_HOME_PATH")):
        log("WARN", CHECK_ID, "未检测到CANN环境变量，可能未执行set_env.sh")

    try:
        if not torch.npu.is_available():
            log("INFO", CHECK_ID, ">>>AUTO_DETAILS_START<<<")
            log("FAIL", CHECK_ID, "SetDevice 无NPU")
            log("INFO", CHECK_ID, ">>>AUTO_DETAILS_END<<<")
            log("ERROR", CHECK_ID, "无可用NPU，请检查驱动(npu-smi info)及CANN环境")
            return 1

        log("INFO", CHECK_ID, f"NPU可用，设备数量: {torch.npu.device_count()}")
        device_count = torch.npu.device_count()

        log("INFO", CHECK_ID, "开始测试 torch.npu.set_device()")

        def test_single_device(device_id: int) -> Tuple[int, bool]:
            try:
                if device_id >= device_count:
                    log("FAIL", CHECK_ID, f"npu {device_id} 不存在 (系统只有 {device_count} 个设备)")
                    return device_id, False

                result = [None]
                exception = [None]
                thread_done = threading.Event()

                def device_operation():
                    try:
                        torch.npu.set_device(device_id)
                        current_device = torch.npu.current_device()
                        result[0] = (current_device == device_id, current_device)
                    except Exception as e:
                        exception[0] = e
                    finally:
                        thread_done.set()

                thread = threading.Thread(target=device_operation)
                thread.daemon = True
                thread.start()
                thread_done.wait(timeout=DEVICE_TIMEOUT)

                if not thread_done.is_set():
                    log("FAIL", CHECK_ID, f"npu {device_id} 操作超时（超过{DEVICE_TIMEOUT}秒）")
                    return device_id, False

                if exception[0] is not None:
                    log("FAIL", CHECK_ID, f"npu {device_id} set device失败: {exception[0]}")
                    return device_id, False

                success, current_device = result[0]
                if success:
                    log("PASS", CHECK_ID, f"npu {device_id} set device设置成功")
                    return device_id, True
                else:
                    log("FAIL", CHECK_ID, f"npu {device_id} set device异常: 当前设备为 {current_device}")
                    return device_id, False

            except Exception as e:
                log("FAIL", CHECK_ID, f"npu {device_id} set device失败: {e}")
                return device_id, False

        start_time = time.time()
        for device_id in range(EXPECTED_DEVICE_COUNT):
            dev_id, success = test_single_device(device_id)
            if not success:
                failed_devices.append(dev_id)
            if device_id % 2 == 0:
                time.sleep(0.1)

        elapsed = time.time() - start_time
        log("INFO", CHECK_ID, f"设备测试完成，总用时: {elapsed:.1f}秒")

    except Exception as e:
        log("FAIL", CHECK_ID, f"Python代码执行异常: {e}")
        return 1

    if failed_devices:
        log("INFO", CHECK_ID, ">>>AUTO_DETAILS_START<<<")
        log("FAIL", CHECK_ID, f"SetDevice: {EXPECTED_DEVICE_COUNT - len(failed_devices)}/{EXPECTED_DEVICE_COUNT}")
        log("INFO", CHECK_ID, ">>>AUTO_DETAILS_END<<<")
        log("INFO", CHECK_ID, f"失败设备数量: {len(failed_devices)}")
        log("INFO", CHECK_ID, f"成功设备数量: {EXPECTED_DEVICE_COUNT - len(failed_devices)}")
        return 1
    else:
        log("INFO", CHECK_ID, ">>>AUTO_DETAILS_START<<<")
        log("PASS", CHECK_ID, "SetDevice PASS")
        log("INFO", CHECK_ID, ">>>AUTO_DETAILS_END<<<")
        return 0
