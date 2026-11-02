#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读取 /proc/uda/namespace_node (logic_devid <-> udevid) 映射，
修复 ranktable.json 中每个 rank_list 条目的 rank_id，使其等于对应的 logic_devid。
前提：udevid == ranktable.json 里的 device_id

用法:
  python3 fix_ranktable_from_uda.py <proc_file> <in_ranktable.json> <out_ranktable.json>
"""

import json
import sys
from pathlib import Path


def die(msg: str, code: int = 1) -> None:
    sys.stderr.write(f"ERROR: {msg}\n")
    raise SystemExit(code)


def warn(msg: str) -> None:
    sys.stderr.write(f"# WARNING: {msg}\n")


def info(msg: str) -> None:
    sys.stderr.write(f"# INFO: {msg}\n")


def parse_uda_map(proc_file: Path) -> dict[int, int]:
    """
    返回: udevid -> logic_devid
    文件格式期望类似:
      logic_devid udevid
          0       24
          1       25
          ...
    """
    text = proc_file.read_text(encoding="utf-8", errors="ignore").splitlines()

    mapping: dict[int, int] = {}
    seen_logic: set[int] = set()

    for line in text:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[0] == "logic_devid" and parts[1] == "udevid":
            continue

        # 只接受两列数字
        try:
            logic = int(parts[0])
            udevid = int(parts[1])
        except ValueError:
            continue

        if udevid in mapping and mapping[udevid] != logic:
            die(f"{proc_file} 中 udevid={udevid} 映射重复且不一致：{mapping[udevid]} vs {logic}")

        if logic in seen_logic:
            # logic_devid 出现重复通常表示映射不可信
            die(f"{proc_file} 中 logic_devid={logic} 出现重复，映射不可信，请等 NPU 全部上线后再试")

        mapping[udevid] = logic
        seen_logic.add(logic)

    if not mapping:
        die(f"未从 {proc_file} 解析到任何 (logic_devid, udevid) 映射，请检查文件内容")

    return mapping


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        die(f"解析 JSON 失败: {path} ({e})")


def main() -> None:
    if len(sys.argv) != 4:
        die("参数错误，用法: python3 fix_ranktable_from_uda.py <proc_file> <in.json> <out.json>")

    proc_file = Path(sys.argv[1])
    in_path = Path(sys.argv[2])
    out_path = Path(sys.argv[3])

    if not proc_file.is_file():
        die(f"proc 文件不存在: {proc_file}")
    if not in_path.is_file():
        die(f"输入 ranktable 不存在: {in_path}")

    uda_map = parse_uda_map(proc_file)
    info(f"已解析 UDA 映射数量: {len(uda_map)} (udevid -> logic_devid)")

    data = load_json(in_path)
    rank_list = data.get("rank_list", None)
    if not isinstance(rank_list, list):
        die("ranktable.json 中缺少 rank_list 或其不是 list")

    # 统计 ranktable 里用到的 device_id
    device_ids_in_ranktable: list[int] = []
    for i, entry in enumerate(rank_list):
        if not isinstance(entry, dict):
            die(f"rank_list[{i}] 不是对象(dict)，请检查 ranktable 格式")
        if "device_id" not in entry:
            die(f"rank_list[{i}] 缺少 device_id")
        dev_raw = entry["device_id"]
        try:
            dev = int(dev_raw)
        except Exception:
            die(f"rank_list[{i}].device_id 不是整数: {dev_raw}")
        device_ids_in_ranktable.append(dev)

    # 强校验：ranktable 中每个 device_id 必须在 UDA 映射里出现
    missing = [d for d in sorted(set(device_ids_in_ranktable)) if d not in uda_map]
    if missing:
        die(
            "ranktable 中存在 device_id 未在 /proc UDA 映射中出现："
            f"{missing}。这通常表示 NPU 尚未全部上线或映射不完整。"
        )

    changed = 0
    for entry in rank_list:
        dev = int(entry["device_id"])
        new_rank = uda_map[dev]
        old_rank = entry.get("rank_id", None)

        if old_rank != new_rank:
            entry["rank_id"] = new_rank
            changed += 1

    info(f"需要修复 rank_id 的条目数: {changed} / {len(rank_list)}")

    # 输出到新文件（不覆盖原文件）
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

    info(f"已写出修复后的 ranktable 到: {out_path}")


if __name__ == "__main__":
    main()