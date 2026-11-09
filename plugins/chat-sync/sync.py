"""Exchange completed Codex conversations through a user-selected private folder."""

from contextlib import contextmanager
from datetime import datetime
import gzip
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid

import workspace_paths

BLOCK = 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
FILE = re.compile(r"rollout-(\d{4}-\d\d-\d\d)T\d\d-\d\d-\d\d-(" + UUID.pattern + r")\.jsonl(?:\.zst)?")
HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
STATE = HOME / "chat-sync"
CONFIG = STATE / "config.json"


class Busy(Exception):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".writing-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read_json(path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_bytes())


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise Busy() from exc
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Busy() from exc
        yield


@contextmanager
def native_writer(tid):
    # Codex serializes opening/unlinking its per-thread lock with this outer lock.
    directory = HOME / "thread-writer-locks"
    if not (directory / ".coordination.lock").is_file():
        raise ValueError("Native thread writer locks unavailable. Open a local Codex chat first.")
    with lock(directory / ".coordination.lock"):
        writer = lock(directory / (tid + ".lock"))
        writer.__enter__()
    try:
        yield
    finally:
        writer.__exit__(None, None, None)


def setup(folder, automatic=False):
    base = Path(folder).expanduser()
    if not base.is_absolute() or not base.is_dir():
        raise ValueError("Choose an existing absolute private sync folder.")
    base = base.resolve()
    vault = base / "codex-chat-sync-v1"
    if base == HOME or HOME in base.parents or base in HOME.parents or vault.is_symlink():
        raise ValueError("Choose a dedicated folder separate from the Codex profile.")
    with lock(STATE / "sync.lock"):
        old = read_json(CONFIG, {})
        if old and old["folder"] != str(base):
            raise ValueError("Disable this location before selecting a different folder.")
        cfg = {
            "folder": str(base),
            "device": old.get("device") or str(uuid.uuid4()),
            "automatic": automatic,
            "colours": bool(old.get("colours")),
            "sidebar": bool(old.get("sidebar")),
        }
        vault.mkdir(mode=0o700, exist_ok=True)
        atomic(CONFIG, encode(cfg))
    return status()


def status():
    cfg = read_json(CONFIG)
    return {
        "configured": bool(cfg),
        "folder": cfg and cfg["folder"],
        "device": cfg and cfg["device"],
        "automatic": bool(cfg and cfg["automatic"]),
        "colours": bool(cfg and cfg.get("colours")),
        "sidebar": bool(cfg and cfg.get("sidebar")),
        "last_run": read_json(STATE / "last.json"),
    }


def set_exchange_option(option, enabled):
    if option not in ("colours", "sidebar") or type(enabled) is not bool:
        raise ValueError("Unknown sync option")
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if not cfg:
            raise ValueError("Choose a private chat sync folder first.")
        cfg[option] = enabled
        atomic(CONFIG, encode(cfg))
    return status()


def disable():
    with lock(STATE / "sync.lock"):
        CONFIG.unlink(missing_ok=True)
    return {"configured": False}


def catalog():
    db = HOME / "state_5.sqlite"
    if not db.is_file():
        raise ValueError("This profile has no supported local Codex thread catalogue.")
    result = {}
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        columns = "id, rollout_path, name, archived, source, cwd"
        if any(info[1] == "project_id" for info in conn.execute("PRAGMA table_info(threads)")):
            columns += ", project_id"
        for row in conn.execute("SELECT " + columns + " FROM threads"):
            if not UUID.fullmatch(row["id"]):
                continue
            result[row["id"]] = dict(row)
    return result


def streams():
    result = {}
    for archived, directory in [(False, HOME / "sessions"), (True, HOME / "archived_sessions")]:
        if not directory.is_dir():
            continue
        for p in directory.rglob("rollout-*.jsonl*"):
            m = FILE.fullmatch(p.name)
            if m and not p.is_symlink() and directory.resolve() in p.resolve().parents and p.is_file():
                tid = m[2]
                if tid in result:
                    # Compression/archival can transiently leave two candidates.
                    result[tid] = None
                else:
                    result[tid] = (p, archived)
    return result


