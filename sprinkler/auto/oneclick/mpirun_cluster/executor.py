"""执行器模块"""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from sprinkler.base.log import RunLog
from sprinkler.connect.connection_runner import create_runner
from sprinkler.connect.targets import ConnectionTarget
from sprinkler.logging import Colors

from .models import ClusterConfig, CabinetTestResult
from .builder import MpirunCommandBuilder, BandwidthOutputParser


class BaseExecutor:
    """执行器基类：提供单OS执行和结果打印"""
    
    def __init__(
        self,
        config: ClusterConfig,
        hosts: list[ConnectionTarget],
        log: RunLog,
        args,
        hccl_test: str = "aicpu_ts",
        interface_names: Optional[list[str]] = None,
        hccl_buffsize: int = 1024,
    ):
        self.config = config
        self.hosts = hosts
        self.log = log
        self.args = args
        self.cmd_builder = MpirunCommandBuilder(config, hccl_test, interface_names, hccl_buffsize)
        self.bw_parser = BandwidthOutputParser()
    
    def execute_on_single_os(
        self,
        target: ConnectionTarget,
        command: str,
        log_file: Optional[Path] = None,
    ) -> tuple[bool, Optional[float], str]:
        """
        在单个OS上执行命令
        参数:
            target: 目标主机
            command: 执行命令
            log_file: 日志文件路径
        返回:
            (是否成功, 带宽值, 输出内容)元组
        """
        try:
            runner = create_runner(args=self.args, run_log=None, connection_type="ssh_os")
            results = runner.run(target, [command])
            
            if not results:
                if log_file:
                    self._write_log(log_file, target, command, "", "", -1, "无返回结果")
                return False, None, ""
            
            result = results[0]
            output = result.get("stdout", "") + result.get("stderr", "")
            bandwidth = self.bw_parser.parse(output)
            
            if log_file:
                self._write_log(
                    log_file,
                    target,
                    command,
                    result.get("stdout", ""),
                    result.get("stderr", ""),
                    result.get("returncode", -1),
                )
            
            return result.get("ok", False), bandwidth, output
            
        except Exception as exc:
            if log_file:
                self._write_log(log_file, target, command, "", "", -1, str(exc))
            self.log.info(f"执行异常: {exc}")
            return False, None, ""
    
    def _write_log(
        self,
        log_file: Path,
        target: ConnectionTarget,
        command: str,
        stdout: str,
        stderr: str,
        returncode: int = -1,
        error: str = "",
    ):
        """写入日志文件"""
        with log_file.open("w", encoding="utf-8") as fp:
            fp.write(f"=== 主控主机: {target.name} ({target.host}) ===\n")
            fp.write(f"=== 执行命令 ===\n{command}\n\n")
            if stdout:
                fp.write(f"=== 标准输出 ===\n{stdout}\n\n")
            if stderr:
                fp.write(f"=== 标准错误 ===\n{stderr}\n\n")
            if error:
                fp.write(f"=== 异常 ===\n{error}\n\n")
            fp.write(f"=== 返回码: {returncode} ===\n")
    
    def build_command(self, *args, **kwargs) -> str:
        """构建命令（由子类实现）"""
        raise NotImplementedError
    
    def print_summary(self, results: dict, test_type: str, mode: str = ""):
        """打印结果汇总"""
        self.log.info("")
        self.log.info("=" * 40)
        self.log.info("结果汇总")
        self.log.info(f"打流算子: {test_type}")
        if mode:
            self.log.info(f"执行模式: {mode}")
        self.log.info("")
        
        for name, bandwidth in results.items():
            if bandwidth is not None:
                self.log.info(f"{Colors.High_GREEN}{name}: {bandwidth:.2f} GB/s{Colors.RESET}")
            else:
                self.log.info(f"{name}: 未获取到带宽数据")


