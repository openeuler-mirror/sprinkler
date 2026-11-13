#!/usr/bin/env bash
# Check BIOS High Performance Mode via /sys/usage
# Rule:
#   - "cat /sys/usage" prints lots of numbers => NOT high performance mode
#   - "No such file or directory" => high performance mode ENABLED
CHECK_ID="bios_perf"

# 引入公共库（支持被单独运行/被总控调用）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../sprinkler/utils/common.sh"

check_bios() {
  local pattern="/sys/devices/system/cpu/cpu*/cpuidle/state2/usage"

  log INFO "$CHECK_ID" "开始检查 BIOS 是否开启【高性能模式】（读取：${pattern}）"

  # 让通配符在没有匹配时返回空（避免 pattern 原样进入数组）
  shopt -s nullglob
  local files=($pattern)
  shopt -u nullglob

  # 没有任何 usage 文件 => 等价于 “no such file” => 高性能模式已开启
  if [ "${#files[@]}" -eq 0 ]; then
    log OK "$CHECK_ID" "未找到 ${pattern}（无 usage 文件），判定：高性能模式【已开启】"
    return 0
  fi

  # 读取所有 usage 文件
  local out rc
  out="$(cat "${files[@]}" 2>&1)"
  rc=$?

  if [ $rc -ne 0 ]; then
    log WARN "$CHECK_ID" "读取 ${pattern} 失败（返回码 rc=$rc），首行错误：$(echo "$out" | head -n 1)"
    return 2
  fi

  # 有数字输出 => 打一堆数字 => 非高性能模式
  if echo "$out" | grep -qE '[0-9]'; then
    local lines preview
    lines="$(echo "$out" | wc -l | tr -d ' ')"
    preview="$(echo "$out" | head -n 5 | tr '\n' ';' | sed 's/[[:space:]]\+/ /g')"
    log FAIL "$CHECK_ID" "检测到 usage 数字输出（匹配到 ${#files[@]} 个文件，输出 ${lines} 行），判定：高性能模式【未开启】；示例(前5行)：${preview}"
    return 1
  fi

  # 有文件但没有数字，属于异常/不符合预期
  log WARN "$CHECK_ID" "找到了 ${#files[@]} 个 usage 文件，但输出未检测到数字，结果不符合预期；示例：$(echo "$out" | head -n 3 | tr '\n' ';')"
  return 2
}

check_bios "$@"
