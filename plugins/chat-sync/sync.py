"""Exchange completed Codex conversations through a user-selected private folder."""

from contextlib import closing, contextmanager
from datetime import datetime
import gzip
import hashlib
import io
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
import zlib

import workspace_paths

BLOCK = 1024 * 1024
LOCAL_SOURCES = ("cli", "vscode", "appServer", "app_server")
HEX = re.compile(r"[0-9a-f]{64}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
FILE = re.compile(
    r"rollout-(\d{4}-\d\d-\d\d)T\d\d-\d\d-\d\d-(" + UUID.pattern + r")\.jsonl(?:\.zst)?"
)
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
        if not old:
            saved = read_json(STATE / "disconnected.json", {})
            old = {**saved.get("devices", {}).get(str(base), {}), **saved.get("exclusions", {})}
        cfg = {
            **old,
            "origin_since": old.get("origin_since", time.time()),
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
    preview = read_json(STATE / "sidebar-preview.json", {}) if cfg and cfg.get("sidebar") else {}
    return {
        "configured": bool(cfg),
        "folder": cfg and cfg["folder"],
        "device": cfg and cfg["device"],
        "automatic": bool(cfg and cfg["automatic"]),
        "paused": bool(cfg and cfg.get("paused")),
        "colours": bool(cfg and cfg.get("colours")),
        "settings": bool(cfg and cfg.get("settings")),
        "sidebar": bool(cfg and cfg.get("sidebar")),
        "order": bool(cfg and cfg.get("order")),
        "tree": bool(cfg and cfg.get("tree")),
        "marks": bool(cfg and cfg.get("marks")),
        "archive": bool(cfg and cfg.get("archive")),
        "encrypted": bool(cfg and cfg.get("encrypted")),
        "device_name": cfg and cfg.get("device_name"),
        "device_emoji": cfg and cfg.get("device_emoji", ""),
        "sidebar_preview": {k: preview[k] for k in ("id", "counts") if k in preview} or None,
        "sidebar_layout_pending": bool(
            cfg and cfg.get("sidebar") and (STATE / "sidebar-layout-pending.json").is_file()
        ),
        "last_run": read_json(STATE / "last.json") if cfg else None,
        "folder_check": last_folder_check(cfg),
    }


def last_folder_check(cfg):
    if cfg:
        return read_json(STATE / ("integrity-" + cfg["device"] + ".json"))
    return None


def set_exchange_option(option, enabled):
    if option not in ("colours", "sidebar", "order", "settings", "tree", "marks", "archive") or type(enabled) is not bool:
        raise ValueError("Unknown sync option")
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if not cfg:
            raise ValueError("Choose a private chat sync folder first.")
        if option in ("order","tree") and enabled and not cfg.get("sidebar"):
            raise ValueError("Enable sidebar sync before sharing sidebar layout.")
        if option == "sidebar" and not enabled:
            cfg["order"] = cfg["tree"] = False
        if option == "settings" and not enabled:
            (STATE / "settings-preview.json").unlink(missing_ok=True)
        if option == "marks" and not enabled:
            for name in ("renderer-marks.json", "renderer-marks-apply.json"):
                (STATE / name).unlink(missing_ok=True)
        if option in ("sidebar", "order","tree") and not enabled:
            queue = STATE / "sidebar-layout-pending.json"
            pending = read_json(queue, {})
            if pending and not pending.get("undo"):
                if option == "tree":
                    pending["changes"] = {k:v for k,v in pending.get("changes", {}).items() if not k.startswith("parent/")}
                if option == "tree" and pending["changes"]:
                    atomic(queue, encode(pending))
                else:
                    queue.unlink(missing_ok=True)
        cfg[option] = enabled
        atomic(CONFIG, encode(cfg))
    return status()


def set_encryption(enabled):
    """Encrypt chat histories and titles this desktop uploads from now on."""
    import vault_crypto

    if type(enabled) is not bool:
        raise ValueError("Choose whether to encrypt chat histories.")
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if not cfg:
            raise ValueError("Choose a private chat sync folder first.")
        created = False
        if enabled:
            if vault_crypto.stored_key() is None:
                vault_crypto.create_key()
                created = True
            # Every chat is published again in its encrypted form on the next pass.
            state_path = STATE / ("state-" + cfg["device"] + ".json")
            state = read_json(state_path, {})
            for row in state.values():
                row.pop("token", None)
            atomic(state_path, encode(state))
        cfg["encrypted"] = enabled
        atomic(CONFIG, encode(cfg))
    return {"encrypted": enabled, "key_file": str(vault_crypto.key_file()), "key_created": created}


def sync_settings_now(action="sync", approval=""):
    import settings
    import projects

    with lock(STATE / "sync.lock"):
        cfg, vault = projects.config()
        if action != "preview":
            require_active(cfg)
        if not cfg.get("settings"):
            raise ValueError("Enable appearance and chat settings sync on this desktop first.")
        if not vault.is_dir():
            raise ValueError("Sync folder unavailable. Check the mount or desktop sync client.")
        return settings.exchange(HOME, vault, cfg["device"], action, approval)


def sync_colours_now():
    import colours
    import projects

    with lock(STATE / "sync.lock"):
        cfg, vault = projects.config()
        require_active(cfg)
        if not cfg.get("colours"):
            raise ValueError("Enable colour sync on this desktop first.")
        if not vault.is_dir():
            raise ValueError("Sync folder unavailable. Check the mount or desktop sync client.")
        index = catalog() if (HOME / "state_5.sqlite").is_file() else {}
        sharing = projects.Sharing(cfg, vault, index, HOME)
        return colours.exchange(HOME, vault, cfg["device"], index, atomic, encode, sharing)


def disable():
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if cfg:
            saved = read_json(STATE / "disconnected.json", {})
            saved.setdefault("devices", {})[cfg["folder"]] = {
                k: cfg[k] for k in ("device", "device_name", "device_emoji", "origin_since", "project_links", "shared_exclusions") if k in cfg
            }
            saved["exclusions"] = {k: cfg.get(k, []) for k in ("private_chats", "private_projects", "private_folders")}
            atomic(STATE / "disconnected.json", encode(saved))
        CONFIG.unlink(missing_ok=True)
        for name in ("sidebar-layout-pending.json", "sidebar-preview.json", "settings-preview.json", "last.json"):
            (STATE / name).unlink(missing_ok=True)
    return {"configured": False}


def require_active(cfg):
    if cfg.get("paused"):
        raise ValueError("Chat sync is paused. Resume it before exchanging data.")


def set_paused(paused):
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if not cfg or type(paused) is not bool:
            raise ValueError("Choose a private sync folder first.")
        cfg["paused"] = paused
        atomic(CONFIG, encode(cfg))
        if not paused and (STATE / "sidebar-layout-pending.json").exists():
            import desktop_layout
            desktop_layout.start_worker(HOME)
    return {"paused": paused}


def catalog(include_titles=False):
    db = HOME / "state_5.sqlite"
    if not db.is_file():
        raise ValueError("This profile has no supported local Codex thread catalogue.")
    result = {}
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        columns = "id, rollout_path, name, archived, source, cwd"
        available = {info[1] for info in conn.execute("PRAGMA table_info(threads)")}
        if "project_id" in available:
            columns += ", project_id"
        if include_titles and "title" in available:
            columns += ", title"
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
            if (
                m
                and not p.is_symlink()
                and directory.resolve() in p.resolve().parents
                and p.is_file()
            ):
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
        try:
            import zstandard
        except ImportError:
            zstandard = None
        if zstandard:
            # The optional package needs no separate program, which Windows lacks by default.
            try:
                with path.open("rb") as raw, zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                    yield io.BufferedReader(reader)
            except zstandard.ZstdError as exc:
                raise ValueError("Could not unpack the compressed conversation.") from exc
            return
        if not shutil.which("zstd"):
            raise ValueError("Compressed chat needs Python 3.14+, the zstandard package or the zstd executable.")
        proc = subprocess.Popen(["zstd", "-qdc", "--", str(path)], stdout=subprocess.PIPE)
        drained = False
        try:
            yield proc.stdout
            # A reader may stop after the header or one reply. Only a stream read
            # to its end can report that the archive itself was unreadable.
            drained = not proc.stdout.read(1)
        finally:
            proc.stdout.close()
            if not drained:
                proc.kill()
            if proc.wait() and drained:
                raise ValueError("Could not unpack the compressed conversation.")
    else:
        try:
            with zstd.open(path, "rb") as stream:
                yield stream
        except zstd.ZstdError as exc:
            raise ValueError("Could not unpack the compressed conversation.") from exc


def block_path(vault, key):
    if not isinstance(key, str) or not HEX.fullmatch(key):
        raise ValueError("Invalid block reference")
    return vault / "blocks" / key[:2] / (key + ".gz")


def block_file(vault, key):
    """The stored file of a block: its encrypted form when present, otherwise the plain one."""
    plain = block_path(vault, key)
    secret = plain.with_suffix(".enc")
    return secret if secret.exists() else plain


def block_form(key, data, secret=None):
    """Return the suffix and bytes that store data under this name, or None for another block."""
    import vault_crypto

    packed = gzip.compress(data, compresslevel=3, mtime=0)
    if digest(data) == key:
        return ".gz", packed
    secret = secret or vault_crypto.stored_key()
    if secret and vault_crypto.block_name(secret, data) == key:
        return ".enc", vault_crypto.seal(secret, b"block:" + bytes.fromhex(key), packed)
    return None


def put_block(vault, data):
    import vault_crypto

    secret = vault_crypto.writing_key()
    key = vault_crypto.block_name(secret, data) if secret else digest(data)
    suffix, stored = block_form(key, data, secret)
    dest = block_path(vault, key).with_suffix(suffix)
    if not dest.exists():
        atomic(dest, stored)
    return key


def block_data(vault, key):
    path = block_file(vault, key)
    if path.stat().st_size > BLOCK + 65536:
        raise ValueError("Oversized block")
    if path.suffix == ".enc":
        import vault_crypto

        secret = vault_crypto.stored_key()
        packed = vault_crypto.unseal(secret, b"block:" + bytes.fromhex(key), path.read_bytes())
        try:
            data = zlib.decompressobj(wbits=31).decompress(packed, BLOCK + 1)
        except zlib.error as exc:
            raise ValueError("Unreadable compressed block") from exc
        if len(data) > BLOCK or vault_crypto.block_name(secret, data) != key:
            raise ValueError("Block checksum mismatch")
        return data
    try:
        with gzip.open(path, "rb") as stream:
            data = stream.read(BLOCK + 1)
    except (gzip.BadGzipFile, EOFError, zlib.error) as exc:
        raise ValueError("Unreadable compressed block") from exc
    if len(data) > BLOCK or digest(data) != key:
        raise ValueError("Block checksum mismatch")
    return data


def stage_rollout(tid, path, st, staged):
    """Freeze the wire representation locally before permitting any shared writes."""
    with open_rollout(path) as stream:
        records = workspace_paths.pieces(stream)
        first = next(records, b"")
        if len(first) > 8 * BLOCK or not first.endswith(b"\n"):
            raise ValueError("Unreadable conversation header")
        meta = json.loads(first)
        info = meta.get("payload") if isinstance(meta, dict) else None
        if not isinstance(info, dict) or meta.get("type") != "session_meta" or info.get("id") != tid:
            raise ValueError("Thread identity differs from filename")
        if info.get("source") is not None and info["source"] not in LOCAL_SOURCES:
            return None
        staged.write(first)
        for part in records:
            staged.write(part)
    size = staged.tell()
    staged.seek(max(0, size - 65536))
    tail = staged.read()
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
        if not isinstance(last, dict) or last.get("type") != "event_msg" or not isinstance(last.get("payload"), dict):
            raise Busy()
        if last["payload"].get("type") not in ("task_complete", "turn_aborted"):
            raise Busy()
    staged.seek(0)
    return info, meta.get("timestamp"), size


def export_name(row, previous):
    if previous.get("pending_name"):
        return previous["pending_name"]
    return row["name"] if row else previous.get("manifest", {}).get("name")


def snapshot_token(entry, st, name):
    path, archived = entry
    return [str(path), st.st_size, st.st_mtime_ns, st.st_ino, name, archived]


def snapshot(vault, tid, entry, name, known):
    path, archived = entry
    st = path.stat()
    token = snapshot_token(entry, st, name)
    if known and known.get("token") == token:
        return known
    with tempfile.SpooledTemporaryFile(max_size=BLOCK, dir=STATE) as staged:
        source = stage_rollout(tid, path, st, staged)
        if source is None:
            return None
        info, timestamp, size = source
        chunks = []
        while data := staged.read(BLOCK):
            chunks.append(put_block(vault, data))
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
    try:
        created_at = datetime.fromisoformat(
            str(info.get("timestamp") or timestamp).replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, OSError, OverflowError):
        created_at = 0
    return {"token": token, "manifest": manifest, "created_at": created_at}


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
    filed = man.get("archive_clock", [0, ""])
    if (
        not isinstance(filed, list)
        or len(filed) != 2
        or type(filed[0]) != int
        or filed[0] < 0
        or not isinstance(filed[1], str)
    ):
        raise ValueError("Invalid archive revision")
    return man


def archive_change(copies, mine):
    """Return [revision, archived] when another desktop filed this chat differently and later."""
    newest = max(
        (m for m in copies.values() if m.get("archive_clock")),
        key=lambda m: tuple(m["archive_clock"]),
        default=None,
    )
    if not newest or tuple(newest["archive_clock"]) <= tuple(mine.get("archive_clock", [0, ""])):
        return None
    if newest["archived"] == mine["archived"]:
        mine["archive_clock"] = newest["archive_clock"]
        return None
    return [newest["archive_clock"], newest["archived"]]


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


def read_version(vault, tid, revision):
    import vault_crypto

    data = (vault / "versions" / tid / (revision + ".json")).read_bytes()
    if vault_crypto.sealed(data):
        data = vault_crypto.unseal(vault_crypto.stored_key(), b"revision:" + bytes.fromhex(revision), data)
    if digest(data) != revision:
        raise ValueError("Invalid snapshot checksum")
    return valid_manifest(json.loads(data), tid)


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
            result.setdefault(tid, {})[path.parent.name] = read_version(vault, tid, revision)
        except (OSError, ValueError, TypeError) as exc:
            skipped[tid] = "Waiting for a complete device snapshot: " + str(exc)
    return result


def publish(vault, device, man):
    data = encode(man)
    key = digest(data)
    path = vault / "versions" / man["id"] / (key + ".json")
    if not path.exists():
        import vault_crypto

        secret = vault_crypto.writing_key()
        atomic(path, vault_crypto.seal(secret, b"revision:" + bytes.fromhex(key), data) if secret else data)
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


def recover_projection(journal):
    with native_writer(journal.name), invalidate_projection(journal.name):
        pass
    journal.unlink(missing_ok=True)


def recover_projections():
    busy = set()
    for journal in (STATE / "pending-projection").glob("*"):
        if UUID.fullmatch(journal.name):
            try:
                recover_projection(journal)
            except (Busy, OSError, ValueError, sqlite3.Error):
                # Only this chat waits for its timeline. Other chats keep syncing.
                busy.add(journal.name)
    return busy


def materialize(vault, man, local, cwd=None, checkout=None):
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
            if checkout and not workspace_paths.local_dir(checkout):
                raise ValueError("The linked checkout folder is unavailable on this device.")
            if checkout:
                fallback = Path(checkout)
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
                out.writelines(workspace_paths.pieces(raw, fallback, bool(checkout)))
                out.flush()
                os.fsync(out.fileno())
            if dest.suffix == ".zst":
                compressed = tmp + ".zst"
                try:
                    try:
                        from compression import zstd
                    except ImportError:
                        try:
                            import zstandard
                        except ImportError:
                            with open(compressed, "wb") as out:
                                subprocess.run(["zstd", "-q", "-c", "--", tmp], stdout=out, check=True)
                        else:
                            with open(tmp, "rb") as raw, open(compressed, "wb") as out:
                                zstandard.ZstdCompressor().copy_stream(raw, out)
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


@contextmanager
def sync_attempt(automatic):
    with lock(STATE / "sync.lock"):
        cfg = read_json(CONFIG)
        if not cfg:
            raise ValueError("Choose a private sync folder first.")
        health_file = STATE / ("health-" + cfg["device"] + ".json")
        health = read_json(health_file, {})
        if automatic and (cfg.get("paused") or not cfg["automatic"]
                          or time.time() - health.get("attempt", 0) < 55):
            yield None
            return
        require_active(cfg)
        health.update(attempt=time.time(), outcome="started", error=None)
        atomic(health_file, encode(health))
        try:
            yield cfg
        except Exception as exc:
            health.update(outcome="failed", error=str(exc) or "Chat is busy. Retry shortly.")
            raise
        else:
            stats = read_json(STATE / "last.json")
            issues = [str(v["error"]) for v in stats.values() if isinstance(v, dict) and v.get("error")]
            deferred = any(stats.get(k) for k in ("skipped", "conflicts", "busy", "pending_names", "unavailable_local"))
            for area in ("sidebar", "colours", "settings"):
                details = stats.get(area, {})
                if any(details.get(k) for k in ("preview_required", "desktop_refresh_on_quit", "pending_chats",
                                                "deferred_devices", "skipped_local", "sections_need_sign_in")):
                    deferred = True
            health.update(outcome="partial" if issues or deferred else "completed", error=" · ".join(issues) or None)
            if health["outcome"] == "completed":
                health["success"] = time.time()
        finally:
            atomic(health_file, encode(health))


def run(automatic=False):
    with sync_attempt(automatic) as cfg:
        if cfg is None:
            return {}
        vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
        if not vault.is_dir():
            raise ValueError("Sync folder unavailable. Check the mount or desktop sync client.")
        device = cfg["device"]
        busy = recover_projections()
        state_path = STATE / ("state-" + device + ".json")
        state = read_json(state_path, {})
        index = catalog()
        import projects, delivery

        import sharing_rules
        sharing_rules.exchange(cfg, vault)
        sharing = projects.Sharing(cfg, vault, index, HOME)
        private = sharing.private_chats
        publication = {**cfg, "private_projects": list(sharing.private_projects),
                       "private_folders": sharing.excluded_folders}
        projects.publish_links(publication, vault)
        if cfg.get("project_links") or cfg.get("sidebar") or (vault / "file-roots-v1" / (device + ".json")).exists():
            import chat_actions
            chat_actions.publish_roots(publication, vault)
        busy.difference_update(private)
        if "origin_since" not in cfg:
            cfg["origin_since"] = time.time()
            atomic(CONFIG, encode(cfg))
        origin_path = STATE / ("origins-" + device + ".json")
        origins = read_json(origin_path, {})
        original_count = len(origins)
        first_origin_pass = not origin_path.exists()
        previously_seen = {
            p.stem for p in (vault / "heads").glob("*/*.json") if p.parent.name != device
        }
        accepted = set()
        checkouts = read_json(STATE / ("checkouts-" + device + ".json"), {})
        local_files = streams()
        snapshots = {}
        imported_ids = set()
        stats = {
            "exported": 0,
            "imported": 0,
            "updated": 0,
            "conflicts": {},
            "skipped": {},
            "pending_names": 0,
        }
        # A missing cloud block or delayed head can always be retried. Local files
        # are only replaced after the complete incoming snapshot has been verified.
        for tid, entry in local_files.items():
            if tid in private or tid in busy:
                continue
            row = index.get(tid)
            if not entry or (row and row["source"] not in LOCAL_SOURCES):
                continue
            prev = state.get(tid, {})
            name = export_name(row, prev)
            try:
                snap = snapshot(vault, tid, entry, name, prev)
                if snap:
                    if tid not in origins and (
                        not prev or first_origin_pass and tid not in previously_seen
                    ):
                        if tid in previously_seen:
                            origins[tid] = "preexisting"
                        elif not prev and snap.get("created_at", 0) >= cfg["origin_since"]:
                            origins[tid] = "started"
                        else:
                            origins[tid] = "first_seen"
                    previous = prev.get("manifest", {})
                    clock = previous.get("name_clock", [0, ""])
                    if name and name != previous.get("name"):
                        clock = [clock[0] + 1, device]
                    snap["manifest"]["name_clock"] = clock
                    # Archiving here is a revision of its own. A change received from
                    # another desktop keeps that desktop's revision once it is applied.
                    filed, queued = previous.get("archive_clock"), prev.get("pending_archive")
                    if queued and queued[1] == snap["manifest"]["archived"]:
                        filed = queued[0]
                        snap.pop("pending_archive", None)
                    elif queued:
                        snap["pending_archive"] = queued
                    elif cfg.get("archive") and previous and previous.get("archived") != snap["manifest"]["archived"]:
                        filed = [(filed or [0, ""])[0] + 1, device]
                    if filed:
                        snap["manifest"]["archive_clock"] = filed
                    pending = prev.get("pending_name")
                    if pending:
                        snap["pending_name"] = pending
                    snapshots[tid] = snap
                    publish(vault, device, snap["manifest"])
                    if snap.get("token") != prev.get("token"):
                        stats["exported"] += 1
                    state[tid] = snap
            except Busy:
                busy.add(tid)
            except (
                OSError,
                ValueError,
                EOFError,
                subprocess.SubprocessError,
                sqlite3.Error,
            ) as exc:
                stats["skipped"][tid] = str(exc)
        if first_origin_pass or len(origins) != original_count:
            atomic(origin_path, encode(origins))
        heads = shared_heads(vault, stats["skipped"])
        for tid, copies in heads.items():
            if tid in private or tid in busy:
                continue
            try:
                if tid in stats["skipped"] or (tid in local_files and tid not in snapshots):
                    continue
                if tid not in local_files and tid in state:
                    continue  # A locally removed thread stays removed here.
                if sharing.blocks_incoming(vault, tid, copies.values()):
                    private.add(tid)
                    continue
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
                checkout = sharing.checkout(tid)
                if up_to_date and checkout and checkouts.get(tid) != checkout:
                    materialize(vault, current, snap, checkout=checkout)
                    checkouts[tid] = checkout
                if up_to_date:
                    accepted.add(tid)
                    if cfg.get("archive"):
                        move = archive_change(copies, current)
                        if move:
                            state[tid]["pending_archive"] = move
                    if latest_name and best["name_clock"] > current.get("name_clock", [0, ""]):
                        state[tid]["manifest"] = {
                            **current,
                            "name": best["name"],
                            "name_clock": best["name_clock"],
                        }
                        state[tid]["pending_name"] = best["name"]
                        state[tid].pop("token", None)
                    continue
                materialize(vault, best, snap, index.get(tid, {}).get("cwd"), checkout)
                accepted.add(tid)
                if checkout:
                    checkouts[tid] = checkout
                imported_ids.add(tid)
                # Carry the name until the native app server indexes the rollout.
                stored = best
                if current:
                    # The file stays where it was filed here. Only its history was replaced.
                    stored = {k: v for k, v in best.items() if k != "archive_clock"}
                    stored["archived"] = current["archived"]
                    if current.get("archive_clock"):
                        stored["archive_clock"] = current["archive_clock"]
                state[tid] = {"manifest": stored, "pending_name": best.get("name")}
                if current and cfg.get("archive"):
                    move = archive_change(copies, stored)
                    if move:
                        state[tid]["pending_archive"] = move
                stats["updated" if current else "imported"] += 1
            except Busy:
                busy.add(tid)
            except (
                OSError,
                ValueError,
                EOFError,
                subprocess.SubprocessError,
                sqlite3.Error,
            ) as exc:
                stats["skipped"][tid] = str(exc)
        stats["busy"] = len(busy)
        atomic(state_path, encode(state))
        delivery.save(STATE / ("checkouts-" + device + ".json"), checkouts)
        pending = {
            tid: s["pending_name"]
            for tid, s in state.items()
            if tid not in private
            and tid not in busy
            and s.get("pending_name")
            and (tid in local_files or "token" not in s)
        }
        if pending:
            from native_names import apply_names

            try:
                named = apply_names(pending, HOME)
            except (OSError, sqlite3.Error):
                named = []  # A busy catalogue keeps the names pending for the next pass.
            for tid in named:
                state[tid].pop("pending_name", None)
            atomic(state_path, encode(state))
        if cfg.get("archive"):
            moves = {
                tid: s["pending_archive"][1]
                for tid, s in state.items()
                if s.get("pending_archive") and tid in local_files and tid not in private and tid not in busy
            }
            if moves:
                from native_names import apply_archive

                try:
                    apply_archive(moves, HOME)
                except (OSError, sqlite3.Error, subprocess.SubprocessError):
                    pass  # The next pass sees where each file is and tries again.
                stats["archive_moves"] = len(moves)
        stats["pending_names"] = sum(bool(s.get("pending_name")) for s in state.values())
        stats["unavailable_local"] = sum(
            tid not in local_files
            for tid, row in index.items()
            if row["source"] in LOCAL_SOURCES
        )
        delivery.exchange(
            vault,
            cfg,
            heads,
            accepted,
            busy,
            stats,
            state,
            private,
            set(snapshots) | imported_ids,
            origins,
        )
        import chat_actions
        try:
            arrived = chat_actions.announce(cfg, vault, private)
        except (OSError, ValueError):
            arrived = 0  # The notice is a convenience. The inbox still lists the handoff.
        if arrived:
            stats["handoffs_received"] = arrived
        if cfg.get("sidebar"):
            import sidebar

            try:
                # Only exchange membership for the local human chats this engine handles.
                stats["sidebar"] = sidebar.exchange(
                    HOME,
                    vault,
                    device,
                    snapshots.keys() | imported_ids,
                    atomic,
                    encode,
                    cfg=cfg,
                    sharing=sharing,
                )
            except (
                OSError,
                EOFError,
                ValueError,
                sqlite3.Error,
                subprocess.SubprocessError,
            ) as exc:
                stats["sidebar"] = {"error": str(exc)}
        if cfg.get("colours"):
            import colours

            try:
                stats["colours"] = colours.exchange(
                    HOME, vault, device, index, atomic, encode, sharing
                )
            except (OSError, ValueError, sqlite3.Error) as exc:
                stats["colours"] = {"error": str(exc)}
        if cfg.get("settings"):
            import settings

            try:
                stats["settings"] = settings.exchange(HOME, vault, device)
            except (OSError, EOFError, ValueError, subprocess.SubprocessError) as exc:
                stats["settings"] = {"error": str(exc)}
        if cfg.get("marks"):
            import marks

            try:
                stats["marks"] = marks.exchange(cfg, vault)
            except Busy:
                stats["marks"] = {"waiting_for_desktop": 1}  # A bookmark is being edited right now.
            except (OSError, ValueError) as exc:
                stats["marks"] = {"error": str(exc)}
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
        require_active(cfg)
        vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
        import projects

        if tid in projects.Sharing(cfg, vault, catalog(), HOME).private_chats:
            raise ValueError("This chat is marked local only.")
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
        cfg = read_json(CONFIG)
        if not cfg or cfg["device"] == device:
            raise ValueError("Select a different device from this chat’s conflict report.")
        require_active(cfg)
        journal = STATE / "pending-projection" / tid
        if journal.is_file():
            recover_projection(journal)
        vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
        import projects

        if tid in projects.Sharing(cfg, vault, catalog(), HOME).private_chats:
            raise ValueError("This chat is marked local only.")
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
