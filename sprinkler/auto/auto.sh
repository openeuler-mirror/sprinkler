#!/usr/bin/env bash

# 引入公共库
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"
GRANDPARENT_DIR="$(dirname "$(dirname "$(dirname "$PARENT_DIR")")")"
LOGS_ROOT=${GRANDPARENT_DIR}/sprinkler_logs
mkdir -p "$LOGS_ROOT"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/common.sh"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../sprinkler/utils/config_util.sh"

# 全局日志目录变量
LOGS_ROOT_DIR="$LOGS_ROOT/auto_check_logs"
LOG_BASE_DIR=""

# 预先收集所有子检查项需要的参数
pre_collect_parameters_L1() {
    log INFO "$AUTO_CHECK_ID"  "====== 开始收集检查项所需参数 ======"
    log INFO "$AUTO_CHECK_ID"  "配置将保存到: $CONFIG_FILE"
    log INFO "$AUTO_CHECK_ID" 
    
    # 前置配置参数
    config_require_keys \
        "POD_IP|请输入CPU OS的IP地址||0|validate_ip" \
        "POD_USER|请输入连接CPU OS的用户名|root|0|validate_non_empty" \
        "POD_PASSWORD|请输入CPU OS的登录密码(输入不回显)||1|validate_non_empty" \
        "ENV_FILE|如果存在环境变量配置文件，请输入文件路径（如cann.sh）| |0|" \
        "ROOTINFO_FILE|请输入rootinfo的json配置文件路径|/etc/hccl_rootinfo.json|0|" \
        "POD_NPU_USER|请输入连接NPU用户名(提示：直接回车将使用默认值 [root])|root|0|validate_non_empty" \
        "POD_NPU_PASSWORD|请输入登录NPU密码(输入不回显)||1|validate_non_empty" \
        "HCCL_TOPO_FILE_PATH|请输入topo的json配置文件路径|/etc/superpod_1d_noroce.json|0|" \
        "CPU_BMC_IP|请输入CPU_BMC_IP地址||0|validate_ip" \
        "CPU_BMC_PASS|请输入CPU_BMC登录密码(输入不回显)||1|validate_non_empty" \
        "CPU1213_IP|请输入CPU 1213后台IP地址||0|validate_ip" \
        "CPU1213_PASS|请输入CPU 1213前台登录密码(输入不回显)||1|validate_non_empty" \
        "CPU1213_MAC|请输入CPU 1213 MAC地址(如20:26:21:25:xx:xx，随意修改其中数字与其他环境不重复即可)||0|validate_non_empty" \
        "NPU_BMC_IP|请输入NPU_BMC_IP地址||0|validate_ip" \
        "NPU_BMC_PASS|请输入NPU_BMC登录密码(输入不回显)||1|validate_non_empty" \
        "NPU1213_PASS|请输入NPU 1213前台登录密码(输入不回显)||1|validate_non_empty" \
        "NPU1213_IP|请输入NPU 1213后台IP地址||0|validate_ip" \
        "NPU1213_MAC|请输入NPU 1213 MAC地址(如20:26:21:25:xx:xx，随意修改其中数字与其他环境不重复即可)||0|validate_non_empty"

    log INFO "$AUTO_CHECK_ID"  "✅ 所有参数收集完成"
    echo
}