@contextmanager
def open_rollout(path):
    if path.suffix != ".zst":
        with path.open("rb") as stream:
            yield stream
        return
    try:
        from compression import zstd
    except ImportError:
        if not shutil.which("zstd"):
            raise ValueError("Compressed chat needs Python 3.14+ or the zstd executable.")
        proc = subprocess.Popen(["zstd", "-qdc", "--", str(path)], stdout=subprocess.PIPE)
        try:
            yield proc.stdout
        finally:
            proc.stdout.close()
            if proc.wait():
                raise ValueError("Could not unpack the compressed conversation.")
    else:
        with zstd.open(path, "rb") as stream:
            yield stream


def put_block(vault, data):
    key = digest(data)
    dest = vault / "blocks" / key[:2] / (key + ".gz")
    if not dest.exists():
        atomic(dest, gzip.compress(data, compresslevel=3, mtime=0))
    return key


def block_data(vault, key):
    if not isinstance(key, str) or not HEX.fullmatch(key):
        raise ValueError("Invalid block reference")
    path = vault / "blocks" / key[:2] / (key + ".gz")
    if path.stat().st_size > BLOCK + 65536:
        raise ValueError("Oversized block")
    with gzip.open(path, "rb") as stream:
        data = stream.read(BLOCK + 1)
    if len(data) > BLOCK or digest(data) != key:
        raise ValueError("Block checksum mismatch")
    return data


def snapshot(vault, tid, entry, name, known):
    path, archived = entry
    st = path.stat()
    token = [str(path), st.st_size, st.st_mtime_ns, st.st_ino, name, archived]
    if known and known.get("token") == token:
        return known
    with open_rollout(path) as stream:
        records = workspace_paths.pieces(stream)
        first = next(records, b"")
        if len(first) > 8 * BLOCK or not first.endswith(b"\n"):
            raise ValueError("Unreadable conversation header")
        meta = json.loads(first)
        info = meta.get("payload", {})
        if meta.get("type") != "session_meta" or info.get("id") != tid:
            raise ValueError("Thread identity differs from filename")
        if info.get("source") not in ("cli", "vscode", "appServer", "app_server", None):
            return None
        chunks, size, tail = [], 0, b""
        pending = bytearray()
        for part in itertools.chain((first,), records):
            pending.extend(part)
            while len(pending) >= BLOCK:
                block = bytes(pending[:BLOCK])
                del pending[:BLOCK]
                chunks.append(put_block(vault, block))
                tail = block[-65536:]
            size += len(part)
        if pending:
            chunks.append(put_block(vault, bytes(pending)))
            tail = (tail + pending)[-65536:]
    end = path.stat()
    if (end.st_ino, end.st_size, end.st_mtime_ns) != (
        st.st_ino,
        st.st_size,
        st.st_mtime_ns,
    ) or not tail.endswith(b"\n"):
        raise Busy()
    # An open desktop chat may still be locked after a completed turn. A terminal
    # event lets another device see that completed snapshot without importing a partial turn.
    try:
        with native_writer(tid):
            pass
    except Busy:
        try:
            last = json.loads(tail.rstrip(b"\n").split(b"\n")[-1])
        except ValueError:
            raise Busy()
        if last.get("type") != "event_msg" or last.get("payload", {}).get("type") not in (
            "task_complete",
            "turn_aborted",
        ):
            raise Busy()
    manifest = {
        "v": 1,
        "id": tid,
        "date": FILE.fullmatch(path.name)[1],
        "file": path.name.removesuffix(".zst"),
        "chunks": chunks,
        "size": size,
        "name": name,
        "archived": archived,
    }
    return {"token": token, "manifest": manifest}


def valid_manifest(man, tid):
    if not isinstance(man, dict) or man.get("v") != 1 or man.get("id") != tid:
        raise ValueError("Unknown snapshot format")
    m = FILE.fullmatch(man.get("file", ""))
    if not m or man["file"].endswith(".zst") or (m[1], m[2]) != (man.get("date"), tid):
        raise ValueError("Invalid thread filename")
    datetime.strptime(m[1], "%Y-%m-%d")
    size = man.get("size")
    chunks = man.get("chunks")
    if (
        type(size) != int
        or size < 1
        or not isinstance(chunks, list)
        or len(chunks) != (size + BLOCK - 1) // BLOCK
    ):
        raise ValueError("Invalid snapshot length")
    if not all(isinstance(h, str) and HEX.fullmatch(h) for h in chunks):
        raise ValueError("Invalid snapshot hash")
    if type(man.get("archived")) != bool or (
        man.get("name") is not None and not isinstance(man["name"], str)
    ):
        raise ValueError("Invalid thread metadata")
    clock = man.get("name_clock", [0, ""])
    if (
        not isinstance(clock, list)
        or len(clock) != 2
        or type(clock[0]) != int
        or clock[0] < 0
        or not isinstance(clock[1], str)
    ):
        raise ValueError("Invalid name revision")
    return man


