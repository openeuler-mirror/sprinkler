"""Excel 三级台账导入（superpod_<sp>.xlsx）。

- 复用 ``config/superpod_info.preprocess_column_names`` 做列名清洗/IP分端口/槽位子系统填充
- 子系统过滤：仅导入 CPU / NPU 低速管理板 / 1D SFU 板(含 1825 板)，
  跳过 SMN、RM310、承重隔断、网关、子网掩码、账务注记、串口服务器等（见 design.md §4.2）
- BMC / OS 密码经 ``utils.password_store`` 的 AESCryptor 加密封装后入库；
  整体校验失败整单拒绝并返回 <页签,行,列,原因> 错误清单
"""

import ipaddress
import re
from pathlib import Path

import pandas as pd

from sprinkler.config.superpod_info import preprocess_column_names
from sprinkler.config.cabinet_info import parse_port
from sprinkler.utils.password_store import encrypt_password
from sprinkler.db import dao
from sprinkler.db.database import init_db

_RE_SLOT_CPU_NPU = re.compile(r"^[Ss]\d+")
_RE_SLOT_SFU = re.compile(r"^B\d+")
_RE_CPU = re.compile(r"^CPU\d+$")
_RE_NPU = re.compile(r"^NPU低速管理板\d+$")
_RE_SFU_PLATE = re.compile(r"^1D SFU板$|^1D SFU \d+板$")
_RE_SFU_1825 = re.compile(r"^1825(上板|下板)")


# ----------------------------- 基础工具 -----------------------------

def _structured_item(code, severity, sheet, row, column, reason,
                     value=None, conflict=None, hint=""):
    """构造统一错误/警告条目（字段对齐 API 契约）。"""
    item = {
        "code": code,
        "severity": severity,
        "sheet": sheet,
        "row": int(row or 0),
        "column": column,
    }
    if value is not None:
        item["value"] = value
    if conflict:
        item["conflict"] = conflict
    item["reason"] = reason
    if hint:
        item["hint"] = hint
    return item


def _error(code, sheet, row, column, reason, value=None, conflict=None, hint=""):
    return _structured_item(code, "error", sheet, row, column, reason,
                            value=value, conflict=conflict, hint=hint)


def _warning(code, sheet, row, column, reason, value=None, conflict=None, hint=""):
    return _structured_item(code, "warning", sheet, row, column, reason,
                            value=value, conflict=conflict, hint=hint)


def normalize_category(category):
    """Excel 类别归一化为内部 key（见 design.md §4.3）。无法识别的返回 None。"""
    text = re.sub(r"[\s\u3000]", "", str(category)).upper()
    if not text:
        return None
    if text == "BMCIP":
        return "bmc_ip"
    if "BMCBIOS" in text:
        return None
    if text == "BMCCOM":
        return "bmc_com"
    if "后台IP" in text or text == "后台":
        return "ubm_ip"
    if "前台IP" in text or text == "前台":
        return "frontend_ip"
    if text == "CPUOSIP":
        return "os_ip"
    if text == "1260COM":
        return "1260_com"
    return None


def _is_valid_slot(text):
    text = str(text).strip()
    return bool(_RE_SLOT_CPU_NPU.match(text) or _RE_SLOT_SFU.match(text))


def _pick_first(rows, key, column, default=""):
    """在归一化类别为 key 的行中取指定列的第一个非空值。"""
    for row in rows:
        if row.get("_cat") == key:
            value = row[column]
            if _has_value(value):
                return str(value).strip()
    return default


def _has_value(value):
    if value is None:
        return False
    text = str(value).strip()
    return text not in ("", "nan")


def _first_slot(rows):
    for row in rows:
        raw = str(row["槽位"]).strip() if _has_value(row["槽位"]) else ""
        if _is_valid_slot(raw):
            return raw.lower()
    return ""


def _classify(subsystem_name):
    if _RE_CPU.match(subsystem_name) or _RE_NPU.match(subsystem_name):
        return True
    return bool(_RE_SFU_PLATE.match(subsystem_name) or _RE_SFU_1825.match(subsystem_name))


# ----------------------------- 页签解析 -----------------------------

