"""User-initiated actions for local replies and linked checkouts."""
import base64
import codecs
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import time
import uuid

import delivery
import projects
import sync


def checked_id(value):
    if not projects.valid_id(value):
        raise ValueError("Invalid chat, turn or device ID.")
    return value


def peers(vault):
    result = {}
    for path in (vault / "receipts-v1").glob("*.json"):
        if not projects.valid_id(path.stem):
            continue
        doc = projects.peer_file(path)
        if doc.get("v") == 1 and isinstance(doc.get("name"), str):
            result[path.stem] = doc["name"][:60]
    return result


def source_path(value):
    # Parse using the sender's path syntax, irrespective of the receiver's OS.
    return PureWindowsPath(value) if re.match(r"^[A-Za-z]:[\\/]", value) else PurePosixPath(value)


def root_key(root):
    name = root.as_posix()
    if isinstance(root, PureWindowsPath):
        name = name.casefold()
    return hashlib.sha256(name.encode()).hexdigest()


def root_keys(folder):
    paths = [folder]
    if os.name == "nt":
        # Shells and desktop links can use the 8.3 spelling of an ancestor.
        import ctypes
        short_name = ctypes.windll.kernel32.GetShortPathNameW
        length = short_name(str(folder), None, 0)
        if length:
            buf = ctypes.create_unicode_buffer(length)
            if 0 < short_name(str(folder), buf, length) < length:
                short = PureWindowsPath(buf.value)
                long = PureWindowsPath(folder)
                if len(long.parts) == len(short.parts):
                    paths.extend(str(PureWindowsPath(*short.parts[:n], *long.parts[n:]))
                                 for n in range(1, len(short.parts) + 1))
    return {root_key(source_path(path)) for path in paths}


def publish_roots(cfg, vault):
    local = projects.local_projects(sync.HOME)
    links = projects.Links(cfg, vault, local)
    excluded = {links.wire(pid) for pid in cfg.get("private_projects", [])}
    rows = {}
    for pid, project in local.items():
        wire = links.wire(pid)
        if wire in excluded or not projects.valid_id(pid):
            continue
        checkout = links.checkouts.get(wire)
        folders = [checkout] if checkout else project["folders"]
        # A relative suffix is ambiguous across multiple different roots.
        for folder in folders if len(folders) == 1 else ():
            if folder and Path(folder).is_absolute() and not projects.folder_excluded(folder, cfg):
                rows.setdefault(wire, set()).update(root_keys(folder))
    data = {pid: sorted(values) for pid, values in rows.items()}
    path = vault / "file-roots-v1" / (cfg["device"] + ".json")
    if data or path.exists():
        delivery.save(path, data)


def resolve_file(thread, reference, *, allow_missing=False, relative=False):
    checked_id(thread)
    cfg, vault = projects.config()
    index = sync.catalog()
    sharing = projects.Sharing(cfg, vault, index, sync.HOME)
    root = sharing.checkout(thread)
    if thread in sharing.private_chats or not root:
        raise ValueError("Choose a shared chat with an explicitly linked local checkout.")
    if not isinstance(reference, str) or not 0 < len(reference) <= 4096 or any(ord(c) < 32 for c in reference):
        raise ValueError("Select a project file path.")
    reference = re.sub(r"(?<=\S):\d+(?::\d+)?(?:-\d+(?::\d+)?)?$|#L\d+(?:-L\d+)?$", "", reference.strip())
    if reference.startswith(("//", "\\")) or "://" in reference:
        raise ValueError("Choose a path inside the linked project.")
    source = source_path(reference.replace("\\", "/"))
    if ".." in source.parts:
        raise ValueError("The reference leaves the project.")
    if source.is_absolute():
        wire = sharing.links.wire(sharing.members[thread])
        hashes = {root_key(source_path(root))}
        for f in (vault / "file-roots-v1").glob("*.json"):
            if projects.valid_id(f.stem):
                for pid, keys in projects.peer_file(f).items():
                    if projects.valid_id(pid) and sharing.links.wire(pid) == wire and isinstance(keys, list):
                        hashes.update(k for k in keys if isinstance(k, str) and sync.HEX.fullmatch(k))
        base = next((p for p in source.parents if root_key(p) in hashes), None)
        if base is None:
            raise ValueError("Unknown source root. Sync the linked project on its original desktop, or enter the project-relative path.")
        source = source.relative_to(base)
    if any(":" in part or part.endswith((".", " ")) or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part, re.I) for part in source.parts):
        raise ValueError("Unsupported project filename.")
    checkout = Path(root).resolve(strict=True)
    file = checkout.joinpath(*source.parts).resolve()
    if checkout not in file.parents or projects.folder_excluded(str(file), {"private_folders":sharing.excluded_folders}):
        raise ValueError("The file is outside the shared checkout.")
    if not allow_missing and not file.is_file():
        raise ValueError("This file is missing from the linked checkout on this PC.")
    return file.relative_to(checkout).as_posix() if relative else file


