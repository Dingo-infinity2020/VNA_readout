import argparse
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import report
import usb_import
import vna


class WorkflowTests(unittest.TestCase):
    def make_run(self, root):
        p = vna.load_profile(vna.ROOT / "configs/filter.json")
        r = vna.Recorder(root, {"source": "SIMULATED"})
        r.save(1, vna.demo_rows(p), {"source": "SIMULATED", "profile": p})
        r.event("finished", {"sweeps": 1})
        return r.directory

    def test_report_marks_simulated_uncalibrated(self):
        with tempfile.TemporaryDirectory() as root:
            directory = self.make_run(root)
            self.assertTrue(report.inspect_run(directory)["valid"])
            path = report.create_report(directory)
            page = path.read_text()
            self.assertIn("SIMULATED / UNCALIBRATED", page)
            self.assertIn("wrapped phase", page)
            self.assertIn("complex plane", page)
            self.assertTrue((directory / "summary.csv").exists())

    def test_corruption_detected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = self.make_run(root)
            path = directory / "sweep_000001.csv"
            path.write_text(path.read_text().replace("1000000000.0", "1111111111.0", 1))
            result = report.inspect_run(directory)
            self.assertFalse(result["valid"])
            self.assertIn("SHA256", str(result["errors"]))
            with self.assertRaises(ValueError):
                report.create_report(directory)

    def test_uncommitted_data_excluded(self):
        with tempfile.TemporaryDirectory() as root:
            directory = self.make_run(root)
            (directory / "sweep_000002.csv").write_text("partial")
            result = report.inspect_run(directory)
            self.assertEqual(len(result["valid_files"]), 1)
            self.assertIn("sweep_000002.csv", result["uncommitted_files"])

    def test_committed_sweep_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            r = vna.Recorder(root, {})
            rows = [(1e9, 1, 0), (4e9, 1, 0)]
            r.save(1, rows, {})
            with self.assertRaises(FileExistsError):
                r.save(1, rows, {})

    def test_interrupted_query_invalidates_connection(self):
        c = vna.SCPI.__new__(vna.SCPI)
        c.usable, c.buffer, c.timeout = True, bytearray(), 1
        c.socket = Mock()
        c.socket.recv.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            c.query("*OPC?")
        self.assertFalse(c.usable)
        with self.assertRaises(RuntimeError):
            c.query("*OPC?")
        self.assertEqual(c.socket.sendall.call_count, 1)

    def test_logging_failure_does_not_skip_rf_cleanup(self):
        c = Mock(spec=vna.SCPI)
        c.identity.return_value = "Agilent Technologies,E5071C,TEST,A.11.02"
        c.state.return_value = {}
        c.errors.return_value = []
        c.acquire.side_effect = ValueError("bad measurement")
        recorder = Mock()
        recorder.directory = Path(tempfile.gettempdir())
        def event(kind, details):
            if kind == "stopped":
                raise OSError("disk full")
        recorder.event.side_effect = event
        args = argparse.Namespace(command="capture", host="127.0.0.1", timeout=1,
                serial=None, profile=vna.ROOT / "configs/filter.json", allow_rf=True,
                out="unused", temperature_k=None, notes="")
        with patch.object(vna, "SCPI", return_value=c), patch.object(vna, "Recorder", return_value=recorder):
            with self.assertRaisesRegex(ValueError, "bad measurement"):
                vna.run(args)
        c.rf_off.assert_called_once()
        c.close.assert_called_once()

    def test_rf_off_prevents_trigger(self):
        c = vna.SCPI.__new__(vna.SCPI)
        c.matches = Mock(return_value={"rf_output": "0"})
        c.write = Mock()
        with self.assertRaisesRegex(ValueError, "RF must be ON"):
            c.acquire({})
        c.write.assert_not_called()

    def test_kid_profile_requires_review_and_limit(self):
        p = json.loads((vna.ROOT / "configs/filter.json").read_text())
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "kid.json"
            for changes in ({"chain_reviewed": False, "max_power_dbm": -30},
                            {"chain_reviewed": True, "max_power_dbm": -40}):
                path.write_text(json.dumps({**p, "dut_kind": "kid", **changes}))
                with self.assertRaises(ValueError):
                    vna.load_profile(path)

    def native_fixture(self, path):
        (path / "run_info.txt").write_text("TIME_BASIS=VNA_LOCAL_UNVERIFIED\n")
        (path / "status.log").write_text("2026-11-10 13:00:01 RF_OFF_CONFIRMED\n2026-11-10 13:00:01 FINISHED 1\n")
        (path / "sweep_000001.csv").write_text("frequency_hz,s21_real,s21_imag\n1000000000,1,0\n4000000000,0,1\n")
        (path / "manifest.csv").write_text("filename,started_local,ended_local,points,start_hz,stop_hz,ifbw_hz,power_dbm,calibration\n"
           "sweep_000001.csv,2026-11-10 13:00:00,2026-11-10 13:00:01,2,1000000000,4000000000,1000,-30,UNCALIBRATED\n")

    def test_usb_import_is_read_only_and_preserves_local_clock(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "usb"
            source.mkdir()
            self.native_fixture(source)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            target = usb_import.import_usb(source, Path(root)/"out")
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
            self.assertEqual(before, after)
            self.assertTrue(report.inspect_run(target)["valid"])
            meta = json.loads((target / "sweep_000001.json").read_text())
            self.assertNotIn("started_utc", meta)
            self.assertEqual(meta["clock_basis"], "VNA_LOCAL_UNVERIFIED")
            self.assertIn("VNA_LOCAL_UNVERIFIED", report.create_report(target).read_text())

    def test_usb_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "usb"
            source.mkdir()
            self.native_fixture(source)
            manifest = source / "manifest.csv"
            manifest.write_text(manifest.read_text().replace("sweep_000001.csv", "../secret.csv"))
            with self.assertRaisesRegex(ValueError, "filename"):
                usb_import.import_usb(source, Path(root)/"out")


if __name__ == "__main__":
    unittest.main()
