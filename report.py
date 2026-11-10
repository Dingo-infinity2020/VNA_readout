"""Offline integrity checking and a portable HTML report; never connects to a VNA."""
import csv
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re

from vna import atomic, json_text, number, validate_data


def read_sweep(path, metadata):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        data = list(csv.DictReader(stream))
    frequency = [number(r["frequency_hz"]) for r in data]
    pairs = [number(r[k]) for r in data for k in ("s21_real", "s21_imag")]
    return validate_data(frequency, pairs, metadata["profile"])


def inspect_run(directory):
    directory = Path(directory)
    if not (directory / "run.json").is_file():
        raise ValueError("Choose a run directory containing run.json")
    errors, warnings, valid_files, committed = [], [], [], set()
    events = []
    for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
            events.append(event)
        except (ValueError, TypeError):
            errors.append("Incomplete or invalid event log line")
            continue
        if event.get("event") != "committed":
            continue
        filename = event.get("csv", "")
        if not re.fullmatch(r"sweep_\d{6,}\.csv", filename):
            errors.append("Invalid committed filename")
            continue
        if filename in committed:
            errors.append(f"Duplicate commit: {filename}")
            continue
        committed.add(filename)
        try:
            path = directory / filename
            metadata = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != metadata["csv_sha256"] or digest != event["sha256"]:
                raise ValueError("CSV SHA256 mismatch")
            rows = read_sweep(path, metadata)
            valid_files.append({"csv": filename, "points": len(rows)})
        except (OSError, ValueError, KeyError, TypeError) as error:
            errors.append(f"{filename}: {error}")
    orphan = [p.name for p in directory.glob("sweep_*.csv") if p.name not in committed]
    temporary = [p.name for p in directory.glob("*.tmp")]
    if orphan or temporary:
        warnings.append(f"Uncommitted files excluded: {orphan + temporary}")
    if not valid_files:
        errors.append("No complete, verified sweeps")
    if not any(e.get("event") == "finished" for e in events):
        warnings.append("Run did not finish normally; inspect stop events")
    run = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    if run.get("source") != "SIMULATED" and not any(e.get("event") == "rf_off_confirmed" for e in events):
        warnings.append("RF OFF confirmation not present in this log")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "valid_files": valid_files, "uncommitted_files": orphan + temporary}


def graph(curves, title, xlabel, ylabel):
    colors = ("#1769aa", "#cf5b21", "#249163", "#9150ac")
    all_points = [point for curve in curves for point in curve]
    xmin, xmax = min(x for x, _ in all_points), max(x for x, _ in all_points)
    ymin, ymax = min(y for _, y in all_points), max(y for _, y in all_points)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    pad = (ymax-ymin)*0.05
    ymin, ymax = ymin-pad, ymax+pad
    svg = ['<svg viewBox="0 0 860 360" role="img">',
           f'<title>{html.escape(title)}</title>',
           f'<text x="75" y="22">{html.escape(title)}</text>']
    for i in range(6):
        x, y = 75+i*140, 300-i*50
        svg += [f'<path d="M{x} 45V300 M75 {y}H775" stroke="#e0e5ec"/>',
                f'<text x="{x}" y="322" text-anchor="middle">{xmin+(xmax-xmin)*i/5:.5g}</text>',
                f'<text x="65" y="{y+4}" text-anchor="end">{ymin+(ymax-ymin)*i/5:.5g}</text>']
    for index, curve in enumerate(curves):
        points = " ".join(f"{75+700*(x-xmin)/(xmax-xmin):.2f},{300-250*(y-ymin)/(ymax-ymin):.2f}" for x, y in curve)
        svg.append(f'<polyline points="{points}" fill="none" stroke="{colors[index%4]}" stroke-width="1.4" opacity="0.8"/>')
    svg += [f'<text x="420" y="348" text-anchor="middle">{html.escape(xlabel)}</text>',
            f'<text x="4" y="40">{html.escape(ylabel)}</text>', '</svg>']
    return "".join(svg)


