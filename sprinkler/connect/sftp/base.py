"""SFTP connection base connector."""

import hashlib
import socket
from pathlib import Path, PurePosixPath
from typing import Optional

import paramiko

from sprinkler.connect.base import BaseConnector
from sprinkler.connect.targets import ConnectionTarget


class SFTPConnector(BaseConnector):
    """SFTP connector for file upload/download via paramiko.

    Inherits BaseConnector for unified run_target (target validation + exception
    classification). SFTP-specific dict/str command dispatch and per-target
    isolated child connector for thread safety live in _run_commands.
    """

    _CHUNK_SIZE = 32768  # SFTP read/write chunk for resume and md5 streaming

    def __init__(self, args=None, result_processor=None, run_log=None):
        super().__init__(args=args, result_processor=result_processor, run_log=run_log)
        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._connected = False

    @staticmethod
    def backend_name():
        return "sftp"

    def connect(self, target: ConnectionTarget, timeout: int = 10) -> bool:
        """Connect via password auth. Raises RuntimeError on failure (message
        prefixed with classified error_type: timeout / auth / network / unknown)."""
        try:
            self._ssh_connect(target, password=target.password, timeout=timeout)
            self._open_sftp()
            return True
        except (socket.timeout, socket.error, paramiko.SSHException, paramiko.AuthenticationException) as exc:
            self._connected = False
            raise RuntimeError(f"{self._classify_sftp_error(exc)}: {exc}") from exc

    def _ssh_connect(self, target: ConnectionTarget, password: str = None,
                     pkey=None, timeout: int = 10) -> None:
        """Create SSHClient and connect. Shared by connect, connect_with_key,
        and SFTPMeSwitchConnector.connect (which needs SSH before open_sftp
        so it can fix sshd_config in between). Raises paramiko exceptions.

        Security: uses AutoAddPolicy (auto-trusts unknown host keys) — assumes
        internal network with no MITM risk. Change to RejectPolicy if operating
        in untrusted networks.
        """
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=target.host, port=target.port,
            username=target.user, password=password, pkey=pkey,
            timeout=timeout, look_for_keys=False, allow_agent=False,
        )

    def _open_sftp(self) -> None:
        """Open SFTP session over existing SSH connection."""
        self._sftp = self._client.open_sftp()
        self._connected = True

    def _classify_sftp_error(self, exc: Exception) -> str:
        """Classify SSH/SFTP exception: 超时 / 认证失败 / 网络错误 / 未知错误."""
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return "超时"
        if isinstance(exc, paramiko.AuthenticationException):
            return "认证失败"
        if isinstance(exc, paramiko.SSHException):
            text = str(exc).casefold()
            if "timed out" in text or "timeout" in text:
                return "超时"
            if "authentication" in text or "auth" in text or "password" in text:
                return "认证失败"
            return "网络错误"
        if isinstance(exc, (socket.error, OSError)):
            return "网络错误"
        return "未知错误"

    def connect_with_key(self, target: ConnectionTarget, key_path: str, timeout: int = 10) -> bool:
        """Connect via SSH key auth. Tries RSA/Ed25519/ECDSA in turn. Raises RuntimeError
        on failure (same error_type prefix as connect, plus key_not_found / key_encrypted)."""
        try:
            key = self._load_private_key(key_path)
            self._ssh_connect(target, pkey=key, timeout=timeout)
            self._open_sftp()
            return True
        except (socket.timeout, socket.error, paramiko.SSHException, paramiko.AuthenticationException,
                FileNotFoundError, paramiko.PasswordRequiredException) as exc:
            self._connected = False
            if isinstance(exc, FileNotFoundError):
                error_type = "密钥未找到"
            elif isinstance(exc, paramiko.PasswordRequiredException):
                error_type = "密钥加密"
            else:
                error_type = self._classify_sftp_error(exc)
            raise RuntimeError(f"{error_type}: {exc}") from exc

    @staticmethod
    def _load_private_key(key_path: str):
        """Load private key trying RSA/Ed25519/ECDSA. Preserves FileNotFoundError for classification."""
        last_exc = None
        for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                return key_cls.from_private_key_file(key_path)
            except Exception as exc:
                last_exc = exc
        if isinstance(last_exc, (FileNotFoundError, IOError)):
            raise last_exc
        raise paramiko.SSHException(
            f"Cannot load private key {key_path} as RSA/Ed25519/ECDSA: {last_exc}"
        ) from last_exc

    def is_connected(self) -> bool:
        """Check connection — probes transport liveness, not just the _connected flag."""
        if not self._connected or self._sftp is None or self._client is None:
            return False
        try:
            transport = self._client.get_transport()
            if transport is None or not transport.is_active():
                self._connected = False
                return False
            return True
        except Exception:
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Close SFTP + SSH. Idempotent — safe to call repeatedly; swallows close errors."""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connected = False

    def get_file_size(self, remote_path: str) -> int:
        """Return remote file size, or -1 if not found / not connected."""
        if not self.is_connected():
            return -1
        try:
            return self._sftp.stat(remote_path).st_size
        except (IOError, OSError, paramiko.SFTPError):
            return -1

    def _streaming_md5(self, reader, prefix_length: int = 0) -> str:
        """Compute md5 by streaming from a readable object (SFTP file or local file).

        prefix_length > 0: hash only first N bytes. == 0: entire stream.
        Shared by _remote_file_md5 and _local_file_md5 to avoid loop duplication.
        """
        h = hashlib.md5()
        remaining = prefix_length if prefix_length > 0 else None
        while remaining is None or remaining > 0:
            want = self._CHUNK_SIZE if remaining is None else min(self._CHUNK_SIZE, remaining)
            chunk = reader.read(want)
            if not chunk:
                break
            h.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        return h.hexdigest()

    def _remote_file_md5(self, remote_path: str, prefix_length: int = 0) -> Optional[str]:
        """Compute md5 of remote file by streaming via SFTP.

        prefix_length > 0: hash only the first N bytes (for resume prefix check).
        prefix_length == 0: hash the entire file.
        Subclasses MAY override to run remote `md5sum` over SSH for speed.
        Returns None on read error.
        """
        if not self.is_connected():
            return None
        try:
            with self._sftp.open(remote_path, "rb") as rf:
                return self._streaming_md5(rf, prefix_length)
        except (IOError, OSError, paramiko.SFTPError) as exc:
            if self.run_log:
                self.run_log.log("WARN", f"无法计算远程文件md5: {remote_path}, 原因: {exc}")
            return None

    def _local_file_md5(self, local_path: str, prefix_length: int = 0) -> str:
        """Compute md5 of local file by streaming. Mirrors _remote_file_md5.

        prefix_length > 0: hash only first N bytes. == 0: entire file.
        Streams in chunks to avoid OOM on large files.
        """
        with open(local_path, "rb") as f:
            return self._streaming_md5(f, prefix_length)

    def _ensure_dir(self, remote_dir: str) -> bool:
        """Ensure remote dir exists, creating nested dirs component by component.

        Absolute paths keep leading "/" (/a/b/c → mkdir /a, /a/b, /a/b/c);
        relative paths don't (a/b/c → mkdir a, a/b, a/b/c).
        """
        if not self.is_connected():
            return False
        try:
            self._sftp.stat(remote_dir)
            return True
        except (IOError, OSError, paramiko.SFTPError):
            pass
        try:
            is_absolute = remote_dir.startswith("/")
            parts = [p for p in remote_dir.split("/") if p]
            current = ""
            for part in parts:
                # First segment: "/a" (absolute) or "a" (relative);
                # subsequent: append "/part" to existing current.
                if not current:
                    current = f"/{part}" if is_absolute else part
                else:
                    current = f"{current}/{part}"
                try:
                    self._sftp.stat(current)
                except (IOError, OSError, paramiko.SFTPError):
                    self._sftp.mkdir(current)
            return True
        except (IOError, OSError, paramiko.SFTPError):
            return False

    def _compute_resume_position(self, local_file: Path, remote_path: str, file_size: int) -> int:
        """Determine where to resume uploading. Returns byte offset, 0 for fresh start.

        Checks remote leftover: if prefix md5 matches local prefix → resume from
        remote_size; if mismatch (stale file from different version) → delete and
        restart from 0; if no remote file → 0; if md5 cannot be computed (e.g.
        connection dropped during read) → 0 (safe fallback: don't resume without
        integrity verification).
        """
        remote_size = self.get_file_size(remote_path)
        if remote_size <= 0 or remote_size > file_size:
            return 0
        local_prefix = self._local_file_md5(str(local_file), remote_size)
        remote_prefix = self._remote_file_md5(remote_path, remote_size)
        if remote_prefix is None:
            # Cannot verify remote leftover integrity — safe fallback to fresh start.
            if self.run_log:
                self.run_log.log("WARN",
                    f"无法校验远程残留文件md5: {remote_path}, 已删除残留重新上传")
            self._sftp.remove(remote_path)
            return 0
        if remote_prefix != local_prefix:
            if self.run_log:
                self.run_log.log("WARN",
                    f"远程残留文件md5不匹配({remote_prefix} != {local_prefix}), "
                    f"已删除残留文件，重新上传")
            self._sftp.remove(remote_path)
            return 0
        if self.run_log:
            self.run_log.info(f"从字节位置 {remote_size} 继续上传")
        return remote_size

    def upload_file(self, local_path: str, remote_path: str,
                    resume: bool = False, progress_callback=None) -> bool:
        """Upload file with optional resume. progress_callback(bytes_sent, total_bytes).

        Resume: _compute_resume_position checks remote leftover prefix md5 —
        mismatch means stale remote → delete + re-upload. Two branches: putfo
        for fresh (pass file_size so callback total≠0), open("ab")+chunk for
        resume (putfo has no offset). Post-upload size check. Strong hash left
        to subclasses (get_cc_file_md5).
        """
        if not self.is_connected():
            return False
        local_file = Path(local_path)
        if not local_file.exists():
            if self.run_log:
                self.run_log.fail(f"本地文件不存在: {local_path}")
            return False

        file_size = local_file.stat().st_size
        remote_dir = str(PurePosixPath(remote_path).parent)
        if not self._ensure_dir(remote_dir):
            if self.run_log:
                self.run_log.fail(f"创建远程目录失败: {remote_dir}")
            return False

        try:
            resume_pos = self._compute_resume_position(local_file, remote_path, file_size) if resume else 0

            with open(local_file, "rb") as f:
                if resume_pos > 0:
                    f.seek(resume_pos)

                if resume_pos > 0:
                    # Resume: open("ab") + chunk write (putfo has no offset).
                    # callback uses f.tell() (includes already-transferred prefix)
                    # to match the (sent, total) semantics of the putfo branch.
                    with self._sftp.open(remote_path, "ab") as remote_file:
                        while chunk := f.read(self._CHUNK_SIZE):
                            remote_file.write(chunk)
                            if progress_callback:
                                progress_callback(f.tell(), file_size)
                else:
                    # Fresh upload: putfo with file_size so callback total is non-zero.
                    # paramiko callback: (bytes_so_far, total_bytes) — forward as-is.
                    def callback_wrapper(bytes_so_far, total_bytes):
                        if progress_callback:
                            progress_callback(bytes_so_far, total_bytes)

                    self._sftp.putfo(f, remote_path, file_size=file_size,
                                     callback=callback_wrapper, confirm=True)

            if progress_callback:
                progress_callback(file_size, file_size)

            # Post-upload integrity check.
            final_remote_size = self.get_file_size(remote_path)
            if final_remote_size != file_size:
                if self.run_log:
                    self.run_log.fail(
                        f"上传完整性校验失败: 远程文件大小 {final_remote_size} "
                        f"!= 本地文件大小 {file_size}")
                return False
            return True
        except (IOError, OSError, paramiko.SFTPError) as exc:
            if self.run_log:
                self.run_log.fail(f"上传失败: {exc}")
            return False

    def download_file(self, remote_path: str, local_path: str,
                      resume: bool = False, progress_callback=None) -> bool:
        """Download file with optional resume. progress_callback(bytes_received, total_bytes).

        Resume: open remote file + seek to resume_pos + chunk read → local "ab" append.
        Cannot use getfo for resume (it reads from offset 0; "ab" ignores seek → N+M corrupt).
        Before resuming, verifies local leftover prefix md5 matches remote prefix —
        mismatch means stale local file → delete + re-download from scratch.
        Post-download size check.
        """
        if not self.is_connected():
            return False
        try:
            remote_size = self.get_file_size(remote_path)
            if remote_size < 0:
                if self.run_log:
                    self.run_log.fail(f"远程文件不存在: {remote_path}")
                return False

            mode = "wb"
            resume_pos = 0
            if resume and Path(local_path).exists():
                local_size = Path(local_path).stat().st_size
                if 0 < local_size < remote_size:
                    # Verify local leftover prefix matches remote before appending.
                    local_prefix = self._local_file_md5(local_path, local_size)
                    remote_prefix = self._remote_file_md5(remote_path, local_size)
                    if remote_prefix is None or remote_prefix != local_prefix:
                        if self.run_log:
                            self.run_log.log("WARN",
                                f"本地残留文件md5不匹配({local_prefix} vs {remote_prefix}), "
                                f"重新下载")
                        mode = "wb"  # overwrite, not append
                    else:
                        mode = "ab"
                        resume_pos = local_size
                        if self.run_log:
                            self.run_log.info(f"从字节位置 {resume_pos} 继续下载")

            if resume_pos > 0:
                with self._sftp.open(remote_path, "rb") as remote_file:
                    remote_file.seek(resume_pos)
                    # received = cumulative bytes including existing local prefix,
                    # so callback reports total progress not just this session's bytes.
                    received = resume_pos
                    with open(local_path, "ab") as f:
                        while chunk := remote_file.read(self._CHUNK_SIZE):
                            f.write(chunk)
                            received += len(chunk)
                            if progress_callback:
                                progress_callback(received, remote_size)
                if progress_callback:
                    progress_callback(remote_size, remote_size)
            else:
                def callback(bytes_so_far, total_bytes):
                    if progress_callback:
                        progress_callback(bytes_so_far, total_bytes)

                with open(local_path, mode) as f:
                    self._sftp.getfo(remote_path, f, callback=callback)
                if progress_callback:
                    progress_callback(remote_size, remote_size)

            # Post-download integrity check.
            final_local_size = Path(local_path).stat().st_size
            if final_local_size != remote_size:
                if self.run_log:
                    self.run_log.fail(
                        f"下载完整性校验失败: 本地文件大小 {final_local_size} "
                        f"!= 远程文件大小 {remote_size}")
                return False
            return True
        except (IOError, OSError, paramiko.SFTPError) as exc:
            if self.run_log:
                self.run_log.fail(f"下载失败: {exc}")
            return False

    def list_files(self, remote_dir: str = ".") -> list:
        """List files in remote directory. Returns [] on error / not connected."""
        if not self.is_connected():
            return []
        try:
            return self._sftp.listdir(remote_dir)
        except (IOError, OSError, paramiko.SFTPError):
            return []

    def delete_file(self, remote_path: str) -> bool:
        """Delete remote file. Returns False on error / not connected."""
        if not self.is_connected():
            return False
        try:
            self._sftp.remove(remote_path)
            return True
        except (IOError, OSError, paramiko.SFTPError) as exc:
            if self.run_log:
                self.run_log.fail(f"删除文件失败: {exc}")
            return False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.disconnect()
        return False

    def _run_commands(self, target: ConnectionTarget, commands: list, on_result=None):
        """Execute SFTP "commands" (file-transfer dicts) against one target.

        Each command is {"local": ..., "remote": ...}; non-dict fails fast.
        """
        results = []
        # Isolated child connector per target — paramiko SFTPClient is NOT
        # thread-safe; ConnectionRunner.run shares one connector across targets
        # via ThreadPoolExecutor. Each target gets its own _sftp to avoid
        # "Server connection dropped" / "Socket is closed" under concurrency.
        # Direct `with conn: conn.upload_file()` callers bypass this entirely.
        child = self.__class__(args=self.args, result_processor=self.result_processor,
                               run_log=self.run_log)
        try:
            try:
                child.connect(target)
            except RuntimeError as exc:
                error_type = self._parse_error_type(str(exc))
                for cmd in commands:
                    cmd_label = cmd if not isinstance(cmd, dict) \
                        else f"upload {cmd.get('local')} -> {cmd.get('remote')}"
                    results.append(self.result_processor.make(
                        target, cmd_label, False, 255, error_type or "connection",
                        "", str(exc), 0.0, self.backend_name(),
                    ))
                    if on_result:
                        on_result(results[-1])
                return results

            for cmd in commands:
                if isinstance(cmd, dict):
                    local_path = cmd.get("local")
                    remote_path = cmd.get("remote")
                    if local_path and remote_path:
                        success = child.upload_file(local_path, remote_path)
                        # Success result uses SFTP-specific fields (local/remote)
                        # not the standard command-execution fields (rc/stdout).
                        results.append({
                            "ok": success,
                            "cmd": f"upload {local_path} -> {remote_path}",
                            "local": local_path, "remote": remote_path,
                            "target": BaseConnector.target_label(target),
                            "backend": self.backend_name(),
                        })
                    else:
                        results.append({
                            "ok": False, "cmd": str(cmd),
                            "error": "SFTP命令缺少'local'或'remote'键",
                            "target": BaseConnector.target_label(target),
                            "backend": self.backend_name(),
                        })
                else:
                    results.append({
                        "ok": False, "cmd": cmd,
                        "error": "SFTP命令必须是包含'local'和'remote'键的字典",
                        "target": BaseConnector.target_label(target),
                        "backend": self.backend_name(),
                    })
                if on_result:
                    on_result(results[-1])
            return results
        finally:
            child.disconnect()

    @staticmethod
    def _parse_error_type(message: str) -> str:
        """Extract leading 'error_type:' prefix from connect() RuntimeError messages."""
        if ":" in message:
            head = message.split(":", 1)[0].strip().lower()
            if head in ("超时", "认证失败", "网络错误", "未知错误", "密钥未找到", "密钥加密"):
                return head
        return ""

    @staticmethod
    def _timestamped_filename(name: str, timestamp: str) -> str:
        """Insert timestamp before file extension: fw.bin → fw_<ts>.bin, fw → fw_<ts>."""
        if '.' in name:
            base, ext = name.rsplit('.', 1)
            return f"{base}_{timestamp}.{ext}"
        return f"{name}_{timestamp}"

    @staticmethod
    def _safe_remote_filename(filename: str) -> str:
        """Validate filename is safe to join with DEFAULT_UPLOAD_PATH.

        Rejects path traversal: no "/" or ".." allowed — prevents writing
        outside DEFAULT_UPLOAD_PATH (e.g. filename="../etc/passwd" → /etc/passwd).
        Returns the filename if safe, raises ValueError if not.
        """
        if not filename or "/" in filename or ".." in filename:
            raise ValueError(f"不安全的远程文件名(路径遍历?): {filename}")
        return filename

    def _upload_to_default_path(self, local_path: str, remote_filename: str,
                                resume: bool = True, progress_callback=None,
                                log_label: str = None) -> bool:
        """Upload a local file to DEFAULT_UPLOAD_PATH/<remote_filename>.

        Shared by SFTPBMCConnector.upload_package and SFTPMeSwitchConnector.upload_cc_package
        — handles file existence check, path construction, logging, and delegation
        to upload_file. Subclasses only need to set DEFAULT_UPLOAD_PATH.
        Validates remote_filename to prevent path traversal outside DEFAULT_UPLOAD_PATH.
        """
        try:
            self._safe_remote_filename(remote_filename)
        except ValueError as e:
            if self.run_log:
                self.run_log.fail(str(e))
            return False
        local_file = Path(local_path)
        if not local_file.exists():
            if self.run_log:
                self.run_log.fail(f"本地文件不存在: {local_path}")
            return False
        remote_path = str(PurePosixPath(self.DEFAULT_UPLOAD_PATH) / remote_filename)
        if self.run_log and log_label:
            self.run_log.info(f"{log_label}: {local_path} -> {remote_path}")
        return self.upload_file(str(local_file), remote_path, resume, progress_callback)
