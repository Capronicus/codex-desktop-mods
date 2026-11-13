#!/usr/bin/env python3
"""Compare a desktop build's renderer modules with the reviewed fingerprint catalogue.

    python3 tools/catalogue_report.py /usr/lib/chatgpt/resources/app.asar
    python3 tools/catalogue_report.py app.asar --candidates new-modules.json
    python3 tools/catalogue_report.py app.asar --record tests/fixtures/renderer

Each catalogued module is reported as supported, changed or missing. A changed
module is measured again and written as a candidate entry for review. The last
line repeats the real patch on a scratch copy, so its error names the first
hook that no longer fits.

--record saves each supported module that an adapter rewrites, so
tests/renderer_fixtures.py can run the adapters without the desktop app. Those
files are excerpts of the desktop app. Keep them on your own computer.
"""
import argparse
from collections import defaultdict
import hashlib
import importlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# A slash after one of these starts a regular expression rather than a division.
BEFORE_REGEX = b'(,=:[!&|?{};+-*%<>~^'
KEYWORDS = {b'return', b'typeof', b'case', b'in', b'of', b'do', b'else', b'void', b'delete',
            b'throw', b'new', b'instanceof', b'yield', b'await'}
WORD = re.compile(rb'[\w$]+')
SPACE = b' \t\r\n'


class Archive:
    def __init__(self, path):
        self.stream = open(path, 'rb')
        size, pickle_size, _, json_size = struct.unpack('<IIII', self.stream.read(16))
        if size != 4:
            raise ValueError('Unknown app archive format')
        self.tree = json.loads(self.stream.read(json_size))
        self.start = 8 + pickle_size

    def read(self, entry):
        if entry.get('unpacked') or 'offset' not in entry:
            raise ValueError('Expected a packed app component')
        self.stream.seek(self.start + int(entry['offset']))
        data = self.stream.read(entry['size'])
        if len(data) != entry['size']:
            raise ValueError('Incomplete desktop archive')
        return data

    def close(self):
        self.stream.close()


def factory_end(data, brace):
    """Return the offset after the brace matching data[brace], reading minified JavaScript."""
    depth, i, size = 0, brace, len(data)
    resume = []            # Brace depths at which a template expression returns to its literal.
    headers = []           # For each open parenthesis: does it close an if, while, for or with header?
    before, word = b'', b''  # The last two significant bytes, and the last identifier or keyword.

    def literal(at):
        while at < size:
            ch = data[at]
            if ch == 0x5c:
                at += 2
            elif ch == 0x60:
                return at + 1, False
            elif ch == 0x24 and data[at + 1:at + 2] == b'{':
                return at + 2, True
            else:
                at += 1
        raise ValueError('Unterminated template literal')

    while i < size:
        ch = data[i:i + 1]
        if ch in SPACE:
            i += 1
        elif ch in b'"\'':
            i += 1
            while i < size and data[i:i + 1] != ch:
                i += 2 if data[i] == 0x5c else 1
            i += 1
            before, word = b'"', b''
        elif ch == b'`':
            i, opened = literal(i + 1)
            if opened:
                resume.append(depth)
                depth += 1
            before, word = (b'{', b'') if opened else (b'`', b'')
        elif ch == b'{':
            depth += 1
            i += 1
            before, word = b'{', b''
        elif ch == b'}':
            depth -= 1
            i += 1
            if resume and resume[-1] == depth:
                resume.pop()
                i, opened = literal(i)
                if opened:
                    resume.append(depth)
                    depth += 1
                before, word = (b'{', b'') if opened else (b'`', b'')
            elif depth == 0:
                return i
            else:
                before, word = b'}', b''
        elif ch == b'(':
            headers.append(word in (b'if', b'while', b'for', b'with'))
            i += 1
            before, word = b'(', b''
        elif ch == b')':
            # A statement follows a control header, so a slash there starts a regular expression.
            before, word = (b';' if headers and headers.pop() else b')'), b''
            i += 1
        elif ch == b'/' and data[i + 1:i + 2] == b'/':
            i = data.find(b'\n', i)
            i = size if i < 0 else i
        elif ch == b'/' and data[i + 1:i + 2] == b'*':
            i = data.find(b'*/', i + 2)
            if i < 0:
                raise ValueError('Unterminated comment')
            i += 2
        elif ch == b'/' and (word in KEYWORDS or (not word and before[-1:] in BEFORE_REGEX
                                                 and before not in (b'++', b'--')) or not before):
            i += 1
            in_class = False
            while i < size:
                c = data[i]
                if c == 0x5c:
                    i += 1
                elif c == 0x0a:
                    raise ValueError('Unterminated regular expression')
                elif c == 0x5b:
                    in_class = True
                elif c == 0x5d:
                    in_class = False
                elif c == 0x2f and not in_class:
                    break
                i += 1
            i += 1
            before, word = b'/', b'value'
        else:
            match = WORD.match(data, i)
            if match:
                i = match.end()
                before, word = b'a', match[0]
            else:
                i += 1
                before, word = (before + ch)[-2:], b''
    raise ValueError('Unterminated module')


