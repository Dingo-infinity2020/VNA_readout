#!/usr/bin/env python3
"""Small offline E5071C S21 recorder. Python 3.10+, standard library only."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

VERSION = "0.2.1"
ROOT = Path(__file__).resolve().parent


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def atomic(path, text):
    """A final filename appears only after its complete contents have been flushed."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite numeric value")
    return result


def load_profile(path):
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    allowed = {"sample", "start_hz", "stop_hz", "points", "ifbw_hz",
               "power_dbm", "calibration", "cal_id", "notes", "dut_kind",
               "max_power_dbm", "chain_reviewed"}
    if set(profile) - allowed:
        raise ValueError(f"Unknown profile keys: {sorted(set(profile) - allowed)}")
    for key in ("start_hz", "stop_hz", "ifbw_hz", "power_dbm"):
        profile[key] = number(profile[key])
    if not 1e9 <= profile["start_hz"] < profile["stop_hz"] <= 4e9:
        raise ValueError("v0.1 supports frequency sweeps within 1–4 GHz")
    points = profile["points"]
    if type(points) is not int or not 2 <= points <= 1601:
        raise ValueError("points must be an integer from 2 to 1601")
    if not 10 <= profile["ifbw_hz"] <= 10000:
        raise ValueError("ifbw_hz must be 10–10000 Hz")
    if not -55 <= profile["power_dbm"] <= -20:
        raise ValueError("v0.1 permits source power only from -55 to -20 dBm; not a KID safety guarantee")
    kind = profile.get("dut_kind", "passive")
    if kind not in ("passive", "kid"):
        raise ValueError("dut_kind must be passive or kid")
    if kind == "kid":
        if profile.get("chain_reviewed") is not True:
            raise ValueError("KID profile requires a reviewed input/output power budget")
        limit = number(profile["max_power_dbm"])
        if not -55 <= limit <= -20 or profile["power_dbm"] > limit:
            raise ValueError("KID source power exceeds the reviewed max_power_dbm")
    if profile["calibration"] not in ("off", "manual_forward"):
        raise ValueError("calibration must be off or manual_forward")
    if not str(profile.get("sample", "")).strip():
        raise ValueError("sample is required")
    if profile["calibration"] == "off":
        profile["cal_id"] = "UNCALIBRATED"
    elif not profile.get("cal_id") or "REPLACE" in profile["cal_id"]:
        raise ValueError("Set the real cal_id before calibrated acquisition")
    return profile


