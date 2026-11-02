import pandas as pd
import os
import re
from pathlib import Path
from prompt_toolkit import prompt
from prompt_toolkit.completion import PathCompleter
from sprinkler.config.cabinet_info import get_cabinet_info, get_5808_info, get_cabinet_data
from sprinkler.utils.utils import default_config_dir


def clean_sheet_name(name):
    """去除页签名称中的括号及括号内内容（中英文括号）"""
    return re.sub(r'[$\（].*?[$\）]', '', name).strip()

def preprocess_column_names(df):
    """
    预处理列名，将包含换行符且以'IP'开头的列名统一为'IP'
    支持处理类似'IP\nIP跟槽位走，不跟节点走\n环境以串口号为准'的列名
    """
    new_columns = []
    for col in df.columns:
        # 检查列名是否以'IP'开头且包含换行符
        if isinstance(col, str) and col.strip().startswith('IP') and '\n' in col:
            new_columns.append('IP')
        else:
            new_columns.append(col)
    df.columns = new_columns
    if "端口号" not in df.columns:
        df["端口号"] = '22'

    def split_ip_port(row):
        ip_value = row['IP']
        if isinstance(ip_value, str) and (':' in ip_value or '：' in ip_value):
            parts = re.split(r'[:：]', ip_value, maxsplit=1)
            row['IP'] = parts[0]
            row["端口号"] = parts[1]
        return row

    df = df.apply(split_ip_port, axis=1)

    # 列值向下填充
    df["槽位"] = df["槽位"].ffill()
    df["子系统"] = df["子系统"].ffill()

    # SFU COM类别统一
    sfu_mask = df['槽位'].str.match(r'^B10\d+', na=False)
    com_mask = df['类别'].str.contains(r'COM', case=False, na=False)
    combined_mask = sfu_mask & com_mask
    df.loc[combined_mask, '类别'] = '1260 COM'

    return df

def superpod_conf():
    while True:
        file_path = prompt("输入表格路径, 输入q退出：", completer=PathCompleter()).strip()
        if file_path.lower() == 'q':
            return
        if os.path.exists(file_path):
            break
        print("文件不存在，请检查路径！")
    while True:
        sp_num = input("输入超节点编号(0-f), 输入q退出：").strip()
        if sp_num.lower() == 'q':
            return
        if re.fullmatch(r'[0-9a-f]', sp_num):
            break
        print("请输入0-9或a-f的单个字符！")
    
    conf_path = default_config_dir()
    output_path = str(conf_path / f"superpod_{sp_num}.xlsx")

    # 读取所有页签名称
    xls = pd.ExcelFile(file_path)
    raw_sheets = xls.sheet_names
    cleaned_sheets = [clean_sheet_name(name) for name in raw_sheets]

    print("页签列表：\n")
    for i, sheet in enumerate(cleaned_sheets):
        print(f"{i + 1}: {sheet}")

    if os.path.exists(output_path):
        old_sheet = pd.ExcelFile(output_path)
        old_sheet_names = old_sheet.sheet_names
        print("\n\n" + "=" * 150)
        print(f"|| 超节点{sp_num}信息已存在：{output_path}")
        print(f"|| 超节点{sp_num}页签信息：")
        print("|| " + ','.join([name for name in old_sheet_names]))
        print("=" * 150 + "\n\n")

    null_count = 0  # 用于生成 null1, null2...
    while True:
        print("请输入17个页签序号或0（空页签），用逗号分隔（例如：1, 0, 2）")
        user_input = input("支持输入页签名称，当名称为纯数字组合时请使用页签序号, 输入q退出：")
        if user_input.lower() == 'q':
            return
        cleaned_input = user_input.replace(" ", "")
        selected = user_input.split(",")
        
        valid = True
        for i, select in enumerate(selected):
            if select.isdigit():
                if select == '5808':
                    selected[i] = cleaned_sheets.index(select) + 1
                    continue
                if select != "" and (int(select) < 0 or int(select) > len(raw_sheets)):
                    print(f"输入无效：{select}，请输入0到{len(raw_sheets)}之间的数字或空值。")
                    valid = False
                    break
            else:
                if select not in cleaned_sheets:
                    print(f"输入无效：{select}，请输入页签列表中的名称")
                    valid = False
                    break
                else:
                    selected[i] = cleaned_sheets.index(select) + 1
        if not valid:
            continue
        
        break

    new_sheets = []
    new_data = {}
    for i, choice in enumerate(selected):
        if choice == "0":
            null_count += 1
            new_sheet_name = f"null{null_count}"
            new_data[new_sheet_name] = pd.DataFrame()  # 空页签
            print(f'柜号{i}生成空页签{new_sheet_name}')
            new_sheets.append(new_sheet_name)
        else:
            if i < 16:
                sheet_idx = int(choice) - 1  # 转换为 0-based 索引
                sheet_name = raw_sheets[sheet_idx]
                clean_name = cleaned_sheets[sheet_idx]
                df = pd.read_excel(xls, sheet_name=sheet_name)
                df = preprocess_column_names(df)
                try:
                    target_columns = ["槽位", "子系统", "类别", "IP", "端口号", "账号", "密码"]
                    # 检测列头是否包含目标关键字
                    matched = True
                    for col in target_columns:
                        if col not in df.columns:
                            matched = False
                    if matched:
                        df = df[target_columns]
                    else:
                        print(clean_name, '无法匹配["槽位", "子系统", "类别", "IP", "端口号", "账号", "密码"]')
                        df = pd.DataFrame()
                except:
                    print(clean_name, '无法匹配处理柜，请手动处理页签内容')
                    df = pd.DataFrame()
                new_data[clean_name] = df
                new_sheets.append(clean_name)
            else:
                sheet_idx = int(choice) - 1  # 转换为 0-based 索引
                sheet_name = raw_sheets[sheet_idx]
                clean_name = cleaned_sheets[sheet_idx]
                df = pd.read_excel(xls, sheet_name=sheet_name)
                df = preprocess_column_names(df)
                new_data['5808'] = df
                new_sheets.append('5808')

    os.makedirs(conf_path, exist_ok=True)
    with pd.ExcelWriter(output_path) as writer:
        for sheet in new_sheets:
            new_data[sheet].to_excel(writer, sheet_name=sheet, index=False)

    print(f"\n新 Excel 文件已保存为：{output_path}")
    print("页签顺序：", new_sheets)

