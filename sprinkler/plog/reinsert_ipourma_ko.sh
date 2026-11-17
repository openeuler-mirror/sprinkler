#!/bin/bash
# 重插device侧ipourma.ko脚本
# 用于规避问题：Host侧ipo_urma侧wait中断wait不到，未做异常后处理，导致队列阻塞，链路无法ping通

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\1;33m'
NC='\033[0m' # No Color

# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

# 配置参数
config_require_keys \
    "POD_NPU_USER|请输入连接NPU用户名|root|0|validate_non_empty" \
    "POD_NPU_PASSWORD|请输入登录NPU密码(输入不回显)||1|validate_non_empty"

# 日志函数
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}
log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}
log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 获取NPU IP地址
get_npu_ip() {
    if [ ! -f /lib/route.conf ]; then
        log_error "配置文件 /lib/route.conf 不存在"
        return 1
    fi
    
    # 使用原始命令
    local npu_ip=$(cat /lib/route.conf | grep remote | awk '{
        len=length($0)
        if(len < 8) next
        s=substr($0, len-7, 8)
        part1=substr(s,1,2)
        part2=substr(s,3,2)
        part3=substr(s,5,2)
        part4=substr(s,7,2)
        cmd="printf \"%d.%d.%d.%d\\n\" 0x" part1 " 0x" part2 " 0x" part3 " 0x" part4
        system(cmd)
    }')
    
    # 检查是否成功获取到IP
    if [ -z "$npu_ip" ]; then
        log_error "无法获取NPU IP地址"
        return 1
    fi
    
    echo "${npu_ip}" 
    return 0
}

setup_ssh() {
    local target_ip=$1
    local PASSWORD=$2
    local ID=$3
    local user_host="root@$target_ip"

    # 接受主机指纹
    ssh-keyscan -H "$target_ip" >> ~/.ssh/known_hosts 2>/dev/null

    # 使用expect自动化
    expect <<EOF >/dev/null 2>&1
set timeout 30
spawn ssh-copy-id -o StrictHostKeyChecking=no "$user_host"
expect {
    "yes/no" { send "yes\r"; exp_continue }
    "password:" { send "$PASSWORD\r"; exp_continue }
    "Password:" { send "$PASSWORD\r"; exp_continue }
    eof
}
EOF

    # 验证SSH
    if ssh -o BatchMode=yes -o ConnectTimeout=15 "$user_host" "echo success" >/dev/null 2>&1; then
        echo "$(date) - ✓ SSH配置成功"
        
        # 重插ko
        echo "$(date) - 开始重插ko..."
        
        local register_result
        register_result=$(ssh -o BatchMode=yes -o ConnectTimeout=15 "$user_host" "
            echo '=== 开始配置NPU ${ID} ==='
            echo '执行: rmmod ipourma'
            rmmod ipourma 2>&1
            if [ $? -ne 0 ]; then
                echo 'ERROR: rmmod ipourma failed'
                exit 1
            fi
            
            echo '执行: insmod /var/ipourma.ko'
            insmod /var/ipourma.ko 2>&1
            if [ $? -ne 0 ]; then
                echo 'ERROR: insmod /var/ipourma.ko failed'
                exit 1
            fi
            
            echo 'SUCCESS: 配置成功'
            exit 0
        " 2>&1)

        if [ $? -eq 0 ]; then
            echo "$(date) - ✓ ip={$target_ip}配置成功"
            echo "$register_result"
            ssh_success_hosts+=( "$user_host|$node_info" )
            return 0
        else
            echo "$(date) - ✗ ip={$target_ip}配置失败"
            echo "$register_result"
            ssh_fail_hosts+=( "$user_host|$node_info" )
            return 1
        fi
    else
        echo "$(date) - ✗ SSH配置失败"
        ssh_fail_hosts+=( "$user_host|$node_info" )
        return 1
    fi
}

# 显示当前系统信息
show_system_info() {
    log_info "=== 系统信息 ==="
    echo "主机名: $(hostname)"
    echo "系统时间: $(date)"
    echo "CPU架构: $(uname -m)"
    echo "内核版本: $(uname -r)"
    echo ""
}

# 主函数
main() {
    echo "=== 重插每个device上ipourma.ko ==="
    echo ""
    
    # 显示系统信息
    show_system_info

    NPU_IP=$(get_npu_ip)
    
    if [ $? -ne 0 ] || [ -z "$NPU_IP" ]; then
        log_error "自动获取NPU IP失败"
        echo -n "请手动输入NPU IP地址: "
        read -r NPU_IP
        if [ -z "$NPU_IP" ]; then
            log_error "未提供NPU IP地址，退出"
            exit 1
        fi
    else
        log_info "成功获取NPU IP: $NPU_IP"
    fi
    
    npu_ips=($NPU_IP)
    for i in "${!npu_ips[@]}"; do
        npu_ip="${npu_ips[i]}"
        echo "处理第$((i+1))个IP: $npu_ip"
        # ssh免密并执行命令
        setup_ssh "$npu_ip" "$POD_NPU_PASSWORD" "$i"
    done

    log_info "=== 所有device配置流程完成 ==="
}

# 执行主函数
main "$@"