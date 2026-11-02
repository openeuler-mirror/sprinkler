"""Unified runner for various connection types."""

from concurrent.futures import ThreadPoolExecutor

from sprinkler.connect.base import BaseConnector, ResultProcessor
from sprinkler.utils.utils import env_text_b64, positive_int_or_default, read_text_command_file
from sprinkler.connect.ssh.os import SSHOSConnector
from sprinkler.connect.ssh.switch import SwitchSSHConnector
from sprinkler.connect.ssh.bmc import SSHBMCConnector
from sprinkler.connect.telnet.bmc import TelnetBMCConnector
from sprinkler.connect.telnet.com import TelnetCOMConnector
from sprinkler.connect.ssh.sol.npu import NPU1213Connector
from sprinkler.connect.ssh.sol.cpu import CPU1213Connector
from sprinkler.connect.sftp.base import SFTPConnector
from sprinkler.connect.sftp.meswitch import SFTPMeSwitchConnector
from sprinkler.connect.sftp.bmc import SFTPBMCConnector
from sprinkler.connect.targets import ConnectionTarget

def normalize_commands(args=None, commands=None):
    if commands is None:
        commands = list(getattr(args, "cmd", []) or [])
        remote_text = env_text_b64("SPRINKLER_REMOTE_COMMANDS_B64")
        if remote_text:
            commands.extend(remote_text.splitlines())
        file_path = getattr(args, "file", None)
        if file_path:
            commands.extend(read_text_command_file(file_path).splitlines())

    if isinstance(commands, str):
        command_list = [commands.strip()]
    else:
        command_list = []
        for raw in commands:
            cmd = str(raw or "").strip()
            if cmd and not cmd.startswith("#"):
                command_list.append(cmd)

    if not command_list:
        raise ValueError("commands cannot be empty")
    return command_list

class ConnectionRunner:
    DEFAULT_PARALLEL = 8

    def __init__(self, args=None, run_log=None, connector: BaseConnector = None, parallel: int = None):
        self.args = args
        self.run_log = run_log
        self.connector = connector or SSHOSConnector(args=args, result_processor=ResultProcessor(), run_log=run_log)
        self._parallel = parallel

    def run(self, targets, commands=None, on_result=None):
        target_list = self._normalize_targets(targets)
        command_list = self._normalize_commands(commands)
        if not target_list:
            raise ValueError("targets cannot be empty")

        max_workers = self._resolve_parallel(len(target_list))
        if max_workers > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                batches = list(pool.map(
                    lambda t: self.connector.run_target(t, command_list, on_result=on_result),
                    target_list,
                ))
        else:
            batches = [self.connector.run_target(t, command_list, on_result=on_result) for t in target_list]

        results = []
        for target, batch in zip(target_list, batches):
            if self.run_log is not None:
                self.run_log.write_target_results(BaseConnector.target_label(target), batch)
            results.extend(batch)
        return results

    def _resolve_parallel(self, target_count: int) -> int:
        if self._parallel is not None:
            return min(self._parallel, target_count)
        return min(positive_int_or_default(getattr(self.args, "parallel", self.DEFAULT_PARALLEL), self.DEFAULT_PARALLEL, "-parallel"), target_count)

    def _normalize_commands(self, commands):
        return normalize_commands(self.args, commands)
    def _normalize_targets(self, targets):
        if isinstance(targets, (ConnectionTarget, dict, str)):
            target_values = [targets]
        else:
            try:
                target_values = list(targets or [])
            except TypeError:
                target_values = [targets]
        return [ConnectionTarget.from_value(target) for target in target_values]


def create_runner(args=None, run_log=None, connection_type: str = "ssh_os"):
    connector_map = {
        "ssh_os": SSHOSConnector,
        "ssh_switch": SwitchSSHConnector,
        "ssh_bmc": SSHBMCConnector,
        "telnet_bmc": TelnetBMCConnector,
        "telnet_com": TelnetCOMConnector,
        "npu_1213": NPU1213Connector,
        "cpu_1213": CPU1213Connector,
        "sftp": SFTPConnector,
        "sftp_meswitch": SFTPMeSwitchConnector,
        "sftp_bmc": SFTPBMCConnector,
    }
    connector_class = connector_map.get(connection_type)
    if connector_class is None:
        raise ValueError(f"unknown connection type: {connection_type}")
    connector = connector_class(args=args, result_processor=ResultProcessor(), run_log=run_log)
    return ConnectionRunner(args=args, run_log=run_log, connector=connector)
