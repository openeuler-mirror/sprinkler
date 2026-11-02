"""命令构建模块"""

import re
from typing import Optional

from .models import ClusterConfig


class MpirunCommandBuilder:
    """MPirun命令构建器"""
    
    def __init__(self, config: ClusterConfig, hccl_test: str = "aicpu_ts", interface_names: Optional[list[str]] = None, hccl_buffsize: int = 1024):
        self.config = config
        self.hccl_test = hccl_test
        self.interface_names = interface_names
        self.hccl_buffsize = hccl_buffsize
    
    def build_mpirun_command(self, hosts_spec: str, mpi_n: int, test_type: str) -> str:
        """
        构建mpirun命令（统一单机和集群模式）
        参数:
            hosts_spec: 主机规格字符串，格式为 "ip1:n,ip2:n,..." 或 "ip:n"（单机）
            mpi_n: MPI进程数
            test_type: 测试类型
        返回:
            mpirun命令字符串
        """
        return (
            f"mpirun -hosts {hosts_spec} ./bin/{test_type} "
            f"-p {mpi_n} -b 1K -e 1g -f 2 -d fp32 -a {self.hccl_test}"
        )
    
    def build_env_setup_script(self) -> str:
        """
        构建环境设置脚本
        返回:
            环境设置脚本内容
        """
        cann_path = self.config.cann_path
        mpich_path = self.config.mpich_path
        
        if self.interface_names is None:
            ifname_value = "eth0,eth1,eth2,eth3,eth4,eth5,eth-front"
        elif self.interface_names:
            ifname_value = ",".join(self.interface_names)
        else:
            ifname_value = None
        
        lines = [
            f"export CANN_PATH='{cann_path}'",
            f"export MPICH_PATH='{mpich_path}'",
            f"export CANNPATH=${{CANN_PATH}}/ascend-toolkit/latest",
            f"export MPIPATH=${{MPICH_PATH}}",
            "",
            "[[ -f \"${CANN_PATH}/ascend-toolkit/latest/set_env.sh\" ]] && source ${CANN_PATH}/ascend-toolkit/latest/set_env.sh",
            "[[ -f \"/usr/local/Ascend/driver/bin/setenv.bash\" ]] && source /usr/local/Ascend/driver/bin/setenv.bash",
            "[[ -f \"${CANNPATH}/set_env.sh\" ]] && source ${CANNPATH}/set_env.sh",
            "",
            "export LD_LIBRARY_PATH=${MPIPATH}/lib/:$LD_LIBRARY_PATH",
            "export PATH=${MPIPATH}/bin:$PATH",
            "export PATH=/bin:$PATH",
            "",
            "export HCCL_HOST_SOCKET_PORT_RANGE=auto",
        ]
        if ifname_value is not None:
            lines.append(f"export HCCL_SOCKET_IFNAME={ifname_value}")
        lines.extend([
            f"export HCCL_BUFFSIZE={self.hccl_buffsize}",
            "export HCCL_ALG_MULTIPLE_DIMENSION_SPLIT_RATIO=0.75",
            "",
            f"HCCL_TEST_PATH=${{CANNPATH}}/tools/hccl_test",
            "if [[ ! -d \"$HCCL_TEST_PATH\" ]]; then",
            "    echo \"错误: HCCL测试目录不存在: $HCCL_TEST_PATH\"",
            "    exit 1",
            "fi",
            "",
            "cd \"$HCCL_TEST_PATH\" || {",
            "    echo \"错误: 无法切换到目录 $HCCL_TEST_PATH\"",
            "    exit 1",
            "}",
        ])
        
        return "\n".join(lines)


class BandwidthOutputParser:
    """带宽输出解析器"""
    
    DATA_LINE_PATTERN = re.compile(r'^\s*\d+.*\|')
    
    def parse(self, output: str) -> Optional[float]:
        """
        解析输出,提取最后一条带宽数据
        参数:
            output: 命令输出
        返回:
            带宽值,如果无法解析则返回None
        """
        last_bw = None
        
        for line in output.splitlines():
            if not self.DATA_LINE_PATTERN.match(line):
                continue
            
            bw = self._extract_bandwidth(line)
            if bw is not None and bw > 0:
                last_bw = bw
        
        return last_bw
    
    def _extract_bandwidth(self, line: str) -> Optional[float]:
        """
        从单行提取带宽值
        参数:
            line: 数据行
        返回:
            带宽值
        """
        parts = line.split('|')
        if len(parts) < 3:
            return None
        
        value_str = parts[2].strip()
        if not value_str:
            return None
        
        try:
            return float(value_str)
        except (ValueError, TypeError):
            return None
