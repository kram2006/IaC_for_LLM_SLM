import io
import os
import stat
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.setup_official_metrics import safe_extract_tar, safe_extract_zip


class TestSafeArchiveExtraction(unittest.TestCase):
    def test_safe_extract_tar_allows_regular_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                content = b"ok"
                info = tarfile.TarInfo(name="nested/file.txt")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            buf.seek(0)

            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                safe_extract_tar(tar, tmpdir)

            self.assertTrue(Path(tmpdir, "nested", "file.txt").exists())

    def test_safe_extract_tar_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                content = b"bad"
                info = tarfile.TarInfo(name="../escape.txt")
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            buf.seek(0)

            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                with self.assertRaises(ValueError):
                    safe_extract_tar(tar, tmpdir)

    def test_safe_extract_tar_blocks_symlink(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                link = tarfile.TarInfo(name="link")
                link.type = tarfile.SYMTYPE
                link.linkname = "/etc/passwd"
                tar.addfile(link)
            buf.seek(0)

            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                with self.assertRaises(ValueError):
                    safe_extract_tar(tar, tmpdir)

    def test_safe_extract_zip_allows_regular_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w") as zf:
                zf.writestr("nested/file.txt", "ok")
            buf.seek(0)

            with zipfile.ZipFile(buf, mode="r") as zf:
                safe_extract_zip(zf, tmpdir)

            self.assertTrue(Path(tmpdir, "nested", "file.txt").exists())

    def test_safe_extract_zip_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w") as zf:
                zf.writestr("../escape.txt", "bad")
            buf.seek(0)

            with zipfile.ZipFile(buf, mode="r") as zf:
                with self.assertRaises(ValueError):
                    safe_extract_zip(zf, tmpdir)

    def test_safe_extract_zip_blocks_symlink(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, mode="w") as zf:
                info = zipfile.ZipInfo("link")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, "/etc/passwd")
            buf.seek(0)

            with zipfile.ZipFile(buf, mode="r") as zf:
                with self.assertRaises(ValueError):
                    safe_extract_zip(zf, tmpdir)


if __name__ == "__main__":
    unittest.main()
