"""CPU 1213 connection via SSH BMC with SOL initialization sequence."""
from sprinkler.connect.ssh.sol.base import SOL1213Connector


class CPU1213Connector(SOL1213Connector):
    INIT_COMMANDS = [
        "ipmcset -d serialdir -v 4",
        "ipmcset -t sol -d a -v 4 0",
    ]

    ERROR_CLASSIFIER = "paramiko"

    def backend_name(self):
        return "cpu_1213"