def _build_single_blade(rows, sub_name, slot, slot_raw=None):
    """由一个槽位的多行记录构建单块刀片台账。"""
    if slot.startswith("b"):
        device_type = "sfu"
    elif _RE_SFU_1825.match(sub_name):
        device_type = "RoCE"
    elif _RE_NPU.match(sub_name):
        device_type = "npu"
    else:
        device_type = "cpu"

    bmc_ip = _pick_first(rows, "bmc_ip", "IP")
    bmc_user = _pick_first(rows, "bmc_ip", "账号", "Administrator")
    bmc_pwd = _pick_first(rows, "bmc_ip", "密码")
    ubm_ip = _pick_first(rows, "ubm_ip", "IP")
    com_port = _pick_first(rows, "1260_com", "端口号")

    blade = {
        "slot": slot,
        "_slot_raw": slot_raw or slot,
        "board_type": sub_name,
        "device_type": device_type,
        "is_server": False,
        "bmc_ip": bmc_ip,
        "bmc_user": bmc_user,
        "bmc_password": encrypt_password(bmc_pwd),
        "ubm_ip": ubm_ip,
        "ubm_port": parse_port(com_port) if com_port else None,
        "os_ip": "",
        "os_user": "root",
        "os_password": "",
        "os_port": 22,
        "os_type": "",
        "_row": rows[0].get("_excel_row", 0) if rows else 0,
    }

    if device_type == "cpu":
        os_ip = _pick_first(rows, "os_ip", "IP")
        os_user = _pick_first(rows, "os_ip", "账号", "root")
        os_pwd = _pick_first(rows, "os_ip", "密码")
        os_port = _pick_first(rows, "os_ip", "端口号", "22")
        blade["os_ip"] = os_ip
        blade["os_user"] = os_user
        blade["os_password"] = encrypt_password(os_pwd)
        blade["os_port"] = parse_port(os_port, 22)
    return blade


def _parse_blade_group(rows, sub_name, base_row):
    """解析一个刀片子系统的多行记录，按「槽位段」拆分为多块刀片。

    与逐行拆分不同，同一槽位必须连续成段才归并；段内没有任何类别行的
    （如承重隔断行 ffill 污染进来的 s27/s25 裸行）视为杂物直接丢弃。

    :return: (blades list, slot_set, module_warnings)
    :raises LookupError: 整组无可导入槽位
    """
    segments = []
    cur_slot = None
    cur_raw = None
    cur_rows = []
    for row in rows:
        raw = str(row["槽位"]).strip() if _has_value(row["槽位"]) else ""
        slot = raw.lower() if _is_valid_slot(raw) else None
        if slot is None:
            if cur_rows:
                cur_rows.append(row)
            continue
        if slot == cur_slot:
            cur_rows.append(row)
        else:
            if cur_rows:
                segments.append((cur_slot, cur_rows, cur_raw))
            cur_slot, cur_rows, cur_raw = slot, [row], raw
    if cur_rows:
        segments.append((cur_slot, cur_rows, cur_raw))

    blades = []
    slots = set()
    for slot, seg, raw_slot in segments:
        if not any(r.get("_cat") for r in seg):
            continue
        blades.append(_build_single_blade(seg, sub_name, slot, slot_raw=raw_slot))
        slots.add(slot)

    if not slots:
        raise LookupError(f"子系统 {sub_name} 未找到合法槽位")

    module_warnings = []
    for blade in blades:
        if not blade["bmc_ip"]:
            warning = _warning(
                "BMC_IP_MISSING", "", blade.get("_row", base_row), "BMC IP",
                f"{sub_name} 缺少 BMC IP", value="",
                hint="补充 BMC IP 后重新导入")
            warning["_slot"] = blade["slot"]
            module_warnings.append(warning)
        if _RE_CPU.match(sub_name) and not blade["os_ip"]:
            warning = _warning(
                "OS_IP_MISSING", "", blade.get("_row", base_row), "CPU OS IP",
                f"CPU 刀片 {sub_name} 缺少 CPU OS IP，OS 字段留空", value="",
                hint="补充 CPU OS IP 后重新导入")
            warning["_slot"] = blade["slot"]
            module_warnings.append(warning)
    return blades, slots, module_warnings


def _iter_group_rows(group):
    for idx, row in group.iterrows():
        yield int(idx), dict(row)


def _slot_conflict_hint(sub_name):
    return (f"改为 {sub_name} 实际槽位，或确认该槽位应归属哪块刀片")


