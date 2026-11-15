"""Offline first-cooldown preparation. No instrument control or executable profiles.

Read a design XLSX/CSV, retain its source hash and row identities, and propose
overlapping search segments under explicitly stated frequency/Q assumptions.
Python 3.10+, standard library only; never imports the instrument client.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import posixpath
import sys
from uuid import uuid4
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
FIELDS = ("pixel_id", "f_target_hz", "f_model_hz", "f_error_hz", "q_target",
          "q_model", "q_basic", "model_status", "geometry_complete", "source_row")
XLSX_COLUMNS = ("pixel_id", "f_target / Hz", "f / Hz", "f_error_hz / Hz",
                "q_target", "q", "q_basic", "status", "geometry_complete")
SITE_FIELDS = ("sample", "input_loss_db", "output_gain_db", "source_power_dbm",
               "max_source_power_dbm", "chain_reviewed", "temperature_source",
               "design_q_kind", "operator", "notes")
REQUIRED_SITE = SITE_FIELDS[:-1]


def finite(value, label, positive=False):
    if isinstance(value, bool):
        raise ValueError(f"{label}: expected a number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{label}: expected a finite {'positive ' if positive else ''}number")
    return result


def _xlsx_rows(path, sheet):
    """Read only cell data, without Excel, formula evaluation or file extraction."""
    with ZipFile(path) as archive:
        def xml(name):
            if archive.getinfo(name).file_size > 64 * 1024 * 1024:
                raise ValueError(f"XLSX XML member too large: {name}")
            return ET.fromstring(archive.read(name))

        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(si.itertext()) for si in xml("xl/sharedStrings.xml").findall("s:si", NS)]
        sheets = xml("xl/workbook.xml").findall("s:sheets/s:sheet", NS)
        selected = next((s for s in sheets if s.get("name") == sheet), None)
        if selected is None:
            raise ValueError(f"Sheet {sheet!r} not found; available: {[s.get('name') for s in sheets]}")
        relationships = {r.get("Id"): r for r in xml("xl/_rels/workbook.xml.rels")}
        relationship = relationships[selected.get(f"{{{REL}}}id")]
        if relationship.get("TargetMode") == "External":
            raise ValueError("External worksheet relationships are not supported")
        target = relationship.get("Target", "")
        member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
        if not member.startswith("xl/") or ".." in member.split("/"):
            raise ValueError("Invalid worksheet path")
        for row in xml(member).findall("s:sheetData/s:row", NS):
            cells = {}
            for cell in row.findall("s:c", NS):
                column = "".join(c for c in cell.get("r", "") if c.isalpha())
                # Only the first nine columns are imported; geometry is ignored.
                if column not in "ABCDEFGHI" or len(column) != 1:
                    continue
                if cell.find("s:f", NS) is not None:
                    raise ValueError(f"{sheet}!{cell.get('r')}: formula found; export static values first")
                if cell.get("t") == "e":
                    raise ValueError(f"{sheet}!{cell.get('r')}: spreadsheet error")
                v = cell.find("s:v", NS)
                value = v.text if v is not None else "".join(cell.find("s:is", NS).itertext()) if cell.find("s:is", NS) is not None else ""
                if cell.get("t") == "s":
                    value = strings[int(value)]
                cells[column] = value or ""
            yield int(row.get("r")), [cells.get(c, "") for c in "ABCDEFGHI"]


def read_design(path, sheet="结果表"):
    path = Path(path).expanduser()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.suffix.lower() == ".xlsx":
        raw = list(_xlsx_rows(path, sheet))
        if not raw or tuple(raw[0][1]) != XLSX_COLUMNS:
            raise ValueError("Expected XLSX A:I headers: " + ", ".join(XLSX_COLUMNS))
        data = [(i, dict(zip(FIELDS[:-1], values))) for i, values in raw[1:]]
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not {"pixel_id", "f_target_hz"} <= set(reader.fieldnames):
                raise ValueError("CSV requires pixel_id,f_target_hz columns, frequency unit Hz")
            if len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise ValueError("Duplicate CSV column names")
            data = list(enumerate(reader, 2))
        sheet = None
    else:
        raise ValueError("Use a .xlsx or normalized .csv design file")
    rows, seen = [], set()
    for index, record in data:
        if not any(v not in (None, "") for k, v in record.items() if k != "source_row"):
            continue
        pixel = str(record.get("pixel_id") or "").strip()
        if not pixel or pixel in seen:
            raise ValueError(f"Row {index}: missing or duplicate pixel_id {pixel!r}")
        seen.add(pixel)
        normalized = {k: record.get(k, "") or "" for k in FIELDS}
        normalized.update(pixel_id=pixel, source_row=index)
        for key in FIELDS[1:7]:
            value = normalized[key]
            if value == "" and key != "f_target_hz":
                normalized[key] = None
                continue
            normalized[key] = finite(value, f"Row {index} {key}", positive=key != "f_error_hz")
        rows.append(normalized)
    if not rows:
        raise ValueError("Design contains no resonators")
    if len(rows) > 10000:
        raise ValueError("At most 10000 design rows are supported")
    if hashlib.sha256(path.read_bytes()).hexdigest() != before:
        raise ValueError("Source changed while importing; no plan created")
    source = {"name": path.name, "sha256": before, "sheet": sheet,
              "columns": list(XLSX_COLUMNS) if sheet else None, "rows": len(rows),
              "q_semantics": "UNCONFIRMED_DESIGN_Q_NOT_MEASURED_QL"}
    return rows, source


def site_review(path=None):
    """Report missing inputs; it never grants permission or enables RF."""
    site = dict.fromkeys(SITE_FIELDS)
    if path:
        supplied = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        if not isinstance(supplied, dict) or set(supplied) - set(SITE_FIELDS):
            raise ValueError("Unknown site fields; copy configs/site.example.json")
        site.update(supplied)
    missing = [k for k in REQUIRED_SITE if site[k] is None or site[k] == "" or
               (k == "chain_reviewed" and site[k] is not True) or
               (k == "design_q_kind" and site[k] not in ("Qc", "QL", "Qi"))]
    for k in ("input_loss_db", "output_gain_db", "source_power_dbm", "max_source_power_dbm"):
        if site[k] is not None:
            site[k] = finite(site[k], k)
    if site["input_loss_db"] is not None and site["input_loss_db"] < 0:
        raise ValueError("input_loss_db must be nonnegative")
    if site["source_power_dbm"] is not None and site["max_source_power_dbm"] is not None:
        if site["source_power_dbm"] > site["max_source_power_dbm"]:
            raise ValueError("source_power_dbm exceeds reviewed max_source_power_dbm")
    return {"values": site, "missing": missing,
            "status": "MISSING_SITE_INPUTS" if missing else "RECORDED_REQUIRES_FIELD_VALIDATION"}


def make_plan(rows, margin_pct=2, ql_upper=100000, points=1601,
              samples_per_linewidth=8, overlap=0.1, seconds_per_segment=None):
    margin_pct = finite(margin_pct, "margin_pct")
    ql_upper = finite(ql_upper, "ql_upper", positive=True)
    overlap = finite(overlap, "overlap")
    if not 0 <= margin_pct <= 20 or not 0 <= overlap <= 0.5:
        raise ValueError("margin_pct must be 0–20; overlap must be 0–0.5")
    if type(points) is not int or not 2 <= points <= 1601:
        raise ValueError("points must be an integer 2–1601")
    if type(samples_per_linewidth) is not int or not 5 <= samples_per_linewidth <= 100:
        raise ValueError("samples_per_linewidth must be an integer 5–100")
    if seconds_per_segment is not None:
        seconds_per_segment = finite(seconds_per_segment, "seconds_per_segment", positive=True)
    targets, bands = [], []
    for row in rows:
        basis = "f_model_hz" if row["f_model_hz"] is not None else "f_target_hz"
        f = row[basis]
        lower, upper = f * (1 - margin_pct / 100), f * (1 + margin_pct / 100)
        # At zero frequency uncertainty still search a nonzero local window.
        if lower == upper:
            lower, upper = f - 10 * f / ql_upper, f + 10 * f / ql_upper
        if not 1e9 <= lower < upper <= 4e9:
            raise ValueError(f"{row['pixel_id']}: search band outside existing 1–4 GHz recorder range")
        bands.append((lower, upper))
        targets.append({"pixel_id": row["pixel_id"], "design_center_hz": f,
                        "frequency_basis": basis, "search_start_hz": lower,
                        "search_stop_hz": upper,
                        "zoom_span_hint_hz": 20 * f / ql_upper,
                        "source_row": row["source_row"]})
    merged = []
    for lower, upper in sorted(bands):
        if merged and lower <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], upper)
        else:
            merged.append([lower, upper])
    segments = []
    for band_id, (lower, upper) in enumerate(merged, 1):
        start = lower
        while start < upper:
            # Use the lowest frequency in this segment for conservative spacing.
            span = (points - 1) * start / (ql_upper * samples_per_linewidth)
            stop = min(upper, start + span)
            if stop <= start:
                raise ValueError("Resolution assumption is too fine for numeric precision")
            segments.append({"segment_id": len(segments) + 1, "band_id": band_id,
                             "start_hz": start, "stop_hz": stop, "points": points,
                             "step_hz": (stop - start) / (points - 1),
                             "ifbw_hint_hz": 1000, "power_dbm": None})
            if len(segments) > 20000:
                raise ValueError("Over 20000 segments; revise search assumptions")
            if stop == upper:
                break
            start = stop - overlap * (stop - start)
    frequencies = sorted(t["design_center_hz"] for t in targets)
    spacings = [b - a for a, b in zip(frequencies, frequencies[1:])]
    return {"schema": 1, "planner_version": "0.3.0", "status": "PLAN_ONLY_NOT_EXECUTABLE", "power_dbm": None,
            "assumptions": {"frequency_margin_pct": margin_pct, "ql_upper_bound": ql_upper,
                "ql_is_planning_assumption": True, "samples_per_linewidth": samples_per_linewidth,
                "points": points, "overlap_fraction": overlap,
                "seconds_per_segment_measured_end_to_end": seconds_per_segment},
            "summary": {"target_count": len(targets), "merged_band_count": len(merged),
                "segment_count": len(segments), "min_design_hz": frequencies[0],
                "max_design_hz": frequencies[-1],
                "min_neighbor_spacing_hz": min(spacings) if spacings else None,
                "estimated_search_seconds": len(segments) * seconds_per_segment if seconds_per_segment else None},
            "bands_hz": merged, "targets": targets, "segments": segments,
            "warnings": ["设计 Q 不等于实测加载 QL；规划上限是独立假设。",
                "频率裕量是搜索假设；不从文件名的 Qc 容差推导。",
                "覆盖仅对假设频窗与 QL 上限成立；未发现谐振时不能据此判定器件失效。",
                "pixel_id 是设计标签，实测频率顺序可能改变，匹配结果需复核。",
                "分段搜索尚未接入实机执行，文件不能作为采集 profile。"]}


def _csv(path, rows, fields):
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def prepare(source_path, out="data", site_path=None, sheet="结果表", **options):
    rows, source = read_design(source_path, sheet)
    plan = make_plan(rows, **options)
    review = site_review(site_path)
    directory = Path(out).expanduser() / ("PREP_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ_") + uuid4().hex[:6])
    directory.mkdir(parents=True, exist_ok=False)
    def save(name, data):
        (directory / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    # A completion manifest appears last. Partial folders lack PREP_COMPLETE.json.
    _csv(directory / "design.csv", rows, FIELDS)
    _csv(directory / "search_segments.csv", plan["segments"], plan["segments"][0].keys())
    _csv(directory / "targets.csv", plan["targets"], plan["targets"][0].keys())
    save("source.json", source)
    save("plan.json", plan)
    save("site_review.json", review)
    s = plan["summary"]
    q_targets = sorted({r["q_target"] for r in rows if r["q_target"] is not None})
    lines = ["# 首次降温准备结果", "", "状态：PLAN_ONLY_NOT_EXECUTABLE。未连接仪器，没有设置功率或触发测量。", "",
        f"设计条目：{len(rows)}；设计中心 {s['min_design_hz']/1e9:.6f}–{s['max_design_hz']/1e9:.6f} GHz。",
        f"q_target 去重值：{q_targets}。Q 含义须现场确认，不能直接视为 QL。",
        f"源文件：{source['name']}；SHA256：{source['sha256']}。", "",
        f"搜索假设：频率 ±{plan['assumptions']['frequency_margin_pct']:g}%，QL≤{plan['assumptions']['ql_upper_bound']:g}，每线宽至少 {plan['assumptions']['samples_per_linewidth']} 个采样间隔。",
        f"合并后 {s['merged_band_count']} 个频带，{s['segment_count']} 段；重叠 {plan['assumptions']['overlap_fraction']*100:g}%。",
        "搜索耗时：尚未实测单段端到端耗时。" if s['estimated_search_seconds'] is None else f"搜索耗时估算：{s['estimated_search_seconds']:.1f} 秒，不代表温度在全程稳定。", "",
        "## 现场待补", ""]
    lines += [f"- {k}" for k in review["missing"]] or ["- 表单已填齐，仍需实测确认链路与功率边界。"]
    lines += ["", "## 下一步", "", "1. 复核 source.json 与 design.csv，确认此次装片对应哪些 pixel_id。",
        "2. 按 docs/现场交接.md 填写现场表；按 docs/首次降温路线.md 开展有监督的开发与验证。",
        "3. 用无源器件验收多段执行器后，才能将 search_segments.csv 接入 KID 调试。",
        "4. 候选谐振用局部复数 S21 确认；固定窗口确认后再使用原自主记录宏。", "",
        "## 文件", "", "design.csv：源表前九列的规范化副本；source_row 为本次输入行号。",
        "source.json：源文件校验与表名；不导入几何列。",
        "targets.csv：设计标签、候选频窗与局部 Span 提示，不是实测匹配。",
        "search_segments.csv / plan.json：离线搜索建议；power_dbm 留空。",
        "site_review.json：现场输入快照与缺项；不会启用任何采集权限。", "",
        "## 限制", ""] + [f"- {w}" for w in plan["warnings"]]
    (directory / "准备结果.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir() if p.is_file()}
    save("PREP_COMPLETE.json", {"status": "OFFLINE_PREPARED_NOT_MEASURED", "sha256": hashes})
    return directory


def add_parser(sub):
    command = sub.add_parser("prepare", help="首次降温设计导入与搜索规划（离线，不连接仪器）")
    command.add_argument("design", type=Path, help="设计 XLSX 或规范化 CSV")
    command.add_argument("--sheet", default="结果表")
    command.add_argument("--out", default="data")
    command.add_argument("--site", type=Path)
    command.add_argument("--margin-pct", type=float, default=2)
    command.add_argument("--ql-upper", type=float, default=100000)
    command.add_argument("--points", type=int, default=1601)
    command.add_argument("--samples-per-linewidth", type=int, default=8)
    command.add_argument("--overlap", type=float, default=0.1)
    command.add_argument("--seconds-per-segment", type=float)


def run(args):
    directory = prepare(args.design, args.out, args.site, args.sheet,
        margin_pct=args.margin_pct, ql_upper=args.ql_upper, points=args.points,
        samples_per_linewidth=args.samples_per_linewidth, overlap=args.overlap,
        seconds_per_segment=args.seconds_per_segment)
    print("离线准备完成：", directory)
    print("请查看 准备结果.md；未连接仪器，分段执行器尚待开发和实机验证。")
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    add_parser(parser.add_subparsers(dest="command", required=True))
    try:
        run(parser.parse_args())
    except (ValueError, OSError, KeyError, ET.ParseError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        sys.exit(1)