def get_superpod_info(sp_num, ca_num, mode=None):
    file_path = str(default_config_dir() / f"superpod_{sp_num}.xlsx")
    if not os.path.exists(file_path):
        print(f'{file_path}不存在，请确认超节点号{sp_num}是否正确')
        return

    xls = pd.ExcelFile(file_path)
    sheets = xls.sheet_names
    if not mode:
        print("\n" + "=" * 40)
        print(" 柜号 |    柜名")
        for i, sheet in enumerate(sheets[:16]):
            print(f"  {hex(i)[2:]}   :  {sheet}")
        print(f" 5808 :  {sheets[-1]}")
        print("=" * 40 + "\n")
    
    if ca_num:
        ca_list = ca_num.replace(" ", "").split(",") 
        for ca in ca_list:
            if not re.fullmatch(r'[0-9a-f]', ca) and ca != '5808':
                print("ca_num请输入0-9/a-f的单个字符，或5808！")
                return
            else:
                if ca != '5808':
                    ca_name = sheets[int(ca, 16)]
                    cabinet_sheet = pd.read_excel(xls, sheet_name=ca_name)
                    if not mode:
                        mode = 'cpu, npu, sfu, RoCE'
                    grouped_ca_info = get_cabinet_info(cabinet_sheet, ca_name, sp_num, ca, mode)
                else:
                    raw_5808_sheet = pd.read_excel(xls, sheet_name='5808')
                    grouped_5808_info = get_5808_info(raw_5808_sheet, sp_num, ca)

def read_cabinet(sp_num, ca_name, default_user="root", default_password="", file_path=None):
    path = Path(file_path) if file_path else default_config_dir() / f"superpod_{sp_num}.xlsx"
    if not path.exists():
        raise FileNotFoundError(str(path))

    xls = pd.ExcelFile(path)
    try:
        sheets = list(xls.sheet_names)
        if ca_name not in sheets:
            raise KeyError(ca_name)
        df = pd.read_excel(xls, sheet_name=ca_name)
        df = preprocess_column_names(df)
        ca = sheets.index(ca_name)
        return get_cabinet_data(df, ca_name, sp_num, ca, default_user, default_password)
    finally:
        xls.close()

def modify_passwd(sp_num, ca_num, file_path):
    if not file_path:
        if not sp_num:
            while True:
                sp_num = input("输入超节点编号(0-f), 输入q退出：").strip()
                if sp_num.lower() == 'q':
                    return
                if re.fullmatch(r'[0-9a-f]', sp_num):
                    break
                print("请输入0-9或a-f的单个字符！")
        path = default_config_dir() / f"superpod_{sp_num}.xlsx"
    else:
        path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    xls = pd.ExcelFile(path)
    def choose_sheet(xls):
        if len(xls.sheet_names) > 1:
            print("\n可选页签:")
            for i, sheet in enumerate(xls.sheet_names):
                print(f"{hex(i)[2:]}: {sheet}")

            while True:
                try:
                    sheet_idx = input("请选择页签索引 (0-f):")
                    if re.fullmatch(r'[0-9a-f]', sheet_idx):
                        break
                    print("{sheet_idx}输入超出范围，请重试！")
                except ValueError:
                    print("请输入有效数字！")
        else:
            sheet_idx = 0

        return int(sheet_idx, 16)
    
    if not ca_num:
        sheet_idx = choose_sheet(xls)
    else:
        if not re.fullmatch(r'[0-9a-f]', ca_num):
            print("无效参数ca_num:{ca_num}，请输入0-9或a-f的单个字符！")
            sheet_idx =choose_sheet(xls)
        else:
            sheet_idx = int(ca_num, 16)

    df = pd.read_excel(xls, sheet_name=sheet_idx)

    while True:
        choice = input("请选择修改对象 (1.NPU/2.CPU/3.SFU/4.RoCE): ，输入q退出").strip()
        if choice.lower() == 'q':
            return
        if choice in ['1', '2', '3', '4']:
            break
        print("无效选择，请输入 1/2/3/4")

    if choice == '1':
        mask = df['子系统'].str.startswith('NPU低速管理板')
        mode = 'npu'
    elif choice == '2':
        mask = df['子系统'].str.startswith('CPU')
        mode = 'cpu'
    elif choice == '3':
        mask = df['子系统'].str.startswith('1D SFU')
        mode = 'sfu'
    else:
        mask = df['子系统'].str.startswith('1825')
        mode = 'RoCE'
    filtered_df = df[mask].copy()
    if filtered_df.empty:
        print("未找到匹配的行！")
        return
    
    filtered_df['类别前缀'] = filtered_df['类别'].str.replace(r' IP$', '', regex=True)
    unique_prefixes = sorted(filtered_df['类别前缀'].unique())
    print("\n可选类别:")
    for i, prefix in enumerate(unique_prefixes, 1):
        print(f"{i}. {prefix}")
    
    while True:
        try:
            selected_num = int(input("\n请选择要修改的类别编号: "))
            if 1 <= selected_num <= len(unique_prefixes):
                selected_prefix = unique_prefixes[selected_num - 1]
                break
            print("输入超出范围，请重试！")
        except ValueError:
            print("请输入有效数字！")
    
    password = input("请输入密码: ")
    update_mask = (filtered_df['类别前缀'] == selected_prefix)
    df.loc[filtered_df.index[update_mask], '密码'] = password
    
    temp_path = "temp.xlsx"
    try:
        with pd.ExcelWriter(temp_path, engine='openpyxl') as writer:
            for idx, sheet_name in enumerate(xls.sheet_names):
                if idx == sheet_idx:
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                else:
                    pd.read_excel(xls, sheet_name=idx).to_excel(writer, sheet_name=sheet_name, index=False)
            
            # 确保至少有一个工作表可见
            workbook = writer.book
            for sheet in workbook.worksheets:
                sheet.sheet_state = 'visible'
        
        # 13. 用临时文件覆盖原文件
        os.replace(temp_path, path)
        print(f"\n操作完成，文件已保存至: {path}")
    
    except Exception as e:
        print(f"\n保存文件时发生错误: {e}")
        if os.path.exists(temp_path):
            os.remove(temp_path)
        print("临时文件已删除。")
    get_superpod_info(sp_num, ca_num, mode=mode)

if __name__ == "__main__":
    superpod_conf()