def _parse_sheet_inner(ctx, sheet_name, ca, sp, errors, warnings, allow_partial=False):
    blades = []
    seen_slots = {}
    core_groups = []
    aux_groups = []
    for sub_name, group in ctx.groupby("子系统", sort=False):
        label = str(sub_name).strip() if _has_value(sub_name) else ""
        if not label or not _classify(label):
            continue
        rows = []
        first_row = 0
        for idx, row in _iter_group_rows(group):
            item = dict(row)
            item["_excel_row"] = int(idx) + 2
            rows.append(item)
            if first_row == 0:
                first_row = item["_excel_row"]
        item = (rows, label, first_row)
        (aux_groups if _RE_SFU_1825.match(label) else core_groups).append(item)

    for rows, label, base_row in core_groups + aux_groups:
        try:
            group_blades, _group_slots, module_warnings = _parse_blade_group(rows, label, base_row)
        except LookupError as e:
            errors.append(_error(
                "SLOT_INVALID", sheet_name, base_row, "槽位", str(e), value=label,
                hint="检查该子系统行是否填写了合法槽位（SN 或 Bn）"))
            continue

        accepted = []
        for blade in group_blades:
            slot = blade["slot"]
            owner = seen_slots.get(slot)
            if owner is None:
                seen_slots[slot] = {
                    "sheet": sheet_name,
                    "row": blade.get("_row", base_row),
                    "subsystem": label,
                }
                accepted.append(blade)
                continue

            conflict = {"sheet": owner["sheet"], "row": owner["row"],
                        "subsystem": owner["subsystem"]}
            display_slot = blade.get("_slot_raw", slot)
            reason = f"槽位 {display_slot} 已被同机柜的 {owner['subsystem']} 占用"
            hint = _slot_conflict_hint(label)
            downgrade = (
                allow_partial
                and _RE_SFU_1825.match(label)
                and owner["subsystem"] != label
            )
            if downgrade:
                warnings.append(_warning(
                    "PARTIAL_SKIP", sheet_name, blade.get("_row", base_row), "槽位",
                    f"{reason}，已跳过该 1825 刀片组", value=display_slot,
                    conflict=conflict, hint=hint))
            else:
                errors.append(_error(
                    "SLOT_DUPLICATE", sheet_name, blade.get("_row", base_row), "槽位",
                    reason, value=display_slot, conflict=conflict, hint=hint))

        accepted_slots = {b["slot"] for b in accepted}
        for warning in module_warnings:
            if warning.pop("_slot", None) in accepted_slots:
                warning["sheet"] = sheet_name
                warnings.append(warning)
        blades.extend(accepted)
    return blades


# ----------------------------- 工作簿解析 -----------------------------

def parse_workbook(file_path, sp, cluster_name=None, allow_partial=False):
    """解析 superpod_<sp>.xlsx，产出集群三级结构。

    :param allow_partial: True 时仅把 1825 槽位冲突降级为 PARTIAL_SKIP warning
    :return: (cluster dict, cabinets list, errors list, warnings list)
    """
    with pd.ExcelFile(file_path) as xls:
        sheet_names = list(xls.sheet_names)
    cabinets = []
    errors = []
    warnings = []

    cabinet_index = 0
    for sheet_raw in sheet_names:
        sheet_name = str(sheet_raw).strip()
        ca = f"{cabinet_index:x}"
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
        except Exception as e:  # noqa: BLE001
            errors.append(_error("SHEET_READ_FAILED", sheet_name, 1, "-",
                                 f"页签读取失败: {e}",
                                 hint="检查页签格式或重新导出 Excel"))
            cabinet_index += 1
            continue
        if "子系统" not in df.columns:
            cabinets.append({"name": sheet_name, "superpod_id": sp, "ca": ca,
                             "order_index": cabinet_index, "blades": []})
            cabinet_index += 1
            continue
        df = preprocess_column_names(df)
        ctx = df.copy()
        ctx["_cat"] = ctx["类别"].map(normalize_category)
        blades = _parse_sheet_inner(ctx, sheet_name, ca, sp, errors, warnings,
                                    allow_partial=allow_partial)
        cabinets.append({"name": sheet_name, "superpod_id": sp, "ca": ca,
                         "order_index": cabinet_index, "blades": blades})
        cabinet_index += 1

    cluster = {"name": cluster_name or f"superpod_{sp}", "description": ""}
    return cluster, cabinets, errors, warnings


# ----------------------------- 校验 -----------------------------

def validate_ip(value, sheet, row, column, errors):
    if value and not _is_ip(value):
        errors.append({"sheet": sheet, "row": row, "column": column,
                       "reason": f"非法 IP: {value}"})