class SCPI:
    def __init__(self, host, timeout=120, port=5025):
        self.socket = socket.create_connection((host, port), timeout=min(timeout, 5))
        self.socket.settimeout(timeout)
        self.timeout = timeout
        self.buffer = bytearray()
        self.usable = True

    def close(self):
        self.socket.close()

    def write(self, command):
        if not self.usable:
            raise RuntimeError("Connection is unusable; no retry")
        try:
            self.socket.sendall((command + "\n").encode("ascii"))
        except OSError:
            self.usable = False
            raise

    def query(self, command):
        self.write(command)
        deadline = time.monotonic() + self.timeout
        try:
            while b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("SCPI reply deadline exceeded")
                self.socket.settimeout(remaining)
                chunk = self.socket.recv(65536)
                if not chunk:
                    raise ConnectionError("Instrument closed connection")
                self.buffer.extend(chunk)
                if len(self.buffer) > 4_000_000:
                    raise ValueError("SCPI reply exceeds 4 MB")
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            return line.decode("ascii").strip()
        except BaseException:
            # An interrupted query may leave unread bytes. Never trust its next reply.
            self.usable = False
            raise

    def identity(self, expected_serial=None):
        identity = self.query("*IDN?")
        fields = [field.strip() for field in identity.split(",")]
        if len(fields) != 4 or fields[1].upper() != "E5071C":
            raise ValueError(f"Expected E5071C, received {identity!r}")
        if expected_serial and fields[2] != expected_serial:
            raise ValueError("Instrument serial number mismatch")
        return identity

    def state(self):
        queries = {
            "start_hz": ":SENS1:FREQ:STAR?", "stop_hz": ":SENS1:FREQ:STOP?",
            "points": ":SENS1:SWE:POIN?", "ifbw_hz": ":SENS1:BAND?",
            "power_dbm": ":SOUR1:POW?", "correction": ":SENS1:CORR:STAT?",
            "rf_output": ":OUTP?", "parameter": ":CALC1:PAR1:DEF?",
            "averaging": ":SENS1:AVER:STAT?", "sweep_type": ":SENS1:SWE:TYPE?",
        }
        return {key: self.query(cmd) for key, cmd in queries.items()}

    def errors(self):
        errors = []
        for _ in range(20):
            reply = self.query(":SYST:ERR?")
            if int(reply.split(",", 1)[0]) == 0:
                return errors
            errors.append(reply)
        raise RuntimeError("Instrument error queue did not drain: " + repr(errors))

    def assert_no_errors(self):
        errors = self.errors()
        if errors:
            raise RuntimeError("Instrument reported errors: " + repr(errors))

    def matches(self, profile):
        state = self.state()
        for key in ("start_hz", "stop_hz", "points", "ifbw_hz", "power_dbm"):
            if not math.isclose(number(state[key]), profile[key], rel_tol=1e-9, abs_tol=1e-6):
                raise ValueError(f"Setting mismatch: {key}: {state[key]} vs {profile[key]}")
        expected = "0" if profile["calibration"] == "off" else "1"
        if number(state["correction"]) != int(expected):
            raise ValueError("Correction state does not match profile")
        if state["parameter"].strip('"').upper() != "S21":
            raise ValueError("Trace 1 must be S21")
        if number(state["averaging"]) != 0 or state["sweep_type"].strip('"').upper() != "LIN":
            raise ValueError("v0.1 requires linear frequency sweep, averaging OFF")
        if profile["calibration"] == "manual_forward":
            # Applied correction type, NOT the pending calibration collection method.
            cal_type = self.query(":SENS1:CORR:TYPE?").strip('"').upper()
            state["calibration_type"] = cal_type
            if cal_type != "RESPT":
                raise ValueError(f"v0.1 calibrated mode requires Thru Response (RESPT), got {cal_type}")
        self.assert_no_errors()
        return state

    def configure(self, profile):
        # RF is disabled before any settings are changed. Never call Preset/*RST.
        self.write(":OUTP OFF")
        if profile["calibration"] == "off":
            for cmd in (
                ":SENS1:CORR:STAT OFF", ":SENS1:SWE:TYPE LIN",
                f":SENS1:FREQ:STAR {profile['start_hz']}",
                f":SENS1:FREQ:STOP {profile['stop_hz']}",
                f":SENS1:SWE:POIN {profile['points']}",
                f":SENS1:BAND {profile['ifbw_hz']}",
                ":SOUR1:POW:PORT:COUP ON", ":SOUR1:POW:SLOP:STAT OFF",
                ":SOUR1:POW:PORT1:CORR OFF",
                f":SOUR1:POW {profile['power_dbm']}", ":SENS1:AVER:STAT OFF",
            ):
                self.write(cmd)
        # Calibrated mode preserves manually set frequency/power/IFBW/calibration.
        for cmd in (
            ":CALC1:PAR:COUN 1", ":CALC1:PAR1:DEF S21", ":CALC1:PAR1:SEL",
            ":DISP:WIND1:ACT", ":TRIG:SEQ:SCOP ACT", ":INIT1:CONT OFF",
            ":ABOR", ":TRIG:SOUR BUS", ":FORM:DATA ASC",
            ":SYST:ISPC ON", ":SYST:ISPC:PORT 1",
        ):
            self.write(cmd)
        self.matches(profile)

    def acquire(self, profile):
        before = self.matches(profile)
        if number(before["rf_output"]) != 1:
            raise ValueError("RF must be ON before a measurement")
        started, clock = utc(), time.monotonic()
        self.write(":INIT1:CONT ON")
        self.write(":TRIG:SING")
        if number(self.query("*OPC?")) != 1:
            raise RuntimeError("Sweep did not confirm completion")
        self.write(":INIT1:CONT OFF")
        frequency = [number(x) for x in self.query(":SENS1:FREQ:DATA?").split(",")]
        pairs = [number(x) for x in self.query(":CALC1:DATA:SDAT?").split(",")]
        ended, elapsed = utc(), time.monotonic() - clock
        rows = validate_data(frequency, pairs, profile)
        after = self.matches(profile)
        if before != after:
            raise RuntimeError("Settings changed during acquisition")
        return rows, {"started_utc": started, "ended_utc": ended,
                      "elapsed_s": elapsed, "actual_settings": after}

    def rf_off(self):
        self.write(":OUTP OFF")
        self.write(":INIT1:CONT OFF")
        if number(self.query(":OUTP?")) != 0:
            raise RuntimeError("RF OFF was not confirmed")