def inspect(archive, catalogue, record=None):
    """Classify every catalogued module start found in the build's async chunks."""
    starts = defaultdict(list)
    for sha, info in catalogue.items():
        starts[info['start'].encode()].append((sha, info))
    pattern = re.compile(rb'(?<=[{,])(?:' + b'|'.join(re.escape(s) for s in starts) + rb')')
    directory = archive.tree['files']['webview']['files']['assets']['files']['async']['files']
    supported, changed = {}, []
    measured = agreed = 0
    for name, entry in sorted(directory.items()):
        if not name.endswith('.js') or entry.get('unpacked'):
            continue
        data = archive.read(entry)
        for match in pattern.finditer(data):
            start = match[0]
            known = next((sha for sha, info in starts[start]
                          if hashlib.sha256(data[match.start():match.start() + info['size']]).hexdigest() == sha), None)
            try:
                end = factory_end(data, match.end() - 1)
            except ValueError:
                end = None
            if known:
                supported[start] = known
                measured += 1
                agreed += end == match.start() + catalogue[known]['size']
                if record is not None and catalogue[known]['role'] != 'dependency':
                    record[known] = data[match.start():match.start() + catalogue[known]['size']]
                continue
            sample = starts[start][0][1]
            row = {'id': sample['id'], 'role': sample['role'], 'start': sample['start'], 'file': name,
                   'known_sizes': sorted(info['size'] for _, info in starts[start])}
            if end is not None:
                row.update(size=end - match.start(), sha256=hashlib.sha256(data[match.start():end]).hexdigest())
            changed.append(row)
    seen = set(supported) | {row['start'].encode() for row in changed}
    missing = [{'id': rows[0][1]['id'], 'role': rows[0][1]['role'], 'start': rows[0][1]['start']}
               for start, rows in sorted(starts.items()) if start not in seen]
    return {'modules': len(starts), 'supported': len(supported), 'changed': changed, 'missing': missing,
            'scanner': [agreed, measured]}


def save_fixtures(directory, modules, catalogue):
    directory.mkdir(parents=True, exist_ok=True)
    index = {}
    for sha, source in sorted(modules.items()):
        name = catalogue[sha]['role'] + '-' + sha[:12] + '.js'
        (directory / name).write_bytes(source)
        index[name] = {'sha256': sha, 'role': catalogue[sha]['role'], 'id': catalogue[sha]['id']}
    saved = json.loads((directory / 'index.json').read_text(encoding='utf-8')) if (directory / 'index.json').is_file() else {}
    (directory / 'index.json').write_text(json.dumps({**saved, **index}, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    return len(index)


def patch_error(path, scratch=None):
    """Run the real patcher against a scratch copy. None means the build is supported."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    patcher = importlib.import_module('sidebar-colours')
    with tempfile.TemporaryDirectory(dir=scratch) as tmp:
        try:
            patcher.patch_archive(Path(tmp) / 'app.asar', archive=Path(path))
        except Exception as error:
            return f'{type(error).__name__}: {error}'
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('archive', type=Path, help='the desktop build\'s resources/app.asar')
    parser.add_argument('--catalogue', type=Path, default=ROOT / 'renderer-modules.json')
    parser.add_argument('--candidates', type=Path, help='write changed modules as catalogue entries for review')
    parser.add_argument('--record', type=Path, help='save supported adapter modules as local test fixtures')
    parser.add_argument('--json', action='store_true', help='print the report as JSON')
    parser.add_argument('--no-patch', action='store_true', help='skip the patch run on a scratch copy')
    args = parser.parse_args(argv)
    catalogue = json.loads(args.catalogue.read_text(encoding='utf-8'))
    archive = Archive(args.archive)
    recorded = {} if args.record else None
    try:
        report = inspect(archive, catalogue, recorded)
    finally:
        archive.close()
    if args.record:
        report['recorded'] = save_fixtures(args.record, recorded, catalogue)
    report['patch'] = 'skipped' if args.no_patch else patch_error(args.archive) or 'ok'
    candidates = {row['sha256']: {key: row[key] for key in ('id', 'role', 'start', 'size')}
                  for row in report['changed'] if 'sha256' in row}
    if args.candidates:
        args.candidates.write_text(json.dumps(candidates, indent=1) + '\n', encoding='utf-8')
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        agreed, measured = report['scanner']
        print(f"Renderer modules: {report['supported']} supported, {len(report['changed'])} changed, "
              f"{len(report['missing'])} missing, of {report['modules']} catalogued")
        print(f'Measurement check: {agreed} of {measured} supported modules measured to their catalogued size')
        for row in report['changed']:
            detail = (f"size {row['size']} (catalogued {', '.join(map(str, row['known_sizes']))})  sha256 {row['sha256']}"
                      if 'sha256' in row else 'could not be measured')
            print(f"  changed  {row['id']:<8} {row['role']:<13} {row['file']}  {detail}")
        for row in report['missing']:
            print(f"  missing  {row['id']:<8} {row['role']:<13} not present under this module ID")
        if agreed != measured:
            print('  The measurement disagrees with a reviewed module. Check candidate sizes by hand.')
        print('Patch on a scratch copy: ' + report['patch'])
        if args.record:
            print(f"Saved {report['recorded']} modules to {args.record} for tests/renderer_fixtures.py.")
        if args.candidates:
            print(f'Wrote {len(candidates)} candidate entries to {args.candidates}. Review each adapter before adding them.')
    return 0 if not report['changed'] and not report['missing'] and report['patch'] in ('ok', 'skipped') else 1


if __name__ == '__main__':
    sys.exit(main())
