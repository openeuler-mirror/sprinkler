#!/usr/bin/env bash
# 公共日志库：补齐历史上缺失的 sprinkler/auto/common.sh
# 用法: log LEVEL CHECK_ID "message"
# 本文件同时转发 config_util.sh，保证只 source 本文件的脚本仍可用 config_require_keys。

_COMMON_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "${_COMMON_SH_DIR}/config_util.sh" ]; then
    # shellcheck source=/dev/null
    . "${_COMMON_SH_DIR}/config_util.sh"
fi

_common_now() { date '+%Y-%m-%d %H:%M:%S'; }

_COMMON_COLOR_OFF=$'\033[0m'
_COMMON_COLOR_OK=$'\033[32m'
_COMMON_COLOR_WARN=$'\033[33m'
_COMMON_COLOR_FAIL=$'\033[31m'
_COMMON_COLOR_INFO=$'\033[36m'
_COMMON_COLOR_DEBUG=$'\033[34m'

log() {
    local level="${1:-INFO}"
    local check_id="${2:-LOG}"
    shift || true
    shift || true
    local msg="$*"

    local color="$_COMMON_COLOR_OFF"
    case "$level" in
        OK)         color="$_COMMON_COLOR_OK" ;;
        WARN)       color="$_COMMON_COLOR_WARN" ;;
        FAIL|ERROR) color="$_COMMON_COLOR_FAIL" ;;
        INFO)       color="$_COMMON_COLOR_INFO" ;;
        DEBUG)      color="$_COMMON_COLOR_DEBUG" ;;
    esac

    printf "%s | %b%-5s%b | %s | %s\n" "$(_common_now)" "$color" "$level" "$_COMMON_COLOR_OFF" "$check_id" "$msg"
}