pre_collect_parameters_L2() {
    log INFO "$AUTO_CHECK_ID"  "====== 开始收集检查项所需参数 ======"
    log INFO "$AUTO_CHECK_ID"  "配置将保存到: $CONFIG_FILE"
    log INFO "$AUTO_CHECK_ID" 
    
    # 前置配置参数
    config_require_keys \
        "POD_IP|请输入CPU OS的IP地址||0|validate_ip" \
        "POD_USER|请输入连接CPU OS的用户名|root|0|validate_non_empty" \
        "POD_PASSWORD|请输入CPU OS的登录密码(输入不回显)||1|validate_non_empty" \
        "ENV_FILE|如果存在环境变量配置文件，请输入文件路径（如cann.sh）| |0|" \
        "ROOTINFO_FILE|请输入rootinfo的json配置文件路径|/etc/hccl_rootinfo.json|0|" \
        "POD_NPU_USER|请输入连接NPU用户名(提示：直接回车将使用默认值 [root])|root|0|validate_non_empty" \
        "POD_NPU_PASSWORD|请输入登录NPU密码(输入不回显)||1|validate_non_empty" \
        "MPICH_PATH|请输入完整的mpich路径（例如 /home/username/mpich）: ||0|validate_non_empty" \
        "ASCEND_PATH|请输入Ascend的安装路径（例如 /home/a00222222/pckg/1204/latest）:||0|validate_non_empty" \
        "HCCL_TEST_PATH|请输入HCCL测试工具的路径（例如 .../latest/tools/hccl_test）:||0|validate_non_empty" \
        "HCCL_TOPO_FILE_PATH|请输入topo的json配置文件路径|/etc/superpod_1d_noroce.json|0|" \
        "CPU_BMC_IP|请输入CPU_BMC_IP地址||0|validate_ip" \
        "CPU_BMC_PASS|请输入CPU_BMC登录密码(输入不回显)||1|validate_non_empty" \
        "CPU1213_IP|请输入CPU 1213后台IP地址||0|validate_ip" \
        "CPU1213_PASS|请输入CPU 1213前台登录密码(输入不回显)||1|validate_non_empty" \
        "CPU1213_MAC|请输入CPU 1213 MAC地址(如20:26:21:25:xx:xx，随意修改其中数字与其他环境不重复即可)||0|validate_non_empty" \
        "NPU_BMC_IP|请输入NPU_BMC_IP地址||0|validate_ip" \
        "NPU_BMC_PASS|请输入NPU_BMC登录密码(输入不回显)||1|validate_non_empty" \
        "NPU1213_PASS|请输入NPU 1213前台登录密码(输入不回显)||1|validate_non_empty" \
        "NPU1213_IP|请输入NPU 1213后台IP地址||0|validate_ip" \
        "NPU1213_MAC|请输入NPU 1213 MAC地址(如20:26:21:25:xx:xx，随意修改其中数字与其他环境不重复即可)||0|validate_non_empty"

    log INFO "$AUTO_CHECK_ID"  "✅ 所有参数收集完成"
    echo
}

# -------- config --------
AUTO_CHECK_ID="auto_main"
DEFAULT_TIMEOUT=60
SCRIPTS_DIR="$(dirname "$SCRIPT_DIR")/auto"
SUMMARY_CSV="$SCRIPT_DIR/auto_check_summary.csv"  # 汇总表格保存路径

# -------- 全局变量 --------
declare -A check_results
declare -A check_messages
declare -a script_order
declare -A LEVEL=""         # 检查级别，默认为空（所有检查项）
declare -A script_log_files  # 保存每个脚本的日志文件路径
declare -A item_to_script  # 映射项目ID到脚本ID
declare -A script_items    # 映射脚本ID到其包含的项目ID列表

# -------- 函数定义 --------
# 创建带时间戳的日志目录
create_log_directory() {
    local timestamp=$(date +%Y%m%d_%H%M%S)
    LOG_BASE_DIR="$LOGS_ROOT_DIR/auto_check_logs_$timestamp"
    
    mkdir -p "$LOG_BASE_DIR"
    if [ $? -eq 0 ]; then
        log OK "$AUTO_CHECK_ID" "检查项日志目录已创建: $LOG_BASE_DIR"
    else
        log ERROR "$AUTO_CHECK_ID" "无法创建日志目录: $LOG_BASE_DIR"
        return 1
    fi
}

# 为检查项创建日志文件路径
get_script_log_file() {
    local script_name="$1"
    local log_file="${LOG_BASE_DIR}/${script_name}.log"
    echo "$log_file"
}

