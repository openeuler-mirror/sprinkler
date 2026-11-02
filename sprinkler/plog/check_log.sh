# #!/bin/bash

set -e  # 遇到错误立即退出脚本

# 颜色定义，用于输出美化
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # 无颜色

# 打印带颜色的消息函数
print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_debug() { echo -e "${PURPLE}[DEBUG]${NC} $1"; }
print_section() { echo -e "${CYAN}=== $1 ===${NC}"; }

# 函数：检查系统服务状态
check_service() {
    local service=$1
    if systemctl is-active "$service" >/dev/null 2>&1; then
        echo "✅ $service: 运行中"
        return 0
    else
        echo "❌ $service: 未运行"
        return 1
    fi
}

# 函数：检查文件是否存在并有读取权限
check_file() {
    local file=$1
    if [ -f "$file" ]; then
        if [ -r "$file" ]; then
            echo "✅ $file: 存在且可读"
            return 0
        else
            echo "❌ $file: 存在但不可读"
            return 1
        fi
    else
        echo "⚠️  $file: 不存在"
        return 2
    fi
}

# 函数：检查磁盘空间
check_disk_space() {
    local dir=$1
    local threshold_gb=${2:-10}
    
    if [ -d "$dir" ]; then
        local available_gb=$(df "$dir" | awk 'NR==2 {print int($4/1024/1024)}')
        local usage_percent=$(df "$dir" | awk 'NR==2 {print $5}')
        
        if [ "$available_gb" -ge "$threshold_gb" ]; then
            echo "✅ $dir: 可用空间 ${available_gb}GB (使用率: $usage_percent)"
            return 0
        else
            echo "⚠️  $dir: 可用空间不足 ${available_gb}GB < ${threshold_gb}GB (使用率: $usage_percent)"
            return 1
        fi
    else
        echo "❌ $dir: 目录不存在"
        return 2
    fi
}

# 函数：检查内核日志级别配置
check_kernel_loglevel() {
    if [ -f /proc/sys/kernel/printk ]; then
        local levels=($(cat /proc/sys/kernel/printk))
        local current=${levels[0]}
        local default=${levels[1]}
        
        echo "   当前级别: $current, 默认级别: $default, 最小级别: ${levels[2]}, 启动级别: ${levels[3]}"
        
        if [ "$current" -ge 4 ]; then
            echo "   ✅ 内核日志级别配置合理"
        else
            echo "   ⚠️  内核日志级别较低，可能无法采集WARNING以下日志"
        fi
    else
        echo "   ❌ 无法读取内核日志级别配置"
    fi
}

# 函数：详细检查ASCEND环境变量配置
check_ascend_environment() {
    print_section "Ascend环境变量检查"
    # 1. 检查当前环境中的ASCEND_GLOBAL_LOG_LEVEL

    local current_ascend_level="${ASCEND_GLOBAL_LOG_LEVEL:-未设置}"
    echo "当前环境中的ASCEND_GLOBAL_LOG_LEVEL: $current_ascend_level"
    
    # 解释不同级别的含义
    case "$current_ascend_level" in
        "0") echo "    DEBUG级别 - 记录所有日志（推荐用于问题诊断）" ;;
        "1") echo "    INFO级别 - 记录信息性消息" ;;
        "2") echo "    WARNING级别 - 记录警告和错误" ;;
        "3") echo "    ERROR级别 - 仅记录错误" ;;
        "未设置")  ;;
        *) echo "    未知级别: $current_ascend_level" ;;
    esac
    
    
    # 4. 推荐设置
    echo ""
    echo "tip:"
    echo "    ASCEND_GLOBAL_LOG_LEVEL=0 显示所有信息，便于查询具体报错信息及细节:[DEBUG]"
    echo "    ASCEND_GLOBAL_LOG_LEVEL=1 记录信息性消息: [INFO]"
    echo "    ASCEND_GLOBAL_LOG_LEVEL=2 记录警告和错误: [WARNING]"
    echo "    ASCEND_GLOBAL_LOG_LEVEL=3 仅记录错误: [ERROR]"
    echo "    默认设置: ASCEND_GLOBAL_LOG_LEVEL=3 (记录ERROR日志)"
    
    # 5. 验证设置命令
    echo ""
    echo "设置和验证命令:"
    echo "   临时设置（当前会话有效）"
    echo "   export ASCEND_GLOBAL_LOG_LEVEL=0"
    echo ""
    echo "   验证设置"
    echo "   echo \$ASCEND_GLOBAL_LOG_LEVEL"
}

# 函数：检查其他Ascend相关环境变量
check_other_ascend_env() {
    print_section "其他Ascend相关环境变量"
    
    local ascend_vars=(
        "ASCEND_SLOG_PRINT_TO_STDOUT"
        "ASCEND_GLOBAL_EVENT_ENABLE" 
        "ASCEND_HOST_LOG_FILE"
        "ASCEND_LOG_PATH"
        "ASCEND_PROCESS_LOG_PATH"
    )
    
    for var in "${ascend_vars[@]}"; do
        local value="${!var:-未设置}"
        echo "   $var: $value"
    done
    
    # 检查日志目录设置
    local log_path="${ASCEND_LOG_PATH:-/var/log/npu}"
    echo ""
    echo "日志目录检查:"
    if [ -d "$log_path" ]; then
        local dir_size=$(du -sh "$log_path" 2>/dev/null | cut -f1)
        echo "    Ascend日志目录: $log_path (大小: ${dir_size:-未知})"
        
        # 检查目录权限
        local perms=$(ls -ld "$log_path" | awk '{print $1}')
        echo "   目录权限: $perms"
    else
        echo "     Ascend日志目录不存在: $log_path"
    fi
}

# 函数：检查关键日志文件
check_critical_logs() {
    local log_files=(
        "/var/log/messages"
        "/var/log/syslog" 
        "/var/log/kern.log"
        "/var/log/dmesg"
        "/var/log/auth.log"
        "/var/log/secure"
        "/var/log/npu/slog/host-0"
        "/var/log/npu/slog/device-0"
        "/var/log/ascend_log"
    )
    
    print_info "检查关键日志文件状态:"
    for file in "${log_files[@]}"; do
        check_file "$file"
    done
}

# 函数：检查日志服务状态
check_log_services() {
    local services=("rsyslog" "syslog-ng" "systemd-journald")
    
    print_info "检查日志服务状态:"
    for service in "${services[@]}"; do
        if systemctl list-unit-files | grep -q "$service"; then
            check_service "$service"
        fi
    done
}

# 主函数
main() {
    echo ""
    
    # 详细检查Ascend环境变量
    check_ascend_environment
    echo ""
    
    # 检查其他Ascend相关环境变量
    check_other_ascend_env
    echo ""
    
    # 检查日志服务状态
    check_log_services
    echo ""
    
    # 检查磁盘空间
    print_section "磁盘空间检查"
    check_disk_space "/var/log" 10
    check_disk_space "/var/log/npu" 5
    echo ""
}

# 脚本入口点
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi