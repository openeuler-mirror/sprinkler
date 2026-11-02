import re

import pandas as pd


def clean_cell(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def pick_value(group, category, column, default=""):
    rows = group.loc[group["类别"] == category, column]
    if rows.empty:
        return default
    return clean_cell(rows.iloc[0])


def parse_port(value, default=22):
    text = clean_cell(value)
    if not text:
        return default
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return int(float(text))
    return text


def collect_cpu_info(group, cpu_info, name):
    ordered_group = group.sort_values(
        by="类别",
        key=lambda x: x.map({
            "BMC IP": 0,
            "BMC COM": 1,
            "1213后台IP": 2,
            "1213前台IP": 3,
            "CPU OS IP": 4,
        }),
    )

    bmc_ip = pick_value(ordered_group, "BMC IP", "IP")
    bmc_passwd = pick_value(ordered_group, "BMC IP", "密码")
    bmc_com = pick_value(ordered_group, "BMC COM", "IP")
    backend_ip = pick_value(ordered_group, "1213后台IP", "IP")
    frontend_ip = pick_value(ordered_group, "1213前台IP", "IP")
    os_ip = pick_value(ordered_group, "CPU OS IP", "IP")
    os_user = pick_value(ordered_group, "CPU OS IP", "账号", "root")
    os_passwd = pick_value(ordered_group, "CPU OS IP", "密码")
    os_port = pick_value(ordered_group, "CPU OS IP", "端口号", "22")

    cpu_num = int(re.search(r"CPU(\d+)", name).group(1))
    cpu_info[cpu_num] = {
        "bmc_ip": bmc_ip,
        "bmc_passwd": bmc_passwd,
        "bmc_com": bmc_com,
        "backend_ip": backend_ip,
        "frontend_ip": frontend_ip,
        "os_ip": os_ip,
        "os_user": os_user,
        "os_passwd": os_passwd,
        "os_port": os_port,
    }
    return cpu_info


def collect_npu_info(group, npu_info, name):
    ordered_group = group.sort_values(
        by="类别",
        key=lambda x: x.map({
            "BMC IP": 0,
            "BMC COM": 1,
            "1213后台IP": 2,
            "1213前台IP": 3,
        }),
    )
    bmc_ip = pick_value(ordered_group, "BMC IP", "IP")
    bmc_passwd = pick_value(ordered_group, "BMC IP", "密码")
    bmc_com = pick_value(ordered_group, "BMC COM", "IP")
    backend_ip = pick_value(ordered_group, "1213后台IP", "IP")
    frontend_ip = pick_value(ordered_group, "1213前台IP", "IP")

    npu_num = int(re.search(r"NPU低速管理板(\d+)", name).group(1))
    npu_info[npu_num] = {
        "bmc_ip": bmc_ip,
        "bmc_passwd": bmc_passwd,
        "bmc_com": bmc_com,
        "backend_ip": backend_ip,
        "frontend_ip": frontend_ip,
    }
    return npu_info


def collect_sfu_info(group, sfu_info, name):
    ordered_group = group.sort_values(
        by="类别",
        key=lambda x: x.map({
            "BMC IP": 0,
            "1213后台IP": 1,
            "1213前台IP": 2,
            "1260 COM": 3,
        }),
    )
    bmc_ip = pick_value(ordered_group, "BMC IP", "IP")
    bmc_passwd = pick_value(ordered_group, "BMC IP", "密码")
    sfu_com = pick_value(ordered_group, "1260 COM", "IP")
    port = pick_value(ordered_group, "1260 COM", "端口号")
    sfu_num = int(re.search(r"B10(\d+)", name).group(1))
    sfu_info[sfu_num] = {
        "bmc_ip": bmc_ip,
        "bmc_passwd": bmc_passwd,
        "sfu_com": sfu_com,
        "port": port,
    }
    return sfu_info


def collect_roce_info(group, roce_info, name):
    """采集 1825 板（RoCE 板）的连接信息。

    1825 板按"子系统"分组（"1825上板"/"1825下板"），与 SFU 按"槽位"分组不同。
    Excel 中 1825 类别名带尾随空格（如 "1260COM "），此处先 strip 再匹配。
    """
    group = group.copy()
    group["类别"] = group["类别"].astype(str).str.strip()
    ordered_group = group.sort_values(
        by="类别",
        key=lambda x: x.map({
            "BMC IP": 0,
            "BMCCOM": 1,
            "1260后台IP": 2,
            "1260前台IP": 3,
            "1260COM": 4,
            "1260 COM": 4,
        }),
    )
    bmc_ip = pick_value(ordered_group, "BMC IP", "IP")
    bmc_passwd = pick_value(ordered_group, "BMC IP", "密码")
    com_ip = (pick_value(ordered_group, "1260COM", "IP")
              or pick_value(ordered_group, "1260 COM", "IP"))
    com_port = (pick_value(ordered_group, "1260COM", "端口号")
                or pick_value(ordered_group, "1260 COM", "端口号"))
    backend_ip = pick_value(ordered_group, "1260后台IP", "IP")
    frontend_ip = pick_value(ordered_group, "1260前台IP", "IP")
    roce_num = 0 if "上板" in name else 1
    roce_info[roce_num] = {
        "bmc_ip": bmc_ip,
        "bmc_passwd": bmc_passwd,
        "com_ip": com_ip,
        "com_port": com_port,
        "backend_ip": backend_ip,
        "frontend_ip": frontend_ip,
        "raw_name": name,
    }
    return roce_info


def collect_smn_info(group, smn_info, name):
    ordered_group = group.sort_values(
        by="类别",
        key=lambda x: x.map({
            "BMC IP": 0,
            "OS IP": 1,
            "COM": 2,
        }),
    )
    bmc_ip = pick_value(ordered_group, "BMC IP", "IP")
    bmc_passwd = pick_value(ordered_group, "BMC IP", "密码")
    os_ip = pick_value(ordered_group, "OS IP", "IP")
    os_passwd = pick_value(ordered_group, "OS IP", "密码")
    com_ip = pick_value(ordered_group, "COM", "IP")
    com_port = pick_value(ordered_group, "COM", "端口号")

    smn_num = int(re.search(r"SMN(\d+)", name).group(1))
    smn_info[smn_num] = {
        "bmc_ip": bmc_ip,
        "bmc_passwd": bmc_passwd,
        "os_ip": os_ip,
        "os_passwd": os_passwd,
        "com_ip": com_ip,
        "com_port": com_port,
    }
    return smn_info


def collect_info(df, ca_name):
    cpu_info = {}
    npu_info = {}
    sfu_info = {}
    smn_info = {}
    roce_info = {}

    if "子系统" in df.columns and "类别" in df.columns and "IP" in df.columns:
        grouped = df.groupby("子系统")
        for name, group in grouped:
            if re.match(r"CPU\d+", str(name)):
                cpu_info = collect_cpu_info(group, cpu_info, name)
            if re.match(r"NPU低速管理板\d+", str(name)):
                npu_info = collect_npu_info(group, npu_info, name)
            if re.match(r"SMN\d+", str(name)):
                smn_info = collect_smn_info(group, smn_info, name)
            if re.match(r"^1825(上板|下板)", str(name)):
                roce_info = collect_roce_info(group, roce_info, name)
        grouped = df.groupby("槽位")
        for name, group in grouped:
            if re.match(r"B10\d+", str(name)):
                sfu_info = collect_sfu_info(group, sfu_info, name)
        if not cpu_info:
            print(f"页签{ca_name}CPU信息无效，请检查表格是否规范")
        if not npu_info:
            print(f"页签{ca_name}NPU信息无效，请检查表格是否规范")
        if not sfu_info:
            print(f"页签{ca_name}SFU信息无效，请检查表格是否规范")
        if not smn_info:
            print(f"页签{ca_name}SMN信息无效，请检查表格是否规范")
        if not roce_info:
            print(f"页签{ca_name}RoCE信息无效，请检查表格是否规范")

    return cpu_info, npu_info, sfu_info, smn_info, roce_info


def get_cabinet_info(df, ca_name, sp_num, ca, mode='cpu, npu, sfu, smn'):
    # 处理IP数据
    cpu_info, npu_info, sfu_info, smn_info, roce_info = collect_info(df, ca_name)

    # 生成IP列表
    sorted_cpus = sorted(cpu_info.keys())
    os_ips = [cpu_info[cpu]["os_ip"] for cpu in sorted_cpus]
    os_pws = [cpu_info[cpu]["os_passwd"] for cpu in sorted_cpus]
    cpu_bmc_ips = [cpu_info[cpu]["bmc_ip"] for cpu in sorted_cpus]
    cpu_bmc_pwds = [cpu_info[cpu]["bmc_passwd"] for cpu in sorted_cpus]

    sorted_npus = sorted(npu_info.keys())
    npu_bmc_ips = [npu_info[npu]["bmc_ip"] for npu in sorted_npus]
    npu_bmc_pwds = [npu_info[npu]["bmc_passwd"] for npu in sorted_npus]

    sorted_sfus = sorted(sfu_info.keys())


    # 打印配置内容
    print("\n" + "=" * 40)
    print(f"[Info]{ca_name}")
    print(f"superpod_num={sp_num}")
    print(f"cabinet_num={ca}")
    if 'cpu' in mode:
        print(f"<<< OS >>>")
        print(f"cpu_os_ips={','.join(os_ips)}")
        print(f"cpu_os_user=root")
        print(f"cpu_os_passwd={','.join(str(pwd) for pwd in os_pws)}")
        print(f"<<< CPU BMC >>>")
        print(f"cpu_bmc_ips={','.join(cpu_bmc_ips)}")
        print(f"cpu_bmc_user=Administrator")
        print(f"cpu_bmc_passwd={','.join(str(pwd) for pwd in cpu_bmc_pwds)}")
    if 'npu' in mode:
        print(f"<<< NPU BMC >>>")
        print(f"npu_bmc_ips={','.join(npu_bmc_ips)}")
        print(f"npu_bmc_user=Administrator")
        print(f"npu_bmc_passwd={','.join(str(pwd) for pwd in npu_bmc_pwds)}")
    if 'sfu' in mode:
        print(f"<<< SFU >>>")
        for sfu in sorted_sfus:
            print(f"sfu{sfu}={sfu_info[sfu]['sfu_com']}:{sfu_info[sfu]['port']}")
    if 'smn' in mode:
        sorted_smns = sorted(smn_info.keys())
        print(f"<<< SMN >>>")
        for smn in sorted_smns:
            print(f"smn{smn}_os_ip={smn_info[smn]['os_ip']}")
            print(f"smn{smn}_bmc_ip={smn_info[smn]['bmc_ip']}")
    print("=" * 40 + "\n")


def get_cabinet_data(df, ca_name, sp_num, ca, default_user="root", default_password=""):
    cpu_info, npu_info, sfu_info, smn_info, roce_info = collect_info(df, ca_name)
    cpu_list = []
    for index, cpu in enumerate(sorted(cpu_info.keys())):
        item = cpu_info[cpu]
        if not item.get("os_ip"):
            continue
        cpu_list.append({
            "index": index,
            "name": f"cpu{index}",
            "raw_name": f"CPU{cpu}",
            "host": item.get("os_ip", ""),
            "user": item.get("os_user") or default_user,
            "password": item.get("os_passwd") or default_password,
            "port": parse_port(item.get("os_port"), 22),
            "cpu_bmc_host": item.get("bmc_ip", ""),
            "cpu_bmc_password": item.get("bmc_passwd", ""),
            "cpu_bmc_com_host": item.get("bmc_com", ""),
            "backend_host": item.get("backend_ip", ""),
            "frontend_host": item.get("frontend_ip", ""),
        })

    npu_list = []
    for index, npu in enumerate(sorted(npu_info.keys())):
        item = npu_info[npu]
        npu_list.append({
            "index": index,
            "name": f"npu{index}",
            "raw_name": f"NPU低速管理板{npu}",
            "bmc_host": item.get("bmc_ip", ""),
            "bmc_password": item.get("bmc_passwd", ""),
            "bmc_com_host": item.get("bmc_com", ""),
            "backend_host": item.get("backend_ip", ""),
            "frontend_host": item.get("frontend_ip", ""),
        })

    sfu_list = []
    for index, sfu in enumerate(sorted(sfu_info.keys())):
        item = sfu_info[sfu]
        sfu_list.append({
            "index": index,
            "name": f"sfu{index}",
            "raw_name": f"B10{sfu}",
            "bmc_host": item.get("bmc_ip", ""),
            "bmc_password": item.get("bmc_passwd", ""),
            "com_host": item.get("sfu_com", ""),
            "com_port": item.get("port", ""),
        })

    smn_list = []
    for index, smn in enumerate(sorted(smn_info.keys())):
        item = smn_info[smn]
        if not item.get("os_ip"):
            continue
        smn_list.append({
            "index": index,
            "name": f"smn{index}",
            "raw_name": f"SMN{smn}",
            "host": item.get("os_ip", ""),
            "user": item.get("os_user") or default_user,
            "password": item.get("os_passwd") or default_password,
            "port": parse_port(item.get("os_port"), 22),
            "bmc_host": item.get("bmc_ip", ""),
            "com_host": item.get("com_ip", ""),
            "com_port": item.get("com_port", ""),
        })

    roce_list = []
    for index, roce in enumerate(sorted(roce_info.keys())):
        item = roce_info[roce]
        roce_list.append({
            "index": index,
            "name": f"roce{index}",
            "raw_name": item.get("raw_name", ""),
            "bmc_host": item.get("bmc_ip", ""),
            "bmc_password": item.get("bmc_passwd", ""),
            "com_host": item.get("com_ip", ""),
            "com_port": item.get("com_port", ""),
            "backend_host": item.get("backend_ip", ""),
            "frontend_host": item.get("frontend_ip", ""),
        })

    return {
        "sp": str(sp_num),
        "ca": str(ca),
        "cabinet_name": str(ca_name),
        "cpu": cpu_list,
        "npu": npu_list,
        "sfu": sfu_list,
        "RoCE": roce_list,
        "smn": smn_list,
    }


def get_5808_info(df, sp_num, ca):
    grouped_5808_info = {}
    df["SFU"] = df["SFU"].ffill()
    print(df)
