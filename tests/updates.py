"""Exercise the application-menu launch path with disposable Linux packages."""
import importlib.util
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer', REPO / 'sidebar-colours.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def start_launcher():
    installer.launch(['codex://chat/example'])


class UpdateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        system = self.root / 'package'
        resources = system / 'resources'
        resources.mkdir(parents=True)
        (system / 'ChatGPT').write_text(
            '#!/bin/sh\nprintf "%s\\n" "$0" "$1" > "$CSC_TEST_LOG/launch-$$"\n')
        (system / 'ChatGPT').chmod(0o700)
        (resources / 'linux-package-metadata.json').write_text('{"version":"1"}')
        self.archive = resources / 'app.asar'
        self.archive.write_bytes(b'one')
        desktop = self.root / 'stock.desktop'
        desktop.write_text('[Desktop Entry]\nExec=chatgpt %U\n')

        def patch_archive(destination):
            contents = self.archive.read_bytes()
            with (self.root / 'builds').open('ab') as log:
                log.write(contents + b'\n')
            if contents == b'incompatible':
                raise ValueError('Unrecognised renderer')
            destination.write_bytes(b'mod:' + contents)

        for mocked in [
            patch.multiple(installer, SYSTEM=system, ARCHIVE=self.archive, CACHE=self.root/'cache',
                           INSTALL=self.root/'install', DESKTOP=self.root/'menu'/'chatgpt.desktop',
                           STOCK_DESKTOP=desktop, patch_archive=patch_archive),
            patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.root), CSC_TEST_LOG=str(self.root)),
            patch.object(installer.shutil, 'which', return_value=None),
        ]:
            mocked.start()
            self.addCleanup(mocked.stop)
        installer.install()

    def launches(self, count=1):
        processes = [multiprocessing.get_context('fork').Process(target=start_launcher) for _ in range(count)]
        for proc in processes:
            proc.start()
        results = []
        for proc in processes:
            proc.join(timeout=10)
            self.assertEqual(proc.exitcode, 0)
            path, arg = (self.root / f'launch-{proc.pid}').read_text().splitlines()
            self.assertEqual(arg, 'codex://chat/example')
            results.append(Path(path))
        return results

    def test_updates_and_recovery(self):
        original, = self.launches()
        self.archive.write_bytes(b'two')
        one, two = self.launches(2)
        self.assertEqual(one, two)
        self.assertNotEqual(one, original)
        self.assertEqual((one.parent/'resources/app.asar').read_bytes(), b'mod:two')
        self.assertEqual(self.archive.read_bytes(), b'two')
        self.assertEqual((self.root/'builds').read_text().splitlines(), ['one','two'])
        self.assertEqual(self.launches(), [one])

        self.archive.write_bytes(b'incompatible')
        self.assertEqual(self.launches(2), [installer.SYSTEM/'ChatGPT'] * 2)
        self.assertEqual((self.root/'builds').read_text().splitlines().count('incompatible'), 1)
        self.assertTrue(one.exists())
        self.assertTrue(installer.DESKTOP.is_file())

        self.archive.write_bytes(b'three')
        restored, = self.launches()
        self.assertNotEqual(restored, one)
        self.assertFalse((installer.INSTALL/'unsupported.json').exists())
        (restored.parent/'resources/app.asar').unlink()
        replacement, = self.launches()
        self.assertNotEqual(replacement, restored)
        (installer.INSTALL/'runtime.json').write_text('{')
        self.assertEqual(self.launches(), [replacement])

    def test_failed_rebuild_does_not_commit(self):
        meta = (installer.INSTALL/'runtime.json').read_bytes()
        self.archive.write_bytes(b'four')
        with patch.object(installer, 'patch_archive', side_effect=OSError('disk full')):
            self.assertEqual(self.launches(), [installer.SYSTEM/'ChatGPT'])
        self.assertEqual((installer.INSTALL/'runtime.json').read_bytes(), meta)
        self.assertFalse((installer.INSTALL/'unsupported.json').exists())
        self.assertNotEqual(self.launches(), [installer.SYSTEM/'ChatGPT'])


if __name__ == '__main__':
    unittest.main()
