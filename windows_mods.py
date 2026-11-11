"""Install the mods in a per-user copy of the Windows ChatGPT desktop app."""
import argparse
import errno
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time

patcher = importlib.import_module('sidebar-colours')
SOURCE = Path(__file__).resolve().parent
FILES = (*patcher.MOD_FILES, 'windows_mods.py')
FAMILY = 'OpenAI.Codex_2p2nqsd0c76g0'
MARKER = 'Capronicus Codex Desktop Mods launcher'
EXE = 'ChatGPT.exe'
ASAR = Path('resources') / 'app.asar'
REV = re.compile(r'[0-9a-f]{64}')
# Electron stores the ASAR header hash in the INTEGRITY/ELECTRONASAR PE resource.
INTEGRITY = re.compile(rb'\[\{"file":"resources\\\\app\.asar","alg":"SHA256","value":"([0-9a-fA-F]{64})"\}\]')


def data_dir():
    return Path(os.environ['LOCALAPPDATA']) / 'OpenAI' / 'Capronicus' / 'CodexDesktopMods'


def shortcut():
    return Path(os.environ['APPDATA']) / 'Microsoft/Windows/Start Menu/Programs/ChatGPT (Desktop Mods).lnk'


def powershell(script, **variables):
    # User paths are data, never interpolated into shell commands.
    exe = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    output = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-Command',
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); " + script],
        env={**os.environ, **{k: str(v) for k,v in variables.items()}}, capture_output=True,
        encoding='utf-8', errors='replace', timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    if output.returncode:
        raise ValueError(output.stderr.strip() or 'Windows PowerShell failed.')
    return output.stdout.strip()


def package():
    info = powershell("""
        $p=Get-AppxPackage -Name OpenAI.Codex |
          Where-Object {$_.PackageFamilyName -eq $env:MOD_FAMILY} |
          Sort-Object Version -Descending | Select-Object -First 1
        if ($p) { @{root=$p.InstallLocation; version=$p.Version.ToString()} | ConvertTo-Json -Compress }
    """, MOD_FAMILY=FAMILY)
    if not info:
        return None
    props = json.loads(info)
    return Path(props['root']) / 'app', props['version']


def asar_hash(archive):
    with archive.open('rb') as src:
        tag, _, _, size = struct.unpack('<IIII', src.read(16))
        if tag != 4 or not 0 < size <= 64 * 1024 * 1024:
            raise ValueError('Unknown desktop archive header.')
        header = src.read(size)
        if len(header) != size:
            raise ValueError('Incomplete desktop archive header.')
    return hashlib.sha256(header).hexdigest()


def set_integrity(exe, original, replacement):
    image = exe.read_bytes()
    if image[:2] != b'MZ':
        raise ValueError('Unrecognised Windows executable.')
    matches = list(INTEGRITY.finditer(image))
    if len(matches) != 1 or matches[0][1].decode().lower() != original:
        raise ValueError('The installed executable and original app archive do not match.')
    start = matches[0].start(1)
    # Fixed-length replacement keeps the resource table and integrity checks intact.
    with exe.open('r+b') as dst:
        dst.seek(start)
        dst.write(replacement.encode('ascii'))


def tool_version():
    digest = hashlib.sha256()
    for name in FILES:
        digest.update((SOURCE / name).read_bytes())
    return digest.hexdigest()


def revision(app, version, tools):
    digest = hashlib.sha256((str(app) + '\0' + version + '\0' + tools).encode('utf-8'))
    for file in (app / EXE, app / ASAR):
        st = file.stat()
        digest.update(struct.pack('<QQ', st.st_size, st.st_mtime_ns))
    return digest.hexdigest()


