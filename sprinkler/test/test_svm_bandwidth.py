"""Unit tests for sprinkler.auto.bandwidth.svm_bandwidth.

Covers the svm_bandwidth(args) flow (L35-189) plus its two helpers
(_is_cross_board and remote_svm_bandwidth) in the same module:

  - local svm_path / cann_path existence checks (early return)
  - non cross-board (local) run: command building, subprocess execution,
    "St pass" counting and summary output
  - cross-board run: target resolution, remote file existence checks
    (fail-fast and all-ok paths), source-side server execution,
    destination-side client dispatch, result collection and summary output

All SSH / subprocess / multiprocessing dependencies are mocked so the tests
run on a dev machine without real NPU / network hardware.
"""

import contextlib
import io
import queue
import types
import unittest
from unittest import mock

from sprinkler.auto.bandwidth.svm_bandwidth import (
    _is_cross_board,
    remote_svm_bandwidth,
    svm_bandwidth,
)

MODULE = "sprinkler.auto.bandwidth.svm_bandwidth"


def make_svm_args(**overrides):
    """Build a SimpleNamespace with every attribute svm_bandwidth accesses."""
    defaults = dict(
        svm_path="/usr/local/bin/st_svm",
        cann_path="/etc/set_env.sh",
        node="",          # empty -> not cross-board
        xlsx=None,
        cycles="10",
        port="52000",
        device_num=2,
        ipc_size="1024",
        incrememt_size="1",
        op_size="8",
        dst_open_num="7",
        pg_type="3",
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class FakeProcess:
    """Replacement for multiprocessing.Process that runs its target inline.

    This keeps the destination-side dispatch fully synchronous so the mocked
    SSH runner fills the result queue deterministically (no real processes).
    Instances record themselves so tests can verify start/join happened.
    """

    instances = []

    def __init__(self, target, args):
        self.target = target
        self.args = args
        self.started = False
        self.joined = False
        FakeProcess.instances.append(self)

    def start(self):
        self.started = True
        self.target(*self.args)

    def join(self):
        self.joined = True


class SvmBandwidthStdoutTest(unittest.TestCase):
    """Shared stdout capture for svm_bandwidth fixtures."""

    def setUp(self):
        self.buffer = io.StringIO()
        self.ctx = contextlib.redirect_stdout(self.buffer)
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)

    def patch_isfile(self, existing):
        return mock.patch(f"{MODULE}.os.path.isfile",
                          side_effect=lambda p: p in existing)


class SvmBandwidthFileCheckTest(SvmBandwidthStdoutTest):
    """Local file existence guards at the top of svm_bandwidth (L38-44)."""

    def test_svm_path_missing_prints_error_and_aborts(self):
        svm_path = "/no/such/svm"
        args = make_svm_args(svm_path=svm_path)
        with self.patch_isfile({}), \
             mock.patch(f"{MODULE}.subprocess.Popen") as popen, \
             mock.patch(f"{MODULE}.TargetContext") as tctx:
            svm_bandwidth(args)
        output = self.buffer.getvalue()
        self.assertIn("svm_path 文件不存在", output)
        self.assertIn(svm_path, output)
        popen.assert_not_called()
        tctx.assert_not_called()

    def test_cann_path_missing_prints_error_and_aborts(self):
        svm_path = "/usr/local/bin/st_svm"
        cann_path = "/no/such/cann"
        args = make_svm_args(svm_path=svm_path, cann_path=cann_path)
        with self.patch_isfile({svm_path}), \
             mock.patch(f"{MODULE}.subprocess.Popen") as popen:
            svm_bandwidth(args)
        output = self.buffer.getvalue()
        self.assertIn("cann source 文件不存在", output)
        self.assertIn(cann_path, output)
        popen.assert_not_called()


