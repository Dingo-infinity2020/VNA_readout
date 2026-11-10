"""E5071C definite-block file transfer. Writes restricted to D:/KID_DATA/."""
from pathlib import PureWindowsPath
import socket
import time

from vna import SCPI


class VNAFiles(SCPI):
    @staticmethod
    def safe_path(path):
        p = PureWindowsPath(path)
        if not p.is_absolute() or p.drive.upper() != "D:" or len(p.parts) < 3 or p.parts[1].upper() != "KID_DATA":
            raise ValueError("File operations restricted to D:/KID_DATA/")
        if any(x in ("..", ".") for x in p.parts) or any(x in path for x in ('"', '\n', '\r')):
            raise ValueError("Invalid remote path")
        return p.as_posix()

    def put_file(self, path, data):
        if not self.usable:
            raise RuntimeError("Connection is unusable; no retry")
        path = self.safe_path(path)
        if not 0 < len(data) <= 100000:
            raise ValueError("E5071C LAN transfer must be 1–100000 bytes")
        size = str(len(data)).encode("ascii")
        command = f':MMEM:TRAN "{path}",'.encode("ascii")
        # The instrument rejects existing destinations; never delete/retry here.
        try:
            self.socket.sendall(command + b"#" + str(len(size)).encode() + size + data + b"\n")
        except BaseException:
            self.usable = False
            raise
        self.assert_no_errors()

    def read_file(self, path):
        path = self.safe_path(path)
        self.write(f':MMEM:TRAN? "{path}"')
        deadline = time.monotonic() + self.timeout
        def read(count):
            while len(self.buffer) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("File transfer deadline exceeded")
                self.socket.settimeout(remaining)
                chunk = self.socket.recv(65536)
                if not chunk:
                    raise ConnectionError("Incomplete file transfer")
                self.buffer.extend(chunk)
            result = bytes(self.buffer[:count])
            del self.buffer[:count]
            return result
        try:
            if read(1) != b"#":
                raise ValueError("Expected definite-length binary block")
            digits = int(read(1))
            if not 1 <= digits <= 9:
                raise ValueError("Indefinite blocks are not supported")
            size = int(read(digits))
            if size > 100000:
                raise ValueError("Remote file exceeds 100000-byte LAN limit")
            data = read(size)
            ending = read(1)
            if ending == b"\r":
                ending = read(1)
            if ending != b"\n":
                raise ValueError("Invalid block terminator")
            return data
        except BaseException:
            self.usable = False
            raise
