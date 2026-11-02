"""SSH connection for BMC (CPU BMC and SFU BMC)."""
import time

from sprinkler.connect.ssh.base import SSHConnector
from sprinkler.connect.targets import ConnectionTarget


class SSHBMCConnector(SSHConnector):
    def _run_commands(self, target: ConnectionTarget, commands: list[str], on_result=None):
        try:
            import paramiko
        except Exception as exc:
            return self.result_processor.failed_many(target, commands, "dependency", f"paramiko unavailable: {exc}", 0.0, self.backend_name())

        start = time.time()
        label = self.target_label(target)

        client, last_error = self._connect_with_passwords(target)

        if not client:
            error_type = "auth" if isinstance(last_error, paramiko.AuthenticationException) else self._classify_paramiko_connect_error(last_error, paramiko)
            return self.result_processor.failed_many(target, commands, error_type, f"All passwords failed: {last_error}", time.time() - start, self.backend_name())

        self.log_info(f'成功连接到 {label}, 开始执行指令')
        results = []
        try:
            for cmd in commands:
                results.append(self._run_ssh_command(client, target, cmd))
        finally:
            client.close()
        self.log_info(f'{label} 指令执行完成')
        return results

    def backend_name(self):
        return "ssh_bmc"
