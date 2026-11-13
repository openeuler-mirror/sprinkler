#!/usr/bin/env bash
# 在已有的 npu_ip_map.conf 基础上，给 HOST / NPU 补充对应的 UDMA 和 eid_idx 列表：
# - HOST_UDMA_LIST     ：与 /lib/route.conf 中每个 CPU EID 一一对应
# - HOST_EIDIDX_LIST   ：与 HOST_UDMA_LIST 对应的 eid_idx
# - NPU{i}_UDMA_LIST   ：与 NPU{i}_EID_LIST 一一对应
# - NPU{i}_EIDIDX_LIST ：与 NPU{i}_UDMA_LIST 对应的 eid_idx
#
# 额外要求：
#   在 ssh 进 NPU 执行 `urma show` 前，NPU 会先 scp 一份 npu_ip_map.conf 过来
#   （CPU IP 通过 /lib/route.conf 里的 local_eid 转 IP 得到）。
#
# 依赖：bash 4+、urma（CPU & NPU 上）、ssh & scp
#
# 使用方式：
#   export SSH_USER=root                     # 登录 CPU & NPU 的用户名（默认 root）
#   export CONF_FILE=/root/npu_ip_map.conf   # CPU 上 npu_ip_map.conf 的路径（默认 ./npu_ip_map.conf）
#   chmod +x update_udma_in_map.sh
#   ./update_udma_in_map.sh
#
# 运行后：
#   - 在 CPU 上的 npu_ip_map.conf 中追加/更新：
#       HOST_UDMA_LIST / HOST_EIDIDX_LIST
#       NPU0_UDMA_LIST / NPU0_EIDIDX_LIST ... NPU{N-1}_*
#   - 会详细打印 CPU/NPU 每个 EID 后缀对应的 UDMA 和 eid_idx，方便检查。

# set -euo pipefail
# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

config_require_keys \
    "POD_NPU_USER|请输入NPU的ssh连接用户名|root|0|validate_non_empty"

SSH_USER=$POD_NPU_USER
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${CONF_FILE:-${SCRIPT_DIR}/npu_ip_map.conf}"
ROUTE_CONF="/lib/route.conf"

# -----------------------------
# 小工具：EID -> 规范十六进制 / IP / 后缀
# -----------------------------
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
    # 这里的“后 4 位”用于匹配 urma_admin show 里 eidXXXX 的 XXXX
    printf '%s' "${eid: -4}"
}

