"""升级编排引擎 —— 按工作流定义对多目标并行执行多步骤升级。

复用 ThreadPoolExecutor 的并行框架（与 ConnectionRunner 同模式），
但将"命令批量执行"升级为"多步骤编排"：每个目标独立执行完整升级流程，
步骤间有状态传递和持久化。

与 ConnectionRunner 的区别：
- ConnectionRunner：单步（命令批量执行），无状态
- UpgradeOrchestrator：多步（上传→触发→轮询→验证），有状态（持久化到 DB）
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from sprinkler.base.log import RunLog
from sprinkler.db import dao
from sprinkler.db.models import utcnow
from sprinkler.checker.upgrade.channels.base import UpgradeResult
from sprinkler.checker.upgrade.channels.selector import select_channel, supports_fallback


class UpgradeOrchestrator:
    """升级编排引擎。

    职责：
    1. 对每个目标创建 upgrade_record（持久化到 DB）
    2. 按通道选择器选择升级通道
    3. 并行执行升级（ThreadPoolExecutor）
    4. Redfish 失败时自动降级到 SSH CLI（如支持）
    5. 更新 upgrade_record 和 upgrade_task 状态

    线程安全：所有 DAO 调用通过 ``_db_lock`` 串行化，避免多线程并发操作
    同一个 ``sqlite3.Connection`` 导致 "bad parameter or other API misuse"。
    并行性体现在通道执行的网络 I/O 上（锁不覆盖 ``_execute_channel``）。
    """

    def __init__(self, conn, task_id: int, run_log: Optional[RunLog] = None,
                 parallel: int = 8, poll_interval: int = 10):
        """
        :param conn: sqlite3.Connection（调用方管理生命周期）
        :param task_id: upgrade_task.id
        :param run_log: 日志对象
        :param parallel: 并行度
        :param poll_interval: Redfish TaskMonitor 轮询间隔
        """
        self.conn = conn
        self.task_id = task_id
        self.run_log = run_log
        self.parallel = parallel
        self.poll_interval = poll_interval
        self._db_lock = threading.Lock()

    def run(self, targets, packages, scenario: str, strategy: str = "auto",
            timeout: int = 1800) -> list[UpgradeResult]:
        """并行执行升级。

        :param targets: ConnectionTarget 列表
        :param packages: dict{component: firmware_path} 映射
                         （component → 固件包本地文件路径）
        :param scenario: 场景（initial/daily/batch）
        :param strategy: 策略（auto/redfish/ssh_cli/sol）
        :param timeout: 每目标升级超时秒数
        :return: UpgradeResult 列表（每目标每组件一个）

        批量场景（batch）启用 cabinet 分批调度：同 cabinet 串行，
        不同 cabinet 并行。其他场景全并行。
        """
        # 更新任务状态为 running
        total = len(targets) * len(packages)
        with self._db_lock:
            dao.update_upgrade_task(self.conn, self.task_id,
                                    status="running", total=total)

        results = []

        if scenario == "batch" and len(targets) > 1:
            results = self._run_batched(targets, packages, scenario,
                                        strategy, timeout)
        else:
            results = self._run_parallel(targets, packages, scenario,
                                         strategy, timeout)

        # 更新任务统计
        succeeded = sum(1 for r in results if r.ok)
        failed = sum(1 for r in results if not r.ok)
        status = "completed" if failed == 0 else ("partial" if succeeded > 0 else "failed")
        with self._db_lock:
            dao.update_upgrade_task(
                self.conn, self.task_id,
                status=status, succeeded=succeeded, failed=failed,
                finished_at=utcnow(),
            )
        return results

    def _run_parallel(self, targets, packages, scenario, strategy, timeout):
        """全并行执行（initial/daily 场景）。"""
        results = []
        max_workers = min(self.parallel, len(targets)) if targets else 1

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = []
            for target in targets:
                for component, firmware_path in packages.items():
                    future = pool.submit(
                        self._upgrade_one, target, component,
                        firmware_path, scenario, strategy, timeout,
                    )
                    futures.append(future)

            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    result = UpgradeResult(ok=False, error=str(exc), channel="error")
                results.append(result)
                self._log_result(result)
        return results

    def _run_batched(self, targets, packages, scenario, strategy, timeout):
        """按 cabinet 分批执行（batch 场景）。

        同 cabinet 的 blade 串行（升级 + 复位需逐台完成），
        不同 cabinet 间并行（受 parallel 限制）。
        """
        from sprinkler.checker.upgrade.scheduler import schedule_by_cabinet

        # 将 targets 转为 blade dict 格式供 scheduler 分组
        blade_dicts = []
        for t in targets:
            meta = getattr(t, "meta", {}) or {}
            blade_dicts.append({
                "cabinet_id": meta.get("cabinet_id"),
                "_target": t,
            })

        batches = schedule_by_cabinet(blade_dicts, parallel=self.parallel)

        results = []
        for batch in batches:
            batch_targets = [b["_target"] for b in batch]
            max_workers = min(self.parallel, len(batch_targets)) if batch_targets else 1

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = []
                for target in batch_targets:
                    for component, firmware_path in packages.items():
                        future = pool.submit(
                            self._upgrade_one, target, component,
                            firmware_path, scenario, strategy, timeout,
                        )
                        futures.append(future)

                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = UpgradeResult(ok=False, error=str(exc), channel="error")
                    results.append(result)
                    self._log_result(result)
        return results

    def _upgrade_one(self, target, component: str, firmware_path: str,
                     scenario: str, strategy: str, timeout: int) -> UpgradeResult:
        """对单个目标单个组件执行升级（含降级逻辑）。"""
        host = getattr(target, "host", "") or str(target.get("host", ""))
        device_type = (getattr(target, "meta", {}) or {}).get("device_type", "")
        blade_id = (getattr(target, "meta", {}) or {}).get("blade_id")

        channel_name = select_channel(component, scenario, strategy)

        # 创建 upgrade_record（DB 操作加锁）
        with self._db_lock:
            record_id = dao.create_upgrade_record(
                self.conn, self.task_id, blade_id, host,
                device_type, component, package_id=None, channel=channel_name,
            )

        # 执行升级（网络 I/O，不加锁）
        result = self._execute_channel(
            channel_name, target, firmware_path, component, timeout,
        )

        # 降级逻辑：Redfish 失败 → SSH CLI
        if (not result.ok and channel_name == "redfish"
                and supports_fallback(component, scenario)):
            if self.run_log:
                self.run_log.info(
                    f"{host} {component} Redfish failed ({result.error}), "
                    f"falling back to ssh_cli"
                )
            with self._db_lock:
                dao.update_upgrade_record(
                    self.conn, record_id,
                    status="failed", error_msg=f"redfish fallback: {result.error}",
                )
                # 创建降级 record
                record_id = dao.create_upgrade_record(
                    self.conn, self.task_id, blade_id, host,
                    device_type, component, package_id=None, channel="ssh_cli",
                )

            # 降级执行（网络 I/O，不加锁）
            result = self._execute_channel(
                "ssh_cli", target, firmware_path, component, timeout,
            )

        # 更新 record（DB 操作加锁）
        with self._db_lock:
            dao.update_upgrade_record(
                self.conn, record_id,
                status="success" if result.ok else "failed",
                version_before=result.version_before,
                version_after=result.version_after,
                error_msg=result.error,
                elapsed=result.elapsed,
            )
        result.host = host
        result.component = component
        return result

    def _execute_channel(self, channel_name: str, target, firmware_path: str,
                         component: str, timeout: int) -> UpgradeResult:
        """实例化并执行指定通道的升级。"""
        if channel_name == "redfish":
            from sprinkler.checker.upgrade.channels.redfish_upgrade import RedfishUpgradeChannel
            channel = RedfishUpgradeChannel(
                timeout=30, poll_interval=self.poll_interval,
            )
        elif channel_name == "ssh_cli":
            from sprinkler.checker.upgrade.channels.ssh_cli_upgrade import SshCliUpgradeChannel
            channel = SshCliUpgradeChannel(timeout=timeout)
        elif channel_name == "sol":
            from sprinkler.checker.upgrade.channels.sol_upgrade import SolUpgradeChannel
            channel = SolUpgradeChannel(timeout=timeout)
        else:
            return UpgradeResult(ok=False, error=f"unknown channel: {channel_name}",
                                 channel=channel_name)

        return channel.upgrade(target, firmware_path, component, timeout=timeout)

    def _log_result(self, result: UpgradeResult):
        if self.run_log is None:
            return
        if result.ok:
            self.run_log.ok(
                f"[{result.host}] {result.component} upgrade success "
                f"({result.version_before} → {result.version_after}) "
                f"via {result.channel} in {result.elapsed:.1f}s"
            )
        else:
            self.run_log.fail(
                f"[{result.host}] {result.component} upgrade failed "
                f"via {result.channel}: {result.error}"
            )