# 运行脚本并收集结果
run_script_with_timeout() {
    local script_name="$1"
    local script_path="$SCRIPT_DIR/$script_name"
    local check_id="${script_name%.*}"  # 移除扩展名作为check_id
    local timeout_duration="${2:-$DEFAULT_TIMEOUT}"  # 支持单独设置超时时间
    local need_log_dir="${3:-0}"  # 是否需要传递日志目录参数，默认不需要(0)
    local log_file="$(get_script_log_file "$check_id")"
    
    sleep 6
    # 保存日志文件路径到全局数组
    script_log_files["$check_id"]="$log_file"
    
    log INFO "$AUTO_CHECK_ID" "开始执行: $script_name (超时: ${timeout_duration}s)"
    
    if [ ! -f "$script_path" ]; then
        log WARN "$AUTO_CHECK_ID" "脚本不存在: $script_path"
        # 记录到日志文件
        echo "$(date '+%Y-%m-%d %H:%M:%S') | WARN | 脚本不存在: $script_path" >> "$log_file"
        check_results["$check_id"]="SKIP"
        check_messages["$check_id"]="脚本不存在"
        script_order+=("$check_id")
        return 1
    fi
    
    # 检查脚本是否有执行权限
    if [ ! -x "$script_path" ]; then
        chmod +x "$script_path"
        log DEBUG "$AUTO_CHECK_ID" "已为 $script_name 添加执行权限"
    fi
    
    # 创建临时文件捕获输出
    local temp_output=$(mktemp)
    
    # 创建日志文件目录
    mkdir -p "$(dirname "$log_file")"
    
    # 执行脚本（带超时）并同时保存到检查项日志文件
    local start_time=$(date +%s)
    
    # 恢复原来的可靠执行方式，但添加进程组管理
    local result=124  # 默认设为超时状态
    
    # 使用进程组来确保所有子进程都能被正确终止
    if [ "$need_log_dir" -eq 1 ]; then
        # 需要传递日志目录：将日志文件所在目录作为参数
        {
            # 使用exec来确保进程组管理
            timeout ${timeout_duration}s bash -c "
                trap 'kill -TERM -\$\$' TERM
                exec '$script_path' '$LOG_BASE_DIR'
            " 2>&1
        } | tee "$temp_output"
    else
        # 不需要传递日志目录：原调用方式
        {
            timeout ${timeout_duration}s bash -c "
                trap 'kill -TERM -\$\$' TERM
                exec '$script_path'
            " 2>&1
        } | tee "$temp_output"
    fi
    
    # 获取管道中第一个命令（timeout）的退出状态
    local result=${PIPESTATUS[0]}
    
    local end_time=$(date +%s)
    local duration=$((end_time - start_time))
    
    # 初始化当前脚本的项目列表
    script_items["$check_id"]=""
    
    # 从临时文件中提取所有项目ID的状态信息
    if [ -f "$temp_output" ]; then
        # 查找所有包含特殊标记的日志块
        if grep -q ">>>AUTO_DETAILS_START<<<" "$temp_output"; then
            # 使用awk提取所有特殊标记块之间的内容
            awk '
                />>>AUTO_DETAILS_START<<</ { in_block=1; next }
                />>>AUTO_DETAILS_END<<</ { in_block=0; next }
                in_block && /[^[:space:]]/ {  # 只在块内且非空行时处理
                    # 解析日志格式：日期 | 状态 | 项目ID | 消息
                    if (split($0, parts, "|") >= 4) {
                        # 清理字段
                        gsub(/^[[:space:]]+|[[:space:]]+$/, "", parts[2])  # 状态
                        gsub(/\x1b\[[0-9;]*m/, "", parts[2])  # 去除ANSI颜色代码
                        gsub(/^[[:space:]]+|[[:space:]]+$/, "", parts[3])  # 项目ID
                        gsub(/\x1b\[[0-9;]*m/, "", parts[3])
                        
                        # 提取消息（从第4个字段开始）
                        message = ""
                        for (i=4; i<=length(parts); i++) {
                            if (i>4) message = message "|"
                            gsub(/^[[:space:]]+|[[:space:]]+$/, "", parts[i])
                            gsub(/\x1b\[[0-9;]*m/, "", parts[i])
                            message = message parts[i]
                        }
                        
                        # 如果项目ID不为空，则记录结果
                        if (parts[3] != "") {
                            print parts[3] "|" parts[2] "|" message
                        }
                    }
                }
            ' "$temp_output" > "${temp_output}_parsed"
            
            # 处理解析出的所有项目结果
            if [ -s "${temp_output}_parsed" ]; then
                while IFS='|' read -r item_id item_status item_message; do
                    # 清理变量
                    item_id=$(echo "$item_id" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                    item_status=$(echo "$item_status" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                    item_message=$(echo "$item_message" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
                    
                    # 使用项目ID作为唯一键
                    local unique_check_id="${item_id}"
                    
                    # 记录项目与脚本的映射关系
                    item_to_script["$item_id"]="$check_id"
                    
                    # 将项目ID添加到脚本的项目列表中
                    if [[ ! " ${script_items[$check_id]} " =~ " $item_id " ]]; then
                        script_items["$check_id"]="${script_items[$check_id]} $item_id"
                    fi
                    
                    # 记录调试信息
                    log DEBUG "$AUTO_CHECK_ID" "解析到项目结果: ID='$item_id', 状态='$item_status', 消息='$item_message'"
                    
                    # 保存结果
                    check_results["$unique_check_id"]="$item_status"
                    check_messages["$unique_check_id"]="$item_message"
                    
                    # 添加到顺序数组（如果尚未添加）
                    if [[ ! " ${script_order[*]} " =~ " ${unique_check_id} " ]]; then
                        script_order+=("$unique_check_id")
                    fi
                    
                done < "${temp_output}_parsed"
                
                log DEBUG "$AUTO_CHECK_ID" "成功解析 $(wc -l < "${temp_output}_parsed") 个项目结果"
            else
                log DEBUG "$AUTO_CHECK_ID" "未解析到有效的项目结果"
            fi
            
            # 清理临时解析文件
            rm -f "${temp_output}_parsed"
        else
            log DEBUG "$AUTO_CHECK_ID" "日志中未找到AUTO_DETAILS标记块"
        fi
    fi
    
    # 重构判断逻辑：检查当前脚本是否有项目级别的结果
    if [ -z "${script_items[$check_id]}" ] || [ "${script_items[$check_id]}" = "" ]; then
        log DEBUG "$AUTO_CHECK_ID" "使用脚本级别状态作为后备"
        
        # 正确处理各种情况的状态
        local final_status=""
        local final_message=""
        
        # 优先处理超时情况
        if [ $result -eq 124 ]; then
            # 超时情况
            final_status="FAIL"
            final_message="执行超时${timeout_duration}s"
            log WARN "$AUTO_CHECK_ID" "$script_name 执行超时${duration}s"
        else
            # 使用基于退出码的默认值
            case $result in
                0)
                    final_status="OK"
                    final_message="执行成功${duration}s"
                    ;;
                *)
                    final_status="FAIL"
                    final_message="执行失败${duration}s"
                    ;;
            esac
        fi
        
        # 根据最终状态设置检查结果
        case "$final_status" in
            OK|PASS|SUCCESS)
                check_results["$check_id"]="OK"
                check_messages["$check_id"]="$final_message"
                ;;
            WARN|WARNING)
                check_results["$check_id"]="WARN"
                check_messages["$check_id"]="$final_message"
                ;;
            FAIL|FAILURE|ERROR|TIMEOUT|FATAL)
                check_results["$check_id"]="FAIL"
                check_messages["$check_id"]="$final_message"
                ;;
            *)
                if [ $result -eq 0 ]; then
                    check_results["$check_id"]="OK"
                    check_messages["$check_id"]="$final_message"
                else
                    check_results["$check_id"]="FAIL"
                    check_messages["$check_id"]="$final_message"
                fi
                ;;
        esac
        
        # 确保脚本ID在顺序数组中
        if [[ ! " ${script_order[*]} " =~ " ${check_id} " ]]; then
            script_order+=("$check_id")
        fi
    else
        log DEBUG "$AUTO_CHECK_ID" "已使用项目级别状态，跳过脚本级别状态"
    fi
    
    # 处理编码并保存到日志文件
    {
        printf '\xEF\xBB\xBF'
        cat "$temp_output"
    } > "$log_file"
    
    # 清理临时文件
    rm -f "$temp_output"

    # 记录日志文件大小
    if [ -f "$log_file" ]; then
        local file_size=$(wc -c < "$log_file")
        log INFO "$AUTO_CHECK_ID" "检查项日志已保存: $log_file (大小: ${file_size}字节)"
    else
        log WARN "$AUTO_CHECK_ID" "警告: 日志文件未创建: $log_file"
    fi
    
    # 调试信息
    log DEBUG "$AUTO_CHECK_ID" "脚本 $script_name 执行完成: result_code=$result, duration=${duration}s"
    
    return $result
}