def is_prefix(vault, a, b):
    if a["size"] > b["size"]:
        return False
    full, partial = divmod(a["size"], BLOCK)
    if a["chunks"][:full] != b["chunks"][:full]:
        return False
    if not partial or a["chunks"][-1] == b["chunks"][full]:
        return True
    return block_data(vault, b["chunks"][full]).startswith(block_data(vault, a["chunks"][-1]))


def conversation_records(vault, man):
    """Digest each exact record except the per-device resume-settings marker."""
    records = []
    pending = bytearray()
    for key in man["chunks"]:
        parts = block_data(vault, key).split(b"\n")
        pending.extend(parts[0])
        for part in parts[1:]:
            # Loading a thread on an idle desktop appends thread_settings_applied.
            # Parse candidates so quoted text or tool output cannot be discarded.
            if b'"thread_settings_applied"' in pending:
                obj = json.loads(pending)
                if (
                    obj.get("type") == "event_msg"
                    and obj.get("payload", {}).get("type") == "thread_settings_applied"
                ):
                    pending = bytearray(part)
                    continue
            records.append(hashlib.sha256(pending).digest())
            pending = bytearray(part)
    if pending:
        raise ValueError("Incomplete final record")
    return records


def latest_history(vault, choices, local):
    best = max(choices, key=lambda m: m["size"])
    if all(is_prefix(vault, m, best) for m in choices):
        return best, bool(local and local["chunks"] == best["chunks"])
    # Most passes finish above without decompressing a complete chat.
    histories = {tuple(m["chunks"]): None for m in choices}
    for m in choices:
        key = tuple(m["chunks"])
        if histories[key] is None:
            histories[key] = conversation_records(vault, m)
    best = max(choices, key=lambda m: len(histories[tuple(m["chunks"])]))
    longest = histories[tuple(best["chunks"])]
    if any(longest[: len(history)] != history for history in histories.values()):
        return None, False
    return best, bool(local and histories[tuple(local["chunks"])] == longest)


def shared_heads(vault, skipped):
    result = {}
    for path in (vault / "heads").glob("*/*.json"):
        tid = path.stem
        if not UUID.fullmatch(path.parent.name) or not UUID.fullmatch(tid):
            continue
        try:
            revision = read_json(path)
            if not isinstance(revision, str) or not HEX.fullmatch(revision):
                raise ValueError("Invalid device head")
            data = (vault / "versions" / tid / (revision + ".json")).read_bytes()
            if digest(data) != revision:
                raise ValueError("Invalid snapshot checksum")
            man = valid_manifest(json.loads(data), tid)
            result.setdefault(tid, {})[path.parent.name] = man
        except (OSError, ValueError, TypeError) as exc:
            skipped[tid] = "Waiting for a complete device snapshot: " + str(exc)
    return result


def publish(vault, device, man):
    data = encode(man)
    key = digest(data)
    path = vault / "versions" / man["id"] / (key + ".json")
    if not path.exists():
        atomic(path, data)
    head = vault / "heads" / device / (man["id"] + ".json")
    if read_json(head) != key:
        atomic(head, encode(key))


@contextmanager
def invalidate_projection(tid):
    """Invalidate only the rebuildable timeline for this one replaced history."""
    db = HOME / "thread_history_1.sqlite"
    if not db.is_file():
        if any(HOME.glob("thread_history_*.sqlite")):
            raise ValueError("Unsupported desktop timeline index version.")
        yield
        return
    tables = {
        "thread_items",
        "thread_turns",
        "thread_history_projection_state",
        "thread_realtime_items",
    }
    # This database contains only the timeline projection. Codex rebuilds the
    # rows from the rollout when it next opens the chat. Refuse unfamiliar layouts.
    from contextlib import closing

    with closing(sqlite3.connect(db.as_uri() + "?mode=rw", uri=True, timeout=2)) as conn:
        known = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if known != tables | {"_sqlx_migrations"}:
            raise ValueError("Unsupported desktop timeline index schema.")
        conn.execute("BEGIN IMMEDIATE")
        for table in sorted(tables):
            conn.execute("DELETE FROM " + table + " WHERE thread_id=?", (tid,))
        yield
        conn.commit()