def cache_directory(path):
    info = path.lstat()
    return (stat.S_ISDIR(info.st_mode)
            and not getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def retire(path):
    # A previous cleanup may still have a retired copy of the same revision.
    return path.rename(path.with_name('retired-' + secrets.token_hex(32)))


def runtime(app, version, root):
    key = revision(app, version, tool_version())
    cache = root / 'runtimes'
    dest = cache / key
    cache.mkdir(parents=True, exist_ok=True)
    if not cache_directory(cache):
        raise ValueError('Expected an ordinary runtime cache directory.')
    if os.path.lexists(dest):
        if not cache_directory(dest):
            raise ValueError('The runtime cache path is not an ordinary directory.')
        if (dest / '.complete').is_file() and (dest / EXE).is_file() and (dest / ASAR).is_file():
            return dest
        if running_copy(dest):
            raise ValueError('Quit the modded desktop app, including its tray icon, then retry to rebuild its cached copy.')
        retire(dest)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        stage = Path(tmp) / 'app'

        def skip(folder, names):
            return ['app.asar'] if Path(folder) == app / 'resources' else []

        # Copy protected package files as ordinary user files without read-only attributes.
        shutil.copytree(app, stage, copy_function=shutil.copyfile, ignore=skip)
        original = app / ASAR
        patcher.patch_archive(stage / ASAR, archive=original)
        set_integrity(stage / EXE, asar_hash(original), asar_hash(stage / ASAR))
        if key != revision(app, version, tool_version()):
            raise ValueError('Windows updated ChatGPT during installation. Retry the operation.')
        (stage / '.complete').write_text(key, encoding='ascii')
        stage.rename(dest)
    return dest


@contextmanager
def locked():
    import msvcrt
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'install.lock').open('a+b') as lock:
        lock.seek(0)
        # Store updates can take longer than the ten-second LK_LOCK limit.
        deadline = time.monotonic() + 240
        while True:
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        try:
            yield root
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def shortcut_kind():
    link = shortcut()
    if not link.exists():
        return 'stock'
    description = powershell("((New-Object -ComObject WScript.Shell).CreateShortcut($env:MOD_LINK)).Description",
                             MOD_LINK=link)
    return 'mod' if description == MARKER else 'custom'


def save_shortcut(root, tools, exe):
    link = shortcut()
    link.parent.mkdir(parents=True, exist_ok=True)
    # A .lnk in the same directory makes the final switch atomic.
    with tempfile.TemporaryDirectory(dir=link.parent) as tmp:
        staged = Path(tmp) / link.name
        python = Path(sys.executable)
        windowed = python.with_name('pythonw.exe')
        powershell("""
            $s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:MOD_LINK)
            $s.TargetPath=$env:MOD_PYTHON; $s.Arguments=$env:MOD_ARGS
            $s.Description=$env:MOD_MARKER; $s.WorkingDirectory=$env:MOD_ROOT
            $s.IconLocation=$env:MOD_ICON+',0'; $s.Save()
        """, MOD_LINK=staged, MOD_PYTHON=windowed if windowed.is_file() else python,
            MOD_ARGS=subprocess.list2cmdline(['-B', str(tools / 'windows_mods.py'), 'launch']),
            MOD_ROOT=root, MOD_MARKER=MARKER, MOD_ICON=exe)
        os.replace(staged, link)


def install_tools(root):
    parent = root / 'tools'
    dest = parent / tool_version()
    if not dest.is_dir():
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            stage = Path(tmp) / 'tools'
            stage.mkdir()
            for name in FILES:
                shutil.copyfile(SOURCE / name, stage / name)
            stage.rename(dest)
    return dest


def running_copy(directory):
    # Do not remove even a helper DLL while the old runtime is in use.
    answer = powershell("""
        $prefix=$env:MOD_CACHE+'\x5c'
        $processes=Get-CimInstance Win32_Process -ErrorAction Stop
        foreach ($p in $processes) {
          if ($p.ExecutablePath -and $p.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) {
            Write-Output 'busy'; break
          }
          if (!$p.ExecutablePath -and $p.Name -in @('ChatGPT.exe','Codex.exe')) { Write-Output 'busy'; break }
        }
    """, MOD_CACHE=directory)
    return answer == 'busy'