# ====== 重试函数 ======
run_retry() {
    local script_name="$1"
    local initial_timeout="$2"
    local need_log_dir="${3:-0}"
    local check_id="${script_name%.*}"
    local attempt=0
    local current_timeout=$initial_timeout
    local success=false

    while [ $attempt -lt 3 ]; do
        ((attempt++))
        log INFO "$AUTO_CHECK_ID" "尝试执行检查项 $script_name (第 $attempt 次，超时: ${current_timeout}s)"
        
        run_script_with_timeout "$script_name" "$current_timeout" "$need_log_dir"
        local result=$?
        
        # 检查是否有项目级别的结果
        local has_item_results=false
        if [ -n "${script_items[$check_id]}" ] && [ "${script_items[$check_id]}" != "" ]; then
            has_item_results=true
        fi
        
        # 根据是否有项目级别结果来决定检查逻辑
        if [ "$has_item_results" = true ]; then
            # 有项目级别结果：检查所有项目是否都成功
            local all_success=true
            local has_failure=false
            local has_timeout=false
            
            # 检查当前脚本的所有项目
            for item_id in ${script_items[$check_id]}; do
                local item_status="${check_results[$item_id]}"
                if [ "$item_status" = "FAIL" ]; then
                    all_success=false
                    has_failure=true
                elif [ "$item_status" = "WARN" ]; then
                    # WARN不算失败，但也不是完全成功
                    all_success=false
                fi
            done
            
            # 检查超时
            if [ $result -eq 124 ]; then
                has_timeout=true
                all_success=false
            fi
            
            if [ "$all_success" = true ]; then
                success=true
                break
            elif [ "$has_timeout" = true ]; then
                log INFO "$AUTO_CHECK_ID" "检查项 $script_name 执行超时，准备重试"
                current_timeout=$(awk "BEGIN {printf \"%d\", $current_timeout * 1.3}")
                log INFO "$AUTO_CHECK_ID" "上次执行超时，下次超时时间增加至 $current_timeout 秒"
            elif [ "$has_failure" = true ]; then
                log INFO "$AUTO_CHECK_ID" "检查项 $script_name 有项目执行失败，准备重试"
            else
                # 只有WARN级别的问题，不算失败
                success=true
                break
            fi
        else
            # 没有项目级别结果：使用脚本级别状态
            local script_status="${check_results[$check_id]}"
            local script_message="${check_messages[$check_id]}"
            
            # 只有当脚本执行失败（result≠0）且状态为FAIL，或者超时时才重试
            if [ $result -eq 124 ]; then
                # 超时情况
                log INFO "$AUTO_CHECK_ID" "检查项 $script_name 执行超时，准备重试"
                current_timeout=$(awk "BEGIN {printf \"%d\", $current_timeout * 1.3}")
                log INFO "$AUTO_CHECK_ID" "上次执行超时，下次超时时间增加至 $current_timeout 秒"
            elif [ $result -ne 0 ] && [ "$script_status" = "FAIL" ]; then
                # 执行失败且状态为FAIL的情况
                log INFO "$AUTO_CHECK_ID" "检查项 $script_name 执行失败，准备重试: $script_message"
            else
                # 其他情况（成功、WARN状态等）认为成功
                success=true
                break
            fi
        fi
        
        # 如果还有重试机会，等待一下再重试
        if [ $attempt -lt 3 ] && [ "$success" = false ]; then
            sleep 2
            if [ "${script_name,,}" = "health/ping6_check.sh" ]; then
                # [DEPRECATED] 引用文件已不存在，功能由 sprinkler -no-pass 取代
                # bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/no_password.sh"  2>&1 | grep -E --line-buffered "ERROR|WARN|INFO"
                :  # no-op（原命令已下线）
            fi
        fi
    done

    if [ "$success" = false ]; then
        log FAIL "$AUTO_CHECK_ID" "检查项 $script_name 经过3次尝试后仍然失败"
    else
        log OK "$AUTO_CHECK_ID" "检查项 $script_name 执行完成"
    fi
}