def _is_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _validate_cabinets(cabinets, sp, errors):
    """跨机柜槽位不重复、BMC IP 合法等结构性校验。"""
    cross_slots = {}
    for cab in cabinets:
        if cab["superpod_id"] != sp:
            errors.append(_error(
                "SP_MISMATCH", cab["name"], 2, "超节点Id",
                f"真实 sp={sp} 与页签超节点Id({cab['superpod_id']}) 不一致",
                value=cab["superpod_id"],
                hint="确认导入参数 sp 与页签超节点Id一致"))
        seen = {}
        for blade in cab["blades"]:
            slot = blade["slot"]
            row = blade.get("_row", 2)
            owner = seen.get(slot)
            if owner is not None:
                errors.append(_error(
                    "SLOT_DUPLICATE", cab["name"], row, "槽位",
                    f"机柜内槽位重复: {blade.get('_slot_raw', slot)}",
                    value=blade.get("_slot_raw", slot),
                    conflict={"sheet": cab["name"], "row": owner.get("_row", 2),
                              "subsystem": owner.get("board_type", "")},
                    hint=_slot_conflict_hint(blade.get("board_type", ""))))
                continue
            seen[slot] = blade
            if blade["bmc_ip"] and not _is_ip(blade["bmc_ip"]):
                errors.append(_error(
                    "IP_INVALID", cab["name"], row, "BMC IP",
                    f"非法 IP: {blade['bmc_ip']}", value=blade["bmc_ip"],
                    hint="改为合法的 IPv4/IPv6 地址"))

            key = (cab["name"], slot)
            previous = cross_slots.get(key)
            if previous is not None:
                errors.append(_error(
                    "CROSS_CABINET_SLOT_DUPLICATE", cab["name"], row, "槽位",
                    "跨机柜存在重复槽位", value=blade.get("_slot_raw", slot),
                    conflict={"sheet": previous["sheet"], "row": previous["row"],
                              "subsystem": previous["subsystem"]},
                    hint="同一槽位号不能出现在不同机柜"))
            else:
                cross_slots[key] = {
                    "sheet": cab["name"], "row": row,
                    "subsystem": blade.get("board_type", ""),
                }
    return errors


# ----------------------------- 导入编排 -----------------------------

def import_superpod(file_path, sp, overwrite=False, cluster_name=None, db_path=None,
                    allow_partial=False):
    """解析并导入一个超节点台账（superpod_<sp>.xlsx）。

    :param file_path: Excel 文件路径
    :param sp: 超节点编号（0-f）
    :param overwrite: 集群已存在时是否覆盖重建
    :param cluster_name: 集群名称，默认 superpod_{sp}
    :param db_path: 数据库文件路径（默认 sprinkler/db/sprinkler.db）
    :param allow_partial: True 时仅把 1825 槽位冲突降级为跳过，其他错误仍整单拒绝
    :return: dict(success, partial, stats?, errors?, warnings?, message?)
    """
    cluster, cabinets, errors, warnings = parse_workbook(
        file_path, sp, cluster_name, allow_partial=allow_partial)
    _validate_cabinets(cabinets, sp, errors)
    blades_skipped = sum(1 for w in warnings if w.get("code") == "PARTIAL_SKIP")

    if errors:
        return {"success": False, "partial": False, "errors": errors,
                "warnings": warnings, "message": "校验失败，整单拒绝导入"}

    conn = init_db(db_path)
    try:
        result = dao.import_cluster_tree(conn, cluster, cabinets, overwrite=overwrite)
        conn.commit()
    except dao.ClusterExistsError as e:
        conn.rollback()
        return {"success": False, "partial": False, "message": str(e),
                "warnings": warnings}
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        raise
    finally:
        conn.close()

    stats = dict(result)
    stats["blades_skipped"] = blades_skipped
    partial = blades_skipped > 0
    message = f"部分导入成功：跳过 {blades_skipped} 块刀片" if partial else "导入成功"
    return {"success": True, "partial": partial, "stats": stats,
            "warnings": warnings, "message": message}


def format_import_result(result):
    """导入结果人性化输出。"""
    if not result["success"]:
        lines = [result["message"]]
        if result.get("errors"):
            lines.append("错误清单（<页签, 行, 列, 原因>）：")
            for e in result["errors"]:
                lines.append(f"  {e['sheet']}, {e['row']}, {e['column']}, {e['reason']}")
        return "\n".join(lines)
    stats = result.get("stats", {})
    prefix = "部分导入成功" if result.get("partial") else "导入成功"
    lines = [
        f"{prefix}：集群 {stats.get('cluster', 0)} 个，机柜 {stats.get('cabinets', 0)} 个，"
        f"刀片 {stats.get('blades', 0)} 块",
    ]
    if stats.get("blades_skipped"):
        lines.append(f"  跳过的刀片：{stats['blades_skipped']} 块")
    for w in result.get("warnings", []):
        lines.append(f"  [警告] {w['sheet']}, 行 {w['row']}, 列 {w['column']}: {w['reason']}")
    return "\n".join(lines)
