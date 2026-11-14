import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from godov.backup import pack, unpack


class ArchiveTests(unittest.TestCase):
    def test_archive_roundtrip_checks_every_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / 'state.json').write_text('{"private": "value"}')
            (source / 'database.dump').write_bytes(b'pg-dump-test')
            archive = root / 'backup.tar.gz'
            pack(source, archive)
            unpack(archive, root / 'restore')
            self.assertEqual((root / 'restore/database.dump').read_bytes(), b'pg-dump-test')

    def test_path_traversal_and_links_are_rejected_before_extracting(self):
        for name, kind in (('../escape', tarfile.REGTYPE), ('/etc/passwd', tarfile.REGTYPE),
                           ('state.json', tarfile.SYMTYPE), ('C:/escape', tarfile.REGTYPE)):
            with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'evil.tar.gz'
                with tarfile.open(archive, 'w:gz') as tar:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    item.linkname = '/etc/shadow'
                    tar.addfile(item, io.BytesIO(b''))
                with self.assertRaises(ValueError):
                    unpack(archive, root / 'restore')
                self.assertFalse((root / 'escape').exists())

    def test_corrupt_or_missing_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'bad.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                for name, raw in [('state.json', b'changed'), ('manifest.json', json.dumps({'state.json': 'bad'}).encode())]:
                    member = tarfile.TarInfo(name)
                    member.size = len(raw)
                    tar.addfile(member, io.BytesIO(raw))
            with self.assertRaises(ValueError):
                unpack(archive, root / 'restore')


if __name__ == '__main__':
    unittest.main()
