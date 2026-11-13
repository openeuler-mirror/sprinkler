#!/bin/bash

dmesg_path=/var/log/messages

### 清理CPU日志 ###
rm -rf ${dmesg_path}/*

### 清理NPU日志 ###

# 获取当前NPU IP
SERVERS=($(bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/npuip.sh"))

# 配置免密操作
# [DEPRECATED] 引用文件已不存在，功能由 sprinkler -no-pass 取代
# bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/no_password.sh"  2>&1 | grep -E --line-buffered "ERROR|WARN|INFO"
:  # no-op（原命令已下线）

# 为每个服务器清理日志
for i in "${!SERVERS[@]}";
do
    SERVER="${SERVERS[$i]}"
    NPU_NUM=$i

    echo "正在清理 NPU$NPU_NUM ($SERVER) 的日志..."

    # 在远程服务器执行rm清理文件
    ssh root@$SERVER "rm -rf ${dmesg_path}/*"

    # 检查是否成功
    if [ $? -eq 0 ] && [ -s "$LOCAL_DMESG_FILE" ] && [ -s "$LOCAL_MESSAGES_FILE" ]; then
        echo "✓ NPU$NPU_NUM 日志收集成功"
    else
        echo "✗ NPU$NPU_NUM 日志收集失败"
    fi
    # 删除后的检查
    COUNT=$(ssh root@$SERVER "find $dmesg_path -type f | wc -l")
    if [ "$COUNT" -eq 0 ]; then
        echo "✅ NPU dmesg 删除成功！目录已清空"
    else
        echo "❌ NPU dmesg 删除失败！还有 $COUNT 个文件"
        ssh  root@$SERVER "ls -la $dmesg_path/"
    fi
done

rm -rf /root/ascend/log/*
rm -rf /root/ascend/atrace/*

# 删除后的检查
echo "删除后的检查："
COUNT=$(find /root/ascend/log -type f | wc -l)
if [ "$COUNT" -eq 0 ]; then
    echo "✅ 删除成功！目录已清空"
else
    echo "❌ 删除失败！还有 $COUNT 个文件"
    ls -la /root/ascend/log/
fi
echo "所有日志清理完成！"
