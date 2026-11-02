#!/usr/bin/env bash
# 修复 ranktable.json 中 rank_id 与 NPU 逻辑 ID 映射不一致的问题
# 思路：
#   1. 从 /proc/uda/namespace_node 获取 logic_devid <-> udevid 映射
#   2. 前提 udevid == ranktable.json 里的 device_id
#   3. 将每个条目的 rank_id 改成对应的 logic_devid
#   4. 不修改原文件，输出到新的 json 文件，并打印新文件路径
# 
# 环境变量：
#   UDA_PROC_FILE  覆盖默认的 /proc/uda/namespace_node
#   PY_FIX_SCRIPT  覆盖默认的同目录 fix_ranktable_from_uda.py

# set -euo pipefail

# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "${PARENT_DIR}/config_util.sh"

config_require_keys \
    "IN_RANKTABLE_PATH|请输入ranktable.json文件路径|/home/hccl/ranktable_rack0_0_7.json|0|validate_non_empty" \
    "OUT_RANKTABLE_PATH|请输入保存新ranktable.json文件的路径|/home/hccl/new_ranktable.json|0|validate_non_empty"

PROC_FILE="${UDA_PROC_FILE:-/proc/uda/namespace_node}"

IN_PATH=$IN_RANKTABLE_PATH
OUT_PATH=$OUT_RANKTABLE_PATH

if [[ ! -f "$IN_PATH" ]]; then
  echo "ERROR: 输入 ranktable 文件不存在: $IN_PATH" >&2
  exit 1
fi

if [[ ! -r "$PROC_FILE" ]]; then
  echo "ERROR: 无法读取 $PROC_FILE（可用 UDA_PROC_FILE 覆盖路径）" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: 未找到 python3，请安装或加入 PATH" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "SCRIPT_DIR:${SCRIPT_DIR}"
PY_SCRIPT="${PY_FIX_SCRIPT:-$SCRIPT_DIR/fix_ranktable_from_uda.py}"

if [[ ! -f "$PY_SCRIPT" ]]; then
  echo "ERROR: 未找到 Python 脚本: $PY_SCRIPT" >&2
  echo "       请确认 fix_ranktable_from_uda.py 与本 sh 放在同一目录，或用 PY_FIX_SCRIPT 指定路径" >&2
  exit 1
fi

# 默认输出路径：与输入同目录，文件名加 fixed 时间戳
if [[ -z "$OUT_PATH" ]]; then
  base="$(basename "$IN_PATH")"
  dir="$(cd "$(dirname "$IN_PATH")" && pwd)"
  ts="$(date +%Y%m%d_%H%M%S)"
  OUT_PATH="$dir/${base%.json}.fixed_${ts}.json"
fi

echo "# INFO: UDA 映射文件: $PROC_FILE" >&2
echo "# INFO: 输入 ranktable: $IN_PATH" >&2
echo "# INFO: 输出新文件  : $OUT_PATH" >&2

python3 "$PY_SCRIPT" "$PROC_FILE" "$IN_PATH" "$OUT_PATH"

echo
echo "✅ 已生成修复后的 ranktable 文件"
echo "👉 请使用这个新文件路径（原文件未被修改）:"
echo "export RANK_TABLE_FILE=$OUT_PATH"