# 保存汇总表格到CSV文件（修复乱码问题）
save_summary_to_csv() {
    local csv_file="${1:-$SUMMARY_CSV}"
    
    log INFO "$AUTO_CHECK_ID" "开始保存汇总表格到CSV文件"
    
    # 删除已存在的文件（如果有）
    rm -f "$csv_file"
    
    # 创建CSV文件并添加UTF-8 BOM头（解决Excel乱码问题）
    printf '\xEF\xBB\xBF' > "$csv_file"  # UTF-8 BOM
    
    # 写入CSV文件头
    echo "检查项目,状态,详细信息,日志文件路径,时间戳" >> "$csv_file"
    
    # 获取当前时间戳
    local current_timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    # 写入每个检查项目的结果
    for check_id in "${script_order[@]}"; do
        local status="${check_results[$check_id]}"
        local message="${check_messages[$check_id]}"
        local log_file_path="${script_log_files[$check_id]}"
        
        # 更好的CSV转义处理
        # 移除可能存在的换行符，替换为空格
        message=$(echo "$message" | tr '\n' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
        
        # 如果消息包含逗号、双引号或换行符，用双引号包围并转义双引号
        if echo "$message" | grep -q '[,"]'; then
            message=$(echo "$message" | sed 's/"/""/g')  # 转义双引号
            message="\"$message\""
        fi
        
        # 处理日志文件路径
        if [ -n "$log_file_path" ]; then
            log_file_path=$(realpath "$log_file_path" 2>/dev/null || echo "$log_file_path")
        else
            log_file_path="N/A"
        fi
        
        echo "$check_id,$status,$message,$log_file_path,$current_timestamp" >> "$csv_file"
    done
    
    # 计算统计信息
    local total=${#script_order[@]}
    local ok_count=0
    local warn_count=0
    local fail_count=0
    local skip_count=0
    
    for status in "${check_results[@]}"; do
        case "$status" in
            OK) ((ok_count++)) ;;
            WARN|TIMEOUT) ((warn_count++)) ;;
            FAIL|SKIP) 
                ((fail_count++))
                if [ "$status" = "SKIP" ]; then
                    ((skip_count++))
                fi
                ;;
        esac
    done
    
    # 计算最终结果
    local final_result
    final_result=$(calculate_final_result)

    # 写入汇总统计信息
    echo "汇总统计,$final_result,总计：$total，成功：$ok_count，警告：$warn_count，失败：$fail_count,$LOG_BASE_DIR,$current_timestamp" >> "$csv_file"
    if [ $skip_count -gt 0 ]; then
        echo "跳过,$skip_count,,,$current_timestamp" >> "$csv_file"
    fi
    
    # 检查文件是否成功创建
    if [ -f "$csv_file" ]; then
        local file_size=$(wc -c < "$csv_file")
        if [ $file_size -gt 0 ]; then
            log OK "$AUTO_CHECK_ID" "汇总表格已保存到CSV文件: $csv_file (大小: ${file_size}字节)"
            log INFO "$AUTO_CHECK_ID" "建议使用Excel或WPS打开，如仍有乱码请选择UTF-8编码"
        else
            log WARN "$AUTO_CHECK_ID" "CSV文件创建成功但内容为空: $csv_file"
        fi
    else
        log ERROR "$AUTO_CHECK_ID" "CSV文件创建失败: $csv_file"
    fi
}

