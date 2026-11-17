#!/bin/bash

# 配置参数
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
# 引入公共配置库
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

# 配置参数 (一次性收集所有的账号、密码、IP)
config_require_keys \
    "SFU_IP1|请输入连接SFU1的IP||0|validate_non_empty" \
    "SFU_USER1|请输入连接SFU1的用户名|Administrator|0|validate_non_empty" \
    "SFU_PASSWORD1|请输入登录SFU1的密码(输入不回显)||1|validate_non_empty" \
    "SFU_IP2|请输入连接SFU2的IP||0|validate_non_empty" \
    "SFU_USER2|请输入连接SFU2的用户名|Administrator|0|validate_non_empty" \
    "SFU_PASSWORD2|请输入登录SFU2的密码(输入不回显)||1|validate_non_empty" \
    "SFU_IP3|请输入连接SFU3的IP||0|validate_non_empty" \
    "SFU_USER3|请输入连接SFU3的用户名|Administrator|0|validate_non_empty" \
    "SFU_PASSWORD3|请输入登录SFU3的密码(输入不回显)||1|validate_non_empty" \
    "SFU_IP4|请输入连接SFU4的IP||0|validate_non_empty" \
    "SFU_USER4|请输入连接SFU4的用户名|Administrator|0|validate_non_empty" \
    "SFU_PASSWORD4|请输入登录SFU4的密码(输入不回显)||1|validate_non_empty" \

# 清一下known_hosts
sed -i '/^|1|/d' /root/.ssh/known_hosts

mkdir -p ${SCRIPT_DIR}/ssh_batch_results
RESULTS_FILE="${SCRIPT_DIR}/ssh_batch_results/nopass_$(date +%Y%m%d_%H%M%S).log"

# 初始化统计变量
total_count=0
ping_success_count=0
ping_fail_count=0
ssh_success_count=0
ssh_fail_count=0

# 存储结果
ping_fail_pairs=()
ssh_success_hosts=()
ssh_fail_hosts=()

# 确保SSH密钥存在
ensure_ssh_key() {
    if [ ! -f ~/.ssh/id_rsa.pub ]; then
        echo "$(date) - 生成SSH密钥..." | tee -a "$RESULTS_FILE"
        if ssh-keygen -t rsa -N "" -f ~/.ssh/id_rsa >/dev/null 2>&1; then
            echo "SSH密钥生成成功" | tee -a "$RESULTS_FILE"
        else
            echo "SSH密钥生成失败" | tee -a "$RESULTS_FILE"
            return 1
        fi
    else
        echo "SSH密钥已存在" | tee -a "$RESULTS_FILE"
    fi
}

# 快速检查SSH连接
quick_ssh_check() {
    local target_ip=$1
    local user_host="$USER@$target_ip"
    
    if ssh -o BatchMode=yes -o ConnectTimeout=3 -o PasswordAuthentication=no \
       "$user_host" "echo 'connected'" >/dev/null 2>&1; then
        return 0  
    else
        return 1  
    fi
}

# 配置SSH
setup_ssh() {
    local target_ip=$1
    local node_info=$2
    local user_host="$USER@$target_ip"
    
    if quick_ssh_check "$target_ip"; then
        echo "$(date) - ✓ SSH已配置（二次检查确认）" | tee -a "$RESULTS_FILE"
        return 0
    fi

    echo "$(date) - 配置SSH免密: $user_host" | tee -a "$RESULTS_FILE"
    [ -n "$node_info" ] && echo "$(date) - 节点信息: $node_info" | tee -a "$RESULTS_FILE"
    
    timeout 5 ssh-keyscan -H "$target_ip" >> ~/.ssh/known_hosts 2>/dev/null

    local setup_success=0

    if command -v sshpass >/dev/null; then
        echo "$(date) - 尝试 sshpass 方式配置..." | tee -a "$RESULTS_FILE"
        if SSHPASS="$PASSWORD" sshpass -e ssh-copy-id -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$user_host" >/dev/null 2>&1; then
            echo "$(date) - ✓ sshpass 方式配置成功" | tee -a "$RESULTS_FILE"
            setup_success=1
        else
            echo "$(date) - ✗ sshpass 方式配置失败" | tee -a "$RESULTS_FILE"
        fi

    elif yum install -y sshpass >/dev/null 2>&1; then
        echo "$(date) - sshpass 安装成功，尝试配置..." | tee -a "$RESULTS_FILE"
        if SSHPASS="$PASSWORD" sshpass -e ssh-copy-id -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$user_host" >/dev/null 2>&1; then
            echo "$(date) - ✓ sshpass 方式配置成功" | tee -a "$RESULTS_FILE"
            setup_success=1
        else
            echo "$(date) - ✗ sshpass 方式配置失败" | tee -a "$RESULTS_FILE"
        fi

    else
        echo "$(date) - 未找到 sshpass，尝试 expect 方式配置..." | tee -a "$RESULTS_FILE"
        export EXP_PASSWORD="$PASSWORD"
        
        if timeout 15 expect <<EOF >/dev/null 2>&1
set timeout 10
log_user 0
spawn ssh-copy-id -o StrictHostKeyChecking=no -o ConnectTimeout=5 $user_host
expect {
    "yes/no" { send "yes\r"; exp_continue }
    "*assword*" { send "\$env(EXP_PASSWORD)\r"; exp_continue }
    "Permission denied" { exit 1 }
    timeout { exit 2 }
    eof
}
EOF
        then
            echo "$(date) - ✓ expect 方式配置成功" | tee -a "$RESULTS_FILE"
            setup_success=1
        else
            echo "$(date) - ✗ expect 方式配置失败" | tee -a "$RESULTS_FILE"
        fi
        unset EXP_PASSWORD
    fi

    if [ $setup_success -eq 1 ] && quick_ssh_check "$target_ip"; then
        echo "$(date) - ✓ SSH免密配置最终验证成功" | tee -a "$RESULTS_FILE"
        ssh_success_hosts+=("$user_host|$node_info")
        return 0
    else
        echo "$(date) - ✗ SSH免密配置失败，请检查密码或目标机权限" | tee -a "$RESULTS_FILE"
        ssh_fail_hosts+=("$user_host|$node_info")
        return 1
    fi
}

# 处理单个主机
process_host() {
    local target_ip="$1"
    local node_info="$2"
    
    ((total_count++))
    
    echo "========================================" | tee -a "$RESULTS_FILE"
    echo "处理第 $total_count 个节点" | tee -a "$RESULTS_FILE"
    echo "目的IP: $target_ip" | tee -a "$RESULTS_FILE"

    if [ -z "$target_ip" ]; then
        echo "$(date) - 跳过无效IP" | tee -a "$RESULTS_FILE"
        return
    fi
    
    if quick_ssh_check "$target_ip"; then
        echo "$(date) - ✓ SSH已配置，跳过" | tee -a "$RESULTS_FILE"
        ((ssh_success_count++))
        ssh_success_hosts+=("$USER@$target_ip|$node_info")
        echo "----------------------------------------" | tee -a "$RESULTS_FILE"
        return 0
    fi

    echo "$(date) - 快速ping检查..." | tee -a "$RESULTS_FILE"
    if ping -c 1 -W 1 "$target_ip" >/dev/null 2>&1; then
        echo "$(date) - ✓ 网络连通" | tee -a "$RESULTS_FILE"
        ((ping_success_count++))
        
        if setup_ssh "$target_ip" "$node_info"; then
            ((ssh_success_count++))
        else
            ((ssh_fail_count++))
        fi
    else
        echo "$(date) - ✗ 网络不通" | tee -a "$RESULTS_FILE"
        ((ping_fail_count++))
        ping_fail_pairs+=("$target_ip|$node_info")
    fi
    
    echo "----------------------------------------" | tee -a "$RESULTS_FILE"
}