class SingleModeExecutor(BaseExecutor):
    """单机模式执行器：在每台主机上独立执行"""
    
    def execute(self, mpi_n: int, test_type: str) -> bool:
        """执行单机模式测试"""
        
        self.log.info("=" * 40)
        self.log.info("单机模式: 并发在每台主机上执行mpirun")
        
        log_dir = Path(self.log.path)
        results = self._execute_parallel(mpi_n, test_type, log_dir)
        
        results_dict = {r["name"]: r["bandwidth"] for r in results}
        self.print_summary(results_dict, test_type)
        
        for result in results:
            if result["log_file"]:
                self.log.info(f"  详细日志: {result['log_file']}")
        
        return all(r["success"] for r in results)
    
    def _execute_parallel(self, mpi_n: int, test_type: str, log_dir: Path) -> list[dict]:
        """并发执行各主机测试"""
        results: list[dict] = []
        
        with ThreadPoolExecutor(max_workers=len(self.hosts)) as executor:
            futures = {}
            
            for target in self.hosts:
                future = executor.submit(
                    self._execute_single_host,
                    target,
                    mpi_n,
                    test_type,
                    log_dir,
                )
                futures[future] = target.name
            
            for future in as_completed(futures):
                target_name = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    self.log.fail(f"主机 {target_name} 执行异常: {exc}")
                    results.append({
                        "name": target_name,
                        "success": False,
                        "bandwidth": None,
                        "log_file": log_dir / f"{target_name.replace(':', '_')}_error.log",
                    })
        
        return results
    
    def _execute_single_host(
        self,
        target: ConnectionTarget,
        mpi_n: int,
        test_type: str,
        log_dir: Path,
    ) -> dict:
        """执行单个主机的测试"""
        command = self.build_command(target, mpi_n, test_type)
        log_file = log_dir / f"{target.name.replace(':', '_')}.log"
        
        success, bandwidth, _ = self.execute_on_single_os(target, command, log_file)
        
        return {
            "name": target.name,
            "success": success,
            "bandwidth": bandwidth,
            "log_file": log_file,
        }
    
    def build_command(self, target: ConnectionTarget, mpi_n: int, test_type: str) -> str:
        """构建单机命令"""
        hosts_spec = f"{target.host}:{mpi_n}"
        mpirun_cmd = self.cmd_builder.build_mpirun_command(hosts_spec, mpi_n, test_type)
        env_script = self.cmd_builder.build_env_setup_script()
        return f"{env_script}\n{mpirun_cmd}"


