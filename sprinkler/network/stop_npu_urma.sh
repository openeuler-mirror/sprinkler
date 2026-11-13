#!/usr/bin/env bash

CHECK_ID="env_ping"

# -------- 参数配置 --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/common.sh"

# 配置参数
config_require_keys \
    "POD_NPU_USER|请输入连接NPU用户名|root|0|validate_non_empty" \
    "POD_NPU_PASSWORD|请输入登录NPU密码(输入不回显)||1|validate_non_empty"

# -------- config (can be overridden by env) --------
PING_COUNT="${PING_COUNT:-2}"
PING_TIMEOUT="${PING_TIMEOUT:-3}"
CONFIG_FILE="${CONFIG_FILE:-${PARENT_DIR}/network/npu_ip_map.conf}"
GEN_CONF_PATH="${GEN_CONF_PATH:-${PARENT_DIR}/network/gen_npu_ip_map.sh}"

chmod +x $GEN_CONF_PATH
# -------- 获取设备列表 --------
log INFO "$CHECK_ID" "获取NPU设备列表..."
DEVICES=($(bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/npuip.sh"))

if [ ${#DEVICES[@]} -eq 0 ]; then
    log FAIL "$CHECK_ID" "未找到任何NPU设备"
    exit 1
fi

log INFO "$CHECK_ID" "发现 ${#DEVICES[@]} 个NPU设备"

# -------- Host到Device连通性检测 --------
log INFO "$CHECK_ID" "开始Host到Device网络连通性检测..."

# 保存通过的设备列表
declare -a REACHABLE_DEVICES
declare -a REACHABLE_INDICES

count_ok=0
count_fail=0
for i in "${!DEVICES[@]}"; do
    target_ip="${DEVICES[$i]}"
    
    if ping -c "$PING_COUNT" -W "$PING_TIMEOUT" "$target_ip" >/dev/null 2>&1; then
        log OK "$CHECK_ID" "Host_to_Device${i}(${target_ip}) - Ping成功"
        REACHABLE_DEVICES+=("$target_ip")
        REACHABLE_INDICES+=("$i")
        ((count_ok++))
    else
        log FAIL "$CHECK_ID" "Host_to_Device${i}(${target_ip}) - Ping失败"
        ((count_fail++))
    fi
done

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


# -------- 生成NPU IP映射配置 --------
log INFO "$CHECK_ID" "生成NPU IP映射配置..."
bash "$GEN_CONF_PATH" >/dev/null 2>&1

# 检查配置文件是否存在
if [[ ! -f "$CONFIG_FILE" ]]; then
    log FAIL "$CHECK_ID" "配置文件 $CONFIG_FILE 不存在"
    log FAIL "$CHECK_ID" "请手动执行 bash $GEN_CONF_PATH"
    exit 1
fi

# 引入NPU IP映射配置
source "$CONFIG_FILE"
log INFO "$CHECK_ID" "NPU IP映射配置加载完成"

# -------- Device到Device连通性检测（只测试可达设备） --------
log INFO "$CHECK_ID" "开始Device到Device网络连通性检测（仅测试${#REACHABLE_DEVICES[@]}个可达设备）..."

count_ok=0
count_fail=0

# 对每个可达设备进行测试
for j in "${!REACHABLE_INDICES[@]}"; do
    i="${REACHABLE_INDICES[$j]}"
    SERVER="${DEVICES[$i]}"
    
    log INFO "$CHECK_ID" "检查Device${i}(${SERVER})上的urma进程状态..."
    
    # 第一步：检查urma进程是否存在
    if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$SERVER" \
         pgrep urma >/dev/null 2>&1; then
        log WARN "$CHECK_ID" "Device${i}(${SERVER}) - 找到urma进程，开始终止..."
        
        # 第二步：执行三次pkill -9 urma确保进程终止
        stop_success=true
        for attempt in {1..3}; do
            if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$SERVER" \
                 pkill -9 urma >/dev/null 2>&1; then
                log INFO "$CHECK_ID" "Device${i}(${SERVER}) - 第${attempt}次终止尝试成功"
            else
                log INFO "$CHECK_ID" "Device${i}(${SERVER}) - 第${attempt}次终止尝试未找到进程"
            fi
            # 短暂等待后再次检查
            sleep 0.5
        done
        
        # 第三步：确认进程是否已被终止
        if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$SERVER" \
             pgrep urma >/dev/null 2>&1; then
            log FAIL "$CHECK_ID" "Device${i}(${SERVER}) - urma进程仍然存在，终止失败"
            ((count_fail++))
        else
            log OK "$CHECK_ID" "Device${i}(${SERVER}) - urma进程已成功终止"
            ((count_ok++))
        fi
    else
        log OK "$CHECK_ID" "Device${i}(${SERVER}) - 未找到urma进程"
        ((count_fail++))
    fi
done