def validate_data(frequency, pairs, profile):
    n = profile["points"]
    if len(frequency) != n or len(pairs) != 2 * n:
        raise ValueError("Incomplete frequency/S21 arrays")
    if any(not math.isfinite(x) for x in frequency + pairs):
        raise ValueError("Non-finite measurement data")
    if any(b <= a for a, b in zip(frequency, frequency[1:])):
        raise ValueError("Frequency axis is not strictly increasing")
    for measured, key in ((frequency[0], "start_hz"), (frequency[-1], "stop_hz")):
        if not math.isclose(measured, profile[key], rel_tol=1e-9, abs_tol=1):
            raise ValueError("Frequency axis does not match configured range")
    return list(zip(frequency, pairs[::2], pairs[1::2]))


def svg_plot(rows, source="INSTRUMENT"):
    magnitudes = [20 * math.log10(max(math.hypot(r, i), 1e-15)) for _, r, i in rows]
    lo, hi = min(magnitudes) - 1, max(magnitudes) + 1
    first, last = rows[0][0], rows[-1][0]
    points = " ".join(f"{60 + 700 * (f-first)/(last-first):.2f},{40 + 300 * (hi-db)/(hi-lo):.2f}"
                      for (f, _, _), db in zip(rows, magnitudes))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="820" height="400" viewBox="0 0 820 400">
