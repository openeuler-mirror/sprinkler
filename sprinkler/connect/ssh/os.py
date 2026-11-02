"""SSH connection for CPU OS."""
import time

from sprinkler.connect.ssh.base import SSHConnector
from sprinkler.connect.targets import ConnectionTarget


class SSHOSConnector(SSHConnector):
    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        try:
            import paramiko
        except Exception as exc:
            return self.result_processor.failed_many(target, commands, "dependency", f"paramiko unavailable: {exc}", 0.0, self.backend_name())

        start = time.time()
        client = self._establish_connection(target)
        label = self.target_label(target)
        results = []
        try:
            for cmd in commands:
                result = self._run_ssh_command(client, target, cmd)
                if self._needs_session_retry(result):
                    client.close()
                    client = self._establish_connection(target)
                    retry_start = time.time()
                    try:
                        result = self._run_ssh_command(client, target, cmd)
                    except Exception as exc:
                        error_type = self._classify_paramiko_connect_error(exc, paramiko)
                        result = self.result_processor.failed_many(target, [cmd], error_type, str(exc),
                                                                   time.time() - retry_start,
                                                                   self.backend_name())[0]
                results.append(result)
                if on_result is not None:
                    on_result(result)
        finally:
            client.close()
        self.log_info(f'{label}——指令执行完成')
        return results

    def _needs_session_retry(self, result):
        text = str(result.get("stderr") or result.get("stdout") or result.get("error_type") or "").casefold()
        return not result.get("ok") and any(value in text for value in ("no existing session", "session not active"))

    def backend_name(self):
        return "paramiko"
