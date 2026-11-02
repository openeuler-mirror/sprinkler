#!/bin/bash

# -------- 引入公共库 --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
source "$SCRIPT_DIR/common.sh"

CHECK_ID="NET_CHECK"

# -------- 参数配置 --------
log INFO "$CHECK_ID" "正在加载配置参数..."

# 引入公共配置库
. "${PARENT_DIR}/config_util.sh"

# 配置参数
config_require_keys \
    "POD_IP|请输入CPU OS的IP地址||0|validate_ip" \


log INFO "$CHECK_ID" "=== 开始网卡检测 ==="

python "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/auto/check_eth.py" $POD_IP