eid_to_ip() {
    # 规则：取 EID 最后 8 个 hex 位 -> 4 字节 -> IPv4
    # 例: ...dfdf0890 -> df df 08 90 -> 223.223.8.144
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

# -----------------------------
# 0. 基本检查
# -----------------------------
if [[ ! -r "$CONF_FILE" ]]; then
    echo "# ERROR: 未找到 npu_ip_map.conf: $CONF_FILE" >&2
    exit 1
fi

if [[ ! -r "$ROUTE_CONF" ]]; then
    echo "# ERROR: $ROUTE_CONF 不存在或不可读" >&2
    exit 1
fi

# 计算 CPU 上该文件的绝对路径，方便 NPU 拉取
if REAL_CONF_PATH=$(readlink -f "$CONF_FILE" 2>/dev/null); then
    :
else
    REAL_CONF_PATH="$CONF_FILE"
fi

# -----------------------------
# 1. 从 /lib/route.conf 获取 NPU 数量 & CPU/NPU IP & CPU EID 后缀
# -----------------------------
# shellcheck disable=SC1090
source "$ROUTE_CONF"

if [[ -z "${pair_device_num:-}" ]]; then
    echo "# ERROR: route.conf 中未定义 pair_device_num" >&2
    exit 1
fi

PAIR_NUM=$((pair_device_num))

declare -a CPU_IP_PER_PAIR   # 每个 pair 对应的 CPU IP（local_eid -> IP）
declare -a NPU_MGMT_IP       # 每个 pair 对应的 NPU 管理 IP（remote_eid -> IP）
declare -a CPU_EID_SUFFIX    # 每个 pair 对应的 CPU EID 后 4 位（用于匹配 urma_admin show）

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
    CPU_EID_SUFFIX[i]="$(eid_suffix "$local_eid")"
done

echo "# INFO: 从 route.conf 获取到的 CPU / NPU IP（按 pair 序号）：" >&2
for ((i = 0; i < PAIR_NUM; i++)); do
    echo "#   pair$i: CPU_IP=${CPU_IP_PER_PAIR[i]}  NPU_IP=${NPU_MGMT_IP[i]}  CPU_EID_SUFFIX=${CPU_EID_SUFFIX[i]}" >&2
done
echo >&2

# -----------------------------
# 2. 从 npu_ip_map.conf 读取 HOST/NPU 的 EID LIST
# -----------------------------
# 这里只用到：
#   HOST_EID_LIST
#   NPU0_EID_LIST ... NPU{PAIR_NUM-1}_EID_LIST
# shellcheck disable=SC1090
source "$CONF_FILE"

if [[ "${#HOST_EID_LIST[@]:-0}" -eq 0 ]]; then
    echo "# WARNING: 在 $CONF_FILE 中未发现 HOST_EID_LIST，可能生成脚本不一致" >&2
fi

# -----------------------------
# 3. CPU 上执行 urma_admin show，构建 CPU_EID_SUFFIX -> (UDMA, eid_idx) 映射，并打印
# -----------------------------
declare -A CPU_EID2UDMA    # key: eid_suffix(4hex,小写) -> udmaX
declare -A CPU_EID2IDX     # key: eid_suffix(4hex,小写) -> eid_idx（数字）

if command -v urma_admin >/dev/null 2>&1; then
    echo "# INFO: 在 CPU OS 上执行 'urma_admin show' 解析 UDMA / eid_idx ..." >&2

    # 注意：用进程替换，而不是管道，避免 while 在子进程修改不了数组
    while read -r udma idx suf; do
        [[ -z "$udma" || -z "$suf" ]] && continue
        CPU_EID2UDMA["$suf"]="$udma"
        CPU_EID2IDX["$suf"]="$idx"
    done < <(
        # 设置超时，避免urma_admin卡住
        timeout 30s urma_admin show 2>/dev/null | awk '
            NR<=2 {next}
            # 列格式:
            # num  ubep_dev  tp_type  eidX  FULL_EID  link  net_dev
            $2 ~ /^udma/ && $4 ~ /^eid[0-9]+$/ {
                full=$5
                n=split(full, arr, ":")
                suf=arr[n]
                idx=substr($4,4)   # "eid1" -> "1"
                printf "%s %s %s\n", $2, idx, tolower(suf)
            }'
    )

    echo "# INFO: CPU 端 EID_SUFFIX -> (UDMA, eid_idx) 映射（按 pairX 顺序）：" >&2
    for ((i = 0; i < PAIR_NUM; i++)); do
        suf="${CPU_EID_SUFFIX[i],,}"
        udma="${CPU_EID2UDMA[$suf]:-}"
        idx="${CPU_EID2IDX[$suf]:-}"
        if [[ -n "$udma" ]]; then
            echo "#   pair$i: EID_SUFFIX=$suf -> UDMA=$udma, eid_idx=${idx:-?}" >&2
        else
            echo "#   pair$i: EID_SUFFIX=$suf -> UDMA=, eid_idx=  (未在 urma_admin show 中找到)" >&2
        fi
    done
else
    echo "# WARNING: CPU 上找不到 urma_admin 命令，HOST_UDMA_LIST / HOST_EIDIDX_LIST 将是空字符串" >&2
fi
echo >&2

# -----------------------------
# 4. 每个 NPU 上：先 scp npu_ip_map.conf，再 urma_admin show，构建 NPU_EID2(UDMA, eid_idx)，并打印
# -----------------------------
declare -A NPU_EID2UDMA    # key: "npuIdx:eid_suffix" -> udmaX
declare -A NPU_EID2IDX     # key: "npuIdx:eid_suffix" -> eid_idx

for ((i = 0; i < PAIR_NUM; i++)); do
    cpu_ip="${CPU_IP_PER_PAIR[i]}"
    npu_ip="${NPU_MGMT_IP[i]}"

    if [[ -z "$cpu_ip" || -z "$npu_ip" ]]; then
        echo "# WARNING: NPU${i} 的 CPU_IP 或 NPU_IP 为空，跳过" >&2
        continue
    fi

    echo "# INFO: 检查 NPU${i}(${npu_ip}) 连通性..." >&2
    
    # 先ping检测连通性
    if ! ping -c 1 -W 2 "$npu_ip" >/dev/null 2>&1; then
        echo "# WARNING: NPU${i}(${npu_ip}) ping 不通，跳过该NPU的处理" >&2
        continue
    fi

    echo "# INFO: 在 NPU${i}(${npu_ip}) 上执行：先 scp npu_ip_map.conf，再 'urma_admin show' ..." >&2

    # 也是用进程替换的方式，把 ssh+awk 的输出喂给 while
    while read -r udma idx suf; do
        [[ -z "$udma" || -z "$suf" ]] && continue
        NPU_EID2UDMA["${i}:${suf}"]="$udma"
        NPU_EID2IDX["${i}:${suf}"]="$idx"
    done < <(
        # 设置SSH和命令超时，避免卡住
        timeout 60s ssh -o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=no \
            "${SSH_USER}@${npu_ip}" \
            "timeout 30s bash -c '
                if [ ! -f /tmp/npu_ip_map.conf ]; then
                    echo \"# INFO: NPU${i} 上未找到 /tmp/npu_ip_map.conf，从 CPU(${cpu_ip}) 拉取 ...\" >&2
                    timeout 30s scp -o ConnectTimeout=10 -o StrictHostKeyChecking=no ${SSH_USER}@${cpu_ip}:${REAL_CONF_PATH} /tmp/npu_ip_map.conf
                fi
                timeout 30s urma_admin show
            '" 2>/dev/null | \
        awk '
            NR<=2 {next}
            $2 ~ /^udma/ && $4 ~ /^eid[0-9]+$/ {
                full=$5
                n=split(full, arr, ":")
                suf=arr[n]
                idx=substr($4,4)
                printf "%s %s %s\n", $2, idx, tolower(suf)
            }'
    )

    # 检查是否成功获取到数据
    if [[ ${#NPU_EID2UDMA[@]} -eq 0 ]]; then
        echo "# WARNING: NPU${i}(${npu_ip}) 未能获取到有效的 UDMA 信息，可能超时或命令执行失败" >&2
    fi

    # 打印 NPU<i> 上每个 EID 的 (UDMA, eid_idx) 映射情况
    # 动态引用 NPU<i>_EID_LIST
    if declare -n eid_list="NPU${i}_EID_LIST" 2>/dev/null; then
        echo "# INFO: NPU${i} 上 EID_SUFFIX -> (UDMA, eid_idx) 映射：" >&2
        for eid in "${eid_list[@]}"; do
            suf="${eid,,}"  # NPU*_EID_LIST 中记录的就是后 4 位
            udma="${NPU_EID2UDMA["${i}:${suf}"]:-}"
            idx="${NPU_EID2IDX["${i}:${suf}"]:-}"
            if [[ -n "$udma" ]]; then
                echo "#   NPU${i}: EID_SUFFIX=$suf -> UDMA=$udma, eid_idx=${idx:-?}" >&2
            else
                echo "#   NPU${i}: EID_SUFFIX=$suf -> UDMA=, eid_idx=  (未在 urma_admin show 中找到)" >&2
            fi
        done
    else
        echo "# WARNING: 在 $CONF_FILE 中未找到 NPU${i}_EID_LIST，无法打印 NPU${i} 的 EID->UDMA/eid_idx 详情" >&2
    fi

    echo "#   完成 NPU${i} 的 EID->UDMA/eid_idx 映射解析" >&2
    echo >&2
done

# -----------------------------
# 5. 生成 HOST_UDMA_LIST / HOST_EIDIDX_LIST / NPU{i}_UDMA_LIST / NPU{i}_EIDIDX_LIST
# -----------------------------
tmp_udma_defs="$(mktemp /tmp/npu_udma_defs.XXXXXX)"

{
    echo
    echo "# ==== UDMA & EIDIDX LISTS ===="

    # 5.1 HOST_UDMA_LIST / HOST_EIDIDX_LIST
    # 这里严格按照 /lib/route.conf 中的 CPU_EID_SUFFIX 顺序匹配 UDMA
    printf 'HOST_UDMA_LIST=('
    for ((i = 0; i < PAIR_NUM; i++)); do
        suf="${CPU_EID_SUFFIX[i],,}"
        udma="${CPU_EID2UDMA[$suf]:-}"
        printf '"%s" ' "$udma"
    done
    printf ')\n'

    printf 'HOST_EIDIDX_LIST=('
    for ((i = 0; i < PAIR_NUM; i++)); do
        suf="${CPU_EID_SUFFIX[i],,}"
        idx="${CPU_EID2IDX[$suf]:-}"
        printf '"%s" ' "$idx"
    done
    printf ')\n'

    # 5.2 NPU{i}_UDMA_LIST / NPU{i}_EIDIDX_LIST: 与 NPU{i}_EID_LIST 一一对应
    for ((i = 0; i < PAIR_NUM; i++)); do
        if ! declare -n eid_list="NPU${i}_EID_LIST" 2>/dev/null; then
            echo "# WARNING: 未找到 NPU${i}_EID_LIST，跳过 UDMA/EIDIDX 生成" >&2
            continue
        fi

        udma_arr=()
        idx_arr=()
        for eid in "${eid_list[@]}"; do
            key="${eid,,}"  # NPU*_EID_LIST 里存的就是后 4 位
            udma_arr+=( "${NPU_EID2UDMA["${i}:${key}"]:-}" )
            idx_arr+=( "${NPU_EID2IDX["${i}:${key}"]:-}" )
        done

        printf 'NPU%d_UDMA_LIST=(' "$i"
        for u in "${udma_arr[@]}"; do
            printf '"%s" ' "$u"
        done
        printf ')\n'

        printf 'NPU%d_EIDIDX_LIST=(' "$i"
        for x in "${idx_arr[@]}"; do
            printf '"%s" ' "$x"
        done
        printf ')\n'
    done
} > "$tmp_udma_defs"

# -----------------------------
# 6. 把原 conf 中旧的 *_UDMA_LIST / *_EIDIDX_LIST 删除，再追加新的定义
# -----------------------------
tmp_conf="$(mktemp /tmp/npu_ip_map.XXXXXX)"

# 过滤掉旧的 UDMA / EIDIDX 定义（如果有）
grep -v -E '^(HOST_UDMA_LIST=|NPU[0-9]+_UDMA_LIST=|HOST_EIDIDX_LIST=|NPU[0-9]+_EIDIDX_LIST=)' \
    "$CONF_FILE" > "$tmp_conf"

# 追加新的定义
cat "$tmp_udma_defs" >> "$tmp_conf"

# 覆盖原 conf
mv "$tmp_conf" "$CONF_FILE"
rm -f "$tmp_udma_defs"

# -----------------------------
# 7. 打印最终的 npu_ip_map.conf 内容
# -----------------------------
echo "# INFO: 已更新 $CONF_FILE，当前内容如下：" >&2
cat "$CONF_FILE"