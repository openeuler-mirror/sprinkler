#!/usr/bin/env bash
# 公共配置库：config_util.sh

# 配置文件路径（可用 CONFIG_FILE 覆盖）
ROOT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GRANDPARENT_DIR="$(dirname "$(dirname "$(dirname "$ROOT_SCRIPT_DIR")")")"
CONFIG_UTIL_ROOT=${GRANDPARENT_DIR}/sprinkler_config
mkdir -p "$CONFIG_UTIL_ROOT"
CONFIG_FILE="${CONFIG_FILE:-${CONFIG_UTIL_ROOT}/config.conf}"


# 工具函数：去掉字符串首尾空
_trim() {
    local s="${1-}"
    # 前导
    s="${s#"${s%%[![:space:]]*}"}"
    # 尾部
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}


# 读取配置：config_get KEY
# - 输出对应 value（不存在返回 1）
config_get() {
    local key="$1"
    [[ -f "$CONFIG_FILE" ]] || return 1

    local line value
    line=$(grep -E "^[[:space:]]*$key[[:space:]]*=" "$CONFIG_FILE" | head -n1 || true)
    [[ -z "$line" ]] && return 1

    value="${line#*=}"
    value=$(_trim "$value")
    printf '%s\n' "$value"
}


# 写入/更新配置：config_set KEY VALUE
# - 不存在则追加
# - 存在则原位修改
#   说明：VALUE 里不要放换行；不要放空格、= 最保险
config_set() {
    local key="$1"
    local value="$2"

    mkdir -p "$(dirname "$CONFIG_FILE")"

    if [[ ! -f "$CONFIG_FILE" ]]; then
        {
            echo "# set shared config"
            echo "# 创建时间: $(date '+%Y-%m-%d %H:%M:%S')"
            echo
        } >"$CONFIG_FILE"
    fi

    # 对 value 做 sed 安全转义
    local esc
    esc=$(printf '%s\n' "$value" | sed -e 's/[&]/\\&/g')

    if grep -qE "^[[:space:]]*$key[[:space:]]*=" "$CONFIG_FILE"; then
        # 覆盖已有
        sed -i -E "s|^[[:space:]]*$key[[:space:]]*=.*|$key=$esc|" "$CONFIG_FILE"
    else
        # 追加
        printf '%s=%s\n' "$key" "$esc" >>"$CONFIG_FILE"
    fi
}


# 删除配置：config_del KEY
config_del() {
    local key="$1"
    [[ -f "$CONFIG_FILE" ]] || return 0
    sed -i -E "/^[[:space:]]*$key[[:space:]]*=/d" "$CONFIG_FILE"
}


# 列出全部配置：config_list
config_list() {
    if [[ -f "$CONFIG_FILE" ]]; then
        cat "$CONFIG_FILE"
    else
        echo "# 尚未存在配置文件：$CONFIG_FILE"
    fi
}


# 校验函数
validate_non_empty() {
    [[ -n "${1:-}" ]]
}

validate_ip() {
    local ip="$1"
    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    local a b c d
    IFS='.' read -r a b c d <<<"$ip"
    for o in "$a" "$b" "$c" "$d"; do
        (( o >= 0 && o <= 255 )) || return 1
    done
}

validate_port() {
    local p="$1"
    [[ "$p" =~ ^[0-9]+$ ]] || return 1
    (( p >= 1 && p <= 65535 ))
}

validate_int() {
    local p="$1"
    [[ "$p" =~ ^[0-9]+$ ]]
}

# 交互式获取并写入配置：
# prompt_and_save KEY "提示语" "默认值" secret_flag validate_func
# - secret_flag=1 时用隐藏输入（密码）
# - validate_func 可以是上面的 validate_xxx，也可以为空
prompt_and_save() {
    local key="$1"
    local prompt="$2"
    local default="${3:-}"
    local secret="${4:-0}"
    local validate_func="${5:-}"

    local current
    current=$(config_get "$key" 2>/dev/null || true)

    local show_default="$default"
    [[ -n "$current" ]] && show_default="$current"

    local input
    if [[ "$secret" -eq 1 ]]; then
        if [[ -n "$show_default" ]]; then
            read -rsp "$prompt [$show_default]: " input
            echo
        else
            read -rsp "$prompt: " input
            echo
        fi
    else
        if [[ -n "$show_default" ]]; then
            read -rp "$prompt [$show_default]: " input
        else
            read -rp "$prompt: " input
        fi
    fi

    [[ -z "$input" ]] && input="$show_default"

    # 校验
    if [[ -n "$validate_func" ]]; then
        if ! "$validate_func" "$input"; then
            echo "❌ 配置项 $key 校验失败：$input" >&2
            exit 1
        fi
    fi

    config_set "$key" "$input"
}


