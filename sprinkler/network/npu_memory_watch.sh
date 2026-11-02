#!/bin/bash

RED='\033[1;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;34m'
NC='\033[0m'

# 服务器列表
DEVICES=($(bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/npuip.sh"))
bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/no_password.sh" 2>&1 | grep -E --line-buffered "ERROR"
# 主循环
for i in "${!DEVICES[@]}"; do
    DEVICE="${DEVICES[$i]}"
    NPU_NUM=$i

    # 获取内存信息
    total_kb=$(ssh root@$DEVICE "cat /sys/devices/system/node/node*/meminfo | grep MemTotal" 2>/dev/null | awk '!/Node 0/{sum += $4} END {print sum+0}')

    free_1gb=$(ssh root@$DEVICE "cat /sys/devices/system/node/node*/hugepages/hugepages-1048576kB/free_hugepages" 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
    free_2mb=$(ssh root@$DEVICE "cat /sys/devices/system/node/node*/hugepages/hugepages-2048kB/free_hugepages" 2>/dev/null | awk '{sum+=$1} END {print sum+0}')
    free_other_kb=$(ssh root@$DEVICE "cat /sys/devices/system/node/node*/meminfo | grep MemFree" 2>/dev/null | awk '!/Node 0/{sum += $4} END {print sum+0}')

    # 计算总内存和可用内存（KB）
    free_kb=$((free_1gb * 1048576 + free_2mb * 2048 + free_other_kb))
    used_kb=$((total_kb - free_kb))

    # 转换为GB
    total_gb=$((total_kb / 1048576))
    free_gb=$((free_kb / 1048576))
    used_gb=$((used_kb / 1048576))

    # 计算百分比
    if [ $total_gb -gt 0 ]; then
        usage_percent=$((used_gb * 100 / total_gb))
        free_percent=$((100 - usage_percent))
    else
        usage_percent=0
        free_percent=0
    fi

    echo -e "正在查询 NPU$NPU_NUM 内存使用情况:" "总内存: ${total_gb} GB" "${RED}已使用: ${used_gb} GB (${usage_percent}%)${NC}"  "${YELLOW}可用内存: ${free_gb} GB (${free_percent}%)${NC}"
    echo
done

echo "所有NPU查询内存成功！！！"
