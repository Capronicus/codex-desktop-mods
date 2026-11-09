"""Exercise the official Store package on a disposable Windows CI runner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_mods as mods


def main():
    # Only run on a disposable hosted runner. This test installs a real Start menu shortcut.
    if sys.platform != 'win32' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('Run this test on a Windows GitHub Actions runner.')
    pkg = mods.package()
    if not pkg:
        raise RuntimeError('Expected the Microsoft Store package OpenAI.Codex.')
    print('Installed package', pkg[1], flush=True)
    if mods.shortcut().exists():
        raise RuntimeError('Expected a clean runner.')
    print(mods.apply(), flush=True)
    assert mods.status()['patch_state'] == 'ready'
    exe = mods.runtime(*pkg, mods.data_dir()) / mods.EXE
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        profile = Path(tmp) / 'profile'
        home = Path(tmp) / 'codex'
        profile.mkdir()
        home.mkdir()
        host_env = {**os.environ, 'CODEX_HOME':str(home), 'CODEX_ELECTRON_USER_DATA_PATH':str(profile)}
        # The debug endpoint exists only in this CI test. Production launches have no debug flags.
        args = subprocess.list2cmdline(['--user-data-dir=' + str(profile),
                                        '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0'])
        try:
            with patch.dict(os.environ, host_env):
                mods.powershell("Invoke-CommandInDesktopPackage -AppId App -PackageFamilyName $env:MOD_FAMILY "
                                "-Command $env:MOD_EXE -Args $env:MOD_ARGS",
                                MOD_FAMILY=mods.FAMILY, MOD_EXE=exe, MOD_ARGS=args)
            endpoint = profile / 'DevToolsActivePort'
            start = time.monotonic()
            urls = []
            while time.monotonic() - start < 90:
                if endpoint.exists():
                    port = endpoint.read_text().splitlines()[0]
                    with urllib.request.urlopen('http://127.0.0.1:' + port + '/json/list', timeout=3) as response:
                        urls = [tab.get('url','') for tab in json.load(response)]
                    if any(url.startswith('app://-/') for url in urls):
                        print('Started real modded desktop renderer in registered package context.',flush=True)
                        return
                time.sleep(1)
            raise RuntimeError('Modded renderer did not start: ' + str([u.split('?')[0] for u in urls]))
        finally:
            mods.powershell("""
                Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -eq $env:MOD_EXE} |
                  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            """, MOD_EXE=exe)
            mods.remove()


if __name__ == '__main__':
    main()
