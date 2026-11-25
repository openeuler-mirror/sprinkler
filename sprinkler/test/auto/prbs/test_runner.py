"""PRBS 全量打流功能集成测试。

外部依赖（NPU、5808、免密、工具下发）全部打桩，
从参数校验一直走到双侧 RX BER 汇总与 finally 关流，验证整体行为。
"""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sprinkler.auto.prbs import runner
from sprinkler.auto.prbs.switch_5808 import SwitchPort


def make_args(**overrides):
    values = dict(
        rank="0-1", macros="0-1", udie=0,
        rounds=2, interval=5,
        parallel=2, slot="3,4", timeout=60, log_root="",
        ip="192.0.2.10,192.0.2.11", user="huawei", pwd="secret",
    )
    # PRBS 下 -5808-front 可省略，-ip 直接指定 5808 前台管理 IP。
    values["5808_front"] = False
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeNpu:
    BER = {
        "NPU0": {1: [1.111111e-09, 1.111111e-09, 2.222222e-09, 3.333333e-09],
                 2: [4.0e-08, 1.0e-07, 5.0e-10, 6.0e-10],
                 3: [3.0e-08, 1.2e-07, 7.0e-10, 8.0e-10]},
        "NPU1": {1: [1.111111e-09, 1.111111e-09, 2.222222e-09, 3.333333e-09],
                 2: [3.0e-08, 9.0e-08, 9.0e-10, 1.0e-09],
                 3: [4.0e-08, 1.1e-07, 1.1e-09, 1.2e-09]},
    }

    def __init__(self, items):
        self.items = items
        self.start_calls = 0
        self.stop_calls = 0
        self.status_calls = 0

    def start(self):
        self.start_calls += 1
        return [{"target": item["target"], "ok": True, "rc": 0,
                 "stdout": "", "stderr": ""} for item in self.items]

    def stop(self):
        self.stop_calls += 1
        return [{"target": item["target"], "ok": True, "rc": 0,
                 "stdout": "", "stderr": ""} for item in self.items]

    def read_status(self):
        self.status_calls += 1
        serial = self.status_calls
        results = []
        for item in self.items:
            for macro in (0, 1):
                lines = [
                    f"serdes[0] macro[{macro}] lane[{lane}] prbs: en 1, "
                    f"type prbs31, status 1, cnt 0x1, alos 0, "
                    f"ber {value:.6e}"
                    for lane, value in enumerate(self.BER[item["target"]][serial])
                ]
                results.append({"target": item["target"], "ok": True, "rc": 0,
                                "stderr": "", "stdout": "\n".join(lines)})
        return results


class FakeSwitch:
    PORTS = [SwitchPort("800GUB1/0/65:1", 0, 256),
             SwitchPort("800GUB1/0/66:1", 0, 260)]

    def __init__(self, host="", user="", password="", port=22, slot=1,
                 args=None, name=""):
        self.host = host
        self.name = name or host
        self.slot = slot
        self.set_calls = []
        self.get_calls = 0

    def query_ports(self):
        return self.PORTS

    def set_prbs(self, ports, enable):
        self.set_calls.append(enable)
        return [{"ok": True, "rc": 0, "stdout": "", "stderr": ""} for _ in ports]

    def get_prbs(self, ports):
        self.get_calls += 1
        serial = self.get_calls
        results = []
        for port in ports:
            base = 1 if port.port == 256 else 2
            rate = serial * base * 1e-7
            block = (
                "\t[PRBS] result of unit 1 serdes 2: en=1, errNum=1, "
                f"rate={rate:.6e}, alos=0\n"
                f"\t[PRBS] PORT {port.port}, EN = 1 P = 8 errNum = 100\n")
            results.append({"ok": True, "rc": 0, "stdout": block, "stderr": ""})
        return results


