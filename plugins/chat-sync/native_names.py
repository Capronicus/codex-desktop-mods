"""Apply display names using Codex's own metadata API, without resuming a thread."""

import json
import os
import shutil
import subprocess
from pathlib import Path


def codex_executable():
    for key in ("CODEX_CHAT_SYNC_CLI", "CODEX_APP_RUNTIME_CLI_PATH"):
        value = os.environ.get(key)
        if value and Path(value).is_file():
            return value
    if os.name == "nt":
        # The Store app may be installed without a separate CLI on PATH.
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        script = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
            "(Get-AppxPackage -Name OpenAI.Codex | "
            "Where-Object PackageFamilyName -eq 'OpenAI.Codex_2p2nqsd0c76g0' | "
            "Sort-Object Version -Descending | Select-Object -First 1).InstallLocation"
        )
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        root = result.stdout.strip()
        if root:
            exe = Path(root) / "app/resources/codex.exe"
            if exe.is_file():
                return str(exe)
    candidates = [
        "/usr/lib/chatgpt/resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        shutil.which("codex"),
    ]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def apply_names(names):
    exe = codex_executable()
    if not exe:
        return []

    def message(i, method, params):
        return json.dumps({"id": i, "method": method, "params": params}) + "\n"

    # A separate local API process is short-lived. These RPCs neither start a
    # model turn nor load a thread's tools. Only acknowledge successful replies.
    proc = subprocess.Popen(
        [exe, "app-server", "--listen", "stdio://"],
        stdin=-1,
        stdout=-1,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    import threading

    deadline = threading.Timer(30, proc.kill)
    deadline.start()

    def call(i, method, params):
        proc.stdin.write(message(i, method, params))
        proc.stdin.flush()
        for line in proc.stdout:
            reply = json.loads(line)
            if reply.get("id") == i:
                return "result" in reply
        raise EOFError()

    done = []
    try:
        if not call(1, "initialize", {"clientInfo": {"name": "chat_sync", "version": "0.1.1"}}):
            return done
        for i, (tid, name) in enumerate(names.items(), 2):
            if call(i * 2, "thread/read", {"threadId": tid}) and call(
                i * 2 + 1, "thread/name/set", {"threadId": tid, "name": name}
            ):
                done.append(tid)
    except (OSError, EOFError, ValueError):
        pass
    finally:
        deadline.cancel()
        proc.terminate()
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return done
