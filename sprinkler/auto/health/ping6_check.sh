#!/bin/bash

# -------- 引入公共库 --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../sprinkler/utils/common.sh"
# echo "$# $1 $2 $3"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -npuid=*)
        NPU_ID="${1#*=}"
        shift
        ;;
        -npuid)
        NPU_ID="${2:-}"
        shift 2
        ;;  
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            # 检查是否是不认识的选项（以-开头）
            if [[ "$1" =~ ^- ]]; then
                echo "Unknown: $1"
                show_help
                exit 1
            fi
            ;;
    esac
done

# 接收NPU ID参数
CHECK_ID="ping_check"
# -------- 参数配置 --------
log INFO "$CHECK_ID" "正在加载配置参数..."

# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../sprinkler/utils/config_util.sh"

# 配置参数
config_require_keys \
    "POD_NPU_USER|请输入连接NPU用户名|root|0|validate_non_empty" \
    "POD_NPU_PASSWORD|请输入登录NPU密码(输入不回显)||1|validate_non_empty" \
    "HCCL_TOPO_FILE_PATH|请输入topo的json配置文件路径|/etc/superpod_1d_noroce.json|0|" \
    "ROOTINFO_FILE|请输入rootinfo的json配置文件路径|/etc/hccl_rootinfo.json|0|"

# -------- config (can be overridden by env) --------
PAIR_NUM="${PAIR_NUM:-8}"
PING_COUNT="${PING_COUNT:-2}"
PING_TIMEOUT="${PING_TIMEOUT:-3}"
NPU_IP_MAP_CONF_FILE="${NPU_IP_MAP_CONF_FILE:-${PARENT_DIR}/network/npu_ip_map.conf}"
GEN_CONF_PATH="${GEN_CONF_PATH:-${PARENT_DIR}/network/gen_npu_ipv6_map.sh}"

