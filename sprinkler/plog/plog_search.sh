#!/bin/bash
error=$1
if [ "$error" ]; then
    # 默认参数
    LOG_LEVEL=1
    SEARCH_CASE=1
    ERROR_NUMBER=15
    CUSTOM_LOG_PATH=""
    VERBOSE=false

    # 显示帮助信息
    show_help() {
        echo "Plog错误搜索工具"
        echo "用法: $0 [选项]"
        echo "选项:"
        echo "  -plevel LEVEL        日志层级: 1-plog, 2-debug, 3-log (默认: 1)"
        echo "  -n NUM              显示报错条数 (默认: 15)"
        echo "  -plog_path PATH      自定义日志路径 (覆盖层级路径)"
        echo "  -v, --verbose       详细输出模式"
        echo "  -h, --help         显示此帮助信息"
    }

    # 修复的解析命令行参数函数
    parse_arguments() {
        # 调试信息：显示所有传入的参数
        if [[ "$VERBOSE" == true ]]; then
            echo "调试: 传入的参数: $@"
        fi
        
        while [[ $# -gt 0 ]]; do
            case "$1" in
                -error)
                    shift
                    ;;
                -plevel)
                    if [[ -n "$2" && "$2" =~ ^[1-3]$ ]]; then
                        LOG_LEVEL="$2"
                        if [[ "$VERBOSE" == true ]]; then
                            echo "调试: 设置 PLOG_LEVEL = $LOG_LEVEL"
                        fi
                        shift 2
                    else
                        echo "错误: -plevel 参数需要 1, 2 或 3"
                        exit 1
                    fi
                    ;;
                -n)
                    if [[ -n "$2" && "$2" =~ ^[0-9]+$ && "$2" -gt 0 ]]; then
                        ERROR_NUMBER="$2"
                        if [[ "$VERBOSE" == true ]]; then
                            echo "调试: 设置 ERROR_NUMBER = $ERROR_NUMBER"
                        fi
                        shift 2
                    else
                        echo "错误: -n 参数需要正整数"
                        exit 1
                    fi
                    ;;        
                -h|--help)
                    show_help
                    exit 0
                    ;;
                *)
                    # 检查是否是不认识的选项（以-开头）
                    if [[ "$1" =~ ^- ]]; then
                        echo "Unknown: $1"
                        show_help
                        exit 1
                    else
                        # 如果不是选项，可能是位置参数，暂时跳过
                        if [[ "$VERBOSE" == true ]]; then
                            echo "调试: 跳过位置参数: $1"
                        fi
                        shift
                    fi
                    ;;
            esac
        done
    }

    # 设置日志目录
    set_log_directory() {
        local level="$1"
        local custom_path="$2"
        
        if [[ -n "$custom_path" ]]; then
            echo "$custom_path"
            return
        fi
        
        local base_dir="/root/ascend/log"
        case "$level" in
            1) echo "$base_dir/debug/plog" ;;
            2) echo "$base_dir/debug" ;;
            3) echo "$base_dir" ;;
            *) echo "$base_dir/debug/plog" ;;
        esac
    }

    # 统一的混叠排序函数
    extract_and_sort_errors() {
        local search_dir="$1"
        local exclude_dirs="$2"
        local description="$3"
        local error_count="$4"  

        echo "$description"
        if [[ -n "$exclude_dirs" ]]; then
            echo "（exclude: $exclude_dirs）"
        fi
        echo "========================================"

        if [[ ! -d "$search_dir" ]]; then
            echo "❌ The directory does not exist.: $search_dir"
            echo ""
            return
        fi

        # 调试信息
        if [[ "$VERBOSE" == true ]]; then
            echo "Debugging: Search Directory: $search_dir"
            echo "Debugging: Troubleshooting Mode: ${exclude_dirs:-无}"
            echo "Debugging: Number of Displayed Items: $error_count"
        fi

        # 构建find命令
        local find_cmd="find '$search_dir' -name '*.log' -type f"
        if [[ -n "$exclude_dirs" ]]; then
            find_cmd="$find_cmd -not -path '$exclude_dirs'"
        fi

        errors=$(eval "$find_cmd -exec grep -nH '\[ERROR\]' {} +" 2>/dev/null | awk -F: '
        function extract_timestamp(content) {
            if (match(content, /[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}\.[0-9]{3}/)) {
                return substr(content, RSTART, RLENGTH)
            }
            if (match(content, /[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}/)) {
                return substr(content, RSTART, RLENGTH)
            }
            if (match(content, /[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}/)) {
                return substr(content, RSTART, RLENGTH)
            }
            return ""
        }

        function normalize_timestamp(ts) {
            if (ts == "") return "99999999999999999999"
            gsub(/[^0-9]/, "", ts)
            while (length(ts) < 17) ts = ts "0"
            return substr(ts, 1, 17)
        }

        /\[ERROR\]/ {
            filename = $1
            line_num = $2
            content = substr($0, length(filename) + length(line_num) + 3)

            timestamp_str = extract_timestamp(content)
            normalized_ts = normalize_timestamp(timestamp_str)

            print normalized_ts "|" timestamp_str "|" filename ":" line_num ":" content
        }' | sort -t'|' -k1,1n | head -n "$error_count")  # 使用双引号确保变量展开

        if [[ -n "$errors" ]]; then
            local count=1
            local total=$(echo "$errors" | wc -l)
            echo "找到 $total 条最早报错（请求显示 $error_count 条）:"
            echo ""
            
            echo "$errors" | while IFS= read -r error; do
                IFS='|' read -r normalized_ts original_ts original_line <<< "$error"
                filename=$(echo "$original_line" | cut -d: -f1)
                line_num=$(echo "$original_line" | cut -d: -f2)
                content=$(echo "$original_line" | cut -d: -f3-)

                echo "--- 报错 $count ---: $filename +$line_num 时间: ${original_ts:-无法解析时间戳}"
                echo "-- 报错信息 --: $content"
                echo ""
                count=$((count + 1))
            done
        else
            echo "❌ 未找到ERROR报错"
        fi
        echo ""

        # 保存到文件，用于后续plog字段匹配
        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        echo "$errors" > "$SCRIPT_DIR/plog_error.log"
    }

    # 主函数
    main() {
        # 解析参数
        parse_arguments "$@"
        
        # 设置日志目录
        local actual_log_dir=$(set_log_directory "$LOG_LEVEL" "$CUSTOM_LOG_PATH")
        
        # 显示配置信息
        echo "=== Plog搜索配置 ==="
        echo "日志层级: $LOG_LEVEL"
        echo "搜索模式: $SEARCH_CASE"
        echo "显示条数: $ERROR_NUMBER"
        echo "搜索目录: $actual_log_dir"
        echo "==================="
        echo ""
        
        # 执行搜索 - 确保传递正确的ERROR_NUMBER
        extract_and_sort_errors "$actual_log_dir" "$exclude_dirs" \
            "Plog错误搜索结果" "$ERROR_NUMBER"
    }

    # 执行主函数
    main "$@"
fi