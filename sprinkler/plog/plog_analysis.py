import pandas as pd
import re

def load_fault_library(csv_path):
    """加载并预处理故障模式库"""
    try:
        df = pd.read_csv(csv_path)
        df['error_code'] = df['error_code'].apply(
            lambda x: str(x).strip() if pd.notna(x) and str(x).strip().lower() not in ['nan', 'na', ''] else ''
        )
        df['keyword_list'] = df['error_keywords'].apply(
            lambda x: [kw.strip().lower() for kw in str(x).split('&') if kw.strip()]
            if pd.notna(x) and str(x).strip() and str(x).strip().lower() not in ['nan', 'na']
            else []
        )
        df = df[~((df['error_code'] == '') & (df['keyword_list'].apply(len) == 0))]
        return df
    except FileNotFoundError:
        print(f"错误：故障模式库文件 {csv_path} 未找到。")
        return None
    except Exception as e:
        print(f"加载故障模式库时出错: {e}")
        return None

def get_log_context(log_lines, anchor_line_number, window_before=10, window_after=20):
    """从日志行列表中提取指定锚点行号（1-based）的上下文窗口。"""
    if anchor_line_number <= 0 or anchor_line_number > len(log_lines) : # 基本校验
        return "" 
    start_index = max(0, anchor_line_number - 1 - window_before)
    end_index = min(len(log_lines), anchor_line_number - 1 + window_after + 1)
    context_lines = log_lines[start_index:end_index]
    return "\n".join(context_lines)

def match_single_fault_pattern(log_context_original, log_context_lower, fault_pattern_row):
    """根据精确规则将单个故障模式与日志上下文进行匹配。"""
    pattern_ec = fault_pattern_row['error_code']
    pattern_ek_list = fault_pattern_row['keyword_list']
    ec_defined = bool(pattern_ec)
    ek_defined = bool(pattern_ek_list)
    ec_matches = False
    if ec_defined:
        if re.search(r"\b" + re.escape(pattern_ec) + r"\b", log_context_original):
            ec_matches = True
    ek_matches = False
    if ek_defined:
        all_keywords_found = True
        for keyword_lower in pattern_ek_list:
            if keyword_lower not in log_context_lower:
                all_keywords_found = False
                break
        if all_keywords_found:
            ek_matches = True
    if ec_defined and ek_defined:
        return ec_matches and ek_matches
    elif ec_defined and not ek_defined:
        return ec_matches
    elif not ec_defined and ek_defined:
        return ek_matches
    else:
        return False

def find_matching_faults_prioritized(log_context_original, fault_df):
    """(此函数在有锚点时使用) 根据优先级规则查找第一个匹配的故障模式。"""
    if not log_context_original or fault_df is None or fault_df.empty:
        return []
    log_context_lower = log_context_original.lower()
    df_priority1 = fault_df[fault_df['error_code'] != '']
    for index, row in df_priority1.iterrows():

        if match_single_fault_pattern(log_context_original, log_context_lower, row):
            return [row.to_dict()]
    df_priority2 = fault_df[(fault_df['error_code'] == '') & (fault_df['keyword_list'].apply(len) > 0)]
    for index, row in df_priority2.iterrows():
        if match_single_fault_pattern(log_context_original, log_context_lower, row):
            return [row.to_dict()]
    return []

def find_first_occurrence_in_log(log_lines, term_to_find, is_error_code=False):
    """查找一个词条在日志中首次出现的行号 (1-based)。"""
    if not term_to_find: return -1
    if is_error_code:
        regex = r"\b" + re.escape(term_to_find) + r"\b" # 全词匹配，区分大小写
        for i, line in enumerate(log_lines):
            if re.search(regex, line):
                return i + 1
    else: # 普通关键词，不区分大小写子串匹配 (此处未使用，但保留)
        term_lower = term_to_find.lower()
        for i, line in enumerate(log_lines):
            if term_lower in line.lower():
                return i + 1
    return -1

