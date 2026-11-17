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

# 配置日志储存目录
save_dir="/mnt/logs"
# 配置CPU log的日志目录
log_dir_cpu="/workdir/runtime/log/"
# 配置需要收集的日志目录
log_dirs=(
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

# ========= 输出参数 =========
echo "[Info][Device] NPU设备数量为: $qemu_device_num"

# ========= 准备工作 =========
max_device_id=$((qemu_device_num - 1))
now_time=$(date +"%Y%m%d_%H%M")
dir_name="log_${qemu_device_num}p_${now_time}"
save_path="$save_dir/$dir_name"
mkdir -p "$save_path"
save_path_cpu="$save_path/cpu"
save_path_npu="$save_path/npu"
mkdir -p "$save_path_cpu"
mkdir -p "$save_path_npu"

# ========= 收集 CPU plog =========
echo "[Info][CPU] 保存 CPU plog..."
save_path_cpu_plog="$save_path_cpu/plog/"
mkdir -p "$save_path_cpu_plog"
cp -r /root/ascend/* "$save_path_cpu_plog"
echo "[Info][CPU] 保存 CPU plog 完成"

# ========= 收集 CPU log =========
echo "[Info][CPU] 保存 CPU log..."
sshpass -p "$qemu_password_cpu" scp -r \
  -o StrictHostKeyChecking=no \
  -P "$qemu_port_cpu" \
  "$qemu_server_ip:$log_dir_cpu" \
  "$save_path_cpu/"
echo "[Info][CPU] 保存 CPU log 完成"

# ========= 收集 NPU slog =========
for idx in $(seq 0 $max_device_id); do
    ip="$qemu_ip_prefix.1$idx"
    echo "[Info][Device] 保存 NPU[$idx] IP[$ip] slog..."
    sshpass -p "$qemu_password_remote" scp -r \
      -o StrictHostKeyChecking=no \
      "$ip:/var/log/npu/slog" \
      "$save_path_npu/slog_$idx"
done
echo "[Info][Device] 保存 NPU slog 完成"

# ========= 收集 1213NPU mami 日志 =========
echo "[Info][MAMI] 保存 mami log..."
sshpass -p "$qemu_password_mami" scp -r \
  -o StrictHostKeyChecking=no \
  "$qemu_ip_mami:/opt/vrpv8/var/log/cpdt_journal*" \
  "$save_path"
echo "[Info][MAMI] 保存 mami log 完成"

# ========= 收集仿真日志 =========
for idx in $(seq 0 $max_device_id); do
    sim_log_path="$save_path_npu/npu${idx}_sim_log"
    mkdir -p "$sim_log_path"
    port=$((qemu_npu_start_port + idx * 10))
    echo "[Info][Device] 保存 NPU[$idx] IP[$qemu_server_ip] qemu log..."

    # 遍历 log_dirs_raw 并安全执行
    for log_dir in "${log_dirs[@]}"; do
        # 使用 jq 解析数组，但需要处理 JSON 数组
        # 这里我们直接用 bash 字符串替换
        sshpass -p "$qemu_password_cpu" scp -r \
          -o StrictHostKeyChecking=no \
          -P "$port" \
          "$qemu_server_ip:$log_dir" \
          "$sim_log_path/"
    done
done
echo "[Info][Device] 保存 NPU qemu log 完成"

# ========= 收集 CPU dmesg =========
echo "[Info][CPU] 保存 CPU dmesg log..."
mkdir -p "$save_dir/$dir_name/dmesg"
dmesg -T > "$save_dir/$dir_name/dmesg/host_dmesg.txt"
echo "[Info][CPU] 保存 CPU dmesg log 完成"

# ========= 收集 NPU dmesg =========
echo "[Info][Device] 保存 NPU dmesg log..."
mkdir -p "$save_dir/$dir_name/dmesg"
for idx in $(seq 0 $max_device_id); do
    ip_npu="${qemu_ip_prefix}.1$idx"
    remote_file="npu${idx}_dmesg.txt"
    sshpass -p "$qemu_password_remote" ssh -o StrictHostKeyChecking=no \
      "root@$ip_npu" "dmesg > /tmp/$remote_file"
    sshpass -p "$qemu_password_remote" scp -r -o StrictHostKeyChecking=no \
      "$ip_npu:/tmp/$remote_file" \
      "$save_dir/$dir_name/dmesg/"
    sshpass -p "$qemu_password_remote" ssh -o StrictHostKeyChecking=no \
      "root@$ip_npu" "rm -f /tmp/$remote_file"
done
echo "[Info][Device] 保存 NPU dmesg log 完成"

echo "[Info][CPU][✅] QEMU日志收集脚本执行完成！日志已保存至：$save_path"