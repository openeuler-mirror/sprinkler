#!/usr/bin/env bash
# 从 /lib/route.conf + ranktable.json + topo.json 构造：
# - HOST_EID_LIST / HOST_IP_LIST
# - NPU{i}_EID_LIST / NPU{i}_IP_LIST
#
# 依赖：bash 4+，python3
#
# 使用方式：
#   chmod +x gen_npu_ip_map.sh
#   # 1）直接在当前 shell 中加载：
#   eval "$(./gen_npu_ip_map.sh)"
#   # 2）或者使用生成的 conf 文件：
#   ./gen_npu_ip_map.sh          # 默认生成 ./npu_ip_map.conf
#   source ./npu_ip_map.conf

# set -euo pipefail

# -----------------------------
# 输出 conf 文件路径（可根据需要修改）
# -----------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "${PARENT_DIR}/config_util.sh"

config_require_keys \
    "HCCL_TOPO_FILE_PATH|请输入topo的json配置文件路径|/etc/superpod_1d_noroce.json|0|" \
    "ROOTINFO_FILE|请输入rootinfo的json配置文件路径|/etc/hccl_rootinfo.json|0|" \
    "POD_NPU_USER|请输入NPU的ssh连接用户名|root|0|validate_non_empty"

OUTPUT_CONF="${OUTPUT_CONF:-${SCRIPT_DIR}/npu_ip_map.conf}"

