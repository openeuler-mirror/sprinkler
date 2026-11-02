"""Redfish 升级通道 —— 通过 Redfish SimpleUpdate + TaskMonitor 执行固件升级。

通过组合 RedfishChannel 复用会话建立能力，
实现 UpgradeChannel 接口提供升级写入能力。

固件分发模式：本地推送。firmware_path 为本地文件路径，
先 SFTP 上传到 BMC /tmp，ImageURI 设为 file:///tmp/<filename>。
"""

import time
from pathlib import Path

from sprinkler.checker.version.query.channels.redfish import RedfishChannel, RedfishReader
from sprinkler.checker.upgrade.channels.base import UpgradeChannel, UpgradeResult
from sprinkler.checker.upgrade.validator import UpgradeValidator


class RedfishUpgradeChannel(UpgradeChannel):
    """Redfish 升级通道。

    组合 RedfishChannel（复用 _build_reader / _new_session），
    实现 UpgradeChannel（upgrade 接口）。
    """

    _reader_factory = None  # 类级默认，便于 @patch.object 在类上定位属性

    def __init__(self, bmc_patterns=(), timeout: int = 30, parallel: int = 1,
                 poll_interval: int = 10, reader_factory=None):
        """:param reader_factory: 可选的可调用对象 ``(target) -> RedfishReader``，
            默认使用 RedfishChannel._build_reader。注入后便于测试 mock。
        """
        self._channel = RedfishChannel(
            bmc_patterns=bmc_patterns, timeout=timeout, parallel=parallel,
        )
        self.poll_interval = poll_interval
        self._validator = UpgradeValidator(timeout=timeout)
        # 仅在显式注入时设置实例属性；否则保留类级 None，由 upgrade() 惰性回退。
        # 这样 @patch.object(Class, "_reader_factory") 能在类级生效不被实例属性遮蔽。
        if reader_factory is not None:
            self._reader_factory = reader_factory

    def upgrade(self, target, firmware_path: str, component: str,
                timeout: int = 1800, **kwargs) -> UpgradeResult:
        """Redfish 固件升级全流程。"""
        start = time.time()
        host = self._extract_host(target)
        result = UpgradeResult(ok=False, host=host, component=component, channel="redfish")

        reader = None
        try:
            factory = self._reader_factory or self._channel._build_reader
            reader = factory(target)
        except (ValueError, RuntimeError) as exc:
            result.error = f"connect: {exc}"
            result.elapsed = time.time() - start
            return result

        try:
            # 1. 升级前版本采集
            result.version_before = self._validator.query_component_version(
                reader, component)

            # 2. 确定 ImageURI
            image_uri = self._resolve_image_uri(firmware_path, target, reader)

            # 3. 检查 UpdateService 可用性
            svc_data, svc_status = reader.get(reader.PATH_UPDATE_SERVICE)
            if svc_status != 200:
                result.error = f"UpdateService unavailable (HTTP {svc_status})"
                result.elapsed = time.time() - start
                return result

            # 4. POST SimpleUpdate
            body = {"ImageURI": image_uri}
            targets = kwargs.get("targets")
            if targets:
                body["Targets"] = targets
            resp_data, post_status, task_uri = reader.post(
                reader.PATH_SIMPLE_UPDATE, body)

            if post_status == 202 and task_uri:
                # 5. 轮询 TaskMonitor
                task_result = self._poll_task(reader, task_uri, timeout, start)
                result.task_state = task_result["state"]
                if task_result["state"] != "Completed":
                    result.error = task_result["error"]
                    result.elapsed = time.time() - start
                    return result
            elif post_status == 200:
                result.task_state = "Completed"
            else:
                result.error = f"SimpleUpdate failed (HTTP {post_status})"
                result.elapsed = time.time() - start
                return result

            # 6. 升级后版本验证（带重试，BMC 版本刷新有延迟）
            result.version_after = self._query_version_with_retry(
                reader, component, retries=3, interval=5)
            result.ok = bool(result.version_after)
            result.elapsed = time.time() - start
            return result

        except Exception as exc:
            result.error = f"upgrade error: {exc}"
            result.elapsed = time.time() - start
            return result
        finally:
            if reader:
                reader.close()

    def _resolve_image_uri(self, firmware_path: str, target, reader) -> str:
        """确定 Redfish ImageURI（本地推送模式）。

        SFTP 上传固件包到 BMC /tmp，ImageURI 设为 file:///tmp/<filename>。
        """
        filename = Path(firmware_path).name
        self._sftp_upload_to_bmc(target, firmware_path)
        return f"file:///tmp/{filename}"

    def _sftp_upload_to_bmc(self, target, local_path: str):
        """通过 SFTPBMCConnector 上传固件包到 BMC /tmp。"""
        from sprinkler.connect.sftp.bmc import SFTPBMCConnector
        from sprinkler.connect.base import ResultProcessor
        from types import SimpleNamespace

        args = SimpleNamespace(
            user=getattr(target, "user", "Administrator"),
            pwd=getattr(target, "password", ""),
        )
        connector = SFTPBMCConnector(
            args=args, result_processor=ResultProcessor(), run_log=None,
        )
        # upload_package 返回 bool
        success = connector.upload_package(local_path)
        if not success:
            raise RuntimeError(f"SFTP upload failed: {local_path}")

    def _query_version_with_retry(self, reader, component: str,
                                  retries: int = 3, interval: float = 5) -> str:
        """查询升级后版本，带重试（BMC 版本刷新有延迟）。

        :param reader: RedfishReader 实例
        :param component: 组件名
        :param retries: 最大重试次数
        :param interval: 重试间隔秒数
        :return: 版本字符串（查询失败返回空字符串）
        """
        for attempt in range(retries):
            time.sleep(interval if attempt > 0 else self.poll_interval)
            version = self._validator.query_component_version(reader, component)
            if version:
                return version
        return ""

    def _poll_task(self, reader: RedfishReader, task_uri: str,
                   total_timeout: int, start: float) -> dict:
        """轮询 Redfish TaskMonitor 直到完成或超时。

        :return: {"state": str, "error": str}
        """
        deadline = start + total_timeout
        while time.time() < deadline:
            data, status = reader.get_task_status(task_uri)
            if status != 200 or data is None:
                time.sleep(self.poll_interval)
                continue
            state = str(data.get("TaskState", ""))
            if state in ("Completed",):
                return {"state": state, "error": ""}
            if state in ("Exception", "Cancelled", "Service"):
                msg = data.get("Messages", [{}])
                err = str(msg[0].get("Message", "")) if msg else state
                return {"state": state, "error": f"task {state}: {err}"}
            # New / Starting / Running / Stopping → 继续轮询
            time.sleep(self.poll_interval)
        return {"state": "Timeout", "error": f"task timeout after {total_timeout}s"}

    def _extract_host(self, target) -> str:
        if hasattr(target, "host"):
            return str(getattr(target, "host", "") or "")
        return str(target.get("host") or target.get("ip") or "")
