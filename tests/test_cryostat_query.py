import base64
import contextlib
import io
import json
import socket
import subprocess
import threading
import unittest
from unittest.mock import patch

import cryostat_query as cryostat


UTC = "2026-01-01T00:00:00.000Z"
TEMPERATURES = ["2.9E+02", "NaN", "0"] + ["NaN"] * 13
SYNTHETIC_LINE = ",".join(TEMPERATURES + ["0", "1", "2", "3", "NaN", "0"])
WIRE = b"\xef\xbb\xbf" + SYNTHETIC_LINE.encode("ascii") + b"\r\n"


class CryostatQueryTests(unittest.TestCase):
    def test_local_socket_reads_one_line_without_waiting_for_eof(self):
        client, peer = socket.socketpair()
        request = []

        def synthetic_server():
            with peer.makefile("rb") as stream:
                request.append(stream.readline(64))
            peer.sendall(WIRE[:3])
            peer.sendall(WIRE[3:])

        with client, peer:
            peer.settimeout(2)
            worker = threading.Thread(target=synthetic_server)
            worker.start()
            try:
                with patch.object(cryostat.socket, "create_connection", return_value=client) as connect:
                    wire, timestamp = cryostat.query_local(2)
                connect.assert_called_once()
                self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 9527))
            finally:
                worker.join(3)
        self.assertEqual(request, [b"READ;ALL\r\n"])
        self.assertEqual(wire, WIRE)
        self.assertTrue(timestamp.endswith("Z"))

    def test_parses_missing_temperature_without_losing_original_or_valid_zeros(self):
        snapshot = cryostat.parse_snapshot(WIRE, UTC)
        self.assertEqual(snapshot["raw_response"], SYNTHETIC_LINE)
        self.assertEqual(snapshot["temperature_k"]["T1"], 290)
        self.assertIsNone(snapshot["temperature_k"]["T2"])
        self.assertIsNone(snapshot["temperature_k"]["T3"])
        self.assertEqual(snapshot["pressure_mbar"]["G1"], 0)
        self.assertIsNone(snapshot["pressure_mbar"]["G5"])
        self.assertEqual(snapshot["flow_l_min"], 0)

    def test_acknowledgement_and_partial_line_are_query_failures(self):
        with self.assertRaisesRegex(ValueError, "Expected 22 telemetry fields"):
            cryostat.parse_snapshot(b"OK\r\n", UTC)
        with self.assertRaisesRegex(ValueError, "complete cryostat response"):
            cryostat.parse_snapshot(WIRE[:-1], UTC)

    def test_ssh_cli_outputs_json_for_the_remote_snapshot(self):
        envelope = json.dumps({"wire": base64.b64encode(WIRE).decode(), "received_at_utc": UTC}).encode()
        result = subprocess.CompletedProcess([], 0, stdout=envelope, stderr=b"")
        output = io.StringIO()
        with patch.object(cryostat.subprocess, "run", return_value=result) as run, contextlib.redirect_stdout(output):
            status = cryostat.main(["--ssh", "coolab-ipc", "--json"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["received_at_utc"], UTC)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--") + 1:][:2], ["coolab-ipc", "powershell.exe"])
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)

    def test_query_timeout_emits_no_snapshot_and_does_not_retry(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(cryostat, "query_local", side_effect=TimeoutError("deadline")) as query:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                status = cryostat.main(["--timeout", "1", "--json"])
        query.assert_called_once_with(1)
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("deadline", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
