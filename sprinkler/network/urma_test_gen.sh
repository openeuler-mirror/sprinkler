#!/usr/bin/env bash
# 根据 npu_ip_map.conf 生成 URMA 打流命令
#
# 约定：
#   ./urma_test_gen.sh <client_range> <server_range>
#   - CPU 用 -1 表示
#   - NPU 用 0 ~ (pair_device_num-1) 表示
#
# 用法示例：
#   # CPU 打 NPU7
#   ./urma_test_gen.sh -1 7
#
#   # NPU1~7 打 NPU0（多打一）
#   ./urma_test_gen.sh 1-7 0
#
#   # NPU0 打 NPU1~7（一打多）
#   ./urma_test_gen.sh 0 1-7
#
#   ./urma_test_gen.sh 1,2,3 0
#   ./urma_test_gen.sh 0 1,3,5-7
#   ./urma_test_gen.sh -1,0,2 7

#   - range 支持：
#       单卡：0 / -1
#       区间：1-7
#       列表：1,2,3
#       混合：0,2,3-5
# 依赖：
#   - /lib/route.conf
#   - npu_ip_map.conf（没有或不完整则自动调用 gen_npu_ip_map.sh + update_udma_in_map.sh）
#   - gen_npu_ip_map.sh / update_udma_in_map.sh 在同目录且可执行
#   - python3（用于 NPU-NPU 拓扑时解析 ranktable / topo）
#   - env 中可找到 RANK_TABLE_FILE  或 ROOTINFO_FILE（作为 ranktable.json 等价输入）、HCCL_TOPO_FILE_PATH（如果有 NPU-NPU 打流）
#   - ssh/scp 可用于 CPU <-> NPU
#
# 并发 & 规则：
#   - 多(client)打一(server)：同一 server 需要多条等待命令 -> server 侧命令行末尾加 '&'
#   - 一(client)打多(server)：同一 client 需要多条发送命令 -> client 侧命令行末尾加 '&'
# 打流程序：
#   - 本地 CPU：URMA_TEST_LOCAL_BIN（默认：/root/urma_perftest_2M）
#   - NPU 上： URMA_TEST_REMOTE_BIN（默认：/usr/bin/urma_perftest_2M）
#   - 生成命令中会直接调用这些路径
#
# 注意：本脚本只生成命令，不实际执行 urma_perftest_2M。

test_type=${3:-write}

# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

config_require_keys \
    "HCCL_TOPO_FILE_PATH|请输入topo的json配置文件路径|/etc/superpod_1d_noroce.json|0|" \
    "ROOTINFO_FILE|请输入rootinfo的json配置文件路径|/etc/hccl_rootinfo.json|0|" \
    "POD_NPU_USER|请输入NPU的ssh连接用户名|root|0|validate_non_empty"

export HCCL_TOPO_FILE_PATH=$HCCL_TOPO_FILE_PATH
export ROOTINFO_FILE=$ROOTINFO_FILE
CPU_USER=$POD_USER
SSH_USER=$POD_NPU_USER

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NPU_IP_MAP_CONF="${NPU_IP_MAP_CONF:-${SCRIPT_DIR}/npu_ip_map.conf}"
# 直接删除文件（如果存在）
[[ -f "$NPU_IP_MAP_CONF" ]] && rm -f "$NPU_IP_MAP_CONF" && echo "已删除旧配置文件: $NPU_IP_MAP_CONF"

ROUTE_CONF="/lib/route.conf"

MAP_GEN_SCRIPT="${MAP_GEN_SCRIPT:-${SCRIPT_DIR}/gen_npu_ipv6_map.sh}"
UDMA_UPDATE_SCRIPT="${UDMA_UPDATE_SCRIPT:-${SCRIPT_DIR}/update_udma_in_map.sh}"