def open_file(thread, reference):
    file = resolve_file(thread, reference)
    with file.open("rb") as stream:
        data = stream.read(256 * 1024 + 1)
    result = {"path": str(file), "truncated": len(data) > 256 * 1024}
    for signature, mime in [(b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF89a", "image/gif"), (b"GIF87a", "image/gif")]:
        if data.startswith(signature) and not result["truncated"]:
            return {**result, "image": "data:" + mime + ";base64," + base64.b64encode(data).decode()}
    if b"\0" not in data:
        try:
            return {**result, "text": codecs.getincrementaldecoder("utf-8-sig")().decode(data[:256*1024], final=not result["truncated"])}
        except UnicodeDecodeError:
            pass
    return {**result, "binary": True}


def local_source(thread):
    entry = sync.streams().get(checked_id(thread))
    if not entry:
        raise ValueError("This chat is not available on this PC yet. Sync it and try again.")
    return entry[0]


def records(stream):
    # Skip oversized tool payloads without buffering them in memory.
    limit = 8 * 1024 * 1024
    while line := stream.readline(limit):
        if len(line) == limit and not line.endswith(b"\n"):
            while line and not line.endswith(b"\n"):
                line = stream.readline(limit)
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict) and isinstance(row.get("payload"), dict):
                yield row
        except (UnicodeError, ValueError):
            continue


def messages(rows):
    turn = last_user = None
    for row in rows:
        p = row["payload"]
        if row["type"] == "turn_context" or (row["type"] == "event_msg" and p.get("type") == "task_started"):
            if p.get("turn_id") and p["turn_id"] != turn:
                turn, last_user = p["turn_id"], None
        if row["type"] == "event_msg" and p.get("type") == "user_message":
            text = p.get("message")
            if isinstance(text,str) and text and text!=last_user:
                last_user = text
                yield {"turn":turn,"item":None,"role":"user","text":text}
        if row["type"] == "event_msg" and p.get("type") == "item_completed" and p.get("item", {}).get("type") == "UserMessage":
            item = p["item"]
            text = "\n".join(x["text"] for x in item.get("content", []) if isinstance(x, dict) and isinstance(x.get("text"), str))
            if text and text!=last_user:
                last_user = text
                yield {"turn": p.get("turn_id") or turn, "item": item.get("id"), "role": "user", "text": text}
        if row["type"] != "response_item" or p.get("type") != "message" or p.get("role") != "assistant":
            continue
        text = "\n".join(x["text"] for x in p.get("content", []) if isinstance(x, dict) and isinstance(x.get("text"), str))
        if text:
            yield {"turn": p.get("turn_id") or turn, "item": p.get("id"), "role": p["role"], "text": text}


def reply(thread, turn, item=""):
    checked_id(turn)
    last = None
    with sync.open_rollout(local_source(thread)) as stream:
        for msg in messages(records(stream)):
            if msg["turn"] == turn and msg["role"] == "assistant":
                last = msg
                if item and item == msg["item"]:
                    break
            elif last and msg["turn"] != turn:
                break
    if not last or (item and last["item"] != item):
        raise ValueError("This reply is not present in this PC's chat history yet.")
    return {"text": last["text"][:16000], "truncated": len(last["text"]) > 16000}


def fingerprint(file):
    value = hashlib.sha256()
    with file.open("rb") as stream:
        before = os.fstat(stream.fileno())
        for block in iter(lambda:stream.read(1024*1024),b""):
            value.update(block)
        after = os.fstat(stream.fileno())
    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise ValueError("The file changed during comparison. Try again.")
    return value.hexdigest()


def handoff(thread, turn, device, request, note="", item="", reference=""):
    for ident in (thread, turn, device, request):
        checked_id(ident)
    if not isinstance(note, str) or len(note) > 500 or not isinstance(item, str) or len(item) > 160:
        raise ValueError("Use a note of up to 500 characters.")
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = projects.config()
        sync.require_active(cfg)
        if device == cfg["device"] or device not in peers(vault):
            raise ValueError("Choose another desktop that has synced this folder.")
        if thread in projects.Sharing(cfg, vault, sync.catalog(), sync.HOME).private_chats:
            raise ValueError("This chat is local only.")
        reply(thread, turn, item)
        path = vault / "handoffs-v1" / device / cfg["device"] / (request + ".json")
        obj = {"thread": thread, "turn": turn, "item": item, "note": note}
        if reference:
            obj["file"] = resolve_file(thread, reference, relative=True)
        old = sync.read_json(path, {})
        if old and (obj != {k: old.get(k) for k in obj} or old.get("file") != obj.get("file")):
            raise ValueError("This handoff was already sent.")
        if not old:
            if reference: obj["sha256"] = fingerprint(resolve_file(thread,reference))
            sync.atomic(path, sync.encode({**obj, "created": int(time.time())}))
        return {"sent": True}


def inbox():
    cfg, vault = projects.config()
    labels = peers(vault)
    hidden = sync.read_json(sync.STATE / ("handoffs-done-" + cfg["device"] + ".json"), {})
    files = list((vault / "handoffs-v1" / cfg["device"]).glob("*/*.json"))
    if not files:return []
    private = projects.Sharing(cfg, vault, sync.catalog(), sync.HOME).private_chats
    result = []
    for f in files:
        sender = f.parent.name
        if not all(projects.valid_id(i) for i in (sender, f.stem)) or sender + "/" + f.stem in hidden:
            continue
        obj = projects.peer_file(f)
        if not all(projects.valid_id(obj.get(k)) for k in ("thread", "turn")) or obj["thread"] in private:
            continue
        if not isinstance(obj.get("note"), str) or not isinstance(obj.get("item"), str) or type(obj.get("created")) is not int:
            continue
        result.append({**{k: obj[k] for k in ("thread","turn","created")}, "item": obj["item"][:160],
                       "note": obj["note"][:500], "file": obj.get("file","") if isinstance(obj.get("file"),str) and isinstance(obj.get("sha256"),str) and sync.HEX.fullmatch(obj["sha256"]) else "", "sender": sender, "name": labels.get(sender, sender), "id": f.stem})
    return sorted(result, key=lambda r: -r["created"])


def compare_handoff_file(sender, request):
    checked_id(sender)
    checked_id(request)
    cfg, vault = projects.config()
    obj = projects.peer_file(vault / "handoffs-v1" / cfg["device"] / sender / (request+".json"))
    reference, digest = obj.get("file"), obj.get("sha256")
    if not isinstance(reference,str) or not isinstance(digest,str) or not sync.HEX.fullmatch(digest):
        raise ValueError("This handoff has no file fingerprint.")
    local = resolve_file(obj["thread"], reference, allow_missing=True)
    state = "Missing here" if not local.is_file() else "Same version" if fingerprint(local)==digest else "Changed here"
    return {"status":state,"reference":reference}


def dismiss(sender, request):
    checked_id(sender)
    checked_id(request)
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, _ = projects.config()
        path = sync.STATE / ("handoffs-done-" + cfg["device"] + ".json")
        done = sync.read_json(path, {})
        done[sender + "/" + request] = True
        sync.atomic(path, sync.encode(done))
    return {"done": True}


def family(thread):
    checked_id(thread)
    index = sync.catalog(include_titles=True)
    parent = {}
    local = sync.streams()
    for tid, entry in local.items():
        if not entry:
            continue
        try:
            with sync.open_rollout(entry[0]) as stream:
                meta = json.loads(stream.readline(2 * 1024 * 1024))
                p = meta.get("payload", {})
                if meta.get("type") == "session_meta" and p.get("id") == tid and projects.valid_id(p.get("forked_from_id")):
                    parent[tid] = p["forked_from_id"]
        except (ValueError, OSError, EOFError):
            continue
    def row(tid, relation):
        r = index.get(tid, {})
        return {"thread": tid, "name": r.get("name") or r.get("title") or tid, "relation": relation, "available": bool(local.get(tid))}
    rows = [row(parent[thread], "Parent")] if thread in parent else []
    for tid, pid in parent.items():
        if pid == thread:
            rows.append(row(tid, "Branch"))
        elif tid != thread and pid == parent.get(thread):
            rows.append(row(tid, "Sibling"))
    return rows


def conflict(thread, left, right):
    from contextlib import ExitStack
    from itertools import zip_longest
    import tempfile

    for ident in (thread, left, right):
        checked_id(ident)
    cfg, vault = projects.config()
    if left == right or thread in projects.Sharing(cfg, vault, sync.catalog(), sync.HOME).private_chats:
        raise ValueError("Choose two different snapshots of a shared chat.")
    missing = {}
    copies = sync.shared_heads(vault, missing).get(thread, {})
    if thread in missing or left not in copies or right not in copies:
        raise ValueError("Wait for both device snapshots to finish syncing.")
    sides = [[], []]
    counts = [0, 0]
    common = None
    diverged = False

    def snippet(msg):
        return {"role": msg["role"], "text": msg["text"][:1400] + ("…" if len(msg["text"]) > 1400 else "")}

    with ExitStack() as stack:
        streams = []
        for device in (left, right):
            man = copies[device]
            f = stack.enter_context(tempfile.TemporaryFile(dir=sync.STATE))
            for i, key in enumerate(man["chunks"]):
                data = sync.block_data(vault, key)
                if len(data) != min(sync.BLOCK, man["size"] - i * sync.BLOCK):
                    raise ValueError("Incomplete device snapshot.")
                f.write(data)
            f.seek(0)
            streams.append(messages(records(f)))
        for a, b in zip_longest(*streams):
            if not diverged and a and b and a["role"] == b["role"] and a["text"] == b["text"]:
                common = snippet(a)
                continue
            diverged = True
            for i, msg in enumerate((a,b)):
                if msg:
                    counts[i] += 1
                    if len(sides[i]) < 12:
                        sides[i].append(snippet(msg))
    return {"common": common, "left": sides[0], "right": sides[1], "counts": counts}


def desktop_context():
    doc = sync.read_json(sync.STATE / "desktop-request.json", {})
    if not isinstance(doc.get("at"), (int, float)) or not 0 <= time.time() - doc["at"] / 1000 < 600:
        return {}
    if doc.get("action") == "new-bookmark":
        from bookmarks import account_id
        if doc.get("account") != account_id(): return {}
    return doc


def dashboard():
    cfg = sync.read_json(sync.CONFIG, {})
    result = {"context": desktop_context(), "devices": {}, "inbox": [], "projects": {},
              "folders": cfg.get("private_folders", []), "privateProjects": cfg.get("private_projects", []),
              "conflicts": {}, "performance": sync.read_json(sync.STATE / "desktop-performance.json", {})}
    if not cfg:
        return result
    vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
    if not vault.is_dir():
        return result
    import sharing_rules
    labels = peers(vault)
    local = projects.local_projects(sync.HOME)
    links = projects.Links(cfg,vault,local)
    result["globalRules"] = {k: (local.get(links.local(k.partition("/")[0]),{}).get("name") or k.partition("/")[0])
                            + ("/"+k.partition("/")[2] if "/" in k else "")
                            for k,r in sharing_rules.canonical(sharing_rules.records(cfg,vault),links).items() if r[1]}
    result["projectKeys"] = {pid:links.wire(pid) for pid in local}
    result.update(devices={k: v for k, v in labels.items() if k != cfg["device"]}, labels=labels,
                  inbox=inbox(), projects=local)
    stats = sync.read_json(sync.STATE / "last.json", {})
    result["conflicts"] = stats.get("conflicts", {})
    if result["conflicts"]:
        index = sync.catalog(include_titles=True)
        result["conflictNames"] = {t: index[t].get("name") or index[t].get("title") or t
                                    for t in result["conflicts"] if t in index}
    return result


def desktop_command(action, thread="", turn="", item=""):
    if action not in ("measure", "navigate") or (action == "navigate" and not projects.valid_id(thread)):
        raise ValueError("Unknown desktop action.")
    if turn:
        checked_id(turn)
    if len(item) > 160:
        raise ValueError("Invalid reply identifier.")
    doc = {"action": action, "id": str(uuid.uuid4()), "thread": thread, "turn": turn,
           "item": item, "at": int(time.time()*1000)}
    sync.atomic(sync.STATE / "desktop-command.json", sync.encode(doc))
    return {"requested": action}


def edit_file(thread, reference):
    import shutil
    import subprocess
    file = resolve_file(thread, reference)
    # Always pass a document to a known editor. A script in the checkout is data.
    if os.name == "nt":
        editor = Path(os.environ["SystemRoot"]) / "System32" / "notepad.exe"
    else:
        editor = next((path for name in ("code","kate","gnome-text-editor","gedit","mousepad")
                       if (path := shutil.which(name))), None)
    if not editor:
        raise ValueError("No supported text editor was found. Use Show in folder to choose an application.")
    subprocess.Popen([str(editor), str(file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"path": str(file)}


def reveal_file(thread, reference):
    import subprocess
    import sys
    file = resolve_file(thread, reference)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", "/select,", str(file)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(file)])
    else:
        subprocess.Popen(["xdg-open", str(file.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"path": str(file)}