class PrbsIntegrationTest(unittest.TestCase):
    def test_prbs_end_to_end(self):
        # 1) -ip 必填：PRBS 下 -ip 即 5808 前台管理 IP，缺失时预检失败。
        rejected = io.StringIO()
        with redirect_stdout(rejected):
            rc = runner.prbs_exec(make_args(ip=""))
        self.assertEqual(rc, runner.EXIT_USAGE)
        self.assertIn("-ip", rejected.getvalue())

        # slot 唯一：每台 5808 一个 slot，数量与 -ip 一致且不能重复。
        for slot in ("3", "3,3"):
            rejected = io.StringIO()
            with redirect_stdout(rejected):
                rc = runner.prbs_exec(make_args(slot=slot))
            self.assertEqual(rc, runner.EXIT_USAGE)
            self.assertIn("-slot", rejected.getvalue())

        # 2) 完整流程：预检 -> NPU/5808 开流 -> 多轮采样 -> 关流 -> 双侧汇总。
        with tempfile.TemporaryDirectory() as log_root:
            args = make_args(log_root=log_root)
            items = [
                {"rank": 0, "target": "NPU0", "execute_eid": "eid0"},
                {"rank": 1, "target": "NPU1", "execute_eid": "eid1"},
            ]
            npu = FakeNpu(items)
            switches = []

            def make_switch(*positional, **keyword):
                switch = FakeSwitch(*positional, **keyword)
                switches.append(switch)
                return switch

            output = io.StringIO()
            with mock.patch.object(runner, "build_local_npu_items", return_value=items), \
                 mock.patch.object(runner, "ensure_no_pass", return_value=True), \
                 mock.patch.object(runner, "ensure_serdestool", return_value=True), \
                 mock.patch.object(runner, "NpuPrbs", return_value=npu), \
                 mock.patch.object(runner, "Switch5808", side_effect=make_switch), \
                 mock.patch.object(runner.time, "sleep") as sleep, \
                 redirect_stdout(output):
                rc = runner.prbs_exec(args)

            text = output.getvalue()
            logs = list(Path(log_root).glob("PRBS/*/run.log"))
            self.assertEqual(len(logs), 1)
            log_text = logs[0].read_text(encoding="utf-8")

        self.assertEqual(rc, 0)
        self.assertIn("PRBS 完成，全部命令执行成功", text)
        self.assertIn("PRBS 明细日志", text)

        # 开流 / 采样 / 关流的整体时序与轮次（rounds=2 -> 采样 3 轮，首轮预热丢弃）。
        self.assertEqual(npu.start_calls, 1)
        self.assertEqual(npu.stop_calls, 1)
        self.assertEqual(npu.status_calls, 3)
        self.assertEqual([switch.name for switch in switches],
                         ["192.0.2.10", "192.0.2.11"])
        self.assertEqual([switch.slot for switch in switches], [3, 4])
        for switch in switches:
            self.assertEqual(switch.set_calls, [True, False])
            self.assertEqual(switch.get_calls, 3)
        self.assertIn("192.0.2.10 端口数: 2", text)
        self.assertIn("192.0.2.11 端口数: 2", text)
        self.assertEqual(sleep.call_count, 2)
        sleep.assert_called_with(5)
        self.assertIn("首轮开流就绪校验通过", text)
        self.assertIn("首轮开流就绪校验通过", log_text)
        self.assertNotIn("SNR", text)

        # 逐轮明细只写日志，不打印到控制台。
        self.assertNotIn("--- 第 1/2 轮有效采样结果 ---", text)
        self.assertNotIn("NPU0 serdes0 m0: L0=4.000000e-08", text)
        self.assertIn("--- 第 1/2 轮有效采样结果 ---", log_text)
        self.assertIn("NPU0 serdes0 m0: L0=4.000000e-08 L1=1.000000e-07",
                      log_text)
        self.assertIn("unit1 serdes2: en=1 errNum=1 rate=2.000000e-07 alos=0",
                      log_text)
        for switch_name in ("192.0.2.10", "192.0.2.11"):
            self.assertIn(f"{switch_name} chip0 port256 (800GUB1/0/65:1): "
                          "EN=1 P=8 errNum=100", log_text)
        self.assertNotIn("1.111111e-09", text)
        self.assertNotIn("1.111111e-09", log_text)
        self.assertNotIn("type=prbs31", text)
        self.assertNotIn("type=prbs31", log_text)
        self.assertNotIn("cnt=", text)
        self.assertNotIn("cnt=", log_text)
        self.assertNotIn("status=", text)
        self.assertNotIn("status=", log_text)

        # 双侧 RX 汇总：控制台打印且写入日志，NPU 在前、5808 在后。
        for target in (text, log_text):
            self.assertIn("NPU 侧 RX 误码率统计", target)
            self.assertIn("5808 侧 RX 端口误码率统计", target)
            self.assertLess(target.index("NPU 侧 RX"), target.index("5808 侧 RX"))
            self.assertIn("NPU0 serdes0 m0 L0: max=4.000000e-08 "
                          "mean=3.500000e-08", target)
            self.assertIn("NPU0 serdes0 m0 L1: max=1.200000e-07 "
                          "mean=1.100000e-07", target)
            for switch_name in ("192.0.2.10", "192.0.2.11"):
                self.assertIn(f"{switch_name} chip0 port260 (800GUB1/0/66:1) "
                              "unit1 serdes2: max=6.000000e-07 "
                              "mean=5.000000e-07", target)
                self.assertIn(f"{switch_name} chip0 port256 (800GUB1/0/65:1) "
                              "unit1 serdes2: max=3.000000e-07 "
                              "mean=2.500000e-07", target)
            self.assertNotIn("samples=", target)

        # 3) 开流未就绪：首轮校验失败时不进入有效采样，但仍执行关流清理。
        class NotReadyNpu(FakeNpu):
            def read_status(self):
                results = super().read_status()
                for result in results:
                    result["stdout"] = result["stdout"].replace("en 1", "en 0")
                return results

        with tempfile.TemporaryDirectory() as log_root:
            args = make_args(log_root=log_root, ip="192.0.2.10", slot="3")
            items = [{"rank": 0, "target": "NPU0", "execute_eid": "eid0"}]
            npu = NotReadyNpu(items)
            not_ready_switches = []

            def make_not_ready_switch(*positional, **keyword):
                switch = FakeSwitch(*positional, **keyword)
                not_ready_switches.append(switch)
                return switch

            output = io.StringIO()
            with mock.patch.object(runner, "build_local_npu_items", return_value=items), \
                 mock.patch.object(runner, "ensure_no_pass", return_value=True), \
                 mock.patch.object(runner, "ensure_serdestool", return_value=True), \
                 mock.patch.object(runner, "NpuPrbs", return_value=npu), \
                 mock.patch.object(runner, "Switch5808",
                                   side_effect=make_not_ready_switch), \
                 mock.patch.object(runner.time, "sleep") as sleep, \
                 redirect_stdout(output):
                rc = runner.prbs_exec(args)
            text = output.getvalue()
            logs = list(Path(log_root).glob("PRBS/*/run.log"))
            self.assertEqual(len(logs), 1)
            log_text = logs[0].read_text(encoding="utf-8")

        self.assertEqual(rc, runner.EXIT_RUN)
        self.assertIn("首轮开流就绪校验失败", text)
        self.assertIn("未开始有效采样", text)
        self.assertNotIn("--- 第 1/2 轮有效采样结果 ---", text)
        self.assertEqual(npu.status_calls, 1)
        self.assertEqual(not_ready_switches[0].get_calls, 1)
        self.assertEqual(not_ready_switches[0].set_calls, [True, False])
        self.assertEqual(sleep.call_count, 0)
        self.assertIn("开流未就绪: NPU0 serdes0 m0 L0: en=0", log_text)


if __name__ == "__main__":
    unittest.main()
