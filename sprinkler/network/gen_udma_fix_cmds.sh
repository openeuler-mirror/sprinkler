#!/usr/bin/env bash
# ==============================================================================
#  工具名：gen_udma_fix_cmds.sh
#  作用：
#    基于 /lib/route.conf + 远端 NPU 自检（ip a / ethtool / caps_info / devmem）
#    自动生成寄存器修复 devmem 写入命令
#
#  背景：
#    1) 解析 /lib/route.conf 得到：
#       - pair_device_num
#       - HOST_EID_LIST (0890...089b)
#       - NPU_EID_LIST  (0880...088f)
#    2) 从本机 ip addr 推断 IP 前缀，例如 223.223.8
#       -> 拼出 NPU_IP_LIST，如 223.223.8.128...
#    3) ssh 进 NPU：
#       - 自动识别“需要修复的 ublc 接口”（同名接口有多条 IPv4 -> 3/5 类接口）
#       - ethtool -i 获取 bus-info -> 解析出 ubase -> /sys/kernel/<ubase>/caps_info
#       - 读取 die_id / ue_id / resp_vl
#       - 计算 ue_addr -> devmem 读值 -> 取低 8bit（例：0x..2c）
#       - 计算 udma_base -> 根据 resp_vl（跳过前两个）取 0-15 寄存器地址并生成写入命令
#
#  使用：
#    chmod +x gen_udma_fix_cmds.sh
#    ./gen_udma_fix_cmds.sh
#
#  常用环境变量：
#    ROUTE_CONF=/lib/route.conf
#    SSH_USER=root
#    SSH_PORT=22
#    SSH_OPTS='-o StrictHostKeyChecking=no ...'
#    REMOTE_SUDO=sudo   (远端需要 sudo 执行 devmem 时用；若远端已是 root，设为空：REMOTE_SUDO="")
#
# ==============================================================================

# 配置免密操作
bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/no_password.sh"  2>&1 | grep -E --line-buffered "ERROR|WARN|INFO"

ROUTE_CONF="${ROUTE_CONF:-/lib/route.conf}"
SSH_USER="${SSH_USER:-root}"
SSH_PORT="${SSH_PORT:-22}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -tt}"
REMOTE_SUDO="${REMOTE_SUDO:-}"   # 设为 "" 可禁用
FIRST_ONLY=0
OUT_FILE=""

# ------------------------------- 日志打印 --------------------------------------
_ts() { date '+%F %T'; }
log_info() { echo "[INFO ] $(_ts) $*"; }
log_warn() { echo "[WARN ] $(_ts) $*" >&2; }
log_err()  { echo "[ERROR] $(_ts) $*" >&2; }
die() { log_err "$*"; exit 1; }

[[ -f "$ROUTE_CONF" ]] || die "找不到 ROUTE_CONF：$ROUTE_CONF"

log_info "开始生成修复脚本…"
# log_info "ROUTE_CONF=$ROUTE_CONF, SSH_USER=$SSH_USER, SSH_PORT=$SSH_PORT, REMOTE_SUDO=${REMOTE_SUDO:-<none>}"
# log_info "SSH_OPTS=$SSH_OPTS"

# ----------------------------- 解析 route.conf ---------------------------------
pair_device_num="$(awk -F= '/^\s*pair_device_num\s*=/ {gsub(/[ \t\r]/,"",$2); print $2; exit}' "$ROUTE_CONF")"
[[ -n "${pair_device_num:-}" ]] || die "route.conf 中未找到 pair_device_num"
[[ "$pair_device_num" =~ ^[0-9]+$ ]] || die "pair_device_num 非数字：$pair_device_num"

log_info "pair_device_num=$pair_device_num"

# 从形如 0x....dfdf0890 取最后 4 位：0890
extract_eid4() {
  local v="$1"
  echo "$v" | sed -n 's/.*\([0-9a-fA-F]\{4\}\)\s*$/\1/p' | tr 'A-F' 'a-f'
}

HOST_EIDS=()
NPU_EIDS=()

