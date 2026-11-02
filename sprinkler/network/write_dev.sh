#!/usr/bin/env bash

# 引入公共库
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/auto/common.sh"

# -------- config --------
CHECK_ID="write devmem"
EXPECTED_DEVICE_COUNT="${EXPECTED_DEVICE_COUNT:-8}"

# -------- main --------
log INFO "$CHECK_ID" "修改NPU寄存器值"

# 创建临时文件记录Python进程PID
PID_FILE=$(mktemp)
trap 'rm -f "$PID_FILE"' EXIT

DEVICES=($(bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/npuip.sh"))
python "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/write_dev.py" "${DEVICES[@]}"