def replace_history(temp, dest, tid, diverged):
    if not diverged:
        os.replace(temp, dest)
        return
    journal = STATE / "pending-projection" / tid
    atomic(journal, b"1")
    with invalidate_projection(tid):
        os.replace(temp, dest)
    journal.unlink(missing_ok=True)


def recover_projections():
    for journal in (STATE / "pending-projection").glob("*"):
        if UUID.fullmatch(journal.name):
            with native_writer(journal.name), invalidate_projection(journal.name):
                pass
            journal.unlink(missing_ok=True)


def materialize(vault, man, local, cwd=None):
    tid = man["id"]
    diverged = local is not None and not is_prefix(vault, local["manifest"], man)
    with native_writer(tid):
        if local:
            src = Path(local["token"][0])
            st = src.stat()
            if [st.st_size, st.st_mtime_ns, st.st_ino] != local["token"][1:4]:
                raise Busy()
            # Preserve the local storage format and replace exactly one file.
            dest = src
        else:
            where = (
                HOME / "archived_sessions"
                if man["archived"]
                else HOME / "sessions" / Path(*man["date"].split("-"))
            )
            dest = where / man["file"]
            if dest.exists() or dest.with_suffix(".jsonl.zst").exists():
                raise Busy()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if HOME not in dest.resolve().parents:
            raise ValueError("Unsafe local destination")
        fd, tmp = tempfile.mkstemp(prefix=".chat-sync-", dir=dest.parent)
        try:
            # Read and verify the portable bytes before exposing any local view.
            fallback = STATE / "workspaces" / tid
            if workspace_paths.local_dir(cwd):
                fallback = Path(cwd)
            with os.fdopen(fd, "wb") as out, tempfile.TemporaryFile(dir=dest.parent) as raw:
                for i, key in enumerate(man["chunks"]):
                    data = block_data(vault, key)
                    if len(data) != min(BLOCK, man["size"] - i * BLOCK):
                        raise ValueError("Block length mismatch")
                    raw.write(data)
                raw.seek(0)
                meta = json.loads(raw.readline(8 * BLOCK + 1))
                if meta.get("type") != "session_meta" or meta.get("payload", {}).get("id") != tid:
                    raise ValueError("Snapshot contains a different thread")
                raw.seek(-1, os.SEEK_END)
                if raw.read() != b"\n":
                    raise ValueError("Incomplete final record")
                raw.seek(0)
                out.writelines(workspace_paths.pieces(raw, fallback))
                out.flush()
                os.fsync(out.fileno())
            if dest.suffix == ".zst":
                compressed = tmp + ".zst"
                try:
                    try:
                        from compression import zstd
                    except ImportError:
                        with open(compressed, "wb") as out:
                            subprocess.run(["zstd", "-q", "-c", "--", tmp], stdout=out, check=True)
                    else:
                        with open(tmp, "rb") as raw, zstd.open(compressed, "wb") as out:
                            shutil.copyfileobj(raw, out)
                    with open(compressed, "rb+") as out:
                        os.fsync(out.fileno())
                    replace_history(compressed, dest, tid, diverged)
                finally:
                    Path(compressed).unlink(missing_ok=True)
            else:
                replace_history(tmp, dest, tid, diverged)
        finally:
            Path(tmp).unlink(missing_ok=True)
    return dest