chmod +x ${SCRIPT_DIR}/*

SSH_USER="${SSH_USER:-root}"

# 小工具：规范化路径，支持 ~ 开头
normalize_path() {
    local p="$1"
    # 去掉可能的结尾空白/回车
    p="${p%"${p##*[![:space:]]}"}"

    if [[ "$p" == "~" ]]; then
        printf '%s\n' "$HOME"
    elif [[ "$p" == ~/* ]]; then
        printf '%s/%s\n' "$HOME" "${p#~/}"
    else
        printf '%s\n' "$p"
    fi
}
# 打流程序路径（可用环境变量覆盖）
URMA_TEST_LOCAL_BIN="${URMA_TEST_LOCAL_BIN:-/usr/local/sbin/urma_perftest}"
URMA_TEST_LOCAL_BIN="$(normalize_path "$URMA_TEST_LOCAL_BIN")"
URMA_TEST_REMOTE_BIN="${URMA_TEST_REMOTE_BIN:-/usr/bin/urma_perftest}"

# ============================================================
# 小工具：EID / IP / ENV / RANGE 解析
# ============================================================

eid_normalize() {
    local eid="$1"
    eid="${eid#0x}"
    eid="${eid#0X}"
    eid="${eid,,}"
    if ((${#eid} > 32)); then
        eid="${eid: -32}"
    fi
    printf '%s' "$eid"
}

eid_suffix() {
    local eid
    eid="$(eid_normalize "$1")"
    printf '%s' "${eid: -4}"
}

eid_to_ip() {
    local eid
    eid="$(eid_normalize "$1")"
    local last8="${eid: -8}"
    if ((${#last8} != 8)); then
        echo "0.0.0.0"
        return 1
    fi
    local b1="${last8:0:2}"
    local b2="${last8:2:2}"
    local b3="${last8:4:2}"
    local b4="${last8:6:2}"

    local o1 o2 o3 o4
    o1=$((16#$b1))
    o2=$((16#$b2))
    o3=$((16#$b3))
    o4=$((16#$b4))
    if [[ $o2 -eq 223 ]]; then
        printf '%d.%d.%d.%d\n' "$o1" "$o2" "$o3" "$o4"
    else
        printf '%d.%d.%d.%d\n' "223" "$o1" "$o2" "$o3"
    fi
}

get_env_var_from_env_cmd() {
    local var_name="$1"
    local cur="${!var_name-}"
    if [[ -n "$cur" ]]; then
        printf '%s' "$cur"
        return 0
    fi
    local line
    while IFS= read -r line; do
        case "$line" in
            "${var_name}="*)
                printf '%s' "${line#${var_name}=}"
                return 0
                ;;
        esac
    done < <(env)
    return 1
}

# -----------------------------
# range 解析：支持 "0" "1-7" "-1" "0,2,3-5"
# -----------------------------
parse_range() {
    local expr="$1"
    local -a out=()

    # 支持：0 / 1-7 / -1 / 1,2,3 / 1,3,5-7（允许有空格）
    expr="${expr//[[:space:]]/}"
    if [[ -z "$expr" ]]; then
        echo "# ERROR: range 为空" >&2
        exit 1
    fi

    local -a parts=()
    IFS=',' read -r -a parts <<< "$expr"

    local part
    for part in "${parts[@]}"; do
        [[ -z "$part" ]] && continue

        if [[ "$part" =~ ^-?[0-9]+$ ]]; then
            out+=("$part")
        elif [[ "$part" =~ ^(-?[0-9]+)-(-?[0-9]+)$ ]]; then
            local a="${BASH_REMATCH[1]}"
            local b="${BASH_REMATCH[2]}"
            local i
            if ((a <= b)); then
                for ((i = a; i <= b; i++)); do out+=("$i"); done
            else
                for ((i = a; i >= b; i--)); do out+=("$i"); done
            fi
        else
            echo "# ERROR: 无法解析范围表达式片段: $part (来自: $expr)" >&2
            exit 1
        fi
    done

    # 去重（保序），避免重复生成命令
    local -A seen=()
    local -a uniq=()
    local x
    for x in "${out[@]}"; do
        if [[ -z "${seen[$x]+_}" ]]; then
            seen[$x]=1
            uniq+=("$x")
        fi
    done

    printf '%s\n' "${uniq[@]}"
}

# ============================================================
# 1. 检查 /lib/route.conf 和 pair_device_num，算 CPU/NPU IP
# ============================================================

if [[ ! -r "$ROUTE_CONF" ]]; then
    echo "# ERROR: $ROUTE_CONF 不存在或不可读" >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$ROUTE_CONF"

if [[ -z "${pair_device_num:-}" ]]; then
    echo "# ERROR: route.conf 中未定义 pair_device_num" >&2
    exit 1
fi
PAIR_NUM=$((pair_device_num))

declare -a CPU_IP_PER_PAIR   # 每个 pair 对应的 CPU IP（local_eid -> IP）
declare -a NPU_MGMT_IP       # 每个 pair 对应的 NPU 管理 IP（remote_eid -> IP）

for ((i = 0; i < PAIR_NUM; i++)); do
    local_key="pair${i}_chan0_local_eid"
    remote_key="pair${i}_chan0_remote_eid"

    local_eid="${!local_key:-}"
    remote_eid="${!remote_key:-}"

    if [[ -z "$local_eid" || -z "$remote_eid" ]]; then
        echo "# ERROR: route.conf 缺少 ${local_key} 或 ${remote_key}" >&2
        exit 1
    fi

    CPU_IP_PER_PAIR[i]="$(eid_to_ip "$local_eid")"
    NPU_MGMT_IP[i]="$(eid_to_ip "$remote_eid")"
done

# ============================================================
# 2. 确认本地打流程序存在
# ============================================================

echo "# INFO: 本地 URMA 测试程序路径: $URMA_TEST_LOCAL_BIN" >&2

if [[ ! -f "$URMA_TEST_LOCAL_BIN" ]]; then
    echo "# ERROR: 未找到本地 URMA 测试程序文件: $URMA_TEST_LOCAL_BIN" >&2
    echo "#        请确认路径是否正确（注意：变量里不要只写 ~，要写完整路径或用绝对路径）" >&2
    # exit 1
fi

if [[ ! -x "$URMA_TEST_LOCAL_BIN" ]]; then
    echo "# WARNING: 文件存在但不可执行，尝试自动 chmod +x ..." >&2
    if chmod +x "$URMA_TEST_LOCAL_BIN" 2>/dev/null; then
        echo "# INFO: 已为 $URMA_TEST_LOCAL_BIN 添加执行权限" >&2
    else
        echo "# ERROR: 无法为 $URMA_TEST_LOCAL_BIN 添加执行权限，请手动执行: chmod +x \"$URMA_TEST_LOCAL_BIN\"" >&2
        # exit 1
    fi
fi

# ============================================================
# 3. 确保 npu_ip_map.conf 完整，如不完整则自动生成
# ============================================================

conf_is_complete() {
    [[ -r "$NPU_IP_MAP_CONF" ]] || return 1
    # shellcheck disable=SC1090
    source "$NPU_IP_MAP_CONF"

    local n="$PAIR_NUM"
    # HOST 基本信息
    (( ${#HOST_EID_LIST[@]}    >= n )) || return 1
    (( ${#HOST_IP_LIST[@]}     >= n )) || return 1
    (( ${#HOST_UDMA_LIST[@]}   >= n )) || return 1
    (( ${#HOST_EIDIDX_LIST[@]} >= n )) || return 1

    # NPU 每个都有 EID/IP/UDMA/EIDIDX
    local i lenE lenI lenU lenX
    for ((i = 0; i < n; i++)); do
        eval "lenE=\${#NPU${i}_EID_LIST[@]}"
        eval "lenI=\${#NPU${i}_IP_LIST[@]}"
        eval "lenU=\${#NPU${i}_UDMA_LIST[@]}"
        eval "lenX=\${#NPU${i}_EIDIDX_LIST[@]}"
        (( lenE >= 1 && lenI >= lenE && lenU >= lenE && lenX >= lenE )) || return 1
    done
    return 0
}

regen_conf() {
    echo "# INFO: 尝试重新生成完整的 npu_ip_map.conf ..." >&2
    if [[ ! -x "$MAP_GEN_SCRIPT" ]]; then
        echo "# ERROR: 找不到或无法执行 gen 脚本: $MAP_GEN_SCRIPT" >&2
        exit 1
    fi
    if [[ ! -x "$UDMA_UPDATE_SCRIPT" ]]; then
        echo "# ERROR: 找不到或无法执行 UDMA 更新脚本: $UDMA_UPDATE_SCRIPT" >&2
        exit 1
    fi

    "$MAP_GEN_SCRIPT"
    "$UDMA_UPDATE_SCRIPT"
}

if ! conf_is_complete; then
    regen_conf
    if ! conf_is_complete; then
        echo "# ERROR: 重新生成后 npu_ip_map.conf 依然不完整，请检查上游脚本" >&2
        exit 1
    fi
fi

# 再 source 一次，拿到最新变量
# shellcheck disable=SC1090
source "$NPU_IP_MAP_CONF"

# ============================================================
# 4. 解析命令行参数
# ============================================================

if [ "$#" -ne 2 ] && [ "$#" -ne 3 ]; then
    cat >&2 <<EOF
用法:
  $0 <client_range> <server_range> ?<model>

说明:
  - CPU 用 -1 表示
  - NPU 用 0 ~ $((PAIR_NUM-1)) 表示
  - 支持单个或范围:
      $0 -1 7       # CPU 打 NPU7
      $0 1-7 0      # NPU1~7 打 NPU0
      $0 0 1-7      # NPU0 打 NPU1~7
EOF
    exit 1
fi

mapfile -t CLIENTS < <(parse_range "$1")
mapfile -t SERVERS < <(parse_range "$2")

check_idx_valid() {
    local x="$1"
    if ((x == -1)); then
        return 0
    fi
    if ((x < 0 || x >= PAIR_NUM)); then
        echo "# ERROR: 非法设备索引: $x (合法范围: -1 或 0~$((PAIR_NUM-1)))" >&2
        exit 1
    fi
}

for c in "${CLIENTS[@]}"; do check_idx_valid "$c"; done
for s in "${SERVERS[@]}"; do check_idx_valid "$s"; done

# ============================================================
# 5. 收集所有需要参与打流的 NPU，提前下发 urma_test
# ============================================================

declare -a USED_NPUS=()

add_used_npu() {
    local idx="$1"
    ((idx < 0)) && return 0
    local k
    for k in "${USED_NPUS[@]}"; do
        ((k == idx)) && return 0
    done
    USED_NPUS+=("$idx")
}

for c in "${CLIENTS[@]}"; do add_used_npu "$c"; done
for s in "${SERVERS[@]}"; do add_used_npu "$s"; done

# 计算 CPU 上 NPU_IP_MAP_CONF 的绝对路径（给 NPU scp 用）
if REAL_CONF_PATH=$(readlink -f "$NPU_IP_MAP_CONF" 2>/dev/null); then
    :
else
    REAL_CONF_PATH="$NPU_IP_MAP_CONF"
fi

# 在 NPU 上：如果没有 URMA_TEST_REMOTE_BIN，则用 NPU 自己 scp CPU 上的 URMA_TEST_LOCAL_BIN
#   1）先检查 NPU 是否已经可以免密 ssh 到 CPU_USER@CPU_IP；
#   2）如果没有，则自动在 NPU 上生成 root 的 ssh key，并把公钥追加到 CPU_USER 的 authorized_keys；
#   3）然后再由 NPU 自己 scp 从 CPU 拉取 URMA_TEST_LOCAL_BIN 到 URMA_TEST_REMOTE_BIN。
for npu in "${USED_NPUS[@]}"; do
    cpu_ip="${CPU_IP_PER_PAIR[npu]}"
    npu_ip="${NPU_MGMT_IP[npu]}"

    if [[ -z "$cpu_ip" || -z "$npu_ip" ]]; then
        echo "# WARNING: NPU${npu} 的 CPU_IP 或 NPU_IP 为空，无法下发 urma_perftest" >&2
        continue
    fi

    echo "# INFO: 检查 NPU${npu}(${npu_ip}) 连通性..." >&2
    
    # 先ping检测连通性
    if ! ping -c 1 -W 2 "$npu_ip" >/dev/null 2>&1; then
        echo "# WARNING: NPU${npu}(${npu_ip}) ping 不通，跳过该NPU的处理" >&2
        continue
    fi

    echo "# INFO: 检查 NPU${npu}(${npu_ip}) 是否已经配置对 CPU(${cpu_ip}) 的免密..." >&2

    # 1）先测试：在 NPU 上执行 ssh CPU_USER@CPU_IP true，BatchMode=yes 表示不允许交互输密码
    if timeout 30s ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${SSH_USER}@${npu_ip}" \
           "timeout 10s ssh -o BatchMode=yes -o ConnectTimeout=5 ${CPU_USER}@${cpu_ip} true" \
           </dev/null 2>/dev/null; then
        echo "# INFO: NPU${npu} 已经可以免密访问 CPU ${CPU_USER}@${cpu_ip}" >&2
    else
        echo "# INFO: NPU${npu} 尚未配置免密，开始自动配置 NPU(root) → CPU(${CPU_USER}@${cpu_ip}) ..." >&2

        # 2）在 NPU 上生成 root 的 ssh key（如果没有），并把公钥打印出来
        npu_pubkey="$(timeout 30s ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "${SSH_USER}@${npu_ip}" '
timeout 15s bash -c "
if [ \"\$(id -u)\" -ne 0 ]; then
  # 当前不是 root，用 su - root 执行一串命令
  su - root -c \"mkdir -p /root/.ssh && chmod 700 /root/.ssh && { [ -f /root/.ssh/id_rsa ] || ssh-keygen -q -t rsa -N \\\"\\\" -f /root/.ssh/id_rsa >/dev/null 2>&1; }; cat /root/.ssh/id_rsa.pub\"
else
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  if [ ! -f ~/.ssh/id_rsa ]; then
    ssh-keygen -q -t rsa -N \"\" -f ~/.ssh/id_rsa >/dev/null 2>&1
  fi
  cat ~/.ssh/id_rsa.pub
fi
"')"

        # 只取最后一行，去掉回车
        npu_pubkey="$(echo "$npu_pubkey" | tail -n 1 | tr -d '\r')"

        if [[ -z "$npu_pubkey" ]]; then
            echo "# ERROR: 无法从 NPU${npu} 获取 ssh 公钥，免密配置失败" >&2
            continue  # 跳过这个NPU的后续处理
        else
            # 3）在 CPU 上把这个公钥追加进 CPU_USER 的 authorized_keys
            cpu_home="$(eval echo "~${CPU_USER}" 2>/dev/null || true)"
            if [[ -z "$cpu_home" || ! -d "$cpu_home" ]]; then
                echo "# ERROR: 无法获取 CPU 用户 ${CPU_USER} 的 home 目录，免密配置失败" >&2
                continue  # 跳过这个NPU的后续处理
            else
                cpu_ssh_dir="${cpu_home}/.ssh"
                cpu_auth_keys="${cpu_ssh_dir}/authorized_keys"
                mkdir -p "$cpu_ssh_dir"
                chmod 700 "$cpu_ssh_dir"
                touch "$cpu_auth_keys"
                chmod 600 "$cpu_auth_keys"

                if grep -qxF "$npu_pubkey" "$cpu_auth_keys" 2>/dev/null; then
                    echo "# INFO: NPU${npu} 的公钥已经存在于 ${cpu_auth_keys}" >&2
                else
                    echo "$npu_pubkey" >> "$cpu_auth_keys"
                    echo "# INFO: 已将 NPU${npu} 的公钥追加到 ${cpu_auth_keys}" >&2
                fi

                # 如果当前脚本以 root 身份运行，确保 .ssh 归属于 CPU_USER
                if [[ "$(id -u)" -eq 0 && "$CPU_USER" != "root" ]]; then
                    chown -R "${CPU_USER}:${CPU_USER}" "$cpu_ssh_dir"
                fi
            fi
        fi
    fi

    echo "# INFO: 检查 NPU${npu}(${npu_ip}) 是否已存在 ${URMA_TEST_REMOTE_BIN} ..." >&2

    # 4）真正下发 urma_test：在 NPU 上（root），从 CPU_USER@CPU_IP 拉取 URMA_TEST_LOCAL_BIN 到 URMA_TEST_REMOTE_BIN
    if ! timeout 60s ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no \
        "${SSH_USER}@${npu_ip}" \
        "timeout 30s bash -c \"
        if [ ! -x '$URMA_TEST_REMOTE_BIN' ]; then
            echo '# INFO: 在 NPU${npu} 上未找到 ${URMA_TEST_REMOTE_BIN}，从 CPU(${cpu_ip}) 拉取 ...'
            if [ \\\"\\\$(id -u)\\\" -ne 0 ]; then
                # 切 root 再 scp
                su - root -c \\\"timeout 30s scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no ${CPU_USER}@${cpu_ip}:${URMA_TEST_LOCAL_BIN} '$URMA_TEST_REMOTE_BIN' && chmod +x '$URMA_TEST_REMOTE_BIN'\\\"
            else
                timeout 30s scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no ${CPU_USER}@${cpu_ip}:${URMA_TEST_LOCAL_BIN} '$URMA_TEST_REMOTE_BIN'
                chmod +x '$URMA_TEST_REMOTE_BIN'
            fi
        else
            echo '# INFO: NPU${npu} 上已存在 ${URMA_TEST_REMOTE_BIN}，跳过下发'
        fi
        \"" 2>/dev/null; then
        echo "# WARNING: 无法在 NPU${npu} 上完成 urma_perftest 下发，可能超时或命令执行失败" >&2
    else
        echo "# INFO: NPU${npu} 的 urma_perftest 下发完成" >&2
    fi
done
echo >&2

# ============================================================
# 6. 如存在 NPU-NPU 打流，解析 ranktable/topo 得到 full EID 矩阵
# ============================================================

need_topo=0
for c in "${CLIENTS[@]}"; do
    for s in "${SERVERS[@]}"; do
        if ((c >= 0 && s >= 0 && c != s)); then
            need_topo=1
        fi
    done
done

declare -a ADJ_EID_FULL
declare -a DEV_IDS
declare -A DEV_IDX

if ((need_topo)); then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "# ERROR: 需要 python3 来解析 ranktable/topo" >&2
        exit 1
    fi

    RANK_TABLE_FILE="$(get_env_var_from_env_cmd RANK_TABLE_FILE || true)"
    HCCL_TOPO_FILE_PATH="$(get_env_var_from_env_cmd HCCL_TOPO_FILE_PATH || true)"
    ROOTINFO_FILE="$(get_env_var_from_env_cmd ROOTINFO_FILE || true)"

    if [[ -z "$RANK_TABLE_FILE" && -z "$ROOTINFO_FILE" ]]; then
        echo "# ERROR: 需要环境变量 ranktble 或者 rootinfo" >&2
        echo "# ERROR: export RANK_TABLE_FILE 或者 export ROOTINFO_FILE" >&2
        exit 1
    fi
    if [[ ! -z "$RANK_TABLE_FILE" && ! -r "$RANK_TABLE_FILE" ]]; then
        echo "# ERROR: 无法读取 ranktable 文件" >&2
        exit 1
    fi
    
    if [[ ! -z "$ROOTINFO_FILE" && ! -r "$ROOTINFO_FILE" ]]; then
        echo "# ERROR: 无法读取 rootinfo 文件" >&2
        exit 1
    fi

    if [[ -z "$HCCL_TOPO_FILE_PATH" ]]; then
        echo "# ERROR: 需要topo环境变量 export HCCL_TOPO_FILE_PATH" >&2
        exit 1
    fi
    if [[ ! -r "$HCCL_TOPO_FILE_PATH" ]]; then
        echo "# ERROR: 无法读取 topo JSON 文件" >&2
        exit 1
    fi

    if [[ -z "$RANK_TABLE_FILE" || ! -r "$RANK_TABLE_FILE" ]]; then
        RANK_TABLE_FILE=$ROOTINFO_FILE
    fi
    
    # 6.1 device_id 列表（排序后取前 PAIR_NUM 个）
    # 从 rootinfo/ranktable 解析
    mapfile -t DEV_IDS < <(
        python3 - "$RANK_TABLE_FILE" << 'PY'
import json, sys
path = sys.argv[1]
with open(path, 'r') as f:
    data = json.load(f)
ids = sorted({ int(r["device_id"]) for r in data.get("rank_list", []) })
for d in ids:
    print(d)
PY
    )

    if ((${#DEV_IDS[@]} < PAIR_NUM)); then
        echo "# ERROR: ranktable.json 中 device_id 数量不足: ${#DEV_IDS[@]} < $PAIR_NUM" >&2
        exit 1
    fi
    DEV_IDS=("${DEV_IDS[@]:0:PAIR_NUM}")

    for idx in "${!DEV_IDS[@]}"; do
        DEV_IDX["${DEV_IDS[$idx]}"]="$idx"
    done

    # 6.1.1 从 topo 文件解析 device_id 列表，并建立 topo_dev -> rootinfo_dev 的映射
    # 当 topo 中的 device_id 与 rootinfo 不一致时，基于顺序建立映射
    mapfile -t TOPO_DEV_IDS < <(
        python3 - "$HCCL_TOPO_FILE_PATH" << 'PY'
import json, sys
path = sys.argv[1]
with open(path, 'r') as f:
    data = json.load(f)
# 从 edge_list 中提取所有 device_id
dev_set = set()
for e in data.get("edge_list", []):
    if e.get("link_type") == "PEER2PEER" and e.get("position") == "DEVICE":
        la = e.get("local_a")
        lb = e.get("local_b")
        if la is not None:
            dev_set.add(int(la))
        if lb is not None:
            dev_set.add(int(lb))
for d in sorted(dev_set):
    print(d)
PY
    )

    declare -A TOPO_DEV_TO_IDX   # topo device_id -> 索引 (0,1,2,...)
    declare -A IDX_TO_TOPO_DEV    # 索引 -> topo device_id
    declare -A TOPO_TO_ROOTINFO_IDX  # topo device_id -> rootinfo 索引

    for idx in "${!TOPO_DEV_IDS[@]}"; do
        TOPO_DEV_TO_IDX["${TOPO_DEV_IDS[$idx]}"]="$idx"
        IDX_TO_TOPO_DEV["$idx"]="${TOPO_DEV_IDS[$idx]}"
    done

    # 基于顺序映射：topo 中第 i 个 device 对应 rootinfo 中第 i 个 device
    for topo_idx in "${!TOPO_DEV_IDS[@]}"; do
        if ((topo_idx < PAIR_NUM)); then
            TOPO_TO_ROOTINFO_IDX["${TOPO_DEV_IDS[$topo_idx]}"]="$topo_idx"
        fi
    done

    echo "# INFO: rootinfo device_id: ${DEV_IDS[*]}" >&2
    echo "# INFO: topo device_id: ${TOPO_DEV_IDS[*]}" >&2
    echo "# INFO: 映射关系 (topo_dev -> rootinfo_idx):" >&2
    for td in "${TOPO_DEV_IDS[@]}"; do
        echo "#   topo[$td] -> rootinfo_idx[${TOPO_TO_ROOTINFO_IDX[$td]}]" >&2
    done

    # 6.2 (device_id, port) -> full EID (使用 rootinfo 中的 device_id)
    declare -A PORT_EID_FULL
    while IFS=$'\t' read -r dev port addr; do
        [[ -z "$dev" || -z "$port" || -z "$addr" ]] && continue
        PORT_EID_FULL["$dev|$port"]="$addr"
    done < <(
        python3 - "$RANK_TABLE_FILE" << 'PY'
import json, sys
path = sys.argv[1]
with open(path, 'r') as f:
    data = json.load(f)

for rank in data.get("rank_list", []):
    dev = rank.get("device_id")
    if dev is None:
        continue
    for level in rank.get("level_list", []):
        for addr in level.get("rank_addr_list", []):
            if addr.get("addr_type") != "EID":
                continue
            full = addr.get("addr")
            ports = addr.get("ports") or []
            for p in ports:
                print(f"{dev}\t{p}\t{full}")
PY
    )

    # 6.3 构建 ADJ_EID_FULL[i->j]
    # 使用 TOPO_TO_ROOTINFO_IDX 映射：topo device_id -> rootinfo 索引
    # PORT_EID_FULL 的 key 使用 rootinfo device_id
    for ((k = 0; k < PAIR_NUM * PAIR_NUM; k++)); do
        ADJ_EID_FULL[k]=""
    done

    while IFS=$'\t' read -r la pa lb pb; do
        [[ -z "$la" || -z "$lb" ]] && continue

        # 将 topo device_id 映射到 rootinfo 索引 (0,1,2,...)
        if [[ -z "${TOPO_TO_ROOTINFO_IDX[$la]+_}" || -z "${TOPO_TO_ROOTINFO_IDX[$lb]+_}" ]]; then
            echo "# WARNING: topo device $la 或 $lb 无法映射到 rootinfo，跳过" >&2
            continue
        fi
        i="${TOPO_TO_ROOTINFO_IDX[$la]}"
        j="${TOPO_TO_ROOTINFO_IDX[$lb]}"

        # PORT_EID_FULL 的 key 使用 rootinfo device_id (DEV_IDS[i])
        rootinfo_dev_a="${DEV_IDS[$i]}"
        rootinfo_dev_b="${DEV_IDS[$j]}"

        keyA="${rootinfo_dev_a}|${pa}"
        keyB="${rootinfo_dev_b}|${pb}"

        if [[ -n "${PORT_EID_FULL[$keyA]+_}" ]]; then
            ADJ_EID_FULL[$((i * PAIR_NUM + j))]="${PORT_EID_FULL[$keyA]}"
        fi
        if [[ -n "${PORT_EID_FULL[$keyB]+_}" ]]; then
            ADJ_EID_FULL[$((j * PAIR_NUM + i))]="${PORT_EID_FULL[$keyB]}"
        fi
    done < <(
        python3 - "$HCCL_TOPO_FILE_PATH" << 'PY'
import json, sys
path = sys.argv[1]
with open(path, 'r') as f:
    data = json.load(f)

for e in data.get("edge_list", []):
    if e.get("link_type") != "PEER2PEER":
        continue
    if e.get("position") != "DEVICE":
        continue
    la = e.get("local_a")
    lb = e.get("local_b")
    if la is None or lb is None:
        continue
    ports_a = e.get("local_a_ports") or []
    ports_b = e.get("local_b_ports") or []
    for pa in ports_a:
        for pb in ports_b:
            print(f"{la}\t{pa}\t{lb}\t{pb}")
PY
    )
fi

find_idx_in_npu_eid_list() {
    local npu="$1"
    local short="$2"
    short="${short,,}"
    local idx
    local val
    eval 'local -a arr=("${NPU'"$npu"'_EID_LIST[@]}")'
    for idx in "${!arr[@]}"; do
        val="${arr[$idx]}"
        val="${val: -4}"
        if [[ "${val,,}" == "$short" ]]; then
            echo "$idx"
            return 0
        fi
    done
    echo "-1"
    return 1
}

# ============================================================
# 7. 先计算本次总连接数，用来决定是否需要 -c
# ============================================================

total_pairs=0
for c in "${CLIENTS[@]}"; do
    for s in "${SERVERS[@]}"; do
        # 跳过无效组合
        if ((c == -1 && s == -1)); then
            continue
        fi
        if ((c >= 0 && s >= 0 && c == s)); then
            continue
        fi
        ((total_pairs++))
    done
done

conn_idx=0   # 用于给 client 分配 -c，从 0 开始

# ============================================================
# 8. 生成打流命令（全部带 --eid_idx，必要时带 -c）
# ============================================================

echo "================================================================"
echo "# URMA 打流命令生成"
echo "#  - CPU: -1 (本机执行 ${URMA_TEST_LOCAL_BIN})"
echo "#  - NPU: 0 ~ $((PAIR_NUM-1)) (远端执行 ${URMA_TEST_REMOTE_BIN})"
echo "#  - client 在第 1 个参数集合，server 在第 2 个参数集合"
echo "#  - 本次总连接数: ${total_pairs}"
echo "================================================================"
echo

# -----------------------------
# -c base 映射
# -----------------------------
c_base_for_client() {
  local dev="$1"
  # CPU 也给 0（一般不会在 CPU 上并发很多流）
  if (( dev == -1 )); then
    echo 0
    return
  fi
  if (( dev <= 0 )); then
    echo 192-287
  elif (( dev <= 1 )); then
    echo 192-287
  elif (( dev <= 2 )); then
    echo 0-95
  elif (( dev <= 3 )); then
    echo 0-95
  elif (( dev <= 4 )); then
    echo 0-95
  elif (( dev <= 5 )); then
    echo 0-95
  elif (( dev <= 6 )); then
    echo 192-287
  else
    echo 192-287
  fi
}

# -----------------------------
# 在生成阶段探测是否支持某个 option（如 --hugepage-size）
#   - 对 CPU：本机运行 "$bin test -h"
#   - 对 NPU：ssh 到 NPU 运行 "$bin test -h"
#   - 探测失败时：返回“不支持”（更安全，避免执行时报错）
# -----------------------------
declare -A OPT_SUPPORT_CACHE  # key: "dev|--hugepage-size" -> 1/0

urma_bin_for_dev() {
  local dev="$1"
  if (( dev == -1 )); then
    if [[ -n "$URMA_TEST_LOCAL_BIN" ]]; then
      echo "$URMA_TEST_LOCAL_BIN"
    else
      echo "urma_perftest"
    fi
  else
    if [[ -n "${URMA_TEST_REMOTE_BIN:-}" ]]; then
      echo "$URMA_TEST_REMOTE_BIN"
    else
      echo "urma_perftest"
    fi
  fi
}

dev_supports_opt() {
  local dev="$1"
  local opt="$2"
  local key="${dev}|${opt}"
  if [[ -n "${OPT_SUPPORT_CACHE[$key]+_}" ]]; then
    [[ "${OPT_SUPPORT_CACHE[$key]}" == "1" ]]
    return $?
  fi

  local bin help ok=0
  bin="$(urma_bin_for_dev "$dev")"

  if (( dev == -1 )); then
    help="$("$bin" write_lat -h 2>&1 || true)"
  else
    local ip="${NPU_MGMT_IP[$dev]}"
    # 远端执行：用 printf %q 规避路径/特殊字符
    local cmd
    cmd="$(printf '%q ' "$bin" write_lat -h)"
    help="$(ssh -o BatchMode=yes -o StrictHostKeyChecking=no "${SSH_USER}@${ip}" "$cmd" 2>&1 || true)"
  fi

  if grep -q -- "$opt" <<<"$help"; then
    ok=1
  else
    ok=0
  fi

  OPT_SUPPORT_CACHE[$key]="$ok"
  [[ "$ok" == "1" ]]
}

# 从参数数组里移除：--hugepage-size <val>  或  --hugepage-size=<val>
filter_out_hugepage_args() {
  local -a in=("$@")
  local -a out=()
  local skip=0
  for a in "${in[@]}"; do
    if ((skip)); then skip=0; continue; fi
    case "$a" in
      --hugepage-size) skip=1; continue ;;
      --hugepage-size=*) continue ;;
    esac
    out+=("$a")
  done
  printf '%s\n' "${out[@]}"
}

# 把 EXTRA_ARGS（字符串）拆成数组，并按 dev 的支持情况过滤
build_extra_args_for_dev() {
  local dev="$1"
  local raw="$2"
  local -a arr=()

  # 简单拆分：要求 raw 不包含复杂引号（现有用法一般没问题）
  if [[ -n "$raw" ]]; then
    read -r -a arr <<<"$raw"
  fi

  if [[ "${#arr[@]}" -eq 0 ]]; then
    return 0
  fi

  # 若不支持 --hugepage-size，则删除该参数
  if ! dev_supports_opt "$dev" "--hugepage_size"; then
    mapfile -t arr < <(filter_out_hugepage_args "${arr[@]}")
  fi

  printf '%s\n' "${arr[@]}"
}

generate_urma_commands() {
    local -A client_count=()  # dev -> count
    local -A server_count=()  # dev -> count

    # 统计每个 client/server 设备要生成多少条命令
    for c in "${CLIENTS[@]}"; do
    for s in "${SERVERS[@]}"; do
        (( c == s )) && continue
        (( c == -1 && s == -1 )) && continue
        client_count["$c"]=$(( ${client_count["$c"]:-0} + 1 ))
        server_count["$s"]=$(( ${server_count["$s"]:-0} + 1 ))
    done
    done

    for c in "${CLIENTS[@]}"; do
        for s in "${SERVERS[@]}"; do
            # 跳过 CPU<->CPU 或 NPU 自己打自己
            if ((c == -1 && s == -1)); then
                echo "# 跳过 CPU(-1) 自己打自己"
                continue
            fi
            if ((c >= 0 && s >= 0 && c == s)); then
                echo "# 跳过 NPU${c} 自己打自己"
                continue
            fi

            # client 端是否需要 -c
            add_c_opt=0
            if ((total_pairs > 1)); then
                add_c_opt=1
            fi

            # ---------- 情况 1：CPU 客户端，NPU 服务器 ----------
            if ((c == -1 && s >= 0)); then
                local_idx="$s"
                server_npu="$s"

                cpu_udma="${HOST_UDMA_LIST[$local_idx]}"
                cpu_eid="${HOST_EID_LIST[$local_idx]}"
                cpu_eididx="${HOST_EIDIDX_LIST[$local_idx]}"
                # server 是 NPU<server_npu>，与 host 的连接在 index 0
                eval 'server_udma="${NPU'"$server_npu"'_UDMA_LIST[0]}"'
                eval 'server_eid="${NPU'"$server_npu"'_EID_LIST[0]}"'
                eval 'server_eididx="${NPU'"$server_npu"'_EIDIDX_LIST[0]}"'
                eval 'server_ip="${NPU'"$server_npu"'_IP_LIST[0]}"'

                server_ssh_ip="${NPU_MGMT_IP[$server_npu]}"

                # 检验关键变量是否为空
                missing_vars=()
                [[ -z "$server_udma" ]] && missing_vars+=("server_udma")
                [[ -z "$cpu_udma" ]] && missing_vars+=("cpu_udma")
                [[ -z "$server_ip" ]] && missing_vars+=("server_ip")
                [[ -z "$server_eididx" ]] && missing_vars+=("server_eididx")
                [[ -z "$cpu_eididx" ]] && missing_vars+=("cpu_eididx")

                if [[ ${#missing_vars[@]} -gt 0 ]]; then
                    echo "# ERROR: 以下关键变量为空，跳过生成打流命令: ${missing_vars[*]}" >&2
                    echo "#   server_udma=$server_udma, cpu_udma=$cpu_udma, server_ip=$server_ip"
                    echo "#   server_eididx=$server_eididx, cpu_eididx=$cpu_eididx" >&2
                    continue
                fi

                # client -c 选项
                client_c_opt=""
                if ((add_c_opt)); then
                    local task_npu
                    task_npu="$(c_base_for_client "$server_npu")"
                    client_c_opt="taskset -c ${task_npu} "
                fi
                URMA_SERVER_EXTRA_ARGS="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 --tp_aware --ctp --hugepage-size 2MB"
                URMA_CLIENT_EXTRA_ARGS="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 --tp_aware --ctp --hugepage-size 2MB"
                # 生成 EXTRA 参数（在生成阶段根据 dev 上的 "urma test -h" 过滤 hugepage）
                local -a s_extra_arr=()
                local -a c_extra_arr=()

                mapfile -t s_extra_arr < <(build_extra_args_for_dev "$s" "$URMA_SERVER_EXTRA_ARGS")
                mapfile -t c_extra_arr < <(build_extra_args_for_dev "$c" "$URMA_CLIENT_EXTRA_ARGS")
                # 拼纯命令（不再有 __ub=... 包装）
                local server_cmd client_cmd
                server_cmd="urma_perftest ${test_type}_bw -d ${server_udma} --eid_idx ${server_eididx}"
                for a in "${s_extra_arr[@]}"; do server_cmd+=" ${a}"; done

                client_cmd="urma_perftest ${test_type}_bw -d ${cpu_udma} -S ${server_ip} --eid_idx ${cpu_eididx}"
                [[ -n "$c_copt" ]] && client_cmd+=" ${c_copt}"
                for a in "${c_extra_arr[@]}"; do client_cmd+=" ${a}"; done

                server_cmd="$server_cmd"
                client_cmd="${client_c_opt} $client_cmd"

                #   server_dev 有多条 -> server_cmd 加 &
                #   client_dev 有多条 -> client_cmd 加 &
                if (( ${server_count["$s"]:-0} > 1 )); then
                    server_cmd="${server_cmd}&"
                fi
                if (( ${client_count["$c"]:-0} > 1 )); then
                    client_cmd="${client_cmd}&"
                fi

                echo "# ===== CPU(-1) client -> NPU${server_npu} server ====="
                # echo "# CPU 侧:  EID=${cpu_eid},  UDMA=${cpu_udma}, eid_idx=${cpu_eididx}"
                # echo "# NPU${server_npu} 侧: EID=${server_eid}, UDMA=${server_udma}, eid_idx=${server_eididx}, 数据IP=${server_ip}"
                # echo "#"
                echo "# 【Server】在 NPU${server_npu} 上执行："
                # echo "ssh ${SSH_USER}@${server_ssh_ip} \"${URMA_TEST_REMOTE_BIN} ${server_udma}\""
                echo $server_cmd
                echo
                echo "# 【Client】在 CPU OS 上执行："
                # echo "${URMA_TEST_LOCAL_BIN} ${cpu_udma} ${server_ip}"
                echo $client_cmd
                echo

                if ((add_c_opt)); then
                    ((conn_idx++))
                fi
                continue
            fi

            # ---------- 情况 2：NPU 客户端，CPU 服务器 ----------
            if ((c >= 0 && s == -1)); then
                local_idx="$c"
                client_npu="$c"

                cpu_udma="${HOST_UDMA_LIST[$local_idx]}"
                cpu_eid="${HOST_EID_LIST[$local_idx]}"
                cpu_eididx="${HOST_EIDIDX_LIST[$local_idx]}"
                cpu_ip="${HOST_IP_LIST[$local_idx]}"

                eval 'client_udma="${NPU'"$client_npu"'_UDMA_LIST[0]}"'
                eval 'client_eid="${NPU'"$client_npu"'_EID_LIST[0]}"'
                eval 'client_eididx="${NPU'"$client_npu"'_EIDIDX_LIST[0]}"'

                client_ssh_ip="${NPU_MGMT_IP[$client_npu]}"

                # 检验关键变量是否为空
                missing_vars=()
                [[ -z "$client_udma" ]] && missing_vars+=("client_udma")
                [[ -z "$cpu_udma" ]] && missing_vars+=("cpu_udma")
                [[ -z "$cpu_ip" ]] && missing_vars+=("cpu_ip")
                [[ -z "$client_eididx" ]] && missing_vars+=("client_eididx")
                [[ -z "$cpu_eididx" ]] && missing_vars+=("cpu_eididx")

                if [[ ${#missing_vars[@]} -gt 0 ]]; then
                    echo "# ERROR: 以下关键变量为空，跳过生成打流命令: ${missing_vars[*]}" >&2
                    echo "#   client_udma=$client_udma, cpu_udma=$cpu_udma, cpu_ip=$cpu_ip"
                    echo "#   client_eididx=$client_eididx, cpu_eididx=$cpu_eididx" >&2
                    continue
                fi

                client_c_opt=""
                if ((add_c_opt)); then
                    local task_npu
                    task_npu="$(c_base_for_client "$client_npu")"
                    client_c_opt="taskset -c ${task_npu} "
                fi

                URMA_SERVER_EXTRA_ARGS="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 --tp_aware --ctp --hugepage-size 2MB"
                URMA_CLIENT_EXTRA_ARGS="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 --tp_aware --ctp --hugepage-size 2MB"
                # 生成 EXTRA 参数（在生成阶段根据 dev 上的 "urma test -h" 过滤 hugepage）
                local -a s_extra_arr=()
                local -a c_extra_arr=()

                mapfile -t s_extra_arr < <(build_extra_args_for_dev "$s" "$URMA_SERVER_EXTRA_ARGS")
                mapfile -t c_extra_arr < <(build_extra_args_for_dev "$c" "$URMA_CLIENT_EXTRA_ARGS")
                # 拼纯命令（不再有 __ub=... 包装）
                local server_cmd client_cmd
                server_cmd="urma_perftest ${test_type}_bw -d ${cpu_udma} --eid_idx ${cpu_eididx}"
                for a in "${s_extra_arr[@]}"; do server_cmd+=" ${a}"; done

                client_cmd="urma_perftest ${test_type}_bw -d ${client_udma} -S ${cpu_ip} --eid_idx ${client_eididx}"
                [[ -n "$c_copt" ]] && client_cmd+=" ${c_copt}"
                for a in "${c_extra_arr[@]}"; do client_cmd+=" ${a}"; done

                server_cmd="${client_c_opt} $server_cmd"
                client_cmd="$client_cmd"

                #   server_dev 有多条 -> server_cmd 加 &
                #   client_dev 有多条 -> client_cmd 加 &
                if (( ${server_count["$s"]:-0} > 1 )); then
                    server_cmd="${server_cmd}&"
                fi
                if (( ${client_count["$c"]:-0} > 1 )); then
                    client_cmd="${client_cmd}&"
                fi

                echo "# ===== NPU${client_npu} client -> CPU(-1) server ====="
                # echo "# CPU 侧:  EID=${cpu_eid},  UDMA=${cpu_udma}, eid_idx=${cpu_eididx}, 数据IP=${cpu_ip}"
                # echo "# NPU${client_npu} 侧: EID=${client_eid}, UDMA=${client_udma}, eid_idx=${client_eididx}"
                # echo "#"
                echo "# 【Server】在 CPU OS 上执行："
                # echo "${URMA_TEST_LOCAL_BIN} ${cpu_udma}"
                echo $server_cmd
                echo
                echo "# 【Client】在 NPU${client_npu} 上执行："
                # echo "ssh ${SSH_USER}@${client_ssh_ip} \"${URMA_TEST_REMOTE_BIN} ${client_udma} ${cpu_ip}\""
                echo $client_cmd
                echo

                if ((add_c_opt)); then
                    ((conn_idx++))
                fi
                continue
            fi

            # ---------- 情况 3：NPU<->NPU ----------
            if ((c >= 0 && s >= 0)); then
                if ((need_topo == 0)); then
                    echo "# ERROR: 逻辑错误：need_topo=0，但出现 NPU-NPU 对" >&2
                    exit 1
                fi
                client_npu="$c"
                server_npu="$s"

                full_cli="${ADJ_EID_FULL[$((client_npu * PAIR_NUM + server_npu))]}"
                full_srv="${ADJ_EID_FULL[$((server_npu * PAIR_NUM + client_npu))]}"

                if [[ -z "$full_cli" || -z "$full_srv" ]]; then
                    echo "# WARNING: NPU${client_npu} 与 NPU${server_npu} 之间未找到直连 EID，跳过"
                    continue
                fi

                short_cli="$(eid_suffix "$full_cli")"
                short_srv="$(eid_suffix "$full_srv")"

                idx_cli="$(find_idx_in_npu_eid_list "$client_npu" "$short_cli")"
                idx_srv="$(find_idx_in_npu_eid_list "$server_npu" "$short_srv")"

                if ((idx_cli < 0 || idx_srv < 0)); then
                    echo "# WARNING: 在 NPU EID_LIST 中找不到对应 EID (NPU${client_npu}->$short_cli, NPU${server_npu}->$short_srv)，跳过"
                    continue
                fi

                eval 'client_udma="${NPU'"$client_npu"'_UDMA_LIST['"$idx_cli"']}"'
                eval 'client_eididx="${NPU'"$client_npu"'_EIDIDX_LIST['"$idx_cli"']}"'
                eval 'server_udma="${NPU'"$server_npu"'_UDMA_LIST['"$idx_srv"']}"'
                eval 'server_eididx="${NPU'"$server_npu"'_EIDIDX_LIST['"$idx_srv"']}"'
                eval 'server_ip="${NPU'"$server_npu"'_EID_IPV6_LIST['"$idx_srv"']}"'
                eval 'client_ip="${NPU'"$client_npu"'_EID_IPV6_LIST['"$idx_srv"']}"'
                client_ssh_ip="${NPU_MGMT_IP[$client_npu]}"
                server_ssh_ip="${NPU_MGMT_IP[$server_npu]}"

                # 检验关键变量是否为空
                missing_vars=()
                [[ -z "$server_udma" ]] && missing_vars+=("server_udma")
                [[ -z "$client_udma" ]] && missing_vars+=("client_udma")
                [[ -z "$server_ip" ]] && missing_vars+=("server_ip")
                [[ -z "$server_eididx" ]] && missing_vars+=("server_eididx")
                [[ -z "$client_eididx" ]] && missing_vars+=("client_eididx")

                if [[ ${#missing_vars[@]} -gt 0 ]]; then
                    echo "# ERROR: 以下关键变量为空，跳过生成打流命令: ${missing_vars[*]}" >&2
                    echo "#   server_udma=$server_udma, client_udma=$client_udma, server_ip=$server_ip"
                    echo "#   server_eididx=$server_eididx, client_eididx=$client_eididx" >&2
                    continue
                fi
                URMA_SERVER_EXTRA_ARGS="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 --tp_aware --ctp -B --hugepage-size 2MB --enable_ipv6"
                URMA_CLIENT_EXTRA_ARGS="-R 4096 -T 4096 -C 16384 -p 0 -y -s 16777216 -L -l 256 -Q 256 --tp_aware --ctp -B --hugepage-size 2MB --enable_ipv6"
                # 生成 EXTRA 参数（在生成阶段根据 dev 上的 "urma test -h" 过滤 hugepage）
                local -a s_extra_arr=()
                local -a c_extra_arr=()

                mapfile -t s_extra_arr < <(build_extra_args_for_dev "$s" "$URMA_SERVER_EXTRA_ARGS")
                mapfile -t c_extra_arr < <(build_extra_args_for_dev "$c" "$URMA_CLIENT_EXTRA_ARGS")
                # 拼纯命令（不再有 __ub=... 包装）
                local server_cmd client_cmd
                server_cmd="urma_perftest ${test_type}_bw -d ${server_udma} --eid_idx ${server_eididx}"
                for a in "${s_extra_arr[@]}"; do server_cmd+=" ${a}"; done

                client_cmd="urma_perftest ${test_type}_bw -d ${client_udma} -S $server_ip --eid_idx ${client_eididx} --bind_ip $client_ip "
                [[ -n "$c_copt" ]] && client_cmd+=" ${c_copt}"
                for a in "${c_extra_arr[@]}"; do client_cmd+=" ${a}"; done

                # server_cmd=($(bash -lc $server_cmd))
                # client_cmd=($(bash -lc $client_cmd))
                #   server_dev 有多条 -> server_cmd 加 &
                #   client_dev 有多条 -> client_cmd 加 &
                if (( ${server_count["$s"]:-0} > 1 )); then
                    server_cmd="${server_cmd}&"
                fi
                if (( ${client_count["$c"]:-0} > 1 )); then
                    client_cmd="${client_cmd}&"
                fi

                echo "# ===== NPU${client_npu} client -> NPU${server_npu} server ====="
                # echo "# client: NPU${client_npu}, EID=${short_cli}, UDMA=${client_udma}, eid_idx=${client_eididx}"
                # echo "# server: NPU${server_npu}, EID=${short_srv}, UDMA=${server_udma}, eid_idx=${server_eididx}, 数据IP=${server_ip}"
                # echo "#"
                echo "# 【Server】在 NPU${server_npu} 上执行："
                # echo "ssh ${SSH_USER}@${server_ssh_ip} \"${URMA_TEST_REMOTE_BIN} ${server_udma}\""
                echo $server_cmd
                echo
                echo "# 【Client】在 NPU${client_npu} 上执行："
                # echo "ssh ${SSH_USER}@${client_ssh_ip} \"${URMA_TEST_REMOTE_BIN} ${client_udma} ${server_ip}\""
                echo $client_cmd
                echo

                if ((add_c_opt)); then
                    ((conn_idx++))
                fi
                continue
            fi
        done
    done
}

generate_urma_commands
