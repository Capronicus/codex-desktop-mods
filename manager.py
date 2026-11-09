"""Manage the upstream Linux patch without touching a running desktop's files."""
import contextlib
import importlib
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
mod = importlib.import_module('sidebar-colours')


def launcher_kind():
    if not mod.DESKTOP.is_file():
        return 'stock'
    return 'mod' if mod.MARKER in mod.DESKTOP.read_text().splitlines() else 'custom'


def status():
    launcher = launcher_kind()
    metadata = mod.SYSTEM / 'resources/linux-package-metadata.json'
    build = json.loads(metadata.read_text()).get('version') if metadata.is_file() else None
    result = {'desktop_build': build, 'menu_launcher': launcher}
    if not (mod.SYSTEM / 'ChatGPT').is_file() or not mod.ARCHIVE.is_file():
        state = 'desktop_package_missing'
    elif launcher == 'custom':
        state = 'personal_launcher_in_use'
    elif launcher == 'stock':
        state = 'mod_files_present' if mod.INSTALL.exists() or mod.CACHE.exists() else 'not_installed'
    else:
        signature = mod.signature()
        revision = mod.revision_for(signature)
        failure = mod.read_state('unsupported.json')
        record = mod.read_state('runtime.json')
        runtime = Path(record.get('path') or '.') if isinstance(record.get('path'), str) else None
        launcher_files_match = all(
            (mod.INSTALL / name).is_file()
            and (mod.INSTALL / name).read_bytes() == (mod.SOURCE / name).read_bytes()
            for name in mod.MOD_FILES
        )
        if failure.get('revision') == revision and launcher_files_match:
            state = 'incompatible_build'
            result['reason'] = failure.get('reason')
        elif (launcher_files_match and record.get('system') == signature and runtime
              and runtime.parent == mod.CACHE and runtime.name.startswith('runtime-' + revision)
              and mod.valid_runtime(runtime)):
            state = 'ready'
        else:
            state = 'needs_refresh'
    return dict(result, patch_state=state)


def cache_in_use():
    """Check the app and its helpers, including sandboxed renderers with hidden exe links."""
    cache = mod.CACHE.resolve()
    try:
        for proc in Path('/proc').iterdir():
            if not proc.name.isdecimal():
                continue
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                try:
                    exe = (proc / 'exe').readlink()
                except PermissionError:
                    if (proc / 'comm').read_text().strip().casefold() != 'chatgpt':
                        continue
                    with (proc / 'cmdline').open('rb') as stream:
                        first = stream.read(4096).split(b'\0', 1)[0]
                    # Chromium can overwrite argv[0] with its full command line.
                    if not first.startswith(b'/') or first.startswith(os.fsencode(cache) + b'/'):
                        return True
                    continue
                if cache in exe.parents:
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue
    except OSError:
        return True
    return False


def apply():
    with mod.locked():
        if status()['patch_state'] == 'ready':
            return 'The installed patch matches this plugin and the current desktop build.'
        with contextlib.redirect_stdout(io.StringIO()) as log:
            mod.install()
        return log.getvalue().strip()


def remove():
    with mod.locked():
        if launcher_kind() == 'mod':
            mod.DESKTOP.unlink()
        if mod.CACHE.is_dir() and cache_in_use():
            return ('The mod launcher is disabled. Fully quit ChatGPT and reopen it from the '
                    'applications menu. Then run Remove desktop mods again to clean up the cached files.')
        with contextlib.redirect_stdout(io.StringIO()):
            mod.uninstall()
        return 'Removed the patch files and cached app. Saved colour preferences remain in the desktop profile.'
