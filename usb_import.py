"""Import native VBA CSVs without modifying the USB drive or using the network."""
import csv
from datetime import datetime
import hashlib
from pathlib import Path
import re

from vna import Recorder, number, validate_data


def import_usb(source, out="data", sample="USB_KID_RUN"):
    source = Path(source)
    manifest = source / "manifest.csv"
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        entries = list(csv.DictReader(stream))
    if not entries:
        raise ValueError("USB manifest contains no committed sweeps")
    info = (source / "run_info.txt").read_text(encoding="utf-8-sig")
    status = (source / "status.log").read_text(encoding="utf-8-sig") if (source / "status.log").exists() else ""
    recorder = Recorder(out, {"source": "VNA_USB", "sample": sample,
                             "source_directory_name": source.name, "native_run_info": info,
                             "time_basis": "VNA_LOCAL_UNVERIFIED"})
    seen = set()
    try:
        for index, entry in enumerate(entries, 1):
            name = entry["filename"]
            if not re.fullmatch(r"sweep_\d{6,}\.csv", name) or name in seen:
                raise ValueError("Invalid or duplicate USB manifest filename")
            seen.add(name)
            if entry["calibration"] != "UNCALIBRATED":
                raise ValueError("This native importer expects explicitly uncalibrated data")
            points = number(entry["points"])
            if points != int(points) or not 2 <= points <= 1601:
                raise ValueError("Invalid native point count")
            profile = {key: number(entry[key]) for key in ("start_hz", "stop_hz", "ifbw_hz", "power_dbm")}
            profile.update(points=int(points), sample=sample, calibration="off", cal_id="UNCALIBRATED")
            for field in ("started_local", "ended_local"):
                datetime.strptime(entry[field], "%Y-%m-%d %H:%M:%S")
            path = source / name
            with path.open(encoding="utf-8-sig", newline="") as stream:
                raw = list(csv.DictReader(stream))
            frequency = [number(r["frequency_hz"]) for r in raw]
            pairs = [number(r[k]) for r in raw for k in ("s21_real", "s21_imag")]
            rows = validate_data(frequency, pairs, profile)
            recorder.save(index, rows, {
                "source": "VNA_USB", "profile": profile, "source_csv": name,
                "source_csv_sha256_at_import": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_hash_note": "Computed at import, not a checksum verified on the VNA",
                "vna_started_local": entry["started_local"], "vna_ended_local": entry["ended_local"],
                "clock_basis": "VNA_LOCAL_UNVERIFIED", "native_run_info": info})
        orphan = [p.name for p in source.glob("sweep_*") if p.name not in seen]
        recorder.event("native_import", {"uncommitted_source_files_excluded": orphan,
                                        "status_log": status})
        if "RF_OFF_CONFIRMED" in status.split():
            recorder.event("rf_off_confirmed", {"basis": "native_log_only_not_live_query"})
        if any(line.split()[-2:-1] == ["FINISHED"] for line in status.splitlines()):
            recorder.event("finished", {"sweeps": len(entries), "basis": "native_log"})
        return recorder.directory
    except BaseException as error:
        recorder.event("stopped", {"reason": repr(error), "operation": "USB_IMPORT"})
        raise