########################################
# 处理单个 key：
# _ensure_one_key KEY PROMPT DEFAULT SECRET VALIDATOR
#
# - 如果 config 中已有并且 validator 通过 → 不提示，直接 export
# - 否则循环提示用户输入，直到合法，写入 config 并 export
#
# SECRET:
#   0 = 普通明文输入
#   1 = 隐藏输入（密码）
########################################
_ensure_one_key() {
    local key="$1"
    local prompt="$2"
    local default="${3:-}"
    local secret="${4:-0}"
    local validator="${5:-}"

    # 先从 config 里读
    local current=""
    if current=$(config_get "$key" 2>/dev/null); then
        current="$current"
    else
        current=""
    fi

    # 情况 1：已经有值且校验通过 → 不打扰用户，直接用
    if [[ -n "$current" ]]; then
        if [[ -z "$validator" ]] || "$validator" "$current"; then
            export "$key=$current"
            return 0
        fi
        # 有旧值但不合法 → 继续往下走，提示用户重新输入
    fi

    # 情况 2：没有值 / 值不合法 → 进入交互
    while true; do
        local show_default=""
        # 优先用 current，当作“之前输入过但不合法”的提示
        if [[ -n "$current" ]]; then
            show_default="$current"
        else
            show_default="$default"
        fi

        # ====== 在真正 read 之前，先给用户清晰提示 ======
        echo
        echo "配置项：$key"
        # [[ -n "$prompt" ]] && echo "$prompt:"
        if [[ -n "$show_default" ]]; then
            [[ -n "$prompt" ]] && echo "$prompt(提示：直接回车将使用默认值 [$show_default]): "
        else
            [[ -n "$prompt" ]] && echo "$prompt(提示：此项没有默认值，不能为空。): "
        fi
        # echo

        local input=""
        if [[ "$secret" -eq 1 ]]; then
            # 隐藏输入（密码）
            if [[ -n "$show_default" ]]; then
                # echo "$prompt"
                # echo "    （输入不回显，直接回车将使用默认值：$show_default）"
                read -rs "input"
                echo
            else
                # echo "$prompt"
                # echo "    （输入不回显，不能为空）"
                read -rs "input"
                echo
            fi
        else
            if [[ -n "$show_default" ]]; then
                # 有默认值：把“回车=默认”说清楚
                read -r "input"
            else
                # 没默认值：简单直说
                read -r "input"
            fi
        fi

        # === 用户没输入内容的情况 ===
        if [[ -z "$input" ]]; then
            if [[ -n "$show_default" ]]; then
                # 有默认值 → 使用默认值
                input="$show_default"
            else
                # 没默认值
                echo "❌ 配置项 $key 不能为空，请重新输入。"
                continue
            fi
        fi

        # === 校验输入 ===
        if [[ -z "$validator" ]] || "$validator" "$input"; then
            config_set "$key" "$input"
            export "$key=$input"
            break
        else
            echo "❌ 输入无效，请重新输入。"
            # 把本次输入记为 current，下轮作为默认提示
            current="$input"
        fi
        echo
    done
}

########################################
# 核心：按需确保一批 key 存在并合法
#
# config_require_keys "规范字符串1" "规范字符串2" ...
#
# 规范字符串格式（用竖线 | 分隔，避免和中文冲突）：
#   KEY|提示语|默认值|secret标志|校验函数名
#
# 说明：
#   - 提示语、默认值可以为空，但竖线位置要保留
#   - secret标志: 0=普通输入, 1=隐藏输入（密码）
#   - 校验函数可以是上面定义的 validate_xxx，也可以留空
#
# 示例：
#   config_require_keys \
#     "FD_USERNAME|请输入用户名||0|validate_non_empty" \
#     "FD_IP|请输入目标IP||0|validate_ip" \
#     "FD_PORT|请输入端口(默认22)|22|0|validate_port" \
#     "FD_PASSWORD|请输入密码(输入不显示)||1|validate_non_empty"
########################################
config_require_keys() {
    echo "参数配置路径：$CONFIG_FILE"
    local spec
    for spec in "$@"; do
        # 防止 IFS 影响全局，只在 read 里生效
        local key prompt default secret validator
        IFS='|' read -r key prompt default secret validator <<<"$spec"

        : "${default:=}"
        : "${secret:=0}"
        : "${validator:=}"

        _ensure_one_key "$key" "$prompt" "$default" "$secret" "$validator"
    done
}


# 高层封装：初始化一套通用连接参数
# - 多个 fastdiag_xxx.sh 都可以用同一套 KEY：
#   FD_USERNAME / FD_PASSWORD / FD_IP / FD_PORT
config_init_common_conn() {
    echo "====== 通用连接参数检查 ======"
    prompt_and_save "FD_USERNAME" "请输入用户名" "" 0 validate_non_empty
    prompt_and_save "FD_IP"       "请输入目标 IP 地址" "" 0 validate_ip
    prompt_and_save "FD_PORT"     "请输入端口号" "22" 0 validate_port
    prompt_and_save "FD_PASSWORD" "请输入登录密码（输入时不显示）" "" 1 validate_non_empty

    echo "✅ 通用连接参数已写入：$CONFIG_FILE"
    echo
}


# 可选：通用“连接配置”快捷函数
# 只有需要的工具才调用，不需要的工具就别调用
config_require_common_conn() {
    config_require_keys \
      "USER|请输入连接用户名||0|validate_non_empty" \
      "POD_IP|请输入目标IP地址||0|validate_ip" \
      "POD_PORT|请输入SSH端口(默认22)|22|0|validate_port" \
      "PASSWORD|请输入登录密码(输入不回显)||1|validate_non_empty"
}