# 打印汇总表格
print_summary_table() {
    log INFO "$AUTO_CHECK_ID" "开始生成检查结果汇总表格"
    
    # 计算表格列宽
    local name_width=15
    local status_width=8
    local message_width=40
    
    # 打印表头
    printf "\n%-${name_width}s | %-${status_width}s | %s\n" "检查项目" "状态" "详细信息"
    printf "%-${name_width}s-+-%-${status_width}s-+-%s\n" \
        "$(printf '%*s' $name_width '' | tr ' ' '-')" \
        "$(printf '%*s' $status_width '' | tr ' ' '-')" \
        "$(printf '%*s' $message_width '' | tr ' ' '-')"
    
    # 打印每一行结果
    for check_id in "${script_order[@]}"; do
        local status="${check_results[$check_id]}"
        local message="${check_messages[$check_id]}"
        
        # 处理 check_id：只保留 / 后的内容（若存在）
        local display_id="${check_id##*/}"
        
        # 根据状态设置颜色
        local color
        case "$status" in
            OK) color="$_color_ok" ;;
            WARN|TIMEOUT) color="$_color_warn" ;;
            FAIL|SKIP) color="$_color_fail" ;;
            *) color="$_color_off" ;;
        esac
        
        printf "%-${name_width}s | %b%-${status_width}s%b | %s\n" \
            "$display_id" \
            "$color" "$status" "$_color_off" \
            "$message"
    done
    
    # 打印汇总统计
    local total=${#script_order[@]}
    local ok_count=0
    local warn_count=0
    local fail_count=0
    local skip_count=0
    
    for status in "${check_results[@]}"; do
        case "$status" in
            OK) ((ok_count++)) ;;
            WARN|TIMEOUT) ((warn_count++)) ;;
            FAIL|SKIP) 
                ((fail_count++))
                if [ "$status" = "SKIP" ]; then
                    ((skip_count++))
                fi
                ;;
        esac
    done

    # 计算最终结果
    local final_result
    final_result=$(calculate_final_result)

    printf "%-${name_width}s-+-%-${status_width}s-+-%s\n" \
        "$(printf '%*s' $name_width '' | tr ' ' '-')" \
        "$(printf '%*s' $status_width '' | tr ' ' '-')" \
        "$(printf '%*s' $message_width '' | tr ' ' '-')"

    local final_color="$_color_off"
    case "$final_result" in
        OK)   final_color="$_color_ok" ;;
        WARN) final_color="$_color_warn" ;;
        FAIL) final_color="$_color_fail" ;;
        INFO) final_color="$_color_info" ;;
        DEBUG) final_color="$_color_debug" ;;
    esac

    printf "%-${name_width}s | %b%-${status_width}s%b | 总计: %d, 成功: %d, 警告: %d, 失败: %d" \
        "汇总" \
        "$final_color" "$final_result" "$_color_off" \
        $total $ok_count $warn_count $fail_count
    
    if [ $skip_count -gt 0 ]; then
        printf ", 跳过: %d" $skip_count
    fi
    printf "\n"
    
    # 显示日志目录信息
    log INFO "$AUTO_CHECK_ID" "详细日志文件保存在: $LOG_BASE_DIR"
}

