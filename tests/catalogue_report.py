"""Check the catalogue report against a synthetic archive with supported, changed and missing modules."""
import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import struct
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalogue_report', REPO / 'tools' / 'catalogue_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)

# Minified shapes that hide braces and slashes from a naive scan.
STEADY = b'AAAA(e,t,n){let a="}{",b=`x${e?`{${t}}`:"}"}y`,c=/[/}]\\/{2}/g.test(a)?1/2:t/n/3;return{a,b,c}}'
EDITED = b'BBBB(e,t,n){let r=e++/2;if(r)/\\}/.exec(t);return function(){return{k:`}${r}`}}}'
REMOVED = b'CCCC(e,t,n){return 1}'


def archive(path, files):
    tree, body = {}, b''
    for name, data in files.items():
        tree[name] = {'size': len(data), 'offset': str(len(body))}
        body += data
    header = json.dumps({'files': {'webview': {'files': {'assets': {'files': {'async': {'files': tree}}}}}}}).encode()
    size = (len(header) + 7) & ~3
    path.write_bytes(struct.pack('<IIII', 4, size + 4, size, len(header)) + header + bytes(size - len(header) - 4) + body)


def entry(code, role):
    start = code[:code.index(b'{') + 1].decode()
    return hashlib.sha256(code).hexdigest(), {'id': start[:4], 'role': role, 'start': start, 'size': len(code)}


class CatalogueReport(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.catalogue = self.root / 'modules.json'
        self.catalogue.write_text(json.dumps(dict([entry(STEADY, 'row'), entry(EDITED, 'prefs'), entry(REMOVED, 'dependency')])))

    def run_report(self, chunk, *extra):
        archive(self.root / 'app.asar', {'chunk.js': b'(self.c=self.c||[]).push([[1],{' + chunk + b'}]);', 'notes.txt': b'AAAA(e,t,n){'})
        out = io.StringIO()
        with redirect_stdout(out):
            code = report.main([str(self.root / 'app.asar'), '--catalogue', str(self.catalogue), '--no-patch', '--json', *extra])
        return code, json.loads(out.getvalue())

    def test_every_module_supported(self):
        code, result = self.run_report(STEADY + b',' + EDITED + b',' + REMOVED)
        self.assertEqual(code, 0)
        self.assertEqual((result['supported'], result['changed'], result['missing']), (3, [], []))
        # The independent measurement finds the catalogued end of each reviewed module.
        self.assertEqual(result['scanner'], [3, 3])

    def test_changed_and_missing_modules(self):
        newer = EDITED.replace(b'e++/2', b'e++/2+`${n}}`.length')
        candidates = self.root / 'candidates.json'
        code, result = self.run_report(STEADY + b',' + newer, '--candidates', str(candidates))
        self.assertEqual(code, 1)
        self.assertEqual((result['supported'], result['scanner']), (1, [1, 1]))
        changed, = result['changed']
        self.assertEqual((changed['id'], changed['role'], changed['file']), ('BBBB', 'prefs', 'chunk.js'))
        self.assertEqual((changed['size'], changed['sha256']), (len(newer), hashlib.sha256(newer).hexdigest()))
        self.assertEqual([row['id'] for row in result['missing']], ['CCCC'])
        self.assertEqual(json.loads(candidates.read_text()),
                         {hashlib.sha256(newer).hexdigest(): {'id': 'BBBB', 'role': 'prefs', 'start': 'BBBB(e,t,n){', 'size': len(newer)}})

    def test_recording_and_installed_build_report(self):
        fixtures = self.root / 'fixtures'
        code, result = self.run_report(STEADY + b',' + EDITED + b',' + REMOVED, '--record', str(fixtures))
        # Dependencies are only fingerprinted. Adapters rewrite the other two.
        self.assertEqual((code, result['recorded']), (0, 2))
        index = json.loads((fixtures / 'index.json').read_text())
        self.assertEqual({row['role'] for row in index.values()}, {'row', 'prefs'})
        for name, row in index.items():
            self.assertEqual(hashlib.sha256((fixtures / name).read_bytes()).hexdigest(), row['sha256'])

        import sys
        from unittest.mock import patch
        sys.path.insert(0, str(REPO))
        import compatibility
        with patch.object(compatibility, 'installed_archive', return_value=(self.root / 'app.asar', '1.2', 'earlier reason', None)):
            status = compatibility.report()
        catalogued = {row['start'] for row in json.loads((REPO / 'renderer-modules.json').read_text()).values()}
        self.assertEqual((status['compatible'], status['supported'], status['patch']), (False, 0, 'not run'))
        self.assertEqual((len(status['missing']), status['recorded_failure']), (len(catalogued), 'earlier reason'))


if __name__ == '__main__':
    unittest.main()