class SvmBandwidthLocalModeTest(SvmBandwidthStdoutTest):
    """Non cross-board branch: build command, run subprocess, count pass."""

    def _run_local(self, stdout_lines, **args_overrides):
        proc = mock.MagicMock()
        proc.stdout = list(stdout_lines)
        proc.wait.return_value = 0
        args = make_svm_args(**args_overrides)
        with self.patch_isfile({args.svm_path, args.cann_path}), \
             mock.patch(f"{MODULE}.subprocess.Popen", return_value=proc) as popen, \
             mock.patch(f"{MODULE}.TargetContext") as tctx:
            svm_bandwidth(args)
        return popen, tctx

    def test_local_mode_builds_command_and_runs_subprocess(self):
        stdout_lines = ["head St pass\n", "tail St pass done\n", "clean\n"]
        popen, tctx = self._run_local(
            stdout_lines,
            node="", xlsx=None,
            svm_path="/opt/svm/st_svm", cann_path="/opt/cann/env.sh",
            device_num=2, ipc_size="2048", pg_type="5", dst_open_num="9",
        )
        output = self.buffer.getvalue()

        # subprocess called exactly once with a shell command
        popen.assert_called_once()
        cmd = popen.call_args.args[0]
        self.assertIn("st_svm_p2p_test_009", cmd)
        self.assertIn("/opt/svm/st_svm", cmd)
        self.assertIn("9999 9 0 10", cmd)          # cycles=10 embedded
        self.assertIn("st_svm_p2p_test_009 $i $j 9 2048 5", cmd)

        # not cross-board -> no TargetContext / SSH usage
        tctx.assert_not_called()

        # summary counts 2 occurrences of "St pass" out of 2*1 expected
        self.assertIn("St pass 应该出现次数为: 2 实际出现次数: 2", output)

    def test_local_mode_reports_zero_when_no_st_pass(self):
        self._run_local(["no results here\n", "still nothing\n"], device_num=2)
        output = self.buffer.getvalue()
        self.assertIn("St pass 应该出现次数为: 2 实际出现次数: 0", output)

    def test_local_mode_counts_each_occurrence_per_line(self):
        self._run_local(
            ["a St pass b St pass\n", "c St pass\n"],
            device_num=3,  # expected 3*2=6 occurrences
        )
        output = self.buffer.getvalue()
        self.assertIn("St pass 应该出现次数为: 6 实际出现次数: 3", output)


