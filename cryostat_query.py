#!/usr/bin/env python3
"""Read one Coolab DR_Project telemetry snapshot locally or via Windows SSH."""

import argparse
import base64
from datetime import datetime, timezone
import json
import math
import socket
import subprocess
import sys
import time


ENDPOINT = ("127.0.0.1", 9527)
REQUEST = b"READ;ALL\r\n"
MAX_LINE_BYTES = 4096

# Windows has PowerShell even when Python is only installed on the SSH client.
# Base64 keeps the reply bytes intact across different Windows console encodings.
REMOTE_QUERY = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$budget = __TIMEOUT_MS__
$maxBytes = __MAX_BYTES__
$request = [Convert]::FromBase64String('__REQUEST__')
$client = [Net.Sockets.TcpClient]::new()
$timer = [Diagnostics.Stopwatch]::StartNew()
function Get-Remaining {
    $left = $budget - [int]$timer.ElapsedMilliseconds
    if ($left -le 0) { throw 'Timed out waiting for the cryostat' }
    return $left
}
try {
    if (-not $client.ConnectAsync('__HOST__', __PORT__).Wait((Get-Remaining))) {
        throw 'Timed out connecting to the cryostat'
    }
    $stream = $client.GetStream()
    $stream.WriteTimeout = Get-Remaining
    $stream.Write($request, 0, $request.Length)
    $buffer = New-Object byte[] $maxBytes
    for ($i = 0; $i -lt $buffer.Length; $i++) {
        $stream.ReadTimeout = Get-Remaining
        $byte = $stream.ReadByte()
        if ($byte -lt 0) { throw 'Cryostat closed before a complete response line' }
        $buffer[$i] = [byte]$byte
        if ($byte -eq 10) {
            @{
                received_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ss.fffZ', [Globalization.CultureInfo]::InvariantCulture)
                wire = [Convert]::ToBase64String($buffer, 0, $i + 1)
            } | ConvertTo-Json -Compress
            exit 0
        }
    }
    throw 'Cryostat response exceeded the line size limit'
} catch {
    [Console]::Error.WriteLine($_.Exception.GetBaseException().Message)
    exit 1
} finally {
    $client.Dispose()
}
"""


def query_local(timeout):
    deadline = time.monotonic() + timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise TimeoutError("Timed out waiting for the cryostat")
        return seconds

    with socket.create_connection(ENDPOINT, timeout=remaining()) as connection:
        connection.settimeout(remaining())
        connection.sendall(REQUEST)
        response = bytearray()
        while len(response) < MAX_LINE_BYTES:
            connection.settimeout(remaining())
            chunk = connection.recv(MAX_LINE_BYTES - len(response))
            if not chunk:
                raise ValueError("Cryostat closed before a complete response line")
            line, newline, _ = chunk.partition(b"\n")
            response.extend(line)
            if newline:
                received = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                return bytes(response) + newline, received.replace("+00:00", "Z")
    raise ValueError("Cryostat response exceeded the line size limit")


def query_ssh(target, timeout):
    script = (REMOTE_QUERY.replace("__TIMEOUT_MS__", str(timeout * 1000))
              .replace("__MAX_BYTES__", str(MAX_LINE_BYTES))
              .replace("__REQUEST__", base64.b64encode(REQUEST).decode("ascii"))
              .replace("__HOST__", ENDPOINT[0]).replace("__PORT__", str(ENDPOINT[1])))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "-o", "ClearAllForwardings=yes", "-o", "ConnectionAttempts=1",
         "-o", f"ConnectTimeout={timeout}", "--", target, "powershell.exe",
         "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=2 * timeout + 5,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()[-1200:]
        raise ValueError(f"SSH query failed (exit {result.returncode}): {detail or 'no error text'}")
    try:
        reply = json.loads(result.stdout)
        wire = base64.b64decode(reply["wire"], validate=True)
        stamp = reply["received_at_utc"]
        if not isinstance(stamp, str) or not stamp.endswith("Z"):
            raise ValueError("Missing UTC timestamp")
        datetime.fromisoformat(stamp[:-1] + "+00:00")
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid SSH response envelope") from exc
    return wire, stamp


def parse_snapshot(wire, received_at):
    if len(wire) > MAX_LINE_BYTES or not wire.endswith(b"\n") or b"\n" in wire[:-1]:
        raise ValueError("Expected one complete cryostat response line")
    raw = wire.removesuffix(b"\n").removesuffix(b"\r").decode("utf-8-sig")
    fields = raw.split(",")
    if len(fields) != 22:
        raise ValueError(f"Expected 22 telemetry fields, received {len(fields)}: {raw[:80]!r}")

    values = []
    for index, field in enumerate(fields, start=1):
        field = field.strip()
        try:
            value = None if field.lower() == "nan" else float(field)
        except ValueError as exc:
            raise ValueError(f"Invalid numeric value in field {index}: {field[:40]!r}") from exc
        if value is not None and not math.isfinite(value):
            raise ValueError(f"Invalid nonfinite value in field {index}: {field[:40]!r}")
        # DR_Project can fill absent temperature channels with zero.
        values.append(None if index <= 16 and value == 0 else value)

    return {
        "received_at_utc": received_at,
        "raw_response": raw,
        "temperature_k": {f"T{i}": value for i, value in enumerate(values[:16], start=1)},
        "pressure_mbar": {f"G{i}": value for i, value in enumerate(values[16:21], start=1)},
        "flow_l_min": values[21],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh", metavar="HOST", help="configured SSH host alias or user@host; default: local loopback")
    parser.add_argument("--timeout", type=int, default=10, metavar="SECONDS", help="query timeout, 1 to 30 seconds (default: 10)")
    parser.add_argument("--json", action="store_true", help="print timestamp, raw reply and parsed telemetry as JSON")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 30:
        parser.error("--timeout must be between 1 and 30 seconds")
    if args.ssh is not None and (not args.ssh or args.ssh.startswith("-") or any(c.isspace() for c in args.ssh)):
        parser.error("--ssh must be a host alias or user@host")
    try:
        wire, received_at = query_ssh(args.ssh, args.timeout) if args.ssh else query_local(args.timeout)
        snapshot = parse_snapshot(wire, received_at)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Cryostat query failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(snapshot, indent=2, allow_nan=False))
    else:
        def display(value):
            return "unknown" if value is None else f"{value:.6g}"

        print(f"Received at {snapshot['received_at_utc']}")
        for field, label in (("temperature_k", "Temperature (K)"), ("pressure_mbar", "Pressure (mbar)")):
            readings = "  ".join(f"{name}={display(value)}" for name, value in snapshot[field].items())
            print(f"{label}: {readings}")
        print(f"Flow (L/min): {display(snapshot['flow_l_min'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