def create_report(directory):
    directory = Path(directory)
    inspection = inspect_run(directory)
    summaries, traces = [], []
    files = inspection["valid_files"]
    # All verified sweeps go into summary.csv; only the last 20 traces are plotted.
    for index, entry in enumerate(files):
        path = directory / entry["csv"]
        meta = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
        rows = read_sweep(path, meta)
        db = [20*math.log10(max(math.hypot(r, i), 1e-15)) for _, r, i in rows]
        minimum = min(range(len(rows)), key=lambda i: db[i])
        summaries.append({"file": entry["csv"], "time": meta.get("started_utc", meta.get("vna_started_local", "unknown")),
                          "clock": "VNA_LOCAL_UNVERIFIED" if "vna_started_local" in meta else "UTC",
                          "source": meta.get("source", "UNKNOWN"),
                          "cal_id": meta["profile"].get("cal_id", "UNKNOWN"),
                          "minimum_frequency_hz": rows[minimum][0], "minimum_db": db[minimum],
                          "minimum_at_edge": minimum in (0, len(rows)-1)})
        if index >= max(0, len(files)-20):
            traces.append(rows)
    if not traces:
        raise ValueError("No verified data available for a report: " + repr(inspection["errors"]))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(summaries[0]))
    writer.writeheader()
    writer.writerows(summaries)
    atomic(directory / "summary.csv", output.getvalue())
    magnitude = [[(f/1e9, 20*math.log10(max(math.hypot(r, i), 1e-15))) for f, r, i in rows] for rows in traces]
    phase = [[(f/1e9, math.degrees(math.atan2(i, r))) for f, r, i in rows] for rows in traces]
    iq = [[(r, i) for _, r, i in rows] for rows in traces]
    cells = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row.values()) + "</tr>" for row in summaries)
    headers = "".join(f"<th>{html.escape(k)}</th>" for k in summaries[0])
    messages = html.escape(json_text({k: inspection[k] for k in ("valid", "errors", "warnings")}))
    labels = sorted({f'{r["source"]} / {r["cal_id"]}' for r in summaries})
    page = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VNA 离线测量报告</title><style>
body{{font:16px system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;background:#f5f7fa;color:#172638}}
section{{background:white;border:1px solid #dce2e9;border-radius:10px;padding:18px;margin:18px 0}}
svg{{width:100%;font:12px sans-serif}}table{{border-collapse:collapse;font-size:12px;white-space:nowrap}}
td,th{{padding:8px;border:1px solid #dce2e9}}pre{{white-space:pre-wrap}}.scroll{{overflow:auto}}
</style><h1>VNA 离线测量报告</h1><p>{html.escape(directory.name)}</p>
<section><b>{html.escape(' · '.join(labels))}</b><p>完整并校验通过：{len(files)} 扫。图中叠加最后 {len(traces)} 扫；每条曲线颜色循环。未提交数据不参与分析。</p>
<p>最小值频率仅为采样点最小值，不是谐振拟合；单调滤波器的最小值常在窗口边缘。不输出未经拟合的 Q 或噪声 PSD。相位为包裹相位，±180° 跳变是显示约定。</p><pre>{messages}</pre></section>
<section>{graph(magnitude, 'S21 magnitude', 'Frequency (GHz)', 'dB')}</section>
<section>{graph(phase, 'S21 wrapped phase', 'Frequency (GHz)', 'deg')}</section>
<section>{graph(iq, 'S21 complex plane', 'Real', 'Imag')}</section>
<section><h2>逐次摘要</h2><p>完整表保存在 summary.csv。时间列的 clock 区分 UTC 与未经同步验证的 VNA 本地时间。</p>
<div class="scroll"><table><tr>{headers}</tr>{cells}</table></div></section></html>'''
    atomic(directory / "report.html", page)
    return directory / "report.html"
