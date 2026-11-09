from pathlib import Path
import tempfile, sys, os, json, uuid, sqlite3, subprocess, gzip

temporary = tempfile.TemporaryDirectory(prefix="chat-sync-storage-")
root = Path(temporary.name)
a, b, v = (root / p for p in ["A", "B", "folder"])
for h in (a, b):
    (h / "thread-writer-locks").mkdir(parents=True)
    (h / "thread-writer-locks/.coordination.lock").touch()
    db = sqlite3.connect(h / "state_5.sqlite")
    db.execute("create table threads(id,rollout_path,archived,name,source)")
    db.close()
v.mkdir()
engine = Path(__file__).resolve().parents[1] / "sync.py"


def run(h, *args):
    result = subprocess.check_output(
        [sys.executable, str(engine), *args], env={**os.environ, "CODEX_HOME": str(h)}, text=True
    )
    return json.loads(result)


def line(data):
    return json.dumps(data).encode() + b"\n"


def header(t):
    return line({"type": "session_meta", "payload": {"id": t, "source": "vscode"}})


tid = str(uuid.uuid4())
name = "rollout-2026-11-09T08-04-00-" + tid + ".jsonl"
p = a / "archived_sessions" / name
p.parent.mkdir()
text = header(tid) + line({"type": "event_msg", "payload": {"type": "tool", "fake": "x" * 1100000}})
p.write_bytes(text)
subprocess.run(["zstd", "--rm", "-q", str(p)], check=True)
run(a, "setup", str(v))
run(b, "setup", str(v))
assert run(a, "sync")["exported"] == 1
vault = v / "codex-chat-sync-v1"
block = next((vault / "blocks").rglob("*.gz"))
old = block.read_bytes()
block.unlink()
assert tid in run(b, "sync")["skipped"] and not (b / "archived_sessions" / name).exists()
block.write_bytes(gzip.compress(b"tampered"))
assert tid in run(b, "sync")["skipped"] and not (b / "archived_sessions" / name).exists()
block.write_bytes(old)
assert run(b, "sync")["imported"] == 1
bp = b / "archived_sessions" / name
assert bp.read_bytes() == text
ext = line({"type": "event_msg", "payload": {"type": "task_complete"}})
with bp.open("ab") as out:
    out.write(ext)
assert run(b, "sync")["exported"] == 1
assert run(a, "sync")["updated"] == 1 and not p.exists()
assert subprocess.check_output(["zstd", "-qdc", str(p) + ".zst"]) == text + ext
assert not run(b, "sync")["conflicts"]
print("PASS multi-block compressed archive, missing/corrupt blocks, retry, continued update in place.")
# Exercise automatic mode through the server, with a disposable profile only.
c = root / "C"
(c / "thread-writer-locks").mkdir(parents=True)
(c / "thread-writer-locks/.coordination.lock").touch()
import shutil, time

shutil.copy(a / "state_5.sqlite", c / "state_5.sqlite")
run(c, "setup", str(v), "--automatic")
server = subprocess.Popen(
    [sys.executable, str(engine.with_name("server.py"))],
    stdin=-1,
    stdout=-1,
    stderr=-1,
    env={**os.environ, "CODEX_HOME": str(c)},
)
try:
    for i in range(75):
        if (c / "archived_sessions" / name).exists():
            break
        time.sleep(0.3)
    assert (c / "archived_sessions" / name).read_bytes() == text + ext
finally:
    server.stdin.close()
    server.wait(5)
print("PASS background import while the configured MCP server is running.")