chmod +x $GEN_CONF_PATH
# -------- 获取设备列表 --------
log INFO "$CHECK_ID" "获取NPU设备列表..."
DEVICES=($(bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../../sprinkler/utils/npuip.sh"))

if [ ${#DEVICES[@]} -eq 0 ]; then
    log FAIL "$CHECK_ID" "未找到任何NPU设备"
    exit 1
fi

log INFO "$CHECK_ID" "检测到 ${#DEVICES[@]} 个设备需要测试"

# Host到Device的连通性检测
log INFO "$CHECK_ID" "开始 Host --> Device 连通性检测..."

# 保存通过的设备列表
declare -a REACHABLE_DEVICES
declare -a REACHABLE_INDICES

count_ok=0
count_fail=0

for i in "${!DEVICES[@]}"; do
    target_ip="${DEVICES[$i]}"
    
    if ping -c "$PING_COUNT" -W "$PING_TIMEOUT" "$target_ip" >/dev/null 2>&1; then
        log OK "$CHECK_ID" "Host_to_Device${i} ($target_ip) - Ping成功"
        REACHABLE_DEVICES+=("$target_ip")
        REACHABLE_INDICES+=("$i")
        ((count_ok++))
    else
        log FAIL "$CHECK_ID" "Host_to_Device${i} ($target_ip) - Ping失败"
        ((count_fail++))
    fi
done

ok_number=$count_ok
log INFO "$CHECK_ID" "Host可达设备列表: ${REACHABLE_DEVICES[*]}"
log INFO "$CHECK_ID" "对应设备索引: ${REACHABLE_INDICES[*]}"

if [ $count_fail -eq 0 ]; then
    log OK "$CHECK_ID" "Host --> Device 网络连通性检测结果: 全部成功 ($count_ok/$count_ok)"
else
    log FAIL "$CHECK_ID" "Host --> Device 网络连通性检测结果: 成功 $count_ok, 失败 $count_fail"
fi

# 如果没有可达设备，直接退出
if [ ${#REACHABLE_DEVICES[@]} -eq 0 ]; then
    log FAIL "$CHECK_ID" "没有可达的NPU设备，无法进行Device到Device测试"
    exit 1
fi

# 生成NPU IP映射文件
log INFO "$CHECK_ID" "正在生成NPU IP映射配置..."
bash "$GEN_CONF_PATH" >/dev/null 2>&1

# 检查配置文件是否存在
if [[ ! -f "$NPU_IP_MAP_CONF_FILE" ]]; then
    log FAIL "$CHECK_ID" "配置文件 $NPU_IP_MAP_CONF_FILE 不存在"
    log FAIL "$CHECK_ID" "请手动执行 bash $GEN_CONF_PATH"
    exit 1
fi

# 引入NPU IP映射配置
log INFO "$CHECK_ID" "加载NPU IP映射配置文件: $NPU_IP_MAP_CONF_FILE"
source "${NPU_IP_MAP_CONF_FILE}"

# Device到Device的连通性检测
log INFO "$CHECK_ID" "开始Device到Device网络连通性检测（仅测试${#REACHABLE_DEVICES[@]}个可达设备）..."

choose_main_by_day_mod_pairnum() {
  # 输入：可用 npu 列表（范围∩pingOK）
  local -a ok=("$@")
  (( ${#ok[@]} > 0 )) || return 1

  local doy start
  doy="$(date +%j)"              # 001..366
  start=$((10#$doy % PAIR_NUM))  # 0..PAIR_NUM-1

  # membership set
  local -A set=()
  local x
  for x in "${ok[@]}"; do set["$x"]=1; done

  # 从 start 往后找，循环一圈
  local k cand
  for ((k=0; k<PAIR_NUM; k++)); do
    cand=$(((start + k) % PAIR_NUM))
    if [[ -n "${set[$cand]+_}" ]]; then
      echo "$cand"
      return 0
    fi
  done
  # 理论不会到这里
  echo "${ok[0]}"
}

# MAIN_ARG 绑定到 D2D_MAIN_NPU（命令行优先于环境变量）
D2D_MAIN_NPU="${D2D_MAIN_NPU:-}"
if [[ -n "$NPU_ID" ]]; then
  # 去掉可能的空白
  MAIN_NPU="${NPU_ID//[[:space:]]/}"
  # 只允许 -1 或非负整数
  if ! [[ "$MAIN_NPU" =~ ^-?[0-9]+$ ]] || { [[ "$MAIN_NPU" == -* && "$MAIN_NPU" != "-1" ]]; }; then
    echo "# ERROR: -npuid 参数非法（只允许 -1 或 0..N）：$MAIN_NPU" >&2
    exit 1
  fi
  D2D_MAIN_NPU="$MAIN_NPU"
fi

if [[ -n "${D2D_MAIN_NPU:-}" ]]; then
    MAIN_NPU=$D2D_MAIN_NPU
else
    # 自动 main：doy%PAIR_NUM + 循环
    MAIN_NPU="$(choose_main_by_day_mod_pairnum "${REACHABLE_INDICES[@]}")"
    log "INFO" "$CHECK_ID" "D2D 主卡自动选择：MAIN=NPU${MAIN_NPU}（doy%8）"
fi
# 确保 NPU_ID 被正确设置
NPU_ID="$MAIN_NPU"

count_ok=0
count_fail=0

if [ $NPU_ID -eq -1 ]; then
    for reach_id in "${!REACHABLE_INDICES[@]}"; do
        npu_id=${REACHABLE_INDICES[$reach_id]}
        log INFO "$CHECK_ID" "==================== MAIN_NPU_ID=${npu_id} ===================="
        # 对每个可达设备进行测试
        for j in "${!REACHABLE_INDICES[@]}"; do
            i="${REACHABLE_INDICES[$j]}"
            SERVER="${DEVICES[$i]}" 
            # 跳过自己ping自己
            if [[ "$i" == "$npu_id" ]]; then
                traget_id=$i
                local_id=$npu_id
                target="NPU${npu_id}_EID_IPV6_LIST"
                target_ip_s=NPU${npu_id}_EID_IPV6_LIST[$traget_id]
                local_ip_s=NPU${i}_EID_IPV6_LIST[$local_id]
                log INFO "$CHECK_ID" "测试Device${i}到Device$npu_id..."
                if ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} >/dev/null 2>&1; then
                    log OK "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping成功"
                    ((count_ok++))
                else
                    log FAIL "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping失败"
                    ((count_fail++))
                fi
            elif [[ $i -lt $npu_id ]]; then
                traget_id=$((i + 1))
                local_id=$npu_id
                target="NPU${npu_id}_EID_IPV6_LIST"
                target_ip_s=NPU${npu_id}_EID_IPV6_LIST[$traget_id]
                local_ip_s=NPU${i}_EID_IPV6_LIST[$local_id]
                log INFO "$CHECK_ID" "测试Device${i}到Device$npu_id..."
                if ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} >/dev/null 2>&1; then
                    log OK "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping成功"
                    ((count_ok++))
                else
                    log FAIL "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping失败"
                    ((count_fail++))
                fi
            elif [[ $i -gt $npu_id ]]; then
                traget_id=$i
                local_id=$((npu_id + 1))
                target="NPU${npu_id}_EID_IPV6_LIST"
                target_ip_s=NPU${npu_id}_EID_IPV6_LIST[$traget_id]
                local_ip_s=NPU${i}_EID_IPV6_LIST[$local_id]
                log INFO "$CHECK_ID" "测试Device${i}到Device$npu_id..."
                if ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} >/dev/null 2>&1; then
                    log OK "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping成功"
                    ((count_ok++))
                else
                    log FAIL "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping失败"
                    ((count_fail++))
                fi
            fi
        done
    done
else
    # 对每个可达设备进行测试
    log INFO "$CHECK_ID" "==================== MAIN_NPU_ID=${NPU_ID} ===================="
    for j in "${!REACHABLE_INDICES[@]}"; do
        i="${REACHABLE_INDICES[$j]}"
        SERVER="${DEVICES[$i]}"
        # 跳过自己ping自己
        if [[ "$i" == "$NPU_ID" ]]; then
            traget_id=$i
            local_id=$NPU_ID
            target="NPU${NPU_ID}_EID_IPV6_LIST"
            target_ip_s=NPU${NPU_ID}_EID_IPV6_LIST[$traget_id]
            local_ip_s=NPU${i}_EID_IPV6_LIST[$local_id]
            log INFO "$CHECK_ID" "测试Device${i}到Device$NPU_ID..."
            if ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} >/dev/null 2>&1; then
                log OK "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping成功"
                ((count_ok++))
            else
                log FAIL "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping失败"
                ((count_fail++))
            fi
        elif [[ $i -lt $NPU_ID ]]; then
            traget_id=$((i + 1))
            local_id=$NPU_ID
            target="NPU${NPU_ID}_EID_IPV6_LIST"
            target_ip_s=NPU${NPU_ID}_EID_IPV6_LIST[$traget_id]
            local_ip_s=NPU${i}_EID_IPV6_LIST[$local_id]
            log INFO "$CHECK_ID" "测试Device${i}到Device$NPU_ID..."
            if ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} >/dev/null 2>&1; then
                log OK "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping成功"
                ((count_ok++))
            else
                log FAIL "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping失败"
                ((count_fail++))
            fi
        elif [[ $i -gt $NPU_ID ]]; then
            traget_id=$i
            local_id=$((NPU_ID + 1))
            target="NPU${NPU_ID}_EID_IPV6_LIST"
            target_ip_s=NPU${NPU_ID}_EID_IPV6_LIST[$traget_id]
            local_ip_s=NPU${i}_EID_IPV6_LIST[$local_id]
            log INFO "$CHECK_ID" "测试Device${i}到Device$NPU_ID..."
            if ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} >/dev/null 2>&1; then
                log OK "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping成功"
                ((count_ok++))
            else
                log FAIL "$CHECK_ID" "ssh $SERVER ping6 -c "$PING_COUNT" -W "$PING_TIMEOUT" ${!target_ip_s} -I ${!local_ip_s} - Ping失败"
                ((count_fail++))
            fi
        fi
    done
fi

if [ $NPU_ID -eq -1 ]; then
    if [ $count_fail -eq 0 ]; then
        log OK "$CHECK_ID" "Device --> Device 网络连通性检测结果: 全部成功 ($count_ok/$(( ${#REACHABLE_DEVICES[@]} * $ok_number )))"
    else
        log WARN "$CHECK_ID" "Device --> Device 网络连通性检测结果: 成功 $count_ok, 失败 $count_fail (共${#REACHABLE_DEVICES[@]}个可达设备)"
    fi
else
    if [ $count_fail -eq 0 ]; then
        log OK "$CHECK_ID" "Device --> Device 网络连通性检测结果: 全部成功 ($count_ok/${#REACHABLE_DEVICES[@]})"
    else
        log WARN "$CHECK_ID" "Device --> Device 网络连通性检测结果: 成功 $count_ok, 失败 $count_fail (共${#REACHABLE_DEVICES[@]}个可达设备)"
    fi
fi
# -------- 总体结果统计 --------
total_host_tests=${#DEVICES[@]}
total_device_tests=${#REACHABLE_DEVICES[@]}

log INFO "$CHECK_ID" "测试统计:"
log INFO "$CHECK_ID" "  - 总设备数: $total_host_tests"
log INFO "$CHECK_ID" "  - Host可达设备数: ${#REACHABLE_DEVICES[@]}"
log INFO "$CHECK_ID" "  - Device间测试数: $total_device_tests"

# -------- 生成详细结果信息 --------
generate_detailed_result() {
    if [ $NPU_ID -eq -1 ]; then
        local total_devices=${#DEVICES[@]}
        local reachable_devices=${#REACHABLE_DEVICES[@]}
        local device_total_num=$((reachable_devices * reachable_devices))
        local device_success=$count_ok
    else
        local total_devices=${#DEVICES[@]}
        local reachable_devices=${#REACHABLE_DEVICES[@]}
        local device_total_num=$reachable_devices
        local device_success=$count_ok
    fi
    
    if [ $count_fail -eq 0 ] && [ $reachable_devices -eq $total_devices ]; then
        output_auto_details OK "$CHECK_ID" "Ping H2D:${total_devices}/${total_devices}, D2D:${device_success}/${device_total_num}"
    else
        # 有设备不可达
        if [ $reachable_devices -gt 0 ]; then
            if [ $device_success -gt 1 ]; then
                # 所有设备可达但D2D测试有失败
                output_auto_details WARN "$CHECK_ID" "Ping D2D:${device_success}/${device_total_num}"
            else
                output_auto_details FAIL "$CHECK_ID" "Ping D2D:${device_success}/${device_total_num}"
            fi
        else
            output_auto_details FAIL "$CHECK_ID" "Ping H2D:${reachable_devices}/${total_devices}"
        fi
    fi
}

# 输出详细结果（供auto.sh捕获）
generate_detailed_result

if [ $count_fail -eq 0 ] && [ ${#REACHABLE_DEVICES[@]} -eq ${#DEVICES[@]} ]; then
    log OK "$CHECK_ID" "网络连通性检测完成: 所有测试均通过"
    exit 0
elif [ $count_fail -eq 0 ]; then
    log OK "$CHECK_ID" "网络连通性检测完成: 可达设备间的连通性全部通过"
    log FAIL "$CHECK_ID" "注意: 有 $(( ${#DEVICES[@]} - ${#REACHABLE_DEVICES[@]} )) 个设备Host不可达"
    exit 1
else
    log WARN "$CHECK_ID" "网络连通性检测完成: 存在 $count_fail 个Device间连接失败"
    exit 1
fi