for ((i=0; i<pair_device_num; i++)); do
  le="$(awk -F= -v k="pair${i}_chan0_local_eid" '$1==k {gsub(/[ \t\r]/,"",$2); print $2; exit}' "$ROUTE_CONF" || true)"
  re="$(awk -F= -v k="pair${i}_chan0_remote_eid" '$1==k {gsub(/[ \t\r]/,"",$2); print $2; exit}' "$ROUTE_CONF" || true)"
  [[ -n "${le:-}" ]] || die "缺少 pair${i}_chan0_local_eid"
  [[ -n "${re:-}" ]] || die "缺少 pair${i}_chan0_remote_eid"
  HOST_EIDS+=("$(extract_eid4 "$le")")
  NPU_EIDS+=("$(extract_eid4 "$re")")
done

# log_info "HOST_EID_LIST=(${HOST_EIDS[*]})"
# log_info "NPU_EID_LIST =(${NPU_EIDS[*]})"

# eid4 (0890) -> low byte (90) -> dec(144)
eid_lowbyte_dec() {
  local eid4="$1"
  local low="${eid4:2:2}"
  echo $((16#$low))
}

# 从本机 IP 推断前三段前缀：优先匹配“最后一段 == HOST0 的低字节十进制”
infer_prefix() {
  local target_last="$1"
  local ips
  ips="$(ip -o -4 addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || true)"
  local ip
  for ip in $ips; do
    local last="${ip##*.}"
    if [[ "$last" == "$target_last" ]]; then
    #   log_info "已通过 HOST0 最后一段匹配推断 IP 前缀：命中本机 IP=$ip (last=$last)"
      echo "${ip%.*}"
      return 0
    fi
  done

  # fallback：取本机第一个全局 IPv4 的前三段
  ip="$(echo "$ips" | head -n1 || true)"
  [[ -n "${ip:-}" ]] || die "本机无全局 IPv4，无法推断 IP 前缀"
  log_warn "未命中 HOST0 last=$target_last，退化使用本机第一个全局 IPv4=$ip 推断前缀"
  echo "${ip%.*}"
}

HOST0_LAST="$(eid_lowbyte_dec "${HOST_EIDS[0]}")"
IP_PREFIX="$(infer_prefix "$HOST0_LAST")"
log_info "IP_PREFIX(前三段)=$IP_PREFIX"

HOST_IPS=()
NPU_IPS=()
for ((i=0; i<pair_device_num; i++)); do
  HOST_IPS+=("${IP_PREFIX}.$(eid_lowbyte_dec "${HOST_EIDS[$i]}")")
  NPU_IPS+=("${IP_PREFIX}.$(eid_lowbyte_dec "${NPU_EIDS[$i]}")")
done

# log_info "HOST_IP_LIST=(${HOST_IPS[*]})"
log_info "NPU_IP_LIST =(${NPU_IPS[*]})"

if [[ "$FIRST_ONLY" -eq 1 ]]; then
  log_warn "启用 --first-only：只对第一个 NPU 生成修复命令"
  NPU_IPS=("${NPU_IPS[0]}")
fi

# --------------------------- 远端探测脚本（核心） -------------------------------
# 远端脚本输出内容：
#  - 以中文注释行开头：# ...
#  - devmem 写命令行：devmem 0x... 32 0x60003
remote_script='
set -euo pipefail
SUDO="${1}"
if [[ -z "${SUDO}" ]]; then SUDO=""; fi

ts(){ date "+%F %T"; }
info(){ echo "# [INFO ] $(ts) $*"; }
warn(){ echo "# [WARN ] $(ts) $*"; }

# 规则：找 ublc* 且“同名接口出现多条 IPv4”
pick_ifaces() {
  ip -o -4 addr show 2>/dev/null \
    | awk "{print \$2}" \
    | sort | uniq -c \
    | awk "\$2 ~ /^ublc/ && \$1 > 1 {print \$2}"
}

# bus-info -> ubase（以 /sys/kernel/debug/ubase/<ubase>/caps_info 是否存在为准）
resolve_ubase() {
  local bus="$1"
  local raw="$bus"
  local hexonly
  hexonly="$(echo "$bus" | tr -cd "0-9a-fA-F" | tr "A-F" "a-f")"

  try_one() {
    local s="$1"
    [[ -n "$s" ]] || return 1
    if [[ -f "/sys/kernel/debug/ubase/$s/caps_info" ]]; then
      echo "$s"; return 0
    fi
    local n="${#s}"
    if [[ "$n" -ge 5 ]]; then
      local last5="${s: -5}"
      if [[ -f "/sys/kernel/debug/ubase/$last5/caps_info" ]]; then
        echo "$last5"; return 0
      fi
    fi
    return 1
  }

  if try_one "$raw"; then return 0; fi
  if try_one "$hexonly"; then return 0; fi

  # 兜底：遍历 /sys/kernel/debug/ubase/* 看有没有可疑匹配
  for d in /sys/kernel/debug/ubase/*; do
    [[ -d "$d" ]] || continue
    local base="${d##*/}"
    [[ -f "$d/caps_info" ]] || continue
    if [[ "$raw" == *"$base"* || "$hexonly" == *"$base"* ]]; then
      echo "$base"; return 0
    fi
  done
  return 1
}

# 从 caps_info 中抽取 die_id/ue_id/resp_vl
get_caps() {
  local caps="$1"
  local die_id ue_id resp_line
  die_id="$(awk "/^[[:space:]]*die_id:/ {print \$2; exit}" "$caps" 2>/dev/null || true)"
  ue_id="$(awk "/^[[:space:]]*ue_id:/ {print \$2; exit}" "$caps" 2>/dev/null || true)"
  resp_line="$(awk -F": *" "/^[[:space:]]*resp_vl:/ {print \$2; exit}" "$caps" 2>/dev/null || true)"
  [[ -n "${die_id}" && -n "${ue_id}" && -n "${resp_line}" ]] || return 1
  echo "$die_id|$ue_id|$resp_line"
}

hex2dec(){ echo $((16#$1)); }
dec2hex(){ printf "0x%x" "$1"; }

devmem_read() {
  local addr_hex="$1"
  if [[ -n "$SUDO" ]]; then
    $SUDO devmem "$addr_hex" 2>/dev/null | tail -n1
  else
    devmem "$addr_hex" 2>/dev/null | tail -n1
  fi
}

main() {
  info "远端主机：$(hostname)  开始探测需要修复的接口…"
  ifaces=( $(pick_ifaces) )
  if [[ "${#ifaces[@]}" -eq 0 ]]; then
    warn "未找到符合规则的 ublc 接口（同名接口多条 IPv4）。可能："
    warn "  1) 当前机器不需要修复；或"
    warn "  2) 接口分配状态与预期不同；请手动 ip a 确认"
    exit 0
  fi

  info "命中接口：${ifaces[*]}"

  for iface in "${ifaces[@]}"; do
    info "------------------------------------------------------------------"
    info "处理接口：$iface"
    bus="$(ethtool -i "$iface" 2>/dev/null | awk -F": *" "/^bus-info:/ {print \$2; exit}" || true)"
    if [[ -z "${bus:-}" ]]; then
      warn "接口 $iface 获取 bus-info 失败（ethtool -i）"
      continue
    fi
    info "bus-info=$bus"

    resolve_ubase "$bus"
    if ! ubase="$(resolve_ubase "$bus")"; then
      warn "无法从 bus-info 推导 ubase（/sys/kernel/debug/ubase/<ubase>/caps_info 不存在）"
      continue
    fi
    caps="/sys/kernel/debug/ubase/$ubase/caps_info"
    info "解析到 ubase=$ubase  caps_info=$caps"

    caps_pack="$(get_caps "$caps" || true)"
    if [[ -z "${caps_pack:-}" ]]; then
      warn "caps_info 解析失败：未取到 die_id/ue_id/resp_vl"
      continue
    fi

    die_id="${caps_pack%%|*}"
    rest="${caps_pack#*|}"
    ue_id="${rest%%|*}"
    resp="${rest#*|}"
    info "die_id=$die_id  ue_id=$ue_id  resp_vl(raw)=$resp"

    # die_base
    if [[ "$die_id" == "0" ]]; then
      die_base=0x2480406
    else
      die_base=0x2c80406
    fi
    base_shift=$(( die_base << 16 ))
    info "die_base=$die_base -> base_shift=$(dec2hex "$base_shift")"

    # ue_addr = base_shift + 0x1000 + ue_id*4
    ue_ofs=$(( ue_id * 4 ))
    ue_addr=$(( base_shift + 0x1000 + ue_ofs ))
    ue_addr_hex="$(dec2hex "$ue_addr")"
    info "ue_addr = base_shift + 0x1000 + ue_id*4 = $ue_addr_hex"

    ue_val="$(devmem_read "$ue_addr_hex" || true)"
    if [[ -z "${ue_val:-}" ]]; then
      warn "devmem 读取 ue_addr 失败：$ue_addr_hex"
      continue
    fi
    info "devmem read $ue_addr_hex => $ue_val"

    # 取低 8bit（最后两位 hex）
    v="$(echo "$ue_val" | awk "{print \$1}" | sed "s/^0x//I" | tr "A-F" "a-f")"
    low2="${v: -2}"
    low_dec="$(hex2dec "$low2")"
    info "ue_val 低8位=0x$low2 (dec=$low_dec)  -> 作为 udma 组号索引"

    # udma_base = base_shift + 0x04000 + (low8*4)
    udma_off=$(( low_dec * 4 ))
    udma_base=$(( base_shift + 0x04000 + udma_off ))
    info "udma_base = base_shift + 0x04000 + low8*4 = $(dec2hex "$udma_base")"

    # resp_vl: 规范化分隔符，跳过前两个值（例子：0 1 15 7 13 -> 取 15/7/13）
    resp_norm="$(echo "$resp" | tr "," " ")"
    read -r -a arr <<< "$resp_norm"

    idxs=()
    if [[ "${#arr[@]}" -gt 2 ]]; then
      for ((k=2; k<${#arr[@]}; k++)); do idxs+=( "${arr[$k]}" ); done
      info "resp_vl 跳过前2个：将使用 idx=(${idxs[*]})"
    else
      idxs=( "${arr[@]}" )
      info "resp_vl 项数<=2：将使用 idx=(${idxs[*]})"
    fi

    # 输出最终写寄存器命令
    info "生成 devmem 写入命令（addr = udma_base + idx*4，写 0x60003）"
    for id in "${idxs[@]}"; do
      [[ "$id" =~ ^[0-9]+$ ]] || { warn "跳过非法 idx=$id"; continue; }
      if (( id < 0 || id > 15 )); then
        warn "跳过越界 idx=$id（合法 0-15）"
        continue
      fi
      addr=$(( udma_base + id * 4 ))
      echo "devmem $(dec2hex "$addr") 32 0x60003"
    done
  done
}

main
'

# --------------------------- 写入每个 NPU 的块 ---------------------------------
append_block_for_npu() {
  local ip="$1"

  {
    echo
    echo "==================== 处理 NPU：$ip ===================="
    echo "提示：若 NPU 需要 sudo 权限，可能会弹出密码输入（仅首次）"
    echo
  }

  # 远端只做“探测+计算+输出命令”，不真正写；真正写在 apply 脚本中执行
  log_info " NPU 探测 NPU=$ip（计算地址/生成 devmem 写命令）…"

  set +e
  remote_out="$(
    ssh "${SSH_USER}@${ip}" \
      "bash -s -- \"${REMOTE_SUDO}\"" <<< "$remote_script" 2>&1
  )"
  rc=$?
  set -e

  if [[ $rc -ne 0 ]]; then
    # log_warn "远端探测失败：$ip (rc=$rc) — 已把原始输出写入 apply 脚本中，便于排查"
    {
      echo " NPU 探测失败：$ip (rc=$rc)"
      echo "$remote_out"
      echo
    }
    return 0
  fi

  # 把远端的详细解释（# 开头）也写进 apply，方便回溯
  {
    echo "---  NPU 探测信息（供回溯）---"
    echo "$remote_out"
    echo "---  NPU 探测信息结束 ---"
    echo
  }

  # 把 devmem 写命令转换成真正执行的 ssh 命令
  local count=0
  echo ssh "${SSH_USER}@${ip}"
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    if [[ "$line" =~ ^devmem\  ]]; then
      ((count++)) || true
      if [[ -n "${REMOTE_SUDO:-}" ]]; then
        echo "${REMOTE_SUDO} $line"
      else
        echo "$line"
      fi
    fi
  done <<< "$remote_out"

  if [[ "$count" -eq 0 ]]; then
    {
      echo
      echo "NPU=$ip 未生成任何 devmem 写命令（可能未命中接口或 resp_vl 不符合预期"
      echo
    }
  else
    {
      echo
      echo "NPU=$ip 共生成 $count 条 devmem 写入命令"
      echo
    }
  fi
}

# ------------------------------- 主循环 ----------------------------------------
# log_info "准备对以下 NPU 生成修复命令：(${NPU_IPS[*]})"
# for ip in "${NPU_IPS[@]}"; do
#   append_block_for_npu "$ip"
# done
log_info "准备对 NPU0 生成修复命令：(${NPU_IPS[0]})"
append_block_for_npu "${NPU_IPS[0]}"
