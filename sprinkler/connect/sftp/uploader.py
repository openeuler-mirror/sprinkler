"""SFTP uploader with retry and directory batch support."""

from pathlib import Path, PurePosixPath
from typing import Optional, Callable

from sprinkler.connect.sftp.base import SFTPConnector


class SFTPUploader:
    """Wraps SFTPConnector with retry, directory batch, and callback adaptation."""

    MAX_RETRIES = 3

    def __init__(self, connector: SFTPConnector, run_log=None):
        self.connector = connector
        self.run_log = run_log

    def upload(self, local_path: str, remote_path: str,
               resume: bool = True,
               progress_callback: Optional[Callable[[int, int], None]] = None) -> bool:
        """Thin wrapper over connector.upload_file. Does NOT pre-read remote size
        (avoids redundant stat + misleading "Resuming" log when upload_file may
        delete+re-upload on prefix-md5 mismatch). Fires final 100% callback."""
        local_file = Path(local_path)
        if not local_file.exists():
            if self.run_log:
                self.run_log.fail(f"本地文件不存在: {local_path}")
            return False
        total_size = local_file.stat().st_size
        success = self.connector.upload_file(local_path, remote_path,
                                              resume=resume, progress_callback=progress_callback)
        if success and progress_callback:
            progress_callback(total_size, total_size)
        return success

    def upload_with_retry(self, local_path: str, remote_path: str,
                          max_retries: int = MAX_RETRIES,
                          progress_callback: Optional[Callable[[int, int], None]] = None) -> bool:
        """Retry upload up to max_retries+1 attempts. Forces resume=True on retries
        so completed chunks aren't re-sent."""
        for attempt in range(max_retries + 1):
            if self.run_log and attempt > 0:
                self.run_log.info(f"重试尝试 {attempt}/{max_retries}")
            if self.connector.upload_file(local_path, remote_path,
                                          resume=True, progress_callback=progress_callback):
                return True
        if self.run_log:
            self.run_log.fail(f"上传失败，已重试{max_retries + 1}次")
        return False

    def upload_directory(self, local_dir: str, remote_dir: str,
                         pattern: str = "*",
                         resume: bool = True,
                         progress_callback: Optional[Callable[[str, int, int], None]] = None) -> dict:
        """Upload all files matching glob pattern. Adapts per-file callback from
        (sent, total) to (filename, sent, total) via default-arg binding (avoids
        late-binding closure issues). Returns {"success": [...], "failed": [...]}."""
        local_path = Path(local_dir)
        if not local_path.is_dir():
            if self.run_log:
                self.run_log.fail(f"不是目录: {local_dir}")
            return {"success": [], "failed": [local_dir]}

        results = {"success": [], "failed": []}
        for file_path in local_path.glob(pattern):
            if not file_path.is_file():
                continue
            remote_file = str(PurePosixPath(remote_dir) / file_path.name)
            if self.run_log:
                self.run_log.info(f"正在上传 {file_path.name}...")
            file_callback = None
            if progress_callback:
                def file_callback(sent, total, name=file_path.name):
                    progress_callback(name, sent, total)
            if self.upload(str(file_path), remote_file, resume=resume, progress_callback=file_callback):
                results["success"].append(file_path.name)
            else:
                results["failed"].append(file_path.name)
        return results
