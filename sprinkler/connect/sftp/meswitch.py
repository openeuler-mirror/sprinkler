"""SFTP connector for MeSwitch file upload."""

import shlex
import socket
import time
from pathlib import Path, PurePosixPath
from typing import Tuple

import paramiko

from sprinkler.connect.sftp.base import SFTPConnector


class SFTPMeSwitchConnector(SFTPConnector):
    """SFTP connector for MeSwitch. Auto-fixes sshd_config Subsystem sftp on connect."""

    DEFAULT_UPLOAD_PATH = "/opt/vrpv8/home/"
    _sshd_restart_wait = 2  # seconds to wait for sshd to fully restart after config fix

    def backend_name(self):
        return "sftp_meswitch"

    def connect(self, target, timeout: int = 10) -> bool:
        """Connect to MeSwitch: SSH → check/fix sshd_config → open SFTP.

        Raises RuntimeError on connection or sftp_config fix failure.
        Use args.no_fix_sftp (CLI -no-fix-sftp) to skip sshd_config auto-fix.
        """
        try:
            self._ssh_connect(target, password=target.password, timeout=timeout)
            if self.run_log:
                self.run_log.info(f"已连接到MeSwitch {target.host}")

            # Save credentials — sshd restart invalidates the current session.
            self._target_host = target.host
            self._target_port = target.port
            self._target_user = target.user
            self._target_password = target.password

            if not self._skip_sftp_fix():
                if not self._check_and_fix_sftp_config():
                    raise RuntimeError(
                        "sftp配置: 无法验证或修复sshd_config的Subsystem sftp配置 - 请查看日志; 可使用-no-fix-sftp跳过"
                    )

            self._open_sftp()
            return True
        except RuntimeError:
            self._connected = False
            raise
        except (paramiko.SSHException, paramiko.AuthenticationException, OSError, socket.error) as exc:
            self._connected = False
            raise RuntimeError(f"{self._classify_sftp_error(exc)}: {exc}") from exc
        except Exception as exc:
            self._connected = False
            raise RuntimeError(f"未知错误: {exc}") from exc

    def _skip_sftp_fix(self) -> bool:
        """True if caller set args.no_fix_sftp (CLI -no-fix-sftp)."""
        return bool(getattr(self.args, "no_fix_sftp", False))

    def _run_cmd(self, cmd: str, timeout: int = 15) -> Tuple[bool, str]:
        """Execute shell command via SSH. Returns (success, stdout)."""
        if not self._client:
            return False, "未连接"
        try:
            stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
            out = stdout.read().decode('utf-8', errors='ignore').strip()
            return (stdout.channel.recv_exit_status() == 0, out)
        except Exception as e:
            return False, str(e)

    def _check_and_fix_sftp_config(self) -> bool:
        """Check sshd_config Subsystem sftp. Three states: correct → True,
        wrong/missing → _fix_sftp_config, read failure → False (don't silently
        continue to a confusing open_sftp EOF)."""
        if self.run_log:
            self.run_log.info("正在检查MeSwitch SSH SFTP配置...")
        try:
            success, out = self._run_cmd("cat /etc/ssh/sshd_config", timeout=10)
            if not success:
                if self.run_log:
                    self.run_log.log("WARN", f"无法读取sshd_config: {out[:100]}")
                return False
            for line in out.split('\n'):
                line = line.strip()
                if line.startswith('Subsystem') and 'sftp' in line.lower():
                    if 'internal-sftp' in line:
                        if self.run_log:
                            self.run_log.info("SFTP配置正确(internal-sftp)")
                        return True
                    if self.run_log:
                        self.run_log.info("SFTP配置不正确，正在修复...")
                    return self._fix_sftp_config()
            if self.run_log:
                self.run_log.info("未找到Subsystem sftp，正在添加...")
            return self._fix_sftp_config()
        except Exception as e:
            if self.run_log:
                self.run_log.log("WARN", f"SFTP配置检查异常: {e}")
            return False

    def _fix_sftp_config(self) -> bool:
        """Fix sshd_config to use internal-sftp and restart sshd.

        Hardening: idempotent (grep before append), sshd -t syntax check before
        restart (abort if invalid — don't lock out SSH), critical-step abort
        (sed/echo failure stops before restart), backup is best-effort.
        """
        if self.run_log:
            self.run_log.info("正在修复SFTP配置...")
        try:
            # 1. Backup (best-effort)
            success, _ = self._run_cmd("cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup", timeout=10)
            if not success and self.run_log:
                self.run_log.log("WARN", "备份sshd_config失败(继续执行 — 无回滚保护)")

            # 2. Comment out existing Subsystem sftp lines
            success, out = self._run_cmd("sed -i 's|^Subsystem.*sftp.*|#&|' /etc/ssh/sshd_config", timeout=10)
            if not success:
                if self.run_log:
                    self.run_log.fail(f"sed命令失败: {out[:100]}")
                return False

            # 3. Append internal-sftp (idempotent — grep first)
            success, _ = self._run_cmd(
                "grep -q '^Subsystem[[:space:]]\\+sftp[[:space:]]\\+internal-sftp' /etc/ssh/sshd_config",
                timeout=10,
            )
            if success:
                if self.run_log:
                    self.run_log.info("Subsystem sftp internal-sftp已存在，跳过添加")
            else:
                success, out = self._run_cmd(
                    "echo 'Subsystem sftp internal-sftp' >> /etc/ssh/sshd_config", timeout=10
                )
                if not success:
                    if self.run_log:
                        self.run_log.fail(f"添加Subsystem sftp失败: {out[:100]}")
                    return False

            # 4. Syntax check before restart
            success, out = self._run_cmd("sshd -t 2>&1 || true", timeout=10)
            if not success or "error" in out.lower() or "bad" in out.lower():
                if self.run_log:
                    self.run_log.fail(
                        f"sshd -t报告配置错误，中止重启: {out[:200]}")
                return False
            if self.run_log:
                self.run_log.info("SFTP配置已更新且语法正确，正在重启sshd...")

            # 5. Restart sshd (try multiple init systems, fallback to HUP)
            restart_cmds = ["systemctl restart sshd", "service ssh restart", "/etc/init.d/ssh restart"]
            restart_success = False
            for restart_cmd in restart_cmds:
                success, _ = self._run_cmd(restart_cmd, timeout=15)
                if success:
                    restart_success = True
                    if self.run_log:
                        self.run_log.info(f"SSH服务已重启: {restart_cmd}")
                    break
            if not restart_success:
                self._run_cmd("pkill -HUP sshd || true", timeout=10)
                if self.run_log:
                    self.run_log.info("已向sshd发送HUP信号")
                restart_success = True

            time.sleep(self._sshd_restart_wait)  # Wait for sshd to fully restart

            # 6. Reconnect — pre-restart session is invalidated.
            # Use _ssh_connect (creates a fresh SSHClient) — reusing the
            # closed instance's connect() may be unreliable per paramiko internals.
            if self.run_log:
                self.run_log.info("SFTP配置已修复，正在重连SSH...")
            # Build a minimal target-like object from saved credentials.
            from sprinkler.connect.targets import ConnectionTarget
            reconnect_target = ConnectionTarget(
                name="meswitch_reconnect",
                host=self._target_host, port=self._target_port,
                user=self._target_user, password=self._target_password,
            )
            self._ssh_connect(reconnect_target, password=self._target_password)
            if self.run_log:
                self.run_log.info("sshd重启后SSH已重连")
            return True
        except Exception as e:
            if self.run_log:
                self.run_log.fail(f"修复SFTP配置失败: {e}")
            return False

    def upload_cc_package(self, local_cc_path: str, remote_filename: str = None,
                          resume: bool = True, progress_callback=None):
        """Upload CC package to /opt/vrpv8/home/. Returns bool."""
        if remote_filename is None:
            remote_filename = Path(local_cc_path).name
        return self._upload_to_default_path(local_cc_path, remote_filename,
                                            resume, progress_callback,
                                            "Uploading CC package to MeSwitch")

    def upload_cc_package_with_timestamp(self, local_cc_path: str, timestamp: str = None,
                                         resume: bool = True, progress_callback=None):
        """Upload CC package with timestamp suffix. Returns (success, remote_filename, error)."""
        local_file = Path(local_cc_path)
        if not local_file.exists():
            if self.run_log:
                self.run_log.fail(f"本地CC文件不存在: {local_cc_path}")
            return False, "", f"本地文件不存在: {local_cc_path}"
        if timestamp is None:
            timestamp = str(int(time.time()))
        remote_filename = self._timestamped_filename(local_file.name, timestamp)
        if self.run_log:
            self.run_log.info(f"正在上传CC包到MeSwitch: {local_file.name} -> {remote_filename}")
        success = self._upload_to_default_path(local_cc_path, remote_filename,
                                               resume, progress_callback)
        if success:
            return True, remote_filename, ""
        if self.run_log:
            self.run_log.fail("上传失败")
        return False, "", "上传失败"

    def verify_cc_file(self, remote_filename: str) -> bool:
        """Verify CC file exists on MeSwitch (size > 0)."""
        try:
            self._safe_remote_filename(remote_filename)
        except ValueError:
            return False
        remote_path = str(PurePosixPath(self.DEFAULT_UPLOAD_PATH) / remote_filename)
        return self.get_file_size(remote_path) > 0

    def get_cc_file_md5(self, remote_filename: str) -> str:
        """Get MD5 of CC file via SSH md5sum. Returns "" on failure. Path is shlex-quoted."""
        if not self.is_connected():
            return ""
        try:
            self._safe_remote_filename(remote_filename)
        except ValueError:
            return ""
        remote_path = str(PurePosixPath(self.DEFAULT_UPLOAD_PATH) / remote_filename)
        try:
            stdin, stdout, stderr = self._client.exec_command(f"md5sum {shlex.quote(remote_path)}")
            output = stdout.read().decode('utf-8')
            if output:
                return output.split()[0]
        except Exception:
            pass
        return ""

    def list_cc_files(self) -> list:
        """List *.cc files in upload dir via SFTP listdir + Python filter (not ls -la parsing)."""
        if not self.is_connected():
            return []
        try:
            entries = self._sftp.listdir(self.DEFAULT_UPLOAD_PATH)
            return [name for name in entries if name.endswith(".cc")]
        except (IOError, OSError, paramiko.SFTPError) as exc:
            if self.run_log:
                self.run_log.log("WARN", f"无法列出目录{self.DEFAULT_UPLOAD_PATH}: {exc}")
            return []

    def delete_cc_file(self, remote_filename: str) -> bool:
        """Delete CC file from MeSwitch."""
        try:
            self._safe_remote_filename(remote_filename)
        except ValueError:
            return False
        remote_path = str(PurePosixPath(self.DEFAULT_UPLOAD_PATH) / remote_filename)
        return self.delete_file(remote_path)

    def cleanup_old_cc_files(self, keep_latest: int = 1) -> dict:
        """Delete old CC files keeping newest N (sorted by filename desc — timestamps embedded).

        Returns {"deleted": [...], "failed": [...]}.
        """
        cc_files = self.list_cc_files()
        if not cc_files:
            return {"deleted": [], "failed": []}
        cc_files.sort(reverse=True)
        files_to_delete = cc_files[keep_latest:] if keep_latest > 0 else cc_files
        results = {"deleted": [], "failed": []}
        for filename in files_to_delete:
            if self.delete_cc_file(filename):
                results["deleted"].append(filename)
            else:
                results["failed"].append(filename)
        return results
