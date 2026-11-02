"""
PTA集群测试执行器
统一处理单机和集群模式：单机=集群特例(1节点本地执行)
"""

import os
import re
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from sprinkler.base.log import RunLog
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.ssh.os import SSHOSConnector
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.logging import Colors, output_auto_details

from ..pta_common import (
    OutputParser,
    RankResult,
    VALID_TEST_TYPES,
    check_torchrun,
    detect_npu_device_count,
    get_local_ip,
    get_local_ips,
    parse_progress_from_log,
    resolve_test_script,
    source_cann_env,
    validate_results,
)
from .models import PtaClusterConfig, PtaNodeInfo, PtaNodeResult


class PtaClusterExecutor:
    CHECK_ID = "pta_cluster"

    def __init__(self, config: PtaClusterConfig, nodes: list[PtaNodeInfo], log: RunLog, args):
        self.config = config
        self.nodes = nodes
        self.log = log
        self.args = args
        self.parser = OutputParser()
        self._master_process: Optional[subprocess.Popen] = None
        self._timeout_reached = False
        self._runner: Optional[object] = None

    @property
    def runner(self):
        if self._runner is None:
            self._runner = create_runner(args=self.args, run_log=self.log, connection_type="ssh_os")
        return self._runner

    def execute(self) -> int:
        try:
            check_torchrun()
        except RuntimeError as exc:
            self.log.fail(str(exc))
            output_auto_details("FAIL", self.CHECK_ID, "torchrun未找到")
            return 1

        script_path = self._resolve_script()
        master_addr = self._resolve_master_addr()
        log_dir = Path(self.log.path)
        log_dir.mkdir(parents=True, exist_ok=True)

        self._print_config(master_addr, script_path, log_dir)

        nnodes = len(self.nodes)
        total_proc = sum(n.nproc for n in self.nodes)
        master_node = self.nodes[0]
        nproc = self._detect_local_nproc(master_node)
        master_log = log_dir / "master.log"
        env_path = self._get_env_path()
        env_content = ""
        if env_path and os.path.isfile(env_path):
            env_content = Path(env_path).read_text(encoding="utf-8", errors="replace")

        if nnodes > 1:
            self._print_remote_plan(master_addr, env_path, env_content, script_path, nnodes)
            self._start_remote_workers(script_path, master_addr, nnodes, env_path)
            import time
            time.sleep(3)

        self._print_master_plan(master_addr, env_path, env_content, script_path, nproc, nnodes)
        source_cann_env()
        cmd = [
            sys.executable, "-m", "torch.distributed.run",
            f"--master_addr={master_addr}",
            f"--master_port={self.config.master_port}",
            f"--nnodes={nnodes}",
            "--node_rank=0",
            f"--nproc_per_node={nproc}",
            str(script_path),
        ]

        self.log.info(f"执行命令: {' '.join(cmd)}")
        os.environ["ASCEND_LAUNCH_BLOCKING"] = "1"
        exit_code = self._run_local_process(cmd, master_log, nproc, is_cluster=nnodes > 1)

        if nnodes > 1:
            self._collect_remote_logs(log_dir)

        if exit_code == 0 and nnodes > 1:
            self._display_cluster_results(log_dir, nnodes, total_proc)
            if self.config.test_type in ("allreduce", "alltoall"):
                reason = validate_results(self.config.test_type, master_log, total_proc)
                if reason:
                    self.log.fail(f"验证失败: {reason}")
                    self._cleanup_all()
                    output_auto_details("FAIL", self.CHECK_ID, f"{self.config.test_type} 多机验证失败")
                    return 1
                self.log.ok("验证通过")
            self._cleanup_all()
            output_auto_details("OK", self.CHECK_ID,
                                f"{self.config.test_type} 多机PASS: {nnodes}节点{total_proc}进程")
            return 0

        if exit_code != 0:
            self.log.fail(f"测试失败 (退出码: {exit_code})")
            self._cleanup_all()
            output_auto_details("FAIL", self.CHECK_ID, f"{self.config.test_type} {'多机' if nnodes > 1 else ''}FAIL")
            return 1

        return 0

    def _resolve_script(self) -> Path:
        try:
            return resolve_test_script(self.config.test_type, self.config.script_path)
        except (FileNotFoundError, ValueError) as exc:
            self.log.fail(str(exc))
            output_auto_details("FAIL", self.CHECK_ID, "脚本不存在")
            sys.exit(1)

    def _resolve_master_addr(self) -> str:
        local_ip = get_local_ip()
        if local_ip:
            return local_ip
        return self.nodes[0].target.host

    def _print_config(self, master_addr: str, script_path: Path, log_dir: Path) -> None:
        self.log.info("=" * 38)
        self.log.info("PTA集群测试配置")
        self.log.info("=" * 38)
        self.log.info(f"测试类型:   {self.config.test_type}")
        self.log.info(f"节点数量:   {len(self.nodes)}")
        total_proc = sum(n.nproc for n in self.nodes)
        self.log.info(f"总进程数:   {total_proc}")
        self.log.info(f"主节点:     {master_addr}:{self.config.master_port}")
        self.log.info(f"超时时间:   {self.config.timeout}s")
        self.log.info(f"日志目录:   {log_dir}")
        self.log.info("-" * 38)
        for node in self.nodes:
            tag = " (本机)" if node.is_local else ""
            self.log.info(f"  节点{node.node_rank}: {node.target.name} -> {node.target.host} 进程数{node.nproc}{tag}")
        self.log.info("=" * 38)

    def _print_remote_plan(self, master_addr: str, env_path: Optional[str], env_content: str, script_path: Path, nnodes: int) -> None:
        self.log.info("=" * 38)
        self.log.info("[远程节点]")
        self.log.info("=" * 38)
        for node in self.nodes:
            if node.is_local:
                continue
            self.log.info(f"  {node.target.host}:")
            if env_content:
                self.log.info(f"    ENV: {env_path}")
            else:
                self.log.info(f"    ENV: (无)")
            self.log.info(f"    CMD: torchrun --master_addr {master_addr} --master_port {self.config.master_port} "
                          f"--nnodes {nnodes} --node_rank {node.node_rank} --nproc_per_node {node.nproc} "
                          f"/tmp/{script_path.name}")
        self.log.info("=" * 38)

    def _print_master_plan(self, master_addr: str, env_path: Optional[str], env_content: str, script_path: Path, nproc: int, nnodes: int) -> None:
        self.log.info("=" * 38)
        self.log.info("[主节点]")
        self.log.info("=" * 38)
        self.log.info(f"  {master_addr}:")
        if env_content:
            self.log.info(f"    ENV: {env_path}")
        else:
            self.log.info(f"    ENV: (无)")
        self.log.info(f"    CMD: torchrun --master_addr {master_addr} --master_port {self.config.master_port} "
                      f"--nnodes {nnodes} --node_rank 0 --nproc_per_node {nproc} {script_path}")
        self.log.info("=" * 38)

    def _start_remote_workers(self, script_path: Path, master_addr: str, nnodes: int, env_path: Optional[str]) -> None:
        remote_nodes = [n for n in self.nodes if not n.is_local]
        if not remote_nodes:
            return
        with ThreadPoolExecutor(max_workers=len(remote_nodes)) as pool:
            futures = {}
            for node in remote_nodes:
                future = pool.submit(self._start_remote_worker, node, script_path, master_addr, nnodes, env_path)
                futures[future] = node

            for future in as_completed(futures):
                node = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    self.log.fail(f"启动远程节点 {node.target.name} 失败: {exc}")

    def _start_remote_worker(
        self,
        node: PtaNodeInfo,
        script_path: Path,
        master_addr: str,
        nnodes: int,
        env_path: Optional[str],
    ) -> None:
        self.log.info(f"SSH到 {node.target.name} ({node.target.host}) 启动测试 "
                      f"(node_rank={node.node_rank}, nproc={node.nproc})")

        remote_script = f"/tmp/pta_remote_{node.target.name.replace(':', '_')}.sh"
        remote_test_script = f"/tmp/{script_path.name}"
        env_content = ""
        if env_path and os.path.isfile(env_path):
            env_content = Path(env_path).read_text(encoding="utf-8", errors="replace")

        script_lines = ["#!/bin/bash"]
        if env_content:
            script_lines.append(f"# ENV: {env_path}")
            script_lines.append(env_content)
        script_lines.append("export ASCEND_LAUNCH_BLOCKING=1")
        script_lines.append(
            f"torchrun --master_addr {master_addr} --master_port {self.config.master_port} "
            f"--nnodes {nnodes} --node_rank {node.node_rank} --nproc_per_node {node.nproc} {remote_test_script}"
        )

        script_full = "\n".join(script_lines) + "\n"
        self._sftp_put(node.target, script_path, remote_test_script)
        local_tmp = Path(f"/tmp/pta_remote_{node.target.name.replace(':', '_')}.sh")
        local_tmp.write_text(script_full, encoding="utf-8")
        self._sftp_put(node.target, local_tmp, remote_script)
        remote_log = f"/tmp/pta_cluster_{node.target.name.replace(':', '_')}.log"
        start_cmd = f"nohup bash -lc 'source {remote_script}' > {remote_log} 2>&1 &"
        try:
            results = self.runner.run(node.target, [start_cmd])
            if results:
                for r in results:
                    if r.get("ok"):
                        pass
                    else:
                        error_reason = r.get("stderr") or r.get("stdout") or r.get("error_type") or "unknown"
                        self.log.fail(f"  [{node.target.host}] SSH命令失败: {error_reason}")
        except Exception as e:
            self.log.fail(f"  [{node.target.host}] SSH执行异常: {e}")

    def _sftp_put(self, target: ConnectionTarget, local_path: Path, remote_path: str) -> None:
        try:
            connector = self.runner.connector
            if isinstance(connector, SSHOSConnector):
                client = connector.connect_client(target)
                try:
                    sftp = client.open_sftp()
                    try:
                        sftp.put(str(local_path), remote_path)
                    finally:
                        sftp.close()
                finally:
                    client.close()
                return
        except Exception as e:
            self.log.fail(f"  [SFTP] {target.host}:{remote_path} 失败: {e}")
            return

        scp_cmd = (
            f"scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
            f"-o ConnectTimeout=8 -o LogLevel=ERROR "
            f"{local_path} {target.user}@{target.host}:{remote_path}"
        )
        result = subprocess.run(scp_cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            self.log.fail(f"  [SCP] {target.host}:{remote_path} 失败: {result.stderr}")

    def _collect_remote_logs(self, log_dir: Path) -> None:
        remote_nodes = [n for n in self.nodes if not n.is_local]
        if not remote_nodes:
            return

        self.log.info("=" * 38)
        self.log.info("收集远程节点日志")
        self.log.info("=" * 38)

        for node in remote_nodes:
            remote_log = f"/tmp/pta_cluster_{node.target.name.replace(':', '_')}.log"
            local_log = log_dir / f"{node.target.name.replace(':', '_')}.log"
            self.log.info(f"  [{node.target.host}] 检查进程状态...")
            try:
                results = self.runner.run(node.target, [
                    f"ps aux | grep -E 'torchrun|python.*test_' | grep -v grep || echo '无进程'",
                    f"cat {remote_log} 2>/dev/null || echo '无日志文件'"
                ])
                if results:
                    for r in results:
                        if r.get("ok"):
                            stdout = r.get("stdout", "").strip()
                            if stdout:
                                self.log.info(f"  [{node.target.host}] {stdout[:200]}")
            except Exception as e:
                self.log.fail(f"  [{node.target.host}] 获取状态失败: {e}")

            try:
                results = self.runner.run(node.target, [f"cat {remote_log} 2>/dev/null"])
                if results and results[0].get("ok"):
                    content = results[0].get("stdout", "")
                    local_log.write_text(content, encoding="utf-8")
                    self.log.info(f"  [{node.target.host}] 日志已保存到 {local_log}")
                    if content:
                        lines = content.strip().splitlines()
                        self.log.info(f"  [{node.target.host}] 日志尾部的10行:")
                        for line in lines[-10:]:
                            self.log.info(f"    {line}")
            except Exception as e:
                self.log.fail(f"  [{node.target.host}] 获取日志失败: {e}")

    def _run_local_process(self, cmd: list[str], log_file: Path, nproc: int, is_cluster: bool = False) -> int:
        self._master_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        timer = threading.Timer(self.config.timeout, self._on_timeout)
        timer.daemon = True
        timer.start()

        result = RankResult()
        try:
            with log_file.open("w", encoding="utf-8", errors="replace") as fp:
                for line in self._master_process.stdout:
                    fp.write(line)
                    fp.flush()
                    summary = self.parser.parse_line(line, self.config.test_type, result)
                    if summary:
                        self.log.info(summary)
        except KeyboardInterrupt:
            self._on_timeout()
        finally:
            timer.cancel()

        exit_code = self._master_process.wait()
        self._cleanup_master()

        if self._timeout_reached:
            self.log.fail(f"测试超时 ({self.config.timeout}s)")
            output_auto_details("FAIL", self.CHECK_ID, f"{self.config.test_type} 超时")
            return 124

        if exit_code == 0 and not is_cluster:
            expected = nproc
            reason = validate_results(self.config.test_type, log_file, expected)
            if reason:
                self.log.fail(f"验证失败: {reason}")
            else:
                self.log.ok("验证通过")
            output_auto_details("RESULT", self.CHECK_ID, f"{self.config.test_type} PASS: {nproc}")

        return exit_code

    def _display_cluster_results(self, log_dir: Path, node_count: int, total_proc: int) -> None:
        node_logs: dict[str, Path] = {}
        for node in self.nodes:
            if node.is_local:
                node_logs[node.target.name] = log_dir / "master.log"
            else:
                node_logs[node.target.name] = log_dir / f"{node.target.name.replace(':', '_')}.log"

        self.log.info("=" * 38)
        self.log.info("集群测试结果")

        if self.config.test_type == "allreduce":
            self._display_allreduce_results(node_logs, total_proc)
        elif self.config.test_type == "alltoall":
            self._display_alltoall_results(node_logs, node_count, total_proc)
        elif self.config.test_type == "gpt":
            self._display_gpt_results(node_logs)

        self.log.ok(f"通过: {total_proc} 个进程 ({node_count}节点)")

    def _display_allreduce_results(self, node_logs: dict[str, Path], target_value: int) -> None:
        for node_name, log_path in node_logs.items():
            if not log_path.exists():
                self.log.info(f"  {node_name}: 无日志")
                continue

            content = log_path.read_text(encoding="utf-8", errors="replace")
            values = re.findall(r"tensor\(\[([0-9.]+)", content)
            if not values:
                self.log.info(f"  {node_name}: 未检测到结果")
                continue

            match_count = sum(1 for v in values if int(float(v)) == target_value)
            status = "达标" if match_count > len(values) // 2 else "未达标"
            self.log.info(f"  {node_name}: HCCL值={values[0]} {status}")

    def _display_alltoall_results(self, node_logs: dict[str, Path], node_count: int, total_proc: int) -> None:
        for node_name, log_path in node_logs.items():
            if not log_path.exists():
                self.log.info(f"  {node_name}: 无日志")
                continue

            content = log_path.read_text(encoding="utf-8", errors="replace")
            tensor_lines = re.findall(r"tensor\(\[([0-9.,\s]+)\]", content)
            if not tensor_lines:
                self.log.info(f"  {node_name}: 未检测到tensor")
                continue

            correct = sum(
                1 for vals in tensor_lines
                if len([v.strip() for v in vals.split(",") if v.strip()]) == total_proc
            )
            status = "达标" if correct == len(tensor_lines) else "未达标"
            self.log.info(f"  {node_name}: {correct}/{len(tensor_lines)} 个tensor正确 {status}")

    def _display_gpt_results(self, node_logs: dict[str, Path]) -> None:
        for node_name, log_path in node_logs.items():
            if not log_path.exists():
                self.log.info(f"  {node_name}: 无日志")
                continue

            content = log_path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                if re.search(r"Device:\s*\d+,", line):
                    self.log.info(f"  {node_name}: {line.strip()}")

    def _detect_local_nproc(self, node: PtaNodeInfo) -> int:
        if self.config.nproc_per_node and self.config.nproc_per_node > 0:
            return self.config.nproc_per_node
        device_count = detect_npu_device_count()
        if device_count > 0:
            return device_count
        return node.nproc

    def _get_env_path(self) -> Optional[str]:
        from sprinkler.config.config_manager import PtaConfigManager
        config_manager = PtaConfigManager()
        return config_manager.get_env_path()

    def _on_timeout(self) -> None:
        self._timeout_reached = True
        if self._master_process and self._master_process.poll() is None:
            try:
                self._master_process.kill()
            except Exception:
                pass

    def _cleanup_master(self) -> None:
        if self._master_process and self._master_process.poll() is None:
            try:
                self._master_process.kill()
            except Exception:
                pass

    def _cleanup_all(self) -> None:
        for node in self.nodes:
            if node.is_local:
                try:
                    subprocess.run(
                        ["pkill", "-f", f"torchrun.*test_{self.config.test_type}"],
                        capture_output=True, check=False,
                    )
                except Exception:
                    pass
            else:
                try:
                    self.runner.run(node.target, [
                        f"pkill -f 'torchrun.*test_{self.config.test_type}' 2>/dev/null; "
                        f"pkill -f 'python.*test_{self.config.test_type}' 2>/dev/null"
                    ])
                except Exception:
                    pass
