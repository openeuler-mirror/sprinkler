#!/usr/bin/env bash

# -------- 引入公共库 --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
FOUR_PARENT_DIR="$(dirname "$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")")"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/common.sh"
mkdir -p $FOUR_PARENT_DIR/reg_rw_logs
SAVE_PATH=$FOUR_PARENT_DIR/reg_rw_logs
# -------- config --------
CHECK_ID="register rw"
EXPECTED_DEVICE_COUNT="${EXPECTED_DEVICE_COUNT:-8}"

# -------- 参数配置 --------
log INFO "$CHECK_ID" "正在加载配置参数..."

# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

# 解析参数
while [[ $# -gt 0 ]]; do
  case "$1" in
    # --- main ---
    --reg_ids=*)
      REG_IDS="${1#*=}"
      shift
      ;;
    --reg_ids|-r)
      REG_IDS="${2:-"0,1,2,3,4,5,6,7"}"  # 默认值为write
      shift 2
      ;;

    # --- 结束符 ---
    --)
      shift
      break
      ;;

    # --- 未知选项 ---
    -*)
      echo "# WARNING: 未知参数已忽略：$(printf '%q' "$1")" >&2
      shift
      ;;
      
  esac
done

# 配置参数
config_require_keys \
    "REG_PATH|请输入要读取或者修改的寄存器命令文件地址（注释或其他命令自行删除）||0|validate_non_empty"

# -------- main --------
log INFO "$CHECK_ID" "访问修改NPU寄存器值"

# 创建临时文件记录Python进程PID
PID_FILE=$(mktemp)
trap 'rm -f "$PID_FILE"' EXIT

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_FILE="${SAVE_PATH}/reg_rw_${TIMESTAMP}.txt"

log INFO "$CHECK_ID" "读取寄存器命令文件： $REG_PATH"

python $(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/reg_rw.py \
        --reg-path $REG_PATH --reg-ids $REG_IDS 2>&1 | tee "$OUTPUT_FILE"

log INFO "$CHECK_ID" "访问修改完成，结果保存在 $OUTPUT_FILE"