class ClusterModeExecutor(BaseExecutor):
    """集群模式执行器：选出一个主控主机执行"""
    
    def __init__(
        self,
        config: ClusterConfig,
        hosts: list[ConnectionTarget],
        log: RunLog,
        args,
        hccl_test: str = "aicpu_ts",
        master_host: Optional[str] = None,
        interface_names: Optional[list[str]] = None,
        hccl_buffsize: int = 1024,
    ):
        super().__init__(config, hosts, log, args, hccl_test, interface_names, hccl_buffsize)
        self.master_host = master_host
    
    def execute(self, mpi_n: int, test_type: str, log_dir: Path) -> bool:
        """执行集群模式测试"""
        if not self.hosts:
            self.log.fail("没有选择任何主机")
            return False
        
        primary_target = self.hosts[0]
        
        self.log.info("=" * 40)
        
        if self.master_host:
            self.log.info("集群模式: SSH到master节点执行mpirun")
            self.log.info(f"Master节点: {self.master_host}")
            master_target = ConnectionTarget(
                name=f"master_{self.master_host}",
                host=self.master_host,
                port=22,
                user="root",
                password="",
            )
            use_master = True
        else:
            self.log.info("集群模式: 在主控主机上执行mpirun")
            self.log.info(f"主控主机: {primary_target.name} ({primary_target.host})")
            master_target = None
            use_master = False
        
        hosts_str = self._format_hosts_info()
        self.log.info(f"参与主机: {hosts_str}")
        
        command, mpirun_cmd = self.build_command(mpi_n, test_type, log_dir)
        self.log.info(f"执行命令: {mpirun_cmd}")
        
        if use_master and master_target:
            success, bandwidth = self._execute_cluster_test_remote(master_target, command, log_dir)
        else:
            success, bandwidth = self._execute_cluster_test(primary_target, command, log_dir)
        
        self._print_result(success, bandwidth, test_type, log_dir)
        return success
    
    def build_command(self, mpi_n: int, test_type: str, log_dir: Path) -> tuple[str, str]:
        """构建集群命令
        返回: (完整命令, mpirun命令)
        """
        hosts_spec = ",".join(f"{t.host}:{mpi_n}" for t in self.hosts)
        
        mpirun_cmd = self.cmd_builder.build_mpirun_command(hosts_spec, mpi_n, test_type)
        env_script = self.cmd_builder.build_env_setup_script()
        return f"{env_script}\n{mpirun_cmd}", mpirun_cmd
    
    def _format_hosts_info(self) -> str:
        """格式化主机信息"""
        sp_groups: dict[str, dict[str, list[str]]] = {}
        for t in self.hosts:
            parts = t.name.split(":")
            if len(parts) >= 3:
                sp_name, cab_name, node_name = parts[0], parts[1], parts[2]
            else:
                sp_name, cab_name, node_name = "default", "default", t.name
            
            if sp_name not in sp_groups:
                sp_groups[sp_name] = {}
            if cab_name not in sp_groups[sp_name]:
                sp_groups[sp_name][cab_name] = []
            sp_groups[sp_name][cab_name].append(node_name)
        
        parts_list = []
        for sp_name, cabinets in sp_groups.items():
            cab_parts = [f"{cab}:{','.join(nodes)}" for cab, nodes in cabinets.items()]
            cab_str = ";".join(cab_parts)
            parts_list.append(f"{sp_name}{{{cab_str}}}")
        
        return "  ".join(parts_list)
    
    def _execute_cluster_test(
        self,
        primary_target: ConnectionTarget,
        command: str,
        log_dir: Path,
    ) -> tuple[bool, Optional[float]]:
        """执行集群测试（本地执行）"""
        env = os.environ.copy()
        env["CANN_PATH"] = self.config.cann_path
        env["MPICH_PATH"] = self.config.mpich_path
        
        mpirun_log_path = log_dir / "mpirun_output.log"
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
                env=env,
            )
            
            output = result.stdout + result.stderr
            bandwidth = self.bw_parser.parse(output)
            
            self._write_cluster_log(mpirun_log_path, primary_target, command, result.stdout, result.stderr, result.returncode)
            
            return result.returncode == 0 and bandwidth is not None, bandwidth
            
        except subprocess.TimeoutExpired:
            self._write_cluster_log(mpirun_log_path, primary_target, command, "", "", -1, f"执行超时 ({self.config.timeout_seconds}秒)")
            self.log.info(f"测试执行超时 ({self.config.timeout_seconds}秒)")
            return False, None
        except Exception as exc:
            self._write_cluster_log(mpirun_log_path, primary_target, command, "", "", -1, str(exc))
            self.log.info(f"测试执行异常: {exc}")
            return False, None
    
    def _execute_cluster_test_remote(
        self,
        master_target: ConnectionTarget,
        command: str,
        log_dir: Path,
    ) -> tuple[bool, Optional[float]]:
        """执行集群测试（远程master节点执行）"""
        mpirun_log_path = log_dir / "mpirun_output.log"
        
        success, bandwidth, output = self.execute_on_single_os(master_target, command, mpirun_log_path)
        
        return success, bandwidth
    
    def _write_cluster_log(self, log_file: Path, target: ConnectionTarget, command: str, stdout: str, stderr: str, returncode: int, error: str = ""):
        """写入集群测试日志"""
        with log_file.open("w", encoding="utf-8") as fp:
            fp.write(f"=== 主控主机: {target.name} ({target.host}) ===\n")
            fp.write(f"=== 环境变量 ===\n")
            fp.write(f"CANN_PATH={self.config.cann_path}\n")
            fp.write(f"MPICH_PATH={self.config.mpich_path}\n\n")
            fp.write(f"=== 执行命令 ===\n{command}\n\n")
            if stdout:
                fp.write(f"=== 标准输出 ===\n{stdout}\n\n")
            if stderr:
                fp.write(f"=== 标准错误 ===\n{stderr}\n\n")
            if error:
                fp.write(f"=== {error} ===\n")
            fp.write(f"=== 返回码: {returncode} ===\n")
    
    def _print_result(self, success: bool, bandwidth: Optional[float], test_type: str, log_dir: Path):
        """打印测试结果"""
        self.log.info("")
        self.log.info("=" * 40)
        self.log.info("结果汇总")
        self.log.info(f"打流算子: {test_type}")
        self.log.info("执行模式: 集群模式")
        self.log.info("")
        
        mpirun_log_path = log_dir / "mpirun_output.log"
        self.log.info(f"详细日志路径: {mpirun_log_path}")
        self.log.info("")
        
        if bandwidth is not None:
            self.log.info(f"{Colors.High_GREEN}1G数据量带宽值: {bandwidth:.2f} GB/s{Colors.RESET}")
        else:
            self.log.info("未解析到带宽数据，请查看详细日志定位问题")


