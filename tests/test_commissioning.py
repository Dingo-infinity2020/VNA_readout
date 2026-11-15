import contextlib
import csv
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

import commissioning as c
import vna


class CommissioningTests(unittest.TestCase):
    def xlsx(self, path, *, formula=False, shared=False):
        headers = c.XLSX_COLUMNS
        values = ["KID-001", 1300000000, 1299999000, -1000, 36000, 37000,
                  36000, "模型验收通过", 1]
        cells = []
        for row, items in ((1, headers), (2, values)):
            for column, value in zip("ABCDEFGHI", items):
                if isinstance(value, str):
                    if shared and row == 1:
                        body = f'<c r="{column}{row}" t="s"><v>{headers.index(value)}</v></c>'
                    else:
                        body = f'<c r="{column}{row}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                else:
                    body = f'<c r="{column}{row}"><v>{value}</v></c>'
                if formula and row == 2 and column == "C":
                    body = '<c r="C2"><f>B2-1000</f><v>1299999000</v></c>'
                cells.append((row, body))
        ns = c.NS["s"]
        sheet = '<worksheet xmlns="' + ns + '"><sheetData>'
        for row in (1, 2):
            sheet += f'<row r="{row}">' + "".join(b for r, b in cells if r == row) + '</row>'
        sheet += '</sheetData></worksheet>'
        with ZipFile(path, "w") as archive:
            archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{c.REL}"><sheets><sheet name="结果表" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>')
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
            if shared:
                archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">' + ''.join(f'<si><t>{escape(s)}</t></si>' for s in headers) + '</sst>')

    def test_xlsx_units_q_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "Qc3600.xlsx"
            self.xlsx(p)
            before = p.read_bytes()
            rows, source = c.read_design(p)
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(source["sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(rows[0]["q_target"], 36000)
            self.assertEqual(rows[0]["f_target_hz"], 1.3e9)
            self.assertEqual(rows[0]["f_error_hz"], -1000)
            self.assertEqual(rows[0]["source_row"], 2)
            self.assertIn("NOT_MEASURED_QL", source["q_semantics"])

    def test_shared_strings_and_formula_rejection(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "test.xlsx"
            self.xlsx(p, shared=True)
            self.assertEqual(c.read_design(p)[0][0]["pixel_id"], "KID-001")
            self.xlsx(p, formula=True)
            with self.assertRaisesRegex(ValueError, "formula"):
                c.read_design(p)

    def test_csv_duplicate_and_wrong_units_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "test.csv"
            p.write_text("pixel_id,f_target_hz\nA,1300000000\nA,1400000000\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                c.read_design(p)
            p.write_text("pixel_id,f_target_hz,f_target_hz\nA,1300000000,1400000000\n")
            with self.assertRaisesRegex(ValueError, "Duplicate CSV column"):
                c.read_design(p)
            p.write_text("pixel_id,f_target_hz\nA,1.3\n")
            rows, _ = c.read_design(p)
            with self.assertRaisesRegex(ValueError, "1–4 GHz"):
                c.make_plan(rows)

    def test_search_coverage_resolution_and_no_gap_across_segments(self):
        rows, _ = c.read_design(vna.ROOT / "configs/design.example.csv")
        p = c.make_plan(rows, ql_upper=100000, samples_per_linewidth=8)
        self.assertEqual(len(p["bands_hz"]), 2)
        for band_id, (lo, hi) in enumerate(p["bands_hz"], 1):
            segments = [s for s in p["segments"] if s["band_id"] == band_id]
            self.assertEqual(segments[0]["start_hz"], lo)
            self.assertEqual(segments[-1]["stop_hz"], hi)
            for left, right in zip(segments, segments[1:]):
                self.assertLess(right["start_hz"], left["stop_hz"])
            for s in segments:
                self.assertLessEqual(s["step_hz"], s["start_hz"] / 100000 / 8 * (1 + 1e-12))
                self.assertIsNone(s["power_dbm"])

    def test_design_q_does_not_control_loaded_q_assumption(self):
        rows, _ = c.read_design(vna.ROOT / "configs/design.example.csv")
        p = c.make_plan(rows)
        for row in rows:
            row["q_target"], row["q_model"] = 3600, 3600
        self.assertEqual(c.make_plan(rows), p)
        self.assertIsNone(p["summary"]["estimated_search_seconds"])
        self.assertEqual(c.make_plan(rows, seconds_per_segment=2)["summary"]["estimated_search_seconds"],
                         2 * p["summary"]["segment_count"])

    def test_invalid_planning_assumptions_and_range(self):
        rows, _ = c.read_design(vna.ROOT / "configs/design.example.csv")
        for options in ({"margin_pct": -1}, {"ql_upper": float("nan")},
                        {"overlap": 1}, {"points": 1602}, {"points": True},
                        {"samples_per_linewidth": 0}, {"seconds_per_segment": 0}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                c.make_plan(rows, **options)
        rows[0]["f_model_hz"] = 1e9
        with self.assertRaisesRegex(ValueError, "outside"):
            c.make_plan(rows)

    def test_zero_margin_has_finite_zoom_window(self):
        rows, _ = c.read_design(vna.ROOT / "configs/design.example.csv")
        plan = c.make_plan(rows[:1], margin_pct=0)
        self.assertGreater(plan["segments"][0]["stop_hz"], plan["segments"][0]["start_hz"])

    def test_site_missing_items_remain_until_all_completed(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "site.local.json"
            p.write_text(json.dumps({"sample": "A", "input_loss_db": 0, "chain_reviewed": True}))
            review = c.site_review(p)
            self.assertNotIn("input_loss_db", review["missing"])
            self.assertNotIn("sample", review["missing"])
            self.assertIn("output_gain_db", review["missing"])
            self.assertIn("design_q_kind", review["missing"])
            p.write_text(json.dumps({"source_power_dbm": -30, "max_source_power_dbm": -40}))
            with self.assertRaisesRegex(ValueError, "exceeds"):
                c.site_review(p)

    def test_prepare_completion_hashes_roundtrip_and_no_network(self):
        with tempfile.TemporaryDirectory() as root, patch("socket.create_connection", side_effect=AssertionError("network forbidden")):
            directory = c.prepare(vna.ROOT / "configs/design.example.csv", root)
            done = json.loads((directory / "PREP_COMPLETE.json").read_text())
            for name, digest in done["sha256"].items():
                self.assertEqual(hashlib.sha256((directory / name).read_bytes()).hexdigest(), digest)
            rows, _ = c.read_design(directory / "design.csv")
            self.assertEqual(len(rows), 3)
            with (directory / "search_segments.csv").open(encoding="utf-8-sig") as f:
                self.assertTrue(all(row["power_dbm"] == "" for row in csv.DictReader(f)))
            with self.assertRaises(ValueError):
                vna.load_profile(directory / "plan.json")
            self.assertNotEqual(directory, c.prepare(directory / "design.csv", root))

    def test_invalid_source_leaves_no_preparation_folder(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "bad.csv"
            p.write_text("pixel_id,f_target_hz\nA,nan\n")
            with self.assertRaises(ValueError):
                c.prepare(p, root)
            self.assertEqual(list(Path(root).glob("PREP_*")), [])

    def test_cli_and_menu_offline_entry(self):
        with tempfile.TemporaryDirectory() as root, patch("socket.create_connection", side_effect=AssertionError("network forbidden")), contextlib.redirect_stdout(io.StringIO()):
            with patch("sys.argv", ["vna.py", "prepare", str(vna.ROOT / "configs/design.example.csv"), "--out", root]):
                self.assertEqual(vna.main(), 0)
            from menu import menu
            with patch("builtins.input", side_effect=["8", str(vna.ROOT / "configs/design.example.csv"), "", "0"]), patch.object(c, "prepare", return_value=Path(root)) as prepare:
                menu()
                prepare.assert_called_once()


if __name__ == "__main__":
    unittest.main()
