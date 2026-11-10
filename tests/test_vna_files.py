import socket
import threading
import unittest
from unittest.mock import Mock

from vna_files import VNAFiles


class FileTests(unittest.TestCase):
    def test_paths_restricted_to_user_area(self):
        for path in ("C:/file", "E:/SysCal/file", "D:/VBA/file", "D:KID_DATA/file", "D:/KID_DATA/../outside", 'D:/KID_DATA/evil"name'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                VNAFiles.safe_path(path)
        self.assertEqual(VNAFiles.safe_path("D:\\KID_DATA\\test.bas"), "D:/KID_DATA/test.bas")

    def test_file_write_framing(self):
        c = VNAFiles.__new__(VNAFiles)
        c.socket, c.usable = Mock(), True
        c.assert_no_errors = Mock()
        c.put_file("D:/KID_DATA/test.txt", b"a\nb\x00")
        self.assertEqual(c.socket.sendall.call_args.args[0], b':MMEM:TRAN "D:/KID_DATA/test.txt",#14a\nb\x00\n')

    def test_no_repeat_after_write_failure(self):
        c = VNAFiles.__new__(VNAFiles)
        c.socket, c.usable = Mock(), True
        c.socket.sendall.side_effect = ConnectionError("broken")
        with self.assertRaises(ConnectionError):
            c.put_file("D:/KID_DATA/test.txt", b"x")
        with self.assertRaises(RuntimeError):
            c.put_file("D:/KID_DATA/test.txt", b"x")
        self.assertEqual(c.socket.sendall.call_count, 1)

    def test_fragmented_binary_payload(self):
        local, peer = socket.socketpair()
        c = VNAFiles.__new__(VNAFiles)
        c.socket, c.usable, c.buffer, c.timeout = local, True, bytearray(), 1
        def serve():
            with peer:
                peer.recv(4096)
                for fragment in (b"#", b"1", b"5", b"a\n", b"b\x00c", b"\n"):
                    peer.sendall(fragment)
        thread = threading.Thread(target=serve)
        thread.start()
        try:
            self.assertEqual(c.read_file("D:/KID_DATA/test.bin"), b"a\nb\x00c")
            self.assertEqual(c.buffer, bytearray())
        finally:
            local.close()
            thread.join(2)

    def test_empty_and_large_upload_rejected(self):
        c = VNAFiles.__new__(VNAFiles)
        c.usable = True
        for data in (b"", b"x"*100001):
            with self.assertRaises(ValueError):
                c.put_file("D:/KID_DATA/test.bin", data)


if __name__ == "__main__":
    unittest.main()
