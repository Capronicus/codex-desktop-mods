"""Report which renderer modules of the installed desktop build the mods recognise."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def installed_archive():
    """Return the archive, the build label, a recorded launch failure and a scratch folder."""
    if sys.platform == 'win32':
        import windows_mods
        package = windows_mods.package()
        if not package:
            raise ValueError('Install the Windows ChatGPT desktop app with Codex from the Microsoft Store.')
        return package[0] / windows_mods.ASAR, package[1], None, None
    if not sys.platform.startswith('linux'):
        raise ValueError('Codex Desktop Mods supports Windows and Linux.')
    import manager
    metadata = manager.mod.SYSTEM / 'resources/linux-package-metadata.json'
    build = json.loads(metadata.read_text(encoding='utf-8')).get('version') if metadata.is_file() else None
    cache = manager.mod.CACHE if manager.mod.CACHE.is_dir() else None
    return manager.mod.ARCHIVE, build, manager.mod.read_state('unsupported.json').get('reason'), cache


def report():
    spec = importlib.util.spec_from_file_location('catalogue_report', ROOT / 'tools' / 'catalogue_report.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    path, build, recorded, scratch = installed_archive()
    if not path.is_file():
        raise ValueError('The desktop app archive was not found.')
    archive = tool.Archive(path)
    try:
        # The first supported desktop build is pinned by whole-file hashes instead.
        assets = archive.tree['files']['webview']['files']['assets']['files']
        if 'async' not in assets:
            return {'desktop_build': build, 'layout': 'pinned', 'recorded_failure': recorded}
        catalogue = json.loads((ROOT / 'renderer-modules.json').read_text(encoding='utf-8'))
        result = tool.inspect(archive, catalogue)
    finally:
        archive.close()
    fits = not result['changed'] and not result['missing']
    # With every module recognised, the remaining hooks are checked by the patch itself.
    patch = (tool.patch_error(path, scratch) or 'ok') if fits else 'not run'
    return {'desktop_build': build, 'layout': 'modules', 'compatible': fits and patch == 'ok',
            'modules': result['modules'], 'supported': result['supported'],
            'changed': [{k: row[k] for k in ('id', 'role', 'file') if k in row} for row in result['changed']],
            'missing': [{k: row[k] for k in ('id', 'role')} for row in result['missing']],
            'patch': patch, 'recorded_failure': recorded}