def prune(root, keep=None):
    directory = root / 'runtimes'
    if not directory.is_dir():
        return True
    if not cache_directory(directory):
        raise ValueError('Expected an ordinary runtime cache directory.')
    old = [p for p in directory.iterdir() if REV.fullmatch(p.name) and p != keep and cache_directory(p)]
    retired = [p for p in directory.glob('retired-*') if REV.fullmatch(p.name[8:]) and cache_directory(p)]
    if (old or retired) and running_copy(directory):
        return False
    done = True
    for path in old:
        try:
            retired.append(retire(path))
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33, 183):
                raise
            done = False
    for path in retired:
        try:
            shutil.rmtree(path)
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33):
                raise
            done = False
    return done


def prune_unused(root, keep):
    # A denied process query or a locked old helper should not block the new app.
    try:
        prune(root, keep)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass


def status():
    pkg = package()
    kind = shortcut_kind()
    if pkg is None:
        state = 'desktop_package_missing'
    elif kind == 'custom':
        state = 'personal_launcher_in_use'
    elif kind == 'stock':
        state = 'not_installed'
    else:
        path = data_dir() / 'runtimes' / revision(*pkg, tool_version())
        state = 'ready' if (path / '.complete').is_file() and (path / EXE).is_file() and (path / ASAR).is_file() else 'needs_refresh'
    return {'desktop_build': pkg and pkg[1], 'menu_launcher': kind, 'patch_state': state}


def apply():
    with locked() as root:
        if shortcut_kind() == 'custom':
            raise ValueError('A different Start menu shortcut already uses this name.')
        pkg = package()
        if not pkg:
            raise ValueError('Install the Windows ChatGPT desktop app with Codex from the Microsoft Store.')
        dest = runtime(*pkg, root)
        tools = install_tools(root)
        save_shortcut(root, tools, pkg[0] / EXE)
        prune_unused(root, dest)
    return 'Installed. Fully quit ChatGPT, then open ChatGPT (Desktop Mods) from Start.'


def remove():
    with locked() as root:
        if shortcut_kind() == 'mod':
            shortcut().unlink()
        if not prune(root):
            return 'Shortcut removed. Quit the modded app and run removal again to clean up the cache.'
        directory = root / 'tools'
        if directory.is_dir():
            for path in directory.iterdir():
                if REV.fullmatch(path.name) and (path / 'windows_mods.py').is_file():
                    shutil.rmtree(path)
    return 'Removed the Windows mod launcher and cached app. Your chat profile is preserved.'


def start_packaged(exe):
    # The app discovers the registered core, sandbox service and profile through
    # its MSIX identity. Keep that identity when starting the private copy.
    powershell("Invoke-CommandInDesktopPackage -PackageFamilyName $env:MOD_FAMILY "
               "-AppId App -Command $env:MOD_EXE", MOD_FAMILY=FAMILY, MOD_EXE=exe)


def open_stock():
    subprocess.Popen(['explorer.exe', 'shell:AppsFolder\\' + FAMILY + '!App'])


def launch():
    try:
        with locked() as root:
            pkg = package()
            if not pkg:
                raise ValueError('The Windows ChatGPT package is unavailable.')
            dest = runtime(*pkg, root)
            prune_unused(root, dest)
            start_packaged(dest / EXE)
    except (OSError, ValueError, KeyError, struct.error, subprocess.SubprocessError) as exc:
        import ctypes
        open_stock()
        ctypes.windll.user32.MessageBoxW(None, 'Opening the standard ChatGPT app.\n\n' + str(exc),
                                         'Codex Desktop Mods', 0x40)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'install', 'launch', 'uninstall'])
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.exit(1, 'This launcher runs on Windows. On Linux use sidebar-colours.py.\n')
    actions = {'status': status, 'install': apply, 'uninstall': remove, 'launch': launch}
    try:
        result = actions[args.action]()
        if result is not None:
            print(json.dumps(result) if isinstance(result, dict) else result)
    except (OSError, ValueError, KeyError, struct.error, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