# 计算最终结果
calculate_final_result() {
    local final_result="OK"
    for status in "${check_results[@]}"; do
        if [ "$status" = "FAIL" ] || [ "$status" = "SKIP" ]; then
            final_result="FAIL"
            break
        elif [ "$status" = "WARN" ] || [ "$status" = "TIMEOUT" ]; then
            if [ "$final_result" = "OK" ]; then
                final_result="WARN"
            fi
        fi
    done
    echo "$final_result"
}

# 显示用法信息
show_usage() {
    echo "用法: $0 [选项]"
    echo "选项:"
    echo "  -l, --level LEVEL    设置检查级别 (L1: 基础检查, 空: 所有检查)"
    echo "  -h, --help           显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                    # 执行所有检查项"
    echo "  $0 --level L1         # 仅执行L1级别检查项"
}

# -------- main --------
main() {
    # 解析命令行参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            -l|--level)
                LEVEL="$2"
                shift 2
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                log ERROR "$AUTO_CHECK_ID" "未知参数: $1"
                show_usage
                exit 1
                ;;
        esac
    done

    # 创建日志目录
    if ! create_log_directory; then
        log ERROR "$AUTO_CHECK_ID" "无法创建日志目录，退出"
        exit 1
    fi

    # 给auto目录下的脚本加执行权限
    if [ -d "$SCRIPTS_DIR" ]; then
        chmod -R +x "$SCRIPTS_DIR"/*
        log DEBUG "$AUTO_CHECK_ID" "已为 $SCRIPTS_DIR 目录下的脚本添加执行权限"
    else
        log WARN "$AUTO_CHECK_ID" "auto目录不存在: $SCRIPTS_DIR"
    fi
    
    # 预先收集所有参数
    log INFO "$AUTO_CHECK_ID" "开始自动检查流程"
    log INFO "$AUTO_CHECK_ID" "检查级别: ${LEVEL:-所有检查项}"
    log INFO "$AUTO_CHECK_ID" "默认超时时间: ${DEFAULT_TIMEOUT}秒"
    log INFO "$AUTO_CHECK_ID" "检查项日志目录: $LOG_BASE_DIR"  

    # [DEPRECATED] 引用文件已不存在，功能由 sprinkler -no-pass 取代
    # bash "$(dirname "$(dirname "$(realpath "${BASH_SOURCE[0]}")")")/network/no_password.sh"  2>&1 | grep -E --line-buffered "ERROR|WARN|INFO"
    :  # no-op（原命令已下线）
    # 根据级别选择执行的检查脚本
    case "$LEVEL" in
        L1|L1)
            pre_collect_parameters_L1
            log INFO "$AUTO_CHECK_ID" "执行L1级别检查项"
            run_script_with_timeout "env_check.sh" 60        # 前置检查
            run_script_with_timeout "device_online.sh" 60    # 设备在线检查
            run_retry "health/ping6_check.sh" 60       # ping检查
            run_script_with_timeout "1213/check_1213_all.sh" 300 # 1213相关：cc包、子卡注册检查
            run_script_with_timeout "n_urma_bandwidth.sh" 640 1  # URMA带宽测试
            ;;
        L2|L2)
            pre_collect_parameters_L2
            log INFO "$AUTO_CHECK_ID" "执行所有检查项"
            # 为每个检查项设置单独的超时时间
            run_script_with_timeout "log_cleanup.sh" 60      # 日志清理
            run_script_with_timeout "env_check.sh" 60
            run_script_with_timeout "device_online.sh" 60
            run_retry "health/ping6_check.sh" 60
            run_retry "set_device.sh" 120        # 设置设备
            run_retry "single_operator.sh" 100 1 # 单算子测试
            run_script_with_timeout "mpirun_bandwidth.sh" 150  # MPI带宽测试
            run_script_with_timeout "n_urma_bandwidth.sh" 640 1  # URMA带宽测试
            run_script_with_timeout "test_snr.sh" 180   # SNR测试
            run_script_with_timeout "1213/check_1213_all.sh" 300 # 1213相关：cc包、子卡注册检查
            run_script_with_timeout "log_collect.sh" 180 1      # 日志收集
            ;;
        *)
            log ERROR "$AUTO_CHECK_ID" "不支持的检查级别: $LEVEL"
            show_usage
            exit 1
            ;;
    esac

    # 打印汇总表格
    print_summary_table

    # 保存汇总表格到CSV文件
    save_summary_to_csv "$SUMMARY_CSV"

    # 根据总体结果决定退出码
    local final_result
    final_result=$(calculate_final_result)

    case "$final_result" in
        OK)
            log OK "$AUTO_CHECK_ID" "所有检查项目完成，系统状态正常"
            exit 0
            ;;
        WARN)
            log WARN "$AUTO_CHECK_ID" "检查完成，存在警告项目，请关注"
            exit 1
            ;;
        FAIL)
            log FAIL "$AUTO_CHECK_ID" "检查完成，存在失败项目，需要处理"
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"
