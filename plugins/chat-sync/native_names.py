"""Apply display names using Codex's own metadata API, without resuming a thread."""

import json
import os
import shutil
import sqlite3
from contextlib import closing, contextmanager
import subprocess
import threading
from pathlib import Path


def windows_store_cli():
    import ctypes as ct

    kernel = ct.WinDLL("kernel32")
    sizep = ct.POINTER(ct.c_uint32)
    names_api = kernel.GetPackagesByPackageFamily
    names_api.argtypes = [ct.c_wchar_p, sizep, ct.POINTER(ct.c_wchar_p), sizep, ct.c_wchar_p]
    path_api = kernel.GetPackagePathByFullName
    path_api.argtypes = [ct.c_wchar_p, sizep, ct.c_wchar_p]
    family = "OpenAI.Codex_2p2nqsd0c76g0"
    count, size = ct.c_uint32(), ct.c_uint32()
    more_space = 122  # ERROR_INSUFFICIENT_BUFFER
    if names_api(family, ct.byref(count), None, ct.byref(size), None) != more_space:
        return None
    names = (ct.c_wchar_p * count.value)()
    storage = ct.create_unicode_buffer(size.value)
    if names_api(family, ct.byref(count), names, ct.byref(size), storage) != 0:
        return None
    for name in names[:count.value]:
        length = ct.c_uint32()
        if path_api(name, ct.byref(length), None) == more_space:
            folder = ct.create_unicode_buffer(length.value)
            if path_api(name, ct.byref(length), folder) == 0:
                exe = Path(folder.value) / "app/resources/codex.exe"
                if exe.is_file():
                    return str(exe)
    return None


def codex_executable():
    for key in ("CODEX_CHAT_SYNC_CLI", "CODEX_APP_RUNTIME_CLI_PATH"):
        value = os.environ.get(key)
        if value and Path(value).is_file():
            return value
    if os.name == "nt":
        bundled = windows_store_cli()
        if bundled:
            return bundled
    candidates = [
        "/usr/lib/chatgpt/resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        shutil.which("codex"),
    ]
    return next((p for p in candidates if p and Path(p).is_file()), None)


@contextmanager
def native_api(home):
    """Open a bounded, metadata-only connection to the receiving Codex profile."""
    exe = codex_executable()
    if not exe:
        raise ValueError("The desktop Codex executable is unavailable.")
    proc = subprocess.Popen(
        [exe, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        env={**os.environ, "CODEX_HOME": str(home)},
    )
    seq = 0

    def call(method, params):
        nonlocal seq
        seq += 1
        deadline = threading.Timer(15, proc.kill)
        deadline.start()
        try:
            proc.stdin.write(json.dumps({"id": seq, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            for line in proc.stdout:
                reply = json.loads(line)
                if reply.get("id") == seq:
                    if "error" in reply:
                        raise ValueError(f"{method}: {reply['error'].get('message', 'request failed')}")
                    return reply["result"]
            raise EOFError(f"Codex stopped during {method}")
        finally:
            deadline.cancel()

    try:
        call("initialize", {"clientInfo": {"name": "chat_sync", "version": "0.9.0"},
                            "capabilities": {"experimentalApi": True}})
        yield call
    finally:
        proc.terminate()
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdin.close()
        proc.stdout.close()


def apply_through_api(names, home):
    done = []
    try:
        with native_api(home) as call:
            for tid, name in names.items():
                try:
                    call("thread/read", {"threadId": tid})
                    call("thread/name/set", {"threadId": tid, "name": name})
                    done.append(tid)
                except ValueError:
                    continue
    except (OSError, EOFError, ValueError):
        pass
    return done


def apply_names(names, home):
    try:
        done = apply_through_api(names, home)
    except (OSError, subprocess.SubprocessError):
        done = []
    # A Store profile may be indexed by the running desktop before a separate
    # app-server can address the chat. Update only an existing native title row.
    pending = names.keys() - set(done)
    if pending:
        database = (home / "state_5.sqlite").as_uri() + "?mode=rw"
        with closing(sqlite3.connect(database, uri=True, timeout=2)) as db, db:
            for tid in pending:
                changed = db.execute("UPDATE threads SET name=? WHERE id=?", (names[tid], tid))
                if changed.rowcount == 1:
                    done.append(tid)
    return done
