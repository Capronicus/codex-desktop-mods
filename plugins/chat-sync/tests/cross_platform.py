"""Exercise the installed plugin with native chats. Exchange only the synthetic vault."""
from contextlib import closing
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from native_fixture import App, prepare, ROOT

REPO = ROOT.parents[2]
NAME = "Cross-platform test café"
FIRST = "Synthetic chat from Linux"
SECOND = "Second metadata check from Windows"
WINDOWS_CHAT = "Created separately on Windows"


def start(app):
    params = {"cwd": str(ROOT), "historyMode": "paginated", "approvalPolicy": "never"}
    return app.call("thread/start", params)["thread"]["id"]


def history(app, tid):
    # Working folders are local to each computer.
    app.call("thread/resume", {"threadId": tid, "cwd": str(ROOT)})
    return str(app.call("thread/turns/list", {"threadId": tid, "itemsView": "full"})["data"])


def find_chat(app, title):
    rows = app.call("thread/list", {"modelProviders": []})["data"]
    matches = [r for r in rows if r.get("name") == title or r.get("preview") == title]
    assert len(matches) == 1, [(r["id"], r.get("name"), r.get("preview")) for r in rows]
    return matches[0]["id"]


def plugin(app):
    tid = app.call("thread/start", {"ephemeral": True, "cwd": str(ROOT)})["thread"]["id"]

    def call(name, **args):
        result = app.call("mcpServer/tool/call", {
            "threadId": tid, "server": "chat-sync", "tool": name, "arguments": args,
        })
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])
    return call


def sync(call):
    result = call("sync_chats")
    print("Sync", result, flush=True)
    assert not result["conflicts"] and not result["skipped"], result
    return result


def exercise(stage, vault, incoming, home):
    cli = os.environ.get("CODEX_CHAT_SYNC_CLI", "codex")
    subprocess.run([cli, "plugin", "marketplace", "add", str(REPO)], check=True)
    subprocess.run([cli, "plugin", "add", "chat-sync@capronicus-desktop-mods"], check=True)
    with closing(App(home)) as app:
        call = plugin(app)
        assert call("chat_sync_status")["configured"] is False
        assert not (home / "chat-sync/config.json").exists()
        call("configure_chat_sync", folder=str(vault), automatic=False)
        assert (home / "chat-sync/config.json").is_file()
        if stage == "seed":
            tid = start(app)
            app.turn(tid, FIRST)
            app.call("thread/name/set", {"threadId": tid, "name": NAME})
            assert sync(call)["exported"] == 1
            return
        if stage == "reply":
            other = start(app)
            app.turn(other, WINDOWS_CHAT)
            # A running app owns this chat's writer lock, including on Windows.
            sys.path.insert(0, str(ROOT.parent))
            import sync as engine
            try:
                with engine.native_writer(other):
                    raise AssertionError("Acquired a writer lock owned by Codex")
            except engine.Busy:
                pass
            result = sync(call)
            assert result["exported"] == 1 and result["imported"] == 1
            tid = find_chat(app, NAME)
            assert FIRST in history(app, tid)
            app.turn(tid, SECOND)
            assert sync(call)["exported"] >= 1
            return
        # Reconstruct the Linux device's initial view using only the seed vault.
        start(app)
        assert sync(call)["imported"] == 1
        tid = find_chat(app, NAME)
        assert FIRST in history(app, tid)
        shutil.copytree(incoming, vault, dirs_exist_ok=True)
        result = sync(call)
        assert result["busy"] >= 1 and result["updated"] == 0, result
    with closing(App(home)) as app:
        call = plugin(app)
        assert sync(call)["updated"] == 1
        assert find_chat(app, NAME) == tid
        turns = history(app, tid)
        assert FIRST in turns and SECOND in turns and "Birch metadata verified." in turns
        assert WINDOWS_CHAT in history(app, find_chat(app, WINDOWS_CHAT))
        print("Verified original ID, both Linux and Windows turns, and Windows-origin chat.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["seed", "reply", "verify"])
    parser.add_argument("vault", type=Path, help="Disposable synthetic shared directory")
    parser.add_argument("--incoming", type=Path)
    args = parser.parse_args()
    vault = args.vault.resolve()
    if args.stage == "seed":
        vault.mkdir(parents=True, exist_ok=False)
    elif not (vault / "codex-chat-sync-v1/heads").is_dir():
        parser.error("Expected a vault from the previous test stage.")
    if args.stage == "verify" and not args.incoming:
        parser.error("verify requires the reply-stage vault in --incoming")
    env_root = tempfile.TemporaryDirectory(prefix="chat-sync-test-", ignore_cleanup_errors=True)
    with env_root as root:
        user = Path(root)
        home = user / ".codex"
        os.environ.update(HOME=str(user), USERPROFILE=str(user), CODEX_HOME=str(home))
        model = subprocess.Popen(
            ["node", "-e",
             "require('./fake-model.cjs')().then(p=>{console.log(p);setInterval(()=>{},60000)})"],
            stdout=subprocess.PIPE, text=True, cwd=ROOT,
        )
        try:
            prepare(home, int(model.stdout.readline()))
            exercise(args.stage, vault, args.incoming, home)
        except Exception:
            log = home / "qa.log"
            if log.exists():
                print(log.read_text(encoding="utf-8", errors="replace")[-7500:], file=sys.stderr)
            raise
        finally:
            model.terminate()
            model.wait()
            model.stdout.close()


if __name__ == "__main__":
    main()
