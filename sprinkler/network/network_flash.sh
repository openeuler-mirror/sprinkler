#!/bin/bash

echo "[Info][Network] Vertify Network flash..."
npunum=$(lspci | grep d80 | wc -l)
for i in $(seq 0 $(($npunum-1))) 
do 
flash_context=$(hccn_tool -i $i -link_stat -g)
machine_id=$(echo "$flash_context" | grep -oP '\[devid \K[0-9]+' | sort -u)
up_down=$(echo "$flash_context" | grep "LINK")

# 定义变量存储闪断记录
flash_count=0
prev_status=""
prev_time=0
during_time=0
flash_records=""

# 提取链路状态记录
while IFS= read -r line; do
    # 提取时间戳和链路状态
    timestamp=$(echo "$line" | awk '{print $3, $4, $5, $6, $7}')
    status=$(echo "$line" | awk '{print $8, $9}')
    if [[ $status == "LINK UP" ]]; then
        prev_status=$status
        prev_time=$timestamp
    elif [[ $prev_status == "LINK UP" && $status == "LINK DOWN" ]]; then
        flash_start_time=$(date -d "$timestamp" +%s)
        flash_end_time=$(date -d "$prev_time" +%s)
        flash_duration=$((flash_end_time - flash_start_time + 1))
        flash_count=$((flash_count + 1))
        flash_records+="[Info][Network] \033[31mFlash $flash_count\033[0m: Started at $(date -d "@$flash_start_time" '+%Y-%m-%d %H:%M:%S'), "
        flash_records+="Ended at $(date -d "@$flash_end_time" '+%Y-%m-%d %H:%M:%S'), Duration: $flash_duration seconds\n"
    else
        echo -e "[\033[31mERROR\033[0m][Network] link $machine_id is down!"
    fi
done <<< "$up_down"

# 输出结果
echo "[Info][Network] Device $machine_id: "
echo "[Info][Network] Total Flash Counts: $flash_count"
echo -e $flash_records

done