def run(automatic=False):
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if not cfg:
            raise ValueError("Choose a private sync folder first.")
        vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
        if not vault.is_dir():
            raise ValueError("Sync folder unavailable. Check the mount or desktop sync client.")
        device = cfg["device"]
        if automatic:
            last = STATE / "last.json"
            if not cfg["automatic"] or (last.is_file() and time.time() - last.stat().st_mtime < 55):
                return {}
        recover_projections()
        state_path = STATE / ("state-" + device + ".json")
        state = read_json(state_path, {})
        index = catalog()
        local_files = streams()
        snapshots = {}
        stats = {
            "exported": 0,
            "imported": 0,
            "updated": 0,
            "busy": 0,
            "conflicts": {},
            "skipped": {},
            "pending_names": 0,
        }
        # A missing cloud block or delayed head can always be retried. Local files
        # are only replaced after the complete incoming snapshot has been verified.
        for tid, entry in local_files.items():
            row = index.get(tid)
            if not entry or (row and row["source"] not in ("cli", "vscode", "appServer", "app_server")):
                continue
            prev = state.get(tid, {})
            name = row["name"] if row else prev.get("manifest", {}).get("name")
            if prev.get("pending_name"):
                name = prev["pending_name"]
            try:
                snap = snapshot(vault, tid, entry, name, prev)
                if snap:
                    previous = prev.get("manifest", {})
                    clock = previous.get("name_clock", [0, ""])
                    if name and name != previous.get("name"):
                        clock = [clock[0] + 1, device]
                    snap["manifest"]["name_clock"] = clock
                    pending = prev.get("pending_name")
                    if pending:
                        snap["pending_name"] = pending
                    snapshots[tid] = snap
                    publish(vault, device, snap["manifest"])
                    if snap.get("token") != prev.get("token"):
                        stats["exported"] += 1
                    state[tid] = snap
            except Busy:
                stats["busy"] += 1
            except (OSError, ValueError, EOFError, subprocess.SubprocessError, sqlite3.Error) as exc:
                stats["skipped"][tid] = str(exc)
        heads = shared_heads(vault, stats["skipped"])
        for tid, copies in heads.items():
            try:
                if tid in stats["skipped"] or (tid in local_files and tid not in snapshots):
                    continue
                if tid not in local_files and tid in state:
                    continue  # A locally removed thread stays removed here.
                snap = snapshots.get(tid)
                current = snap and snap["manifest"]
                best, up_to_date = latest_history(vault, list(copies.values()), current)
                if best is None:
                    stats["conflicts"][tid] = list(copies)
                    continue
                latest_name = max(
                    (m for m in copies.values() if m.get("name")),
                    key=lambda m: tuple(m.get("name_clock", [0, ""])),
                    default=None,
                )
                best = dict(best)
                if latest_name:
                    best.update(
                        name=latest_name["name"], name_clock=latest_name.get("name_clock", [0, ""])
                    )
                if up_to_date:
                    if latest_name and best["name_clock"] > current.get("name_clock", [0, ""]):
                        state[tid]["manifest"] = {
                            **current,
                            "name": best["name"],
                            "name_clock": best["name_clock"],
                        }
                        state[tid]["pending_name"] = best["name"]
                        state[tid].pop("token", None)
                    continue
                materialize(vault, best, snap, index.get(tid, {}).get("cwd"))
                # Carry the name until the native app server indexes the rollout.
                state[tid] = {"manifest": best, "pending_name": best.get("name")}
                stats["updated" if current else "imported"] += 1
            except Busy:
                stats["busy"] += 1
            except (OSError, ValueError, EOFError, subprocess.SubprocessError, sqlite3.Error) as exc:
                stats["skipped"][tid] = str(exc)
        atomic(state_path, encode(state))
        pending = {
            tid: s["pending_name"]
            for tid, s in state.items()
            if s.get("pending_name") and (tid in local_files or "token" not in s)
        }
        if pending:
            from native_names import apply_names

            for tid in apply_names(pending, HOME):
                state[tid].pop("pending_name", None)
            atomic(state_path, encode(state))
        stats["pending_names"] = sum(bool(s.get("pending_name")) for s in state.values())
        stats["unavailable_local"] = sum(
            tid not in local_files
            for tid, row in index.items()
            if row["source"] in ("cli", "vscode", "appServer", "app_server")
        )
        if cfg.get("sidebar"):
            import sidebar
            try:
                # Only exchange membership for the local human chats this engine handles.
                stats["sidebar"] = sidebar.exchange(HOME, vault, device, set(state) & set(streams()), atomic, encode)
            except (OSError, EOFError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
                stats["sidebar"] = {"error": str(exc)}
        if cfg.get("colours"):
            import colours
            try:
                stats["colours"] = colours.exchange(HOME, vault, device, index, atomic, encode)
            except (OSError, ValueError, sqlite3.Error) as exc:
                stats["colours"] = {"error": str(exc)}
        atomic(STATE / "last.json", encode(stats))
        return stats


def keep_branch(tid, device):
    """Copy one divergent head into a separately resumable chat; originals stay intact."""
    if not (UUID.fullmatch(tid) and UUID.fullmatch(device)):
        raise ValueError("Invalid thread or device ID")
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if not cfg:
            raise ValueError("Choose a private sync folder first.")
        vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
        missing = {}
        copies = shared_heads(vault, missing).get(tid, {})
        if tid in missing or device not in copies:
            raise ValueError("Device snapshot is unavailable")
        man = copies[device]
        if all(latest_history(vault, [item, man], None)[0] is not None for item in copies.values()):
            raise ValueError("This conversation has no divergent device history")
        return save_branch(vault, cfg, tid, device, man)


def save_branch(vault, cfg, tid, device, man):
    new_id = str(uuid.uuid5(uuid.UUID(tid), digest(encode(man["chunks"]))))
    if new_id in streams():
        return {"branch": new_id, "existing": True}
    with tempfile.TemporaryDirectory(dir=STATE) as work:
        name = man["file"].replace(tid, new_id)
        target = Path(work) / name
        with tempfile.TemporaryFile(dir=work) as original, target.open("wb") as out:
            for i, key in enumerate(man["chunks"]):
                data = block_data(vault, key)
                if len(data) != min(BLOCK, man["size"] - i * BLOCK):
                    raise ValueError("Invalid snapshot length")
                original.write(data)
            original.seek(0)
            header = json.loads(original.readline(8 * BLOCK + 1))
            meta = header["payload"]
            if header.get("type") != "session_meta" or meta.get("id") != tid:
                raise ValueError("Invalid conflict snapshot")
            meta["id"] = new_id
            if meta.get("session_id") == tid:
                meta["session_id"] = new_id
            # A delta fork still needs its original parent to reconstruct older turns.
            if "forked_from_ordinal_exclusive" not in meta:
                meta["forked_from_id"] = tid
                meta["forked_from_ordinal_exclusive"] = 0
            out.write(encode(header) + b"\n")
            shutil.copyfileobj(original, out)
        label = (man["name"] or "Chat") + " (branch " + device[:6] + ")"
        branch = snapshot(vault, new_id, (target, False), label, None)["manifest"]
        materialize(vault, branch, None)
        state_path = STATE / ("state-" + cfg["device"] + ".json")
        state = read_json(state_path, {})
        state[new_id] = {"manifest": branch, "pending_name": label}
        atomic(state_path, encode(state))
        publish(vault, cfg["device"], branch)
    return {"branch": new_id, "name": label}


def adopt_history(tid, device):
    """Keep this desktop's version as a branch and adopt the chosen original here."""
    if not (UUID.fullmatch(tid) and UUID.fullmatch(device)):
        raise ValueError("Invalid thread or device ID")
    with lock(STATE / "sync.lock"):
        recover_projections()
        cfg = read_json(CONFIG)
        if not cfg or cfg["device"] == device:
            raise ValueError("Select a different device from this chat’s conflict report.")
        vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
        missing = {}
        copies = shared_heads(vault, missing).get(tid, {})
        own, chosen = copies.get(cfg["device"]), copies.get(device)
        if tid in missing or not own or not chosen:
            raise ValueError("Run a sync first and wait for both snapshots to arrive.")
        local = streams().get(tid)
        if not local:
            raise ValueError("This chat is unavailable locally.")
        snap = snapshot(vault, tid, local, own.get("name"), None)
        if not snap or own["chunks"] != snap["manifest"]["chunks"]:
            raise ValueError("The local chat has changed. Sync again before choosing a version.")
        if own["chunks"] == chosen["chunks"]:
            return {"thread": tid, "already_aligned": True}
        if latest_history(vault, [own, chosen], own)[0] is not None:
            raise ValueError("These versions are compatible. Run an ordinary sync.")
        saved = save_branch(vault, cfg, tid, cfg["device"], own)
        # materialize rechecks the local fingerprint while holding Codex's writer lock.
        materialize(vault, chosen, snap, catalog().get(tid, {}).get("cwd"))
        state_path = STATE / ("state-" + cfg["device"] + ".json")
        state = read_json(state_path, {})
        state[tid] = {"manifest": chosen, "pending_name": chosen.get("name")}
        atomic(state_path, encode(state))
        publish(vault, cfg["device"], chosen)
        return {"thread": tid, "kept_local_branch": saved["branch"], "adopted_device": device}


if __name__ == "__main__":
    from cli import main

    main()