class ParallelCabinetExecutor(BaseExecutor):
    """并发柜子模式执行器：按柜子分组并发执行"""
    
    def execute(self, mpi_n: int, test_type: str, log_dir: Path) -> bool:
        """执行并发柜子模式测试"""
        cabinet_groups = self._group_hosts_by_cabinet()
        
        if not cabinet_groups:
            self.log.fail("没有可用的柜子")
            return False
        
        self.log.info("=" * 40)
        self.log.info("并发柜子模式: 每个柜子独立执行mpirun")
        self.log.info(f"柜子数量: {len(cabinet_groups)}")
        
        results = self._execute_parallel(cabinet_groups, mpi_n, test_type, log_dir)
        
        results_dict = {r.cabinet_name: r.bandwidth for r in results}
        self.print_summary(results_dict, test_type, "并发柜子模式")
        
        for result in results:
            self.log.info(f"  详细日志: {result.log_file}")
        
        return all(r.success for r in results)
    
    def _group_hosts_by_cabinet(self) -> dict[str, list[ConnectionTarget]]:
        """按柜子分组主机"""
        groups: dict[str, list[ConnectionTarget]] = {}
        
        for target in self.hosts:
            parts = target.name.split(":")
            cabinet_name = ":".join(parts[:2]) if len(parts) >= 2 else "default"
            
            if cabinet_name not in groups:
                groups[cabinet_name] = []
            groups[cabinet_name].append(target)
        
        return groups
    
    def _execute_parallel(
        self,
        cabinet_groups: dict[str, list[ConnectionTarget]],
        mpi_n: int,
        test_type: str,
        log_dir: Path,
    ) -> list[CabinetTestResult]:
        """并发执行各柜子测试"""
        results: list[CabinetTestResult] = []
        
        with ThreadPoolExecutor(max_workers=len(cabinet_groups)) as executor:
            futures = {}
            
            for cabinet_name, cabinet_targets in cabinet_groups.items():
                future = executor.submit(
                    self._execute_single_cabinet,
                    cabinet_name,
                    cabinet_targets,
                    mpi_n,
                    test_type,
                    log_dir,
                )
                futures[future] = cabinet_name
            
            for future in as_completed(futures):
                cabinet_name = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as exc:
                    self.log.fail(f"柜子 {cabinet_name} 执行异常: {exc}")
                    results.append(CabinetTestResult(
                        cabinet_name=cabinet_name,
                        success=False,
                        bandwidth=None,
                        log_file=log_dir / f"{cabinet_name}_error.log",
                    ))
        
        return results
    
    def _execute_single_cabinet(
        self,
        cabinet_name: str,
        cabinet_targets: list[ConnectionTarget],
        mpi_n: int,
        test_type: str,
        log_dir: Path,
    ) -> CabinetTestResult:
        """执行单个柜子的测试"""
        if not cabinet_targets:
            return CabinetTestResult(
                cabinet_name=cabinet_name,
                success=False,
                bandwidth=None,
                log_file=log_dir / f"{cabinet_name}.log",
            )
        
        primary_target = cabinet_targets[0]
        log_file = log_dir / f"{cabinet_name}.log"
        
        command = self.build_command(cabinet_name, cabinet_targets, mpi_n, test_type)
        
        success, bandwidth, _ = self.execute_on_single_os(primary_target, command, log_file)
        
        return CabinetTestResult(
            cabinet_name=cabinet_name,
            success=success,
            bandwidth=bandwidth,
            log_file=log_file,
        )
    
    def build_command(self, cabinet_name: str, cabinet_targets: list[ConnectionTarget], mpi_n: int, test_type: str) -> str:
        """构建柜子命令"""
        hosts_spec = ",".join(f"{t.host}:{mpi_n}" for t in cabinet_targets)
        mpirun_cmd = self.cmd_builder.build_mpirun_command(hosts_spec, mpi_n, test_type)
        env_script = self.cmd_builder.build_env_setup_script()
        return f"{env_script}\n{mpirun_cmd}"

