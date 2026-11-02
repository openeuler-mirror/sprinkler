"""Write machine-to-machine bandwidth matrices."""

import csv
import re

from sprinkler.auto.bandwidth.common import int_value, is_xboard_pair, link_suffix

SHEET_FIELDS = (("client_mean", "client_bw_average", "client_bw_samples"),
                ("client_min", "client_bw_min", "client_bw_samples"),
                ("client_max", "client_bw_max", "client_bw_samples"),
                ("server_mean", "server_bw_average", "server_bw_samples"),
                ("server_min", "server_bw_min", "server_bw_samples"),
                ("server_max", "server_bw_max", "client_bw_samples"))
SHEET_NAME_RE = re.compile(r"[:\\/?*\[\]]")
NPU_LABEL_RE = re.compile(r"^NPU(\d+)$")
PAIR_LABEL_RE = re.compile(r"^NPU(\d+)->NPU(\d+)")
TAB_COLORS = {"l0": "4F81BD", "l1": "70AD47", "l2": "FFC000", "all": "A5A5A5"}
UNIT = "MB/s"
SELF_CELL = "\u2014\u2014"

class BandwidthReport:
    def __init__(self, results, unit=None):
        self.results = list(results or [])
        self.unit = str(unit or UNIT)

    def write(self, path):
        if path is None:
            return
        from openpyxl import Workbook

        workbook, styles, column_letter = make_report_workbook(Workbook)
        groups = build_bandwidth_groups(self.results)
        csv_rows = []
        for index, group in enumerate(groups.values(), 1):
            write_bandwidth_sheet(workbook, styles, column_letter, csv_rows, index, group, unit=self.unit)
        if not groups:
            write_empty_bandwidth_sheet(workbook, styles)
        write_report_csv(path, csv_rows)
        workbook.save(path / "results.xlsx")

def make_report_workbook(workbook_class):
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    workbook = workbook_class()
    workbook.remove(workbook.active)
    thin = Side(style="thin", color="B8C2CC")
    styles = {"border": Border(left=thin, right=thin, top=thin, bottom=thin),
              "center": Alignment(horizontal="center", vertical="center", wrap_text=True),
              "title_fill": PatternFill("solid", fgColor="1F4E78"),
              "header_fill": PatternFill("solid", fgColor="5B9BD5"),
              "row_fill": PatternFill("solid", fgColor="D9EAF7"),
              "value_fill": PatternFill("solid", fgColor="F7FBFE"),
              "empty_fill": PatternFill("solid", fgColor="F2F2F2"),
              "na_fill": PatternFill("solid", fgColor="FDE9D9"),
              "title_font": Font(name="Calibri", size=11, bold=True, color="FFFFFF"),
              "header_font": Font(name="Calibri", size=10, bold=True, color="FFFFFF"),
              "row_font": Font(name="Calibri", size=10, bold=True),
              "value_font": Font(name="Calibri", size=10),
              "na_font": Font(name="Calibri", size=10, bold=True, color="C00000")}
    return workbook, styles, get_column_letter

def build_bandwidth_groups(results):
    groups = {}
    for result in results:
        item = bandwidth_result_item(result)
        if item is None:
            continue
        group = groups.setdefault(item["key"], {"layout": item["layout"], "kind": item["kind"],
                                 "link": item["link"], "title": item["title"], "columns": [],
                                 "rows": [], "values": {}, "self_dash": item["self_dash"]})
        if item["column"] not in group["columns"]:
            group["columns"].append(item["column"])
        if item["row"] not in group["rows"]:
            group["rows"].append(item["row"])
        for sheet, field, samples in SHEET_FIELDS:
            value = round(float(result.get(field) or 0), 3) if result.get(samples) else "NA"
            group["values"][(sheet, item["row"], item["column"])] = value
    return {key: ordered_group(group) for key, group in groups.items()}

def bandwidth_result_item(result):
    if "bandwidth_phase" not in result and "bw_average" not in result:
        return None
    kind = str(result.get("pair_kind") or result.get("kind") or "").strip()
    if not kind and str(result.get("target") or "").startswith("xboard:"):
        kind = "xboard_d2d"
    link = link_suffix(result.get("link") or result.get("link_mode"), "all")
    if is_xboard_pair(result) or kind == "xboard_d2d":
        layout, row, column, self_dash = "pair", report_machine_label(result), report_pair_label(result), False
    else:
        client_card = endpoint_npu_label(result, "client")
        server_card = endpoint_npu_label(result, "server")
        if not client_card or not server_card:
            return None
        layout, row, column, self_dash = "square", server_card, client_card, kind == "intra_d2d"
    server_machine = machine_label(result.get("server_machine") or "local")
    return {"key": (layout, kind, server_machine, link), "layout": layout, "kind": kind, "link": link,
            "row": row, "column": column, "title": f"{kind or 'bandwidth'} {server_machine}",
            "self_dash": self_dash}

def report_machine_label(result):
    host = result.get("client_cpu_host") or result.get("host") or result.get("client_machine") or "local"
    return machine_label(host)

def machine_label(value):
    text = str(value or "local").strip()
    return "LOCAL" if text.lower() in {"", "local"} else text

def report_pair_label(result):
    client = endpoint_npu_label(result, "client")
    server = endpoint_npu_label(result, "server")
    label = f"{client}->{server}"
    client_host = report_machine_label(result)
    server_host = machine_label(result.get("server_cpu_host") or result.get("server_machine") or "local")
    if client_host != server_host:
        label = f"{label}({server_host}/{client_host})"
    return label