class SvmBandwidthCrossBoardTest(SvmBandwidthStdoutTest):
    """Cross-board branch: remote checks + source/destination execution."""

    def test_cross_board_remote_missing_file_aborts(self):
        target = types.SimpleNamespace(host="10.0.0.1")
        tctx = mock.MagicMock()
        tctx.get_targets.return_value = [target]
        os_conn = mock.MagicMock()
        os_conn.run_command.return_value = {"stdout": "NOT_EXISTS\n"}

        args = make_svm_args(node="10.0.0.1;root;123456")
        with self.patch_isfile({args.svm_path, args.cann_path}), \
             mock.patch(f"{MODULE}.TargetContext", return_value=tctx), \
             mock.patch(f"{MODULE}.SSHOSConnector", return_value=os_conn), \
             mock.patch(f"{MODULE}.subprocess.Popen") as popen, \
             mock.patch(f"{MODULE}.Process") as proc_cls:
            svm_bandwidth(args)

        output = self.buffer.getvalue()
        tctx.get_targets.assert_called_once_with("cpu_os")
        os_conn.connect_client.assert_called_once_with(target)
        self.assertIn("svm_path 文件不存在", output)
        self.assertIn("10.0.0.1", output)
        self.assertIn("远程文件检查未通过，终止执行", output)
        popen.assert_not_called()      # source side never started
        proc_cls.assert_not_called()   # destination side never started

    def test_cross_board_all_ok_runs_server_and_clients(self):
        target1 = types.SimpleNamespace(host="10.0.0.1")
        target2 = types.SimpleNamespace(host="10.0.0.2")
        tctx = mock.MagicMock()
        tctx.get_targets.return_value = [target1, target2]

        os_conn = mock.MagicMock()
        # 4 remote file checks (EXISTS) + 1 destination run per target
        os_conn.run_command.side_effect = [
            {"stdout": "EXISTS\n"},               # t1 svm_path
            {"stdout": "EXISTS\n"},               # t1 cann_path
            {"stdout": "EXISTS\n"},               # t2 svm_path
            {"stdout": "EXISTS\n"},               # t2 cann_path
            {"stdout": "dst St pass\n"},          # t1 client -> 1 pass
            {"stdout": "dst St pass St pass\n"},  # t2 client -> 2 passes
        ]

        src_proc = mock.MagicMock()
        src_proc.stdout = ["server St pass\n", "server ready\n", "St pass final\n"]
        src_proc.wait.return_value = 0

        FakeProcess.instances.clear()
        args = make_svm_args(node="10.0.0.1;root;123456",
                             svm_path="/s/svm", cann_path="/s/cann",
                             device_num=2, port="6000", ipc_size="2048",
                             cycles="5")
        with self.patch_isfile({args.svm_path, args.cann_path}), \
             mock.patch(f"{MODULE}.TargetContext", return_value=tctx), \
             mock.patch(f"{MODULE}.SSHOSConnector", return_value=os_conn), \
             mock.patch(f"{MODULE}.subprocess.Popen", return_value=src_proc) as popen, \
             mock.patch(f"{MODULE}.Queue", lambda: queue.Queue()), \
             mock.patch(f"{MODULE}.Process", FakeProcess):
            svm_bandwidth(args)

        output = self.buffer.getvalue()

        # file checks all passed -> both file labels reported OK
        self.assertEqual(output.count(" 存在"), 4)

        # source-side server command executed once
        popen.assert_called_once()
        src_cmd = popen.call_args.args[0]
        self.assertIn("st_svm_p2p_cs_test_server", src_cmd)
        self.assertIn("9999 9 0 5", src_cmd)      # cycles=5 embedded

        # destination-side dispatch: one process per target, all started/joined
        self.assertEqual(len(FakeProcess.instances), 2)
        self.assertTrue(all(p.started and p.joined for p in FakeProcess.instances))
        self.assertEqual(
            [inst.args[4] for inst in FakeProcess.instances],  # target_ip arg
            ["10.0.0.1", "10.0.0.2"],
        )

        # summary numbers
        self.assertIn("源侧 St pass 出现次数: 2", output)   # 2 occurrences in src
        self.assertIn("目的侧 St pass 合计: 3", output)     # 1 + 2 from clients


class SvmBandwidthHelpersTest(unittest.TestCase):
    """Direct coverage of _is_cross_board and remote_svm_bandwidth."""

    def test_is_cross_board_true_for_ip_spec(self):
        self.assertTrue(_is_cross_board(make_svm_args(node="10.0.0.1;root;pwd")))

    def test_is_cross_board_true_for_xlsx(self):
        self.assertTrue(_is_cross_board(make_svm_args(node="X", xlsx="plan.xlsx")))

    def test_is_cross_board_false_for_empty_node(self):
        self.assertFalse(_is_cross_board(make_svm_args(node="")))

    def test_is_cross_board_false_for_plain_node_without_xlsx(self):
        self.assertFalse(_is_cross_board(make_svm_args(node="X", xlsx=None)))

    def test_remote_svm_bandwidth_puts_counted_result(self):
        os_conn = mock.MagicMock()
        os_conn.run_command.return_value = {"stdout": "a St pass b St pass c"}
        result_queue = queue.Queue()
        remote_svm_bandwidth(os_conn, mock.MagicMock(), "cmd", result_queue, "10.0.0.1")
        label, count, text = result_queue.get_nowait()
        self.assertEqual(label, "10.0.0.1")
        self.assertEqual(count, 2)

    def test_remote_svm_bandwidth_skips_put_when_queue_none(self):
        os_conn = mock.MagicMock()
        os_conn.run_command.return_value = {"stdout": "St pass"}
        remote_svm_bandwidth(os_conn, mock.MagicMock(), "cmd", None, "10.0.0.1")
        os_conn.run_command.assert_called_once()


if __name__ == "__main__":
    unittest.main()
