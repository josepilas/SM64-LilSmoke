# SPDX-License-Identifier: MPL-2.0
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from native_build import extract_source


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def archive(self, entries):
        path = self.root / 'source.tar.gz'
        with tarfile.open(path, 'w:gz') as output:
            for name, data, kind in entries:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.size = len(data) if kind == tarfile.REGTYPE else 0
                if kind == tarfile.SYMTYPE:
                    item.linkname = '/etc/passwd'
                output.addfile(item, io.BytesIO(data) if item.isfile() else None)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def test_source_extracts_to_transaction_only(self):
        path, digest = self.archive([('src/test.c', b'/* fixture */', tarfile.REGTYPE)])
        work = self.root / 'work'
        extract_source(path, work, digest)
        self.assertEqual((work / 'src/test.c').read_bytes(), b'/* fixture */')

    def test_wrong_source_checksum(self):
        path, _ = self.archive([('test.c', b'', tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, 'checksum'):
            extract_source(path, self.root / 'work', '0' * 64)
        self.assertFalse((self.root / 'work').exists())

    def test_path_traversal(self):
        path, digest = self.archive([('../outside', b'bad', tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            extract_source(path, self.root / 'work', digest)
        self.assertFalse((self.root / 'outside').exists())

    def test_links_rejected(self):
        path, digest = self.archive([('linked', b'', tarfile.SYMTYPE)])
        with self.assertRaisesRegex(ValueError, 'links'):
            extract_source(path, self.root / 'work', digest)

    def test_case_collisions_rejected(self):
        path, digest = self.archive([('One.c', b'a', tarfile.REGTYPE), ('one.c', b'b', tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, 'duplicates'):
            extract_source(path, self.root / 'work', digest)


if __name__ == '__main__':
    unittest.main()