def endpoint_npu_label(result, role):
    target = str(result.get(f"{role}_target") or "").strip()
    target = target.split(":", 1)[1] if ":" in target else target
    match = re.search(r"NPU(\d+)", target)
    if match:
        return f"NPU{int(match.group(1))}"
    rank = int_value(result.get(f"{role}_rank"))
    return f"NPU{rank}" if rank is not None else target

def ordered_group(group):
    if group["layout"] == "square":
        labels = sorted(dict.fromkeys([*group["rows"], *group["columns"]]), key=matrix_label_key)
        group["rows"] = labels
        group["columns"] = labels
    else:
        group["rows"] = sorted(group["rows"], key=matrix_label_key)
        group["columns"] = sorted(group["columns"], key=matrix_label_key)
    return group

def matrix_label_key(value):
    text = str(value or "")
    match = NPU_LABEL_RE.match(text)
    if match:
        return 0, int(match.group(1)), "", 0
    match = PAIR_LABEL_RE.match(text)
    if match:
        return 1, int(match.group(1)), "", int(match.group(2))
    return 2, 0, text, 0

def write_bandwidth_sheet(workbook, styles, column_letter, csv_rows, index, group, unit=None):
    unit = str(unit or UNIT)
    page = workbook.create_sheet(bandwidth_sheet_name(index, group))
    setup_bandwidth_sheet(page, styles, column_letter, group)
    csv_rows.append([f"[{group['title']}]", f"link={group['link']}", f"unit={unit}"])
    row = 1
    for sheet, _field, _samples in SHEET_FIELDS:
        row = write_metric_table(page, styles, csv_rows, row, sheet, group, unit=unit)

def bandwidth_sheet_name(index, group):
    suffix = f"_{group['link']}"
    raw_name = f"{index:03d}_{group['kind']}_{group['title']}"
    clean = SHEET_NAME_RE.sub("_", raw_name)
    return f"{clean[:31 - len(suffix)]}{suffix}"

def setup_bandwidth_sheet(page, styles, column_letter, group):
    page.freeze_panes = "B3"
    page.sheet_view.showGridLines = False
    page.sheet_properties.tabColor = TAB_COLORS[group["link"]]
    page.column_dimensions["A"].width = max(18, min(36, max(map(len, group["rows"]), default=13) + 3))
    for column, client in enumerate(group["columns"], 2):
        page.column_dimensions[column_letter(column)].width = max(14, min(36, len(client) + 3))

def write_metric_table(page, styles, csv_rows, row, sheet, group, unit=None):
    unit = str(unit or UNIT)
    title = f"{sheet} ({unit})"
    write_title_row(page, styles, row, title, len(group["columns"]) + 1)
    csv_rows.append([title])
    row += 1
    write_header_row(page, styles, row, ["server/client", *group["columns"]])
    csv_rows.append(["server/client", *group["columns"]])
    row += 1
    for server in group["rows"]:
        values = [SELF_CELL if group.get("self_dash") and server == client
                  else group["values"].get((sheet, server, client), "") for client in group["columns"]]
        write_value_row(page, styles, row, server, values)
        csv_rows.append([server, *values])
        row += 1
    csv_rows.append([])
    return row + 1

def write_title_row(page, styles, row, title, width):
    page.cell(row, 1, title)
    page.row_dimensions[row].height = 24
    for column in range(1, width + 1):
        cell = page.cell(row, column)
        apply_cell_frame(cell, styles)
        cell.fill = styles["title_fill"]
        cell.font = styles["title_font"]

def write_header_row(page, styles, row, values):
    page.row_dimensions[row].height = 28
    for column, value in enumerate(values, 1):
        cell = page.cell(row, column, value)
        apply_cell_frame(cell, styles)
        cell.fill = styles["header_fill"]
        cell.font = styles["header_font"]

def write_value_row(page, styles, row, server, values):
    page.row_dimensions[row].height = 22
    for column, value in enumerate([server, *values], 1):
        cell = page.cell(row, column, value)
        apply_cell_frame(cell, styles)
        apply_value_cell_style(cell, styles, column, value)

def apply_cell_frame(cell, styles):
    cell.border = styles["border"]
    cell.alignment = styles["center"]

def apply_value_cell_style(cell, styles, column, value):
    if column == 1:
        cell.fill = styles["row_fill"]
        cell.font = styles["row_font"]
    elif value == "NA":
        cell.fill = styles["na_fill"]
        cell.font = styles["na_font"]
    elif value in {"", SELF_CELL}:
        cell.fill = styles["empty_fill"]
        cell.font = styles["value_font"]
    else:
        cell.fill = styles["value_fill"]
        cell.font = styles["value_font"]
        cell.number_format = "0.000"

def write_empty_bandwidth_sheet(workbook, styles):
    page = workbook.create_sheet("bandwidth")
    page["A1"] = "NA"
    apply_cell_frame(page["A1"], styles)
    page["A1"].fill = styles["na_fill"]
    page["A1"].font = styles["na_font"]

def write_report_csv(path, csv_rows):
    with (path / "results.csv").open("w", encoding="utf-8-sig", errors="replace", newline="") as fp:
        csv.writer(fp).writerows(csv_rows)
