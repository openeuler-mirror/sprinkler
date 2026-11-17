#!/bin/bash

# =========配置项=========
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

# 配置参数
config_require_keys \
    "qemu_device_num|配置设备数量|8|0|validate_non_empty" \
    "qemu_server_ip|配置服务器ip|141.61.24.37|0|validate_ip" \
    "qemu_port_cpu|配置cpu容器对应的port(默认10112)|10112|0|validate_port" \
    "qemu_npu_start_port|配置起始NPU（NPU0）容器对应的port(默认10032)|10032|0|validate_port" \
    "qemu_ip_prefix|NPU网卡前缀，可在NPU侧ifconfig查看|174.10.100|0|validate_non_empty" \
    "qemu_ip_mami|配置1213npu ip|114|0|validate_port" \
    "qemu_password_cpu|配置容器对应的密码||1|validate_non_empty" \
    "qemu_password_mami|配置1213npu容器对应的密码||1|validate_non_empty" \
    "qemu_password_remote|配置远端ssh容器对应的密码||1|validate_non_empty"

# 配置1213npu ip
qemu_ip_mami="$qemu_ip_prefix.$qemu_ip_mami"

max_device_id=$((device_num - 1))
# 配置日志储存目录
save_dir="/mnt/logs"
# 配置CPU log的日志目录
log_dir_cpu="/workdir/runtime/log/"
# 配置需要收集的日志目录
log_dirs_clean_raw=(
    # CCU日志
    "/workdir/runtime/ccu_log*.dump"
    # STARS日志
    "/workdir/runtime/stars_log*.dump"
    # ubus日志
    "/workdir/runtime/log/*ubus_log*.dump"
    "/workdir/runtime/log/*ubus_log_ummu*.log"
    "/workdir/runtime/linx_npu/log"
)
# =======================

# ========= 日志清理 =========
echo "[Info][Device] 正在清理日志..."

# 清理 CPU plog
echo "[Info][CPU] 清理 CPU plog..."
rm -rf /root/ascend/*
echo "[Info][CPU] 清理 CPU plog 完成"

# 清理 NPU slog
for idx in $(seq 0 $max_device_id); do
    ip_npu="$qemu_ip_prefix.1$idx"
    echo "[Info][Device] 清理 NPU[$idx] IP[$ip_npu] slog..."
    sshpass -p "$qemu_password_remote" ssh -o StrictHostKeyChecking=no \
        "root@$ip_npu" "rm -rf /var/log/npu/slog"
done
echo "[Info][Device] 清理 NPU slog 完成"

# 清理 NPU 仿真日志
for idx in $(seq 0 $max_device_id); do
    port=$((qemu_npu_start_port + idx * 10))
    echo "[Info][Device] 清理 NPU[$idx] IP[$qemu_server_ip] qemu log..."
    for log_dir in "${log_dirs_clean_raw[@]}"; do
        sshpass -p "$qemu_password_cpu" ssh -p "$port" -o StrictHostKeyChecking=no \
            "root@$qemu_server_ip" "rm -rf $log_dir"
    done
done
echo "[Info][Device] 清理 NPU qemu log 完成"