# -----------------------------
# 工具函数：EID 处理
# -----------------------------
eid_normalize() {
    # 输入:  EID 字符串（可能带 0x）
    # 输出:  去掉 0x 后的小写十六进制（最多 32 位）
    local eid="$1"
    eid="${eid#0x}"
    eid="${eid#0X}"
    eid="${eid,,}"               # 转小写
    if ((${#eid} > 32)); then
        eid="${eid: -32}"
    fi
    printf '%s' "$eid"
}

eid_suffix() {
    # 取 EID 后 4 个 hex 字符，作为短 EID
    local eid
    eid="$(eid_normalize "$1")"
    suffix="$eid"
    # 按每4个字符分组，并用冒号连接
    formatted=""
    for ((i=0; i<${#suffix}; i+=4)); do
        chunk="${suffix:$i:4}"
        if [[ -z "$formatted" ]]; then
            formatted="$chunk"
        else
            formatted="$formatted:$chunk"
        fi
    done

    printf '%s\n' "$formatted"
    # printf '%s' "${eid: -20}"
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
# 1. 解析 /lib/route.conf
# -----------------------------
ROUTE_CONF="/lib/route.conf"
if [[ ! -r "$ROUTE_CONF" ]]; then
    echo "# ERROR: $ROUTE_CONF 不存在或不可读" >&2
    exit 1
fi

# 假定 route.conf 是简单的 key=value 格式，直接 source 进来
# shellcheck disable=SC1090
source "$ROUTE_CONF"

if [[ -z "${pair_device_num:-}" ]]; then
    echo "# ERROR: route.conf 中未定义 pair_device_num" >&2
    exit 1
fi

PAIR_NUM=$((pair_device_num))

declare -a HOST_EIDS HOST_IPS
declare -a NPU_INIT_EIDS NPU_INIT_IPS

for ((i = 0; i < PAIR_NUM; i++)); do
    local_key="pair${i}_chan0_local_eid"
    remote_key="pair${i}_chan0_remote_eid"

    local_eid="${!local_key:-}"
    remote_eid="${!remote_key:-}"

    if [[ -z "$local_eid" || -z "$remote_eid" ]]; then
        echo "# ERROR: route.conf 缺少 ${local_key} 或 ${remote_key}" >&2
        exit 1
    fi

    short_local="$(eid_suffix "$local_eid")"
    ip_local="$(eid_to_ip "$local_eid")"

    short_remote="$(eid_suffix "$remote_eid")"
    ip_remote="$(eid_to_ip "$remote_eid")"

    HOST_EIDS[i]="$short_local"
    HOST_IPS[i]="$ip_local"

    NPU_INIT_EIDS[i]="$short_remote"
    NPU_INIT_IPS[i]="$ip_remote"
done

# -----------------------------
# 2. 找到 ranktable.json / topo.json
# -----------------------------

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

RANK_TABLE_FILE="$(get_env_var_from_env_cmd RANK_TABLE_FILE || true)"
HCCL_TOPO_FILE_PATH="$(get_env_var_from_env_cmd HCCL_TOPO_FILE_PATH || true)"

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
if [[ ! -r "$HCCL_TOPO_FILE_PATH" ]]; then
    echo "# ERROR: 无法读取 HCCL_TOPO_FILE_PATH: $HCCL_TOPO_FILE_PATH" >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "# ERROR: 需要 python3，请先安装 python3" >&2
    exit 1
fi

if [[ -z "$RANK_TABLE_FILE" || ! -r "$RANK_TABLE_FILE" ]]; then
    RANK_TABLE_FILE=$ROOTINFO_FILE
fi
# -----------------------------
# 3. 从 ranktable.json 中解析 device_id 列表
# -----------------------------
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

# 简化：参与本机打流的 NPU = 前 PAIR_NUM 个 device_id
DEV_IDS=("${DEV_IDS[@]:0:PAIR_NUM}")

declare -A DEV_IDX
for idx in "${!DEV_IDS[@]}"; do
    dev="${DEV_IDS[$idx]}"
    DEV_IDX["$dev"]="$idx"
done

# 从 topo 文件解析 device_id 列表，并建立 topo_dev -> rootinfo_dev 的映射
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

# -----------------------------
# 4. 构建 (device_id, port) -> (EID/IP) 映射
# -----------------------------
declare -A PORT_EID PORT_IP

# 输出格式：device_id \t port \t addr
while IFS=$'\t' read -r dev port addr; do
    [[ -z "$dev" || -z "$port" || -z "$addr" ]] && continue
    short="$(eid_suffix "$addr")"
    ip="$(eid_to_ip "$addr")"
    key="${dev}|${port}"
    PORT_EID["$key"]="$short"
    PORT_IP["$key"]="$ip"
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

# -----------------------------
# 5. 构建 NPU 间邻接矩阵 ADJ_EID / ADJ_IP (PAIR_NUM x PAIR_NUM)
# -----------------------------
declare -a ADJ_EID ADJ_IP

for ((k = 0; k < PAIR_NUM * PAIR_NUM; k++)); do
    ADJ_EID[k]=""
    ADJ_IP[k]=""
done

# 边列表：local_a, local_a_port, local_b, local_b_port
# 使用 TOPO_TO_ROOTINFO_IDX 映射：topo device_id -> rootinfo 索引
# PORT_EID/PORT_IP 的 key 使用 rootinfo device_id
while IFS=$'\t' read -r la pa lb pb; do
    [[ -z "$la" || -z "$lb" ]] && continue

    # 将 topo device_id 映射到 rootinfo 索引 (0,1,2,...)
    if [[ -z "${TOPO_TO_ROOTINFO_IDX[$la]+_}" || -z "${TOPO_TO_ROOTINFO_IDX[$lb]+_}" ]]; then
        echo "# WARNING: topo device $la 或 $lb 无法映射到 rootinfo，跳过" >&2
        continue
    fi
    i="${TOPO_TO_ROOTINFO_IDX[$la]}"
    j="${TOPO_TO_ROOTINFO_IDX[$lb]}"

    # PORT_EID/PORT_IP 的 key 使用 rootinfo device_id (DEV_IDS[i])
    rootinfo_dev_a="${DEV_IDS[$i]}"
    rootinfo_dev_b="${DEV_IDS[$j]}"

    keyA="${rootinfo_dev_a}|${pa}"
    keyB="${rootinfo_dev_b}|${pb}"

    # A -> B
    if [[ -n "${PORT_EID[$keyA]+_}" ]]; then
        idx=$((i * PAIR_NUM + j))
        ADJ_EID[idx]="${PORT_EID[$keyA]}"
        ADJ_IP[idx]="${PORT_IP[$keyA]}"
    fi

    # B -> A
    if [[ -n "${PORT_EID[$keyB]+_}" ]]; then
        idx=$((j * PAIR_NUM + i))
        ADJ_EID[idx]="${PORT_EID[$keyB]}"
        ADJ_IP[idx]="${PORT_IP[$keyB]}"
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
    # 把两端所有端口两两组合，都输出一遍
    for pa in ports_a:
        for pb in ports_b:
            print(f"{la}\t{pa}\t{lb}\t{pb}")
PY
)

# -----------------------------
# 6. 输出为 Bash 数组定义
#    同时保存到 conf 文件
# -----------------------------

# 用一个大括号把所有 LIST 定义包起来，
# 然后用 tee 同时输出到 stdout 和 conf 文件。
{
    # 先打一行注释：device_index -> device_id
    printf '# device_index -> device_id: '
    for idx in "${!DEV_IDS[@]}"; do
        printf '%s:dev%s ' "$idx" "${DEV_IDS[$idx]}"
    done
    printf '\n'

    printf '\n# ==== EID LISTS ====\n'

    # HOST_EID_LIST
    printf 'HOST_EID_LIST=('
    for v in "${HOST_EIDS[@]}"; do
        printf '"%s" ' "$v"
    done
    printf ')\n'

    # NPU{i}_EID_LIST
    for ((i = 0; i < PAIR_NUM; i++)); do
        eid_list=( "${NPU_INIT_EIDS[$i]}" )
        for ((j = 0; j < PAIR_NUM; j++)); do
            ((j == i)) && continue
            idx=$((i * PAIR_NUM + j))
            eid="${ADJ_EID[$idx]}"
            ip="${ADJ_IP[$idx]}"
            [[ -z "$eid" || -z "$ip" ]] && continue
            eid_list+=("$eid")
        done
        printf 'NPU%d_EID_IPV6_LIST=(' "$i"
        for v in "${eid_list[@]}"; do
            printf '"%s" ' "$v"
        done
        printf ')\n'
    done

    # NPU{i}_EID_LIST
    for ((i = 0; i < PAIR_NUM; i++)); do
        eid_list=( "${NPU_INIT_EIDS[$i]}" )
        for ((j = 0; j < PAIR_NUM; j++)); do
            ((j == i)) && continue
            idx=$((i * PAIR_NUM + j))
            eid="${ADJ_EID[$idx]}"
            ip="${ADJ_IP[$idx]}"
            [[ -z "$eid" || -z "$ip" ]] && continue
            eid_list+=("$eid")
        done
        printf 'NPU%d_EID_LIST=(' "$i"
        for v in "${eid_list[@]}"; do
            printf '"%s" ' "${v: -4}"
        done
        printf ')\n'
    done

    printf '\n# ==== IP LISTS ====\n'

    # HOST_IP_LIST
    printf 'HOST_IP_LIST=('
    for v in "${HOST_IPS[@]}"; do
        printf '"%s" ' "$v"
    done
    printf ')\n'

    # NPU{i}_IP_LIST
    for ((i = 0; i < PAIR_NUM; i++)); do
        ip_list=( "${NPU_INIT_IPS[$i]}" )
        for ((j = 0; j < PAIR_NUM; j++)); do
            ((j == i)) && continue
            idx=$((i * PAIR_NUM + j))
            eid="${ADJ_EID[$idx]}"
            ip="${ADJ_IP[$idx]}"
            [[ -z "$eid" || -z "$ip" ]] && continue
            ip_list+=("$ip")
        done
        printf 'NPU%d_IP_LIST=(' "$i"
        for v in "${ip_list[@]}"; do
            printf '"%s" ' "$v"
        done
        printf ')\n'
    done

# 同时写入文件 + 打印到 stdout
} | tee "$OUTPUT_CONF"

# 如果是直接跑脚本，顺便提示一下
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "# INFO: 已生成 $OUTPUT_CONF" >&2
    # cat "$OUTPUT_CONF" >&2
fi
