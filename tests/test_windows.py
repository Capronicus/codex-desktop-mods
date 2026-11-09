"""Exercise Windows staging, archive binding, updates and fallback with synthetic packages."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_mods as mods


def archive(path, value):
    data = json.dumps({'files': {}, 'fixture': value}).encode()
    size = (len(data) + 7) & ~3
    path.write_bytes(struct.pack('<IIII', 4, size + 4, size, len(data)) + data + bytes(size - len(data) - 4))


class WindowsModsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = self.root / 'Program Files (fixture)' / 'app'
        (self.app / 'resources').mkdir(parents=True)
        (self.app / 'resources' / 'helper.exe').write_bytes(b'test helper')
        self.cache = self.root / 'local data'
        self.executable = self.app / mods.EXE
        self.write_package('stock')

    def write_package(self, value):
        archive(self.app / mods.ASAR, value)
        h = mods.asar_hash(self.app / mods.ASAR)
        binding = json.dumps([{'file':r'resources\app.asar', 'alg':'SHA256','value':h}], separators=(',',':'))
        self.executable.write_bytes(b'MZfixture' + binding.encode())

    def test_update_and_original(self):
        original = self.executable.read_bytes()
        with patch.object(mods.patcher, 'patch_archive', side_effect=lambda dest,**kw: archive(dest, 'patched')) as mod:
            first = mods.runtime(self.app, '1', self.cache)
            self.assertEqual(first, mods.runtime(self.app, '1', self.cache))
            self.assertEqual(mod.call_count, 1)
            self.assertEqual(self.executable.read_bytes(), original)
            self.assertIn(mods.asar_hash(first/mods.ASAR).encode(), (first/mods.EXE).read_bytes())
            self.assertEqual((first/'resources/helper.exe').read_bytes(), b'test helper')
            self.write_package('updated')
            second = mods.runtime(self.app, '2', self.cache)
            self.assertNotEqual(first, second)
            self.assertTrue((first/'.complete').is_file())
        with patch.object(mods,'running_copy',return_value=True):
            self.assertFalse(mods.prune(self.cache, second))
        with patch.object(mods,'running_copy',return_value=False):
            self.assertTrue(mods.prune(self.cache, second))
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())

    def test_mismatched_binding_does_not_activate(self):
        wrong = self.executable.read_bytes().replace(mods.asar_hash(self.app/mods.ASAR).encode(), b'0'*64)
        self.executable.write_bytes(wrong)
        with patch.object(mods.patcher, 'patch_archive', side_effect=lambda dest,**kw: archive(dest,'patched')):
            with self.assertRaisesRegex(ValueError,'do not match'):
                mods.runtime(self.app,'1',self.cache)
        self.assertFalse(list(self.cache.glob('runtimes/*')))
        self.assertEqual(wrong,self.executable.read_bytes())

    @unittest.skipUnless(sys.platform == 'win32', 'Uses the native Windows Shell')
    def test_windows_shortcut_and_removal(self):
        link = self.root / 'Start menu' / 'ChatGPT (Desktop Mods).lnk'
        with patch.object(mods,'shortcut',return_value=link), patch.object(mods,'data_dir',return_value=self.cache), \
             patch.object(mods,'package',return_value=(self.app,'1')), \
             patch.object(mods.patcher,'patch_archive',side_effect=lambda dest,**kw: archive(dest,'patched')):
            mods.apply()
            self.assertEqual(mods.status()['patch_state'],'ready')
            args = mods.powershell("((New-Object -ComObject Wscript.Shell).CreateShortcut($env:MOD_LINK)).Arguments",MOD_LINK=link)
            self.assertIn('windows_mods.py',args)
            with patch.object(mods,'start_packaged') as start:
                mods.launch()
                self.assertTrue(Path(start.call_args.args[0]).is_file())
            mods.remove()
            self.assertFalse(link.exists())
            self.assertTrue(self.executable.exists())

if __name__ == '__main__':
    unittest.main()
