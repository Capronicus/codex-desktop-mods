"""Run the renderer adapters over modules recorded from a desktop build.

Record them once per build with:
    python3 tools/catalogue_report.py <app.asar> --record tests/fixtures/renderer
The recordings are excerpts of the desktop app and stay out of the repository.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'renderer'
sys.path.insert(0, str(REPO))
import renderer_updates


class RecordedModules(unittest.TestCase):
    def test_adapters_fit_recorded_modules(self):
        index = FIXTURES / 'index.json'
        if not index.is_file():
            self.skipTest('No recorded modules. See the instructions at the top of this file.')
        catalogue = json.loads((REPO / 'renderer-modules.json').read_text(encoding='utf-8'))
        node = shutil.which('node')
        checked = 0
        for name, row in sorted(json.loads(index.read_text(encoding='utf-8')).items()):
            with self.subTest(module=name):
                source = (FIXTURES / name).read_bytes()
                sha = hashlib.sha256(source).hexdigest()
                self.assertEqual(sha, row['sha256'], 'The recording was edited.')
                if sha not in catalogue:
                    continue  # Recorded from a build the catalogue no longer lists.
                role = catalogue[sha]['role']
                adapted = renderer_updates.adapt(role, source.decode(), lambda f: (REPO / f).read_text(encoding='utf-8'),
                                                 {'codex': '/assets/codex-spritesheet-v1.0.webp'})
                self.assertNotEqual(adapted, source.decode())
                if node:
                    # A module factory is an object method. Wrap it to check that it still parses.
                    with tempfile.TemporaryDirectory() as tmp:
                        script = Path(tmp) / 'module.js'
                        script.write_text('({' + adapted + '});', encoding='utf-8')
                        result = subprocess.run([node, '--check', str(script)], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                checked += 1
        self.assertGreater(checked, 0, 'None of the recordings belong to a catalogued build.')


if __name__ == '__main__':
    unittest.main()