def main_fault_matcher(log_file_path, fault_library_csv_path, 
                       no_anchor_scan_context_config # 用于无锚点扫描时，EC命中后的上下文提取配置
                      ):
    fault_df = load_fault_library(fault_library_csv_path)
    if fault_df is None:
        print("无法加载故障库，程序退出。")
        return

    try:
        with open(log_file_path, "r", encoding="utf-8") as f:
            all_log_lines = [line.rstrip("\n") for line in f.readlines()]
    except FileNotFoundError:
        print(f"错误：日志文件 {log_file_path} 未找到。")
        return
    except Exception as e:
        print(f"读取日志文件时出错: {e}")
        return

    final_match_details = None # 用于存储最终的匹配结果和相关信息


    # --- 场景: 首报错行未知或无效 ---
    print("已获取 plog 日志，开始基于故障库中的 error_code 进行扫描...")
    # 优先处理定义了 error_code 的模式
    # 按原始顺序遍历，如果需要可以增加其他排序逻辑（例如按模式的某种置信度）
    df_with_ec = fault_df[fault_df['error_code'] != ''].copy()

    for _, pattern_row in df_with_ec.iterrows():
        pattern_ec_to_find = pattern_row['error_code']
        print(f"  扫描日志查找 error_code: '{pattern_ec_to_find}' ")

        # 在整个日志中查找此 error_code 的首次出现
        found_ec_line_num = find_first_occurrence_in_log(all_log_lines, pattern_ec_to_find, is_error_code=True)

        if found_ec_line_num > 0:
            print(f"    找到 error_code '{pattern_ec_to_find}' 在日志行 {found_ec_line_num}。提取上下文...")
            # 围绕找到的 error_code 行提取固定大小的上下文
            wb_no_anchor = no_anchor_scan_context_config[0]
            wa_no_anchor = no_anchor_scan_context_config[1]
            context_original = get_log_context(all_log_lines, found_ec_line_num,
                                               window_before=wb_no_anchor,
                                               window_after=wa_no_anchor)
            if context_original:
                context_lines_for_print_no_anchor = context_original.split('\n')
                if not context_lines_for_print_no_anchor:
                    print("        (上下文为空)")
            else:
                print("        (未能提取有效上下文)")

            context_lower = context_original.lower()

            # 直接用此特定模式对此上下文进行匹配
            if match_single_fault_pattern(context_original, context_lower, pattern_row):
                print(f"    🎉 模式 '{pattern_row['pattern_name']}' 与基于 error_code '{pattern_ec_to_find}' (定位在行 {found_ec_line_num}) 的上下文匹配成功!")
                print(f'首报错上下文：')
                print(context_original)
                final_match_details = {
                    "fault_info": pattern_row.to_dict(),
                    "match_method": "无锚点扫描 (基于error_code)",
                    "anchor_line": found_ec_line_num, # 此处anchor是动态找到的EC行
                    "trigger_info": f"error_code '{pattern_ec_to_find}'",
                    "window_config_used": f"前{wb_no_anchor}, 后{wa_no_anchor}"
                }
                break # 找到一个匹配，停止扫描其他 error_code
        else:
            wb_no_anchor = 1
            wa_no_anchor = len(all_log_lines)
            context_original = get_log_context(all_log_lines, 1,
                                               window_before=wb_no_anchor,
                                               window_after=wa_no_anchor)
            matched_faults_list = find_matching_faults_prioritized(context_original, fault_df)
            if matched_faults_list:
                final_match_details = {
                    "fault_info": matched_faults_list[0],
                    "match_method": "无锚点扫描 (基于key_words)",
                    "anchor_line": 0,
                    "window_config_used": f"前{wb_no_anchor}, 后{wa_no_anchor}"
                }
                print(f"  🎉 在窗口 (第{wb_no_anchor}行到第{wa_no_anchor}行) 中找到匹配!")
                break # 找到匹配，停止迭代

    if not final_match_details:
        print("  未找到匹配的故障模式，建议求助plog首报错对应的组件。")
        # 此处可以添加对仅含关键词的模式的扫描逻辑，但会更复杂且可能慢

    print("\n")
    # --- 统一打印结果 ---
    if final_match_details:
        fault = final_match_details["fault_info"]
        print(f"\n--------- 最终匹配结果 ({final_match_details['match_method']}) ---------")

        if "anchor_line" in final_match_details:
             print(f"  定位/锚点行: {final_match_details['anchor_line']}")
        if "trigger_info" in final_match_details:
             print(f"  触发信息: {final_match_details['trigger_info']}")
        print(f"  使用上下文配置: {final_match_details['window_config_used']}")

        print(f"  错误码 (Error Code in pattern): {fault.get('error_code', 'N/A') if fault.get('error_code') else '未指定'}")
        original_keywords_str = ' & '.join(fault.get('keyword_list', [])) if fault.get('keyword_list') else '未指定'
        print(f"  错误关键词 (Error Keywords in pattern): {original_keywords_str}")
        print(f"  环境类别 (env_class): {fault.get('env_class', 'N/A')}")
        print(f"  错误原因 (Error Cause): {fault.get('error_cause', 'N/A')}")
        print(f"  解决方案 (Resolution): {fault.get('resolution', 'N/A')}")
        print(f"  关键日志 (Key Log): {fault.get('key_log', 'N/A')}")
    else:
        print("\n--------- 最终未找到任何匹配的故障模式 ---------")
    print("\n")