# 显示结果
show_summary() {
    echo "" | tee -a "$RESULTS_FILE"
    echo "[INFO] ========================================" | tee -a "$RESULTS_FILE"
    echo "[INFO] 结果汇总" | tee -a "$RESULTS_FILE"
    echo "[INFO] ========================================" | tee -a "$RESULTS_FILE"
    echo "[INFO] 总处理: $total_count" | tee -a "$RESULTS_FILE"
    echo "[INFO] Ping成功: $ping_success_count" | tee -a "$RESULTS_FILE"
    echo "[INFO] Ping失败: $ping_fail_count" | tee -a "$RESULTS_FILE"
    echo "[INFO] SSH成功: $ssh_success_count" | tee -a "$RESULTS_FILE"
    echo "[INFO] SSH失败: $ssh_fail_count" | tee -a "$RESULTS_FILE"
    echo "[INFO] ----------------------------------------" | tee -a "$RESULTS_FILE"
    
    if [ ${#ping_fail_pairs[@]} -gt 0 ]; then
        echo "[ERROR] Ping失败:" | tee -a "$RESULTS_FILE"
        for pair in "${ping_fail_pairs[@]}"; do
            IFS='|' read -r ips info <<< "$pair"
            echo "[ERROR]  ✗ $ips" | tee -a "$RESULTS_FILE"
            [ -n "$info" ] && echo "[ERROR]      $info" | tee -a "$RESULTS_FILE"
        done
        echo "[ERROR] ----------------------------------------" | tee -a "$RESULTS_FILE"
    fi
    
    if [ ${#ssh_fail_hosts[@]} -gt 0 ]; then
        echo "[ERROR] SSH失败:" | tee -a "$RESULTS_FILE"
        for host in "${ssh_fail_hosts[@]}"; do
            IFS='|' read -r h info <<< "$host"
            echo "[ERROR]   ✗ $h" | tee -a "$RESULTS_FILE"
            [ -n "$info" ] && echo "[ERROR]      $info" | tee -a "$RESULTS_FILE"
        done
        echo "[ERROR] ----------------------------------------" | tee -a "$RESULTS_FILE"
    fi
    
    if [ ${#ssh_success_hosts[@]} -gt 0 ]; then
        echo "[SUCCESS]SSH成功:" | tee -a "$RESULTS_FILE"
        for host in "${ssh_success_hosts[@]}"; do
            IFS='|' read -r h info <<< "$host"
            echo "[SUCCESS]  ✓ $h" | tee -a "$RESULTS_FILE"
            [ -n "$info" ] && echo "[SUCCESS]     $info" | tee -a "$RESULTS_FILE"
        done
    fi
    
    echo "[INFO] ========================================" | tee -a "$RESULTS_FILE"
}

# 主函数
no_pass() {
    echo "开始时间: $(date)" | tee "$RESULTS_FILE"
    echo "开始配置SSH免密..." | tee -a "$RESULTS_FILE"
    
    ensure_ssh_key
    
    # 循环遍历 4 个 SFU
    for i in 1 2 3 4; do
        # 动态获取 IP、USER、PASSWORD 变量
        ip_var="SFU_IP${i}"
        user_var="SFU_USER${i}"
        pass_var="SFU_PASSWORD${i}"
        
        # 把配置里的值取出来
        sfu_ips="${!ip_var}"
        USER="${!user_var}"
        PASSWORD="${!pass_var}"
        
        echo "" | tee -a "$RESULTS_FILE"
        echo "###############################################" | tee -a "$RESULTS_FILE"
        echo "[INFO] 正在处理 SFU${i} (IP: $sfu_ips, 账号: $USER)" | tee -a "$RESULTS_FILE"
        echo "###############################################" | tee -a "$RESULTS_FILE"
        
        # 如果配置里没填 IP，跳过
        if [ -z "$sfu_ips" ]; then
            echo "[INFO] 未配置 SFU${i} 的IP，跳过" | tee -a "$RESULTS_FILE"
            continue
        fi
        
        local index=1
        # 遍历当前 SFU 的 IP (如果你在配置里用空格隔开了多个IP，这里会自动拆分遍历)
        for target_ip in $sfu_ips; do
            node_info="SFU${i}_Node_${index}"
            process_host "$target_ip" "$node_info"
            ((index++))
        done
    done

    show_summary
}

# 执行
no_pass "$@"