<rect width="820" height="400" fill="white"/>
<g font-family="sans-serif" font-size="14" fill="#222">
<text x="60" y="24">{source}: S21 (dB) — calibration status in JSON</text>
<path d="M60 40 V340 H760" fill="none" stroke="#555"/>
<text x="5" y="50">{hi:.1f}</text><text x="5" y="340">{lo:.1f}</text>
<text x="60" y="370">{first/1e9:.6f} GHz</text>
<text x="620" y="370">{last/1e9:.6f} GHz</text></g>
<polyline points="{points}" fill="none" stroke="#1769aa" stroke-width="1.5"/></svg>'''


class Recorder:
    def __init__(self, root, metadata):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.directory = root / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8])
        self.directory.mkdir()
        atomic(self.directory / "run.json", json_text(metadata))
        self.event("created", {"version": VERSION})

    def event(self, kind, details):
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"utc": utc(), "event": kind, **details}, ensure_ascii=False,
                                    allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def save(self, index, rows, metadata):
        stem = f"sweep_{index:06d}"
        if any((self.directory / (stem + suffix)).exists() for suffix in (".csv", ".json")):
            raise FileExistsError("Refusing to overwrite an existing sweep")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["frequency_hz", "s21_real", "s21_imag", "magnitude_db", "phase_deg"])
        for f, real, imag in rows:
            writer.writerow([f, real, imag, 20*math.log10(max(math.hypot(real, imag), 1e-15)),
                             math.degrees(math.atan2(imag, real))])
        text = output.getvalue()
        atomic(self.directory / (stem + ".csv"), text)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        atomic(self.directory / (stem + ".json"), json_text({
            **metadata, "schema_version": 1, "sweep_id": index, "csv_sha256": digest,
            "software_version": VERSION}))
        self.event("committed", {"sweep_id": index, "csv": stem + ".csv", "sha256": digest})
        # The preview is derived; failure here leaves the committed raw CSV/JSON intact.
        atomic(self.directory / (stem + ".svg"), svg_plot(rows, metadata.get("source", "UNKNOWN")))
        return self.directory / (stem + ".csv")


def demo_rows(profile, kind="filter", index=0):
    n = profile["points"]
    rows = []
    for i in range(n):
        f = profile["start_hz"] + (profile["stop_hz"] - profile["start_hz"]) * i/(n-1)
        phase = -2*math.pi*f*2e-9
        if kind == "kid":
            f0 = (profile["start_hz"] + profile["stop_hz"])/2 + index*200
            z = 0.8 * (1 - 0.7/(1 + 2j*50000*(f/f0-1)))
        else:
            z = 0.8 / math.sqrt(1 + ((f-2.5e9)/2.5e8)**8)
        z *= complex(math.cos(phase), math.sin(phase))
        rows.append((f, z.real, z.imag))
    return rows


def run(args):
    if args.command == "demo":
        profile = load_profile(args.profile)
        if args.kind == "kid":
            profile = {**profile, "sample": "SIMULATED_KID", "start_hz": 2.999e9, "stop_hz": 3.001e9}
        recorder = Recorder(args.out, {"version": VERSION, "source": "SIMULATED", "profile": profile})
        for index in range(args.count):
            recorder.save(index+1, demo_rows(profile, args.kind, index),
                          {"source": "SIMULATED", "profile": profile,
                           "calibration_status": "SIMULATED", "started_utc": utc()})
        recorder.event("finished", {"sweeps": args.count, "source": "SIMULATED"})
        print("SIMULATED data:", recorder.directory)
        return recorder.directory
    if not args.host:
        raise ValueError("Supply --host IP or set VNA_HOST")
    profile = None if args.command == "check" else load_profile(args.profile)
    if profile and not args.allow_rf:
        raise ValueError("Acquisition requires --allow-rf after checking DUT and source power")
    connection = None
    recorder = None
    modified = False
    try:
        connection = SCPI(args.host, args.timeout)
        identity = connection.identity(args.serial)
        before = connection.state()
        if args.command == "check":
            print(json_text({"identity": identity, "settings": before}))
            return
        recorder = Recorder(args.out, {"version": VERSION, "identity": identity,
                            "host": args.host, "before": before, "profile": profile,
                            "temperature_k": args.temperature_k, "notes": args.notes})
        print("Run directory:", recorder.directory, flush=True)
        existing_errors = connection.errors()
        recorder.event("preexisting_errors", {"errors": existing_errors})
        if existing_errors:
            raise RuntimeError("Instrument had pre-existing errors; recorded, stop before configuration")
        modified = True
        connection.configure(profile)
        connection.write(":OUTP ON")
        connection.assert_no_errors()
        count = args.count if args.command == "monitor" else 1
        scheduled = time.monotonic()
        for index in range(1, count + 1):
            if shutil.disk_usage(recorder.directory).free < 100_000_000:
                raise OSError("Less than 100 MB free; stop before triggering")
            time.sleep(max(0, scheduled - time.monotonic()))
            cycle_started = time.monotonic()
            recorder.event("trigger_requested", {"sweep_id": index})
            rows, acquisition = connection.acquire(profile)
            path = recorder.save(index, rows, {"source": "INSTRUMENT", "identity": identity,
                       "profile": profile, "temperature_k": args.temperature_k,
                       "temperature_source": "manual_start_value_not_live" if args.temperature_k is not None else "unknown",
                       "notes": args.notes, **acquisition})
            print(f"[{index}/{count}] {path.name}: {len(rows)} points saved", flush=True)
            if args.command == "monitor":
                elapsed = time.monotonic() - cycle_started
                period = max(args.interval, 1.2 * elapsed)
                scheduled = cycle_started + period
                recorder.event("schedule", {"period_s": period, "cycle_s": elapsed})
        recorder.event("finished", {"sweeps": count})
    except BaseException as error:
        if recorder:
            try:
                recorder.event("stopped", {"reason": repr(error), "automatic_retry": False})
            except OSError:
                print("Could not write stop event; check disk.", file=sys.stderr)
        raise
    finally:
        if connection:
            off_error = None
            if modified:
                try:
                    connection.rf_off()
                    print("RF OFF confirmed.", flush=True)
                    if recorder:
                        try:
                            recorder.event("rf_off_confirmed", {})
                        except OSError:
                            print("RF OFF confirmed but log could not be saved.", file=sys.stderr)
                except Exception as error:
                    off_error = error
                    print(f"RF OFF NOT CONFIRMED: {error}. Check front panel.", file=sys.stderr)
            connection.close()
            if off_error and sys.exc_info()[0] is None:
                raise RuntimeError("Acquisition saved, but RF OFF was not confirmed") from off_error
    return recorder.directory if recorder else None


def positive(value):
    result = number(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("menu", help="中文操作菜单")
    command = sub.add_parser("import-usb", help="Import a completed native VNA USB run (offline)")
    command.add_argument("directory", type=Path)
    command.add_argument("--out", default="data")
    command.add_argument("--sample", default="USB_KID_RUN")
    command.add_argument("--storage-origin", choices=("unknown", "usb", "internal_d"), default="unknown")
    for name in ("report", "verify", "note"):
        command = sub.add_parser(name)
        command.add_argument("directory", type=Path)
        if name == "note":
            command.add_argument("--temperature-k", type=number)
            command.add_argument("--notes", required=True)
    for name in ("check", "capture", "monitor", "demo"):
        command = sub.add_parser(name)
        if name != "demo":
            command.add_argument("--host", default=os.environ.get("VNA_HOST"))
            command.add_argument("--serial", help="Optional expected instrument serial number")
            command.add_argument("--timeout", type=positive, default=120)
        if name != "check":
            command.add_argument("--profile", default=str(ROOT / "configs/filter.json"))
            command.add_argument("--out", default="data")
        if name in ("capture", "monitor"):
            command.add_argument("--allow-rf", action="store_true")
            command.add_argument("--temperature-k", type=number)
            command.add_argument("--notes", default="")
        if name == "monitor":
            command.add_argument("--interval", type=positive, default=60)
            command.add_argument("--count", type=int, default=60)
        if name == "demo":
            command.add_argument("--kind", choices=("filter", "kid"), default="filter")
            command.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    if args.command in ("monitor", "demo") and args.count < 1:
        parser.error("--count must be at least 1")
    try:
        if args.command in (None, "menu"):
            from menu import menu
            menu()
        elif args.command in ("verify", "report"):
            from report import inspect_run, create_report
            if args.command == "verify":
                inspection = inspect_run(args.directory)
                print(json_text(inspection))
                return 0 if inspection["valid"] else 1
            print("Report:", create_report(args.directory))
        elif args.command == "note":
            if not (args.directory / "run.json").is_file():
                raise ValueError("Not a run directory")
            recorder = Recorder.__new__(Recorder)
            recorder.directory = args.directory
            recorder.event("manual_note", {"temperature_k": args.temperature_k, "notes": args.notes})
            print("Timestamped manual note saved (not live temperature acquisition).")
        elif args.command == "import-usb":
            from usb_import import import_usb
            from report import create_report
            directory = import_usb(args.directory, args.out, args.sample, args.storage_origin)
            print("Imported:", directory)
            print("Report:", create_report(directory))
        else:
            directory = run(args)
            if directory:
                from report import create_report
                print("Report:", create_report(directory))
    except KeyboardInterrupt:
        print("Stopped by user. Saved sweeps retained.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"STOP: {error}. No automatic retry.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