if __name__ == '__main__':
    LOG_FILE = "plog_error.log"
    FAULT_CSV = "plog_fault_library.csv"
    
    fault_data_dict = {
        'env_class':          ['操作系统', '运行时',   '运行时',    '网络',   '操作系统',   '应用层', '存储'],
        'pattern_name':          ['P1_磁盘满严格','P1_CudaXID错误','P2_Cuda内存溢出','P2_RDMA超时','P1_文件系统错误','P2_通用断言失败', 'P1_NFSStaleHandle'],
        'error_cause':           ['磁盘空间耗尽','GPU硬件错误','CUDA内存耗尽','网络连接问题','文件系统损坏','内部逻辑断言失败', 'NFS句柄失效'],
        'resolution':            ['清理磁盘','重启节点/更换GPU','减小批量/优化模型','检查线缆/交换机','执行fsck','调试框架代码', '重新挂载/检查NFS服务'],
        'key_log':               ['磁盘空间不足','XID错误','内存分配失败','LID状态为DOWN','读写错误','断言失败', 'Stale file handle'],
        'error_code':            ['SYS_E001','GPU_E031',    '',           '',           'FS_E005',     '', 'NFS_E002'], 
        'error_keywords':        ['no space left', 'XID',  'cudaErrorMemoryAllocation&out of memory', 'port_rcv_errors&LID', 'I/O error', 'AssertionError&Check failed', 'Stale file handle&Operation not permitted']
    }
    max_len = 0
    for k_vc_main in fault_data_dict:
        if len(fault_data_dict[k_vc_main]) > max_len: max_len = len(fault_data_dict[k_vc_main])
    for k_vc_main in fault_data_dict:
        if len(fault_data_dict[k_vc_main]) < max_len: fault_data_dict[k_vc_main].extend([None]*(max_len - len(fault_data_dict[k_vc_main])))
    pd.DataFrame(fault_data_dict).to_csv(FAULT_CSV, index=False)

    log_content_list = [
        "INFO: 训练任务开始。", #1
        "DEBUG: 初始化参数...", #2
        "WARN: /data 目录磁盘空间不足。当前使用率 90%。", #3
        "ERROR: 严重系统错误。错误码: SYS_E001。日志: /var/log/messages。详情: /data 分区设备上没有剩余空间 (No space left on device)。", #4 (匹配 P1_磁盘满严格)
        "INFO: 第 1 轮训练完成。", #5
        "ERROR: GPU 0 返回 XID 31。错误码: GPU_E031。强制重置GPU。", #6 (匹配 P1_CudaXID错误)
        "INFO: 第 2 轮训练完成。", #7
        "FATAL: CUDA 运行时错误: cudaErrorMemoryAllocation: out of memory。文件: allocator.cpp, 行: 72", #8 (匹配 P2_Cuda内存溢出)
        "INFO: 第 3 轮训练完成。", #9
        "WARN: mlx5_0 网卡出现大量 port_rcv_errors。适配器 LID status 为 DOWN。", #10 (匹配 P2_RDMA超时)
        "INFO: 第 4 轮训练完成。", #11
        "ERROR: 在 /dev/sdb1 上检测到文件系统损坏。错误码: FS_E005。消息: 读取时发生 I/O error。", #12 (匹配 P1_文件系统错误)
        "INFO: 第 5 轮训练完成。", #13
        "CRITICAL: 应用逻辑失败。AssertionError: 在 validation_step 中 Check failed。值为 None。", #14 (匹配 P2_通用断言失败)
        "INFO: 训练结束。", #15
        "DEBUG: 尝试访问NFS路径 /mnt/nfs_data/dataset.zip", #16
        "ERROR: mount.nfs: Stale file handle. Operation not permitted on /mnt/nfs_data. Error Code: NFS_E002", #17 (匹配 P1_NFSStaleHandle)
        "INFO: 日志记录结束。" #18
    ]
    with open(LOG_FILE, "w", encoding="utf-8") as f: f.write("\n".join(log_content_list))
    print(f"模拟日志文件 {LOG_FILE} 和故障库 {FAULT_CSV} 已创建。\n")
    
    # --- 配置 ---
    # 示例场景：当首报错行未知时，进行无锚点扫描，如果找到error_code，以此为锚点提取上下文的配置
    NO_ANCHOR_SCAN_CONTEXT = (10, 30) # (向前行数, 向后行数)

    print("\n=============== plog首报错匹配测试场景:首报错行未知 ===============")
    # 模拟首报错脚本未找到明确的 "error" 或 "failure" 行
    # 脚本将尝试基于 fault_library 中的 error_code 进行扫描
    # 期望它能通过扫描找到 SYS_E001 (在第4行)并匹配 P1_磁盘满严格
    # (如果 P1_磁盘满严格是 fault_df 中第一个带 SYS_E001 的可匹配模式)
    main_fault_matcher(LOG_FILE, FAULT_CSV,
                       no_anchor_scan_context_config=NO_ANCHOR_SCAN_CONTEXT)
                       