from native_fixture import App, prepare, ROOT
from pathlib import Path
import os, sys, subprocess, json, shutil
import tempfile

root = Path(tempfile.mkdtemp(prefix="chat-sync-roundtrip-"))
pkg = ROOT.parent


def run(home, *args):
    r = subprocess.run(
        [sys.executable, str(pkg / "sync.py"), *args],
        env={**os.environ, "CODEX_HOME": str(home)},
        capture_output=True,
        text=True,
        timeout=45,
    )
    if r.returncode:
        raise ValueError(r.stderr)
    return json.loads(r.stdout)


def hist(app, tid):
    app.call("thread/resume", {"threadId": tid})
    turns = app.call("thread/turns/list", {"threadId": tid, "itemsView": "full"})
    return str(turns["data"])


if __name__ == "__main__":
    model = subprocess.Popen(
        [
            "node",
            "-e",
            "require('./fake-model.cjs')().then(p=>{console.log(p);setInterval(()=>{},60000)})",
        ],
        stdout=-1,
        text=True,
        cwd=ROOT,
    )
    servers = []
    try:
        port = int(model.stdout.readline())
        a = root / "A"
        b = root / "B"
        vault = root / "sync"
        vault.mkdir()
        prepare(a, port)
        prepare(b, port)
        aa = App(a)
        servers.append(aa)
        bb = App(b)
        servers.append(bb)
        dummy = bb.call("thread/start", {"cwd": str(ROOT), "historyMode": "paginated"})["thread"]
        tid = aa.call(
            "thread/start", {"cwd": str(ROOT), "historyMode": "paginated", "approvalPolicy": "never"}
        )["thread"]["id"]
        aa.turn(tid, "Hello from first desktop")
        aa.call("thread/name/set", {"threadId": tid, "name": "Native Sync Fixture"})
        run(a, "setup", str(vault))
        run(b, "setup", str(vault))
        print("A push", run(a, "sync"), flush=True)
        print("B pull", run(b, "sync"), flush=True)
        item = next(x for x in bb.call("thread/list", {"modelProviders": []})["data"] if x["id"] == tid)
        assert item["name"] == "Native Sync Fixture", item.get("name")
        assert "Hello from first desktop" in hist(bb, tid)
        bb.turn(tid, "Second metadata check")
        print("B push", run(b, "sync"), flush=True)
        print("A while loaded", run(a, "sync"), flush=True)
        aa.close()
        servers.remove(aa)
        print("A pull", run(a, "sync"), flush=True)
        aa = App(a)
        servers.append(aa)
        turns = hist(aa, tid)
        assert "Second metadata check" in turns and "Birch metadata verified." in turns
        aa.turn(tid, "Independent A")
        bb.turn(tid, "Independent B")
        print("A diverge", run(a, "sync"), flush=True)
        conflict = run(b, "sync")["conflicts"]
        assert tid in conflict
        print("conflict", conflict, flush=True)
        peer = next(
            k
            for k in conflict[tid]
            if k == json.loads((a / "chat-sync/config.json").read_text())["device"]
        )
        kept = run(b, "keep-branch", tid, peer)["branch"]
        print("kept", kept, flush=True)
        run(b, "sync")
        kept_history = hist(bb, kept)
        assert "Independent A" in kept_history and "Independent B" not in kept_history, kept_history[
            -1500:
        ]
        assert run(b, "keep-branch", tid, peer)["branch"] == kept
        aa.close()
        servers.remove(aa)
        run(a, "sync")
        aa = App(a)
        servers.append(aa)
        assert "Independent A" in hist(aa, kept)
        aa.call("thread/name/set", {"threadId": kept, "name": "Renamed on A"})
        aa.close()
        servers.remove(aa)
        bb.close()
        servers.remove(bb)
        print("rename A push", run(a, "sync"), flush=True)
        print("rename B pull", run(b, "sync"), flush=True)
        bb = App(b)
        servers.append(bb)
        assert (
            next(x for x in bb.call("thread/list", {"modelProviders": []})["data"] if x["id"] == kept)[
                "name"
            ]
            == "Renamed on A"
        )
        print("PASS divergent copy resumes on both hosts and title changes propagate", flush=True)
        aligned = run(b, "adopt", tid, peer)
        assert aligned["thread"] == tid
        preserve = aligned["kept_local_branch"]
        assert not run(b, "sync")["conflicts"] and not run(a, "sync")["conflicts"]
        bb.close()
        servers.remove(bb)
        bb = App(b)
        servers.append(bb)
        original = hist(bb, tid)
        saved = hist(bb, preserve)
        assert "Independent A" in original and "Independent B" not in original
        assert "Independent B" in saved and "Independent A" not in saved
        aa = App(a)
        servers.append(aa)
        assert "Independent B" in hist(aa, preserve)
        bb.turn(tid, "Continuing after the merge")
        bb.close()
        servers.remove(bb)
        aa.close()
        servers.remove(aa)
        run(b, "sync")
        assert not run(a, "sync")["conflicts"]
        aa = App(a)
        servers.append(aa)
        assert "Continuing after the merge" in hist(aa, tid)
        print(
            "PASS original converges, losing branch survives on both, subsequent reply syncs back.",
            flush=True,
        )

    finally:
        for app in servers:
            app.close()
        model.terminate()
        model.wait()
        shutil.rmtree(root)
