"""NPU 1213 connection via ssh BMC with SOL initialization sequence."""
from sprinkler.connect.ssh.sol.base import SOL1213Connector


class NPU1213Connector(SOL1213Connector):
    INIT_COMMANDS = [
        "source /etc/profile",
        "export LD_LIBRARY_PATH=:$LD_LIBRARY_PATH/data bmcdfx",
        "cd /data",
        "ipmcset -d ser -v 0",
        "ipmcset -t sol -d act -v 19 0",
    ]

    ERROR_CLASSIFIER = "native"

    def backend_name(self):
        return "npu_1213"
