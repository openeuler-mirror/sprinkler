"""SFTP connector for BMC file upload."""

import time
from pathlib import Path, PurePosixPath

from sprinkler.connect.sftp.base import SFTPConnector


class SFTPBMCConnector(SFTPConnector):
    """SFTP connector for BMC. Default upload path /tmp."""

    DEFAULT_UPLOAD_PATH = "/tmp"

    def backend_name(self):
        return "sftp_bmc"

    def upload_package(self, local_path: str, remote_filename: str = None,
                       resume: bool = True, progress_callback=None):
        """Upload package to /tmp/<filename>. Returns bool."""
        if remote_filename is None:
            remote_filename = Path(local_path).name
        return self._upload_to_default_path(local_path, remote_filename,
                                            resume, progress_callback, "Uploading to BMC")

    def upload_package_with_timestamp(self, local_path: str, timestamp: str = None,
                                       resume: bool = True, progress_callback=None):
        """Upload with timestamp suffix (e.g. fw_1730000000.bin). Returns (success, filename, error)."""
        local_file = Path(local_path)
        if not local_file.exists():
            if self.run_log:
                self.run_log.fail(f"本地文件不存在: {local_path}")
            return False, "", f"本地文件不存在: {local_path}"
        if timestamp is None:
            timestamp = str(int(time.time()))
        remote_filename = self._timestamped_filename(local_file.name, timestamp)
        if self.run_log:
            self.run_log.info(f"正在上传到BMC: {local_file.name} -> {remote_filename}")
        success = self._upload_to_default_path(local_path, remote_filename,
                                               resume, progress_callback)
        if success:
            return True, remote_filename, ""
        if self.run_log:
            self.run_log.fail("上传失败")
        return False, "", "上传失败"

    def verify_file(self, remote_filename: str) -> bool:
        """Verify file exists on BMC (size > 0)."""
        try:
            self._safe_remote_filename(remote_filename)
        except ValueError:
            return False
        remote_path = str(PurePosixPath(self.DEFAULT_UPLOAD_PATH) / remote_filename)
        return self.get_file_size(remote_path) > 0

    def list_files(self, remote_dir: str = None) -> list:
        """List files in /tmp (or explicit remote_dir)."""
        if remote_dir is None:
            remote_dir = self.DEFAULT_UPLOAD_PATH
        return super().list_files(remote_dir)

    def delete_file(self, remote_path: str = None, *, remote_filename: str = None) -> bool:
        """Delete file from BMC.

        Two calling conventions (LSP-compatible with base delete_file):
        - Positional: delete_file("/tmp/fw.bin") — full path, same as base.
        - Keyword: delete_file(remote_filename="fw.bin") — combined with /tmp.
        If both given, remote_path wins.
        """
        if remote_path is None and remote_filename is None:
            if self.run_log:
                self.run_log.fail("delete_file需要指定remote_filename或remote_path")
            return False
        if remote_path is None:
            remote_path = str(PurePosixPath(self.DEFAULT_UPLOAD_PATH) / remote_filename)
        return super().delete_file(remote_path)
