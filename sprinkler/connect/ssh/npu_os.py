"""通过 ``ssh root@npu<rank>`` 登录 NPU OS。

本机模式直接调用系统 ssh；给 ``-ip`` 时先 paramiko 登录该 CPU OS，
再在跳板机内执行同样的 ssh 命令。免密依赖 ``sprinkler -no-pass``，
未配置时只报错提示，不自动修改配置。
"""

import socket
import subprocess
import time

from sprinkler.connect.npu_access import npu_os_command
from sprinkler.connect.ssh.base import SSHConnector
from sprinkler.connect.targets import ConnectionTarget


class NPUOSConnector(SSHConnector):
    def backend_name(self):
        return "npu_os"

    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        rank = (target.meta or {}).get("rank")
        if rank is None:
            return self.result_processor.failed_many(
                target, commands, "target", "target has no rank", 0.0, self.backend_name())
        alias = f"npu{rank}"
        if (target.meta or {}).get("source") == "local":
            return self._run_local(target, alias, commands, on_result)
        return self._run_via_jump(target, alias, commands, on_result)

    def _run_local(self, target, alias, commands, on_result=None):
        error_type, reason = self._probe_local(alias)
        if reason:
            if error_type == "auth":
                message = f"{alias} 免密不可用: {reason}；请先执行 sprinkler -no-pass"
            else:
                message = f"{alias} 连接探测失败({error_type}): {reason}"
            return self.result_processor.failed_many(
                target, commands, error_type, message, 0.0, self.backend_name())
        return self._collect(
            commands, lambda cmd: self._run_system_ssh(target, alias, cmd), on_result)

    def _probe_local(self, alias):
        try:
            done = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
                 "-o", f"ConnectTimeout={self.connect_timeout}", alias, "true"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=self.connect_timeout + 2)
        except subprocess.TimeoutExpired:
            return "timeout", f"SSH 探测超时（{self.connect_timeout + 2}s）"
        except FileNotFoundError:
            return "dependency", "未找到 ssh 可执行文件"
        except PermissionError:
            return "dependency", "执行 ssh 权限不足"
        except OSError as exc:
            return "dependency", f"执行 ssh 失败: {exc}"
        if done.returncode == 0:
            return "", ""
        return "auth", self._last_line(done.stderr) or f"rc={done.returncode}"

    def _run_system_ssh(self, target, alias, cmd):
        start = time.time()
        try:
            done = subprocess.run(
                npu_os_command(alias, cmd), shell=True, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.command_timeout)
        except subprocess.TimeoutExpired as exc:
            return self.result_processor.make(
                target, cmd, False, 255, "timeout", self._decode_text(exc.stdout),
                f"command timed out after {self.command_timeout}s",
                time.time() - start, self.backend_name())
        return self.result_processor.make(
            target, cmd, done.returncode == 0, done.returncode,
            "" if done.returncode == 0 else "command",
            done.stdout or "", done.stderr or "", time.time() - start, self.backend_name())

    def _run_via_jump(self, target, alias, commands, on_result=None):
        try:
            client = self._establish_connection(target)
        except Exception as exc:
            return self.result_processor.failed_many(
                target, commands, "connection",
                f"跳板机连接失败: {exc}", 0.0, self.backend_name())
        try:
            error_type, reason = self._probe_jump(client, alias)
            if reason:
                if error_type == "auth":
                    message = (f"跳板机 {target.host} 无法免密登录 {alias}: {reason}；"
                               f"请在跳板机执行 sprinkler -no-pass")
                else:
                    message = (f"跳板机 {target.host} 探测 {alias} 失败"
                               f"({error_type}): {reason}")
                return self.result_processor.failed_many(
                    target, commands, error_type, message, 0.0, self.backend_name())
            return self._collect(
                commands,
                lambda cmd: self._run_jump_command(client, target, alias, cmd),
                on_result)
        finally:
            client.close()

    def _probe_jump(self, client, alias):
        command = (f"ssh -o BatchMode=yes -o PasswordAuthentication=no "
                   f"-o ConnectTimeout={self.connect_timeout} {alias} true")
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=self.command_timeout)
            out = self._decode_text(stdout.read())
            err = self._decode_text(stderr.read())
            rc = int(stdout.channel.recv_exit_status())
        except (socket.timeout, TimeoutError):
            return "timeout", f"SSH 探测超时（{self.command_timeout}s）"
        except Exception as exc:
            return "network", str(exc)
        if rc == 0:
            return "", ""
        return "auth", self._last_line(err) or self._last_line(out) or f"rc={rc}"

    def _run_jump_command(self, client, target, alias, cmd):
        start = time.time()
        try:
            _stdin, stdout, stderr = client.exec_command(
                npu_os_command(alias, cmd), timeout=self.command_timeout)
            stdout.channel.settimeout(self.command_timeout)
            out = self._decode_text(stdout.read())
            err = self._decode_text(stderr.read())
            rc = int(stdout.channel.recv_exit_status())
            return self.result_processor.make(
                target, cmd, rc == 0, rc, "" if rc == 0 else "command",
                out, err, time.time() - start, self.backend_name())
        except (socket.timeout, TimeoutError) as exc:
            return self.result_processor.make(
                target, cmd, False, 255, "timeout", "", str(exc),
                time.time() - start, self.backend_name())
        except Exception as exc:
            return self.result_processor.make(
                target, cmd, False, 255, "command", "", str(exc),
                time.time() - start, self.backend_name())

    @staticmethod
    def _collect(commands, run, on_result):
        results = []
        for cmd in commands:
            results.append(run(cmd))
            if on_result is not None:
                on_result(results[-1])
        return results

    @staticmethod
    def _last_line(text):
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        return lines[-1] if lines else ""
