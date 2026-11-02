# /bin/bash
# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "${PARENT_DIR}/config_util.sh"

# 配置参数
config_require_keys \
    "POD_NPU_USER|请输入NPU连接用户名|root|0|validate_non_empty" \
    "POD_NPU_PASSWORD|请输入NPU登录密码(输入不回显)||1|validate_non_empty" \
    "POD_NPU_HW_USER|请输入NPU连接Hw用户名|HwHiAiUser|0|validate_non_empty" \
    "POD_NPU_HW_PASSWORD|请输入NPU Hw登录密码(输入不回显)||1|validate_non_empty"

USER=$POD_NPU_USER
PASSWORD=$POD_NPU_PASSWORD
HW_USER=$POD_NPU_HW_USER
HW_PASSWORD=$POD_NPU_HW_PASSWORD
ip_index=($(bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/npuip.sh"))
chmod +x $(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/autoroot.sh
for ((i=0; i<=7; i++)); do
    "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/autoroot.sh" ${ip_index[$i]} $POD_NPU_PASSWORD $HW_USER $HW_PASSWORD
done