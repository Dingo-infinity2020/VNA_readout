import csv
import json
import math
from pathlib import Path
import socket
import tempfile
import threading
import unittest

import vna


def profile():
    return vna.load_profile(Path(__file__).resolve().parents[1] / "configs/filter.json")


class Server:
    """Small local SCPI peer; not an instrument-qualification simulator."""
    def __init__(self, respond):
        self.respond = respond
        self.commands = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            conn, _ = self.listener.accept()
            with conn:
                conn.settimeout(2)
                buffer = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buffer += chunk
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        command = raw.decode()
                        self.commands.append(command)
                        reply = self.respond(command)
                        if reply is False:
                            return
                        if reply is not None:
                            data = (reply + "\n").encode()
                            # Deliberately fragment even short replies.
                            conn.sendall(data[:2])
                            conn.sendall(data[2:])
        except (OSError, ValueError):
            pass

    def close(self):
        self.thread.join(3)
        self.listener.close()


class Tests(unittest.TestCase):
    def test_fragmented_response(self):
        server = Server(lambda c: "Agilent Technologies,E5071C,TEST,A.11.02")
        conn = vna.SCPI("127.0.0.1", 1, server.port)
        try:
            self.assertIn("E5071C", conn.identity())
        finally:
            conn.close()
            server.close()

    def test_wrong_serial_stops(self):
        server = Server(lambda c: "Agilent Technologies,E5071C,OTHER,A.11.02")
        conn = vna.SCPI("127.0.0.1", 1, server.port)
        try:
            with self.assertRaisesRegex(ValueError, "serial"):
                conn.identity("EXPECTED")
        finally:
            conn.close()
            server.close()
        self.assertEqual(server.commands, ["*IDN?"])

    def test_disconnect_no_retry(self):
        server = Server(lambda c: False)
        conn = vna.SCPI("127.0.0.1", 0.1, server.port)
        try:
            with self.assertRaises(ConnectionError):
                conn.query("*OPC?")
            with self.assertRaises(RuntimeError):
                conn.query("*OPC?")
        finally:
            conn.close()
            server.close()
        self.assertEqual(server.commands, ["*OPC?"])

    def test_timeout_no_retry(self):
        server = Server(lambda c: None)
        conn = vna.SCPI("127.0.0.1", 0.05, server.port)
        try:
            with self.assertRaises(TimeoutError):
                conn.query("*OPC?")
            self.assertFalse(conn.usable)
        finally:
            conn.close()
            server.close()
        self.assertEqual(server.commands, ["*OPC?"])

    def test_malformed_data_rejected(self):
        p = {**profile(), "points": 2}
        for freq, data in (([1e9, 4e9], [1, 0]), ([4e9, 1e9], [1, 0, 1, 0]),
                           ([1e9, 4e9], [math.nan, 0, 1, 0]),
                           ([1e9, 3e9], [1, 0, 1, 0])):
            with self.subTest(freq=freq, data=data), self.assertRaises(ValueError):
                vna.validate_data(freq, data, p)

    def test_complete_data_pairs(self):
        p = {**profile(), "points": 2}
        self.assertEqual(vna.validate_data([1e9, 4e9], [1, -2, 3, -4], p),
                         [(1e9, 1, -2), (4e9, 3, -4)])

    def test_commit_hash_and_csv(self):
        with tempfile.TemporaryDirectory() as root:
            r = vna.Recorder(root, {"source": "SIMULATED"})
            path = r.save(1, [(1e9, 1, 0), (4e9, 0, 1)], {"source": "SIMULATED"})
            metadata = json.loads(path.with_suffix(".json").read_text())
            import hashlib
            self.assertEqual(metadata["csv_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            with path.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(float(rows[1]["s21_imag"]), 1)
            self.assertTrue(path.with_suffix(".svg").exists())
            self.assertFalse(list(r.directory.glob("*.tmp")))
            self.assertIn('"event": "committed"', (r.directory / "events.jsonl").read_text())

    def test_profile_failures(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "p.json"
            for change in ({"power_dbm": None}, {"power_dbm": 0}, {"points": 3.5},
                           {"start_hz": 5e9}, {"ifbw_hz": float("nan")}, {"typo": 1}):
                path.write_text(json.dumps({**profile(), **change}))
                with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                    vna.load_profile(path)

    def test_sweep_waits_and_reads_once(self):
        p = {**profile(), "points": 2}
        replies = {
            ":SENS1:FREQ:STAR?": "1000000000", ":SENS1:FREQ:STOP?": "4000000000",
            ":SENS1:SWE:POIN?": "2", ":SENS1:BAND?": "1000", ":SOUR1:POW?": "-30",
            ":SENS1:CORR:STAT?": "0", ":OUTP?": "1", ":CALC1:PAR1:DEF?": "S21",
            ":SENS1:AVER:STAT?": "0", ":SENS1:SWE:TYPE?": "LIN",
            ":SYST:ERR?": '0,"No error"', "*OPC?": "1",
            ":SENS1:FREQ:DATA?": "1000000000,4000000000", ":CALC1:DATA:SDAT?": "1,0,0,1",
        }
        server = Server(lambda c: replies.get(c))
        conn = vna.SCPI("127.0.0.1", 1, server.port)
        try:
            rows, metadata = conn.acquire(p)
            self.assertEqual(len(rows), 2)
            self.assertGreaterEqual(metadata["elapsed_s"], 0)
        finally:
            conn.close()
            server.close()
        self.assertEqual(server.commands.count(":TRIG:SING"), 1)
        self.assertLess(server.commands.index("*OPC?"), server.commands.index(":CALC1:DATA:SDAT?"))


if __name__ == "__main__":
    unittest.main()
