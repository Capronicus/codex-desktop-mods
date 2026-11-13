"""Exchange receipts for verified history revisions. Device labels are user-editable."""

import os
from pathlib import Path
import platform
import time
import re
import sync

# Accept a single symbol, flag or joined emoji, including skin tones and keycaps.
_SYMBOL = r"[\U0001F000-\U0001F1E5\U0001F200-\U0001F3FA\U0001F400-\U0001FAFF\u2300-\u23FF\u2600-\u27BF\u2194-\u2199\u21A9\u21AA\u24C2\u25AA-\u25FE\u2934\u2935\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55\u00A9\u00AE\u203C\u2049\u2122\u2139\u3030\u303D\u3297\u3299]\uFE0F?[\U0001F3FB-\U0001F3FF]?"
_EMOJI = re.compile(r"(?:[\U0001F1E6-\U0001F1FF]{2}|[0-9#*]\uFE0F?\u20E3|\U0001F3F4[\U000E0061-\U000E007A]+\U000E007F|" + _SYMBOL + r"(?:\u200D" + _SYMBOL + r")*)")


def valid_emoji(value):
    return isinstance(value, str) and len(value) <= 32 and bool(_EMOJI.fullmatch(value))


def revision(man):
    return sync.digest(sync.encode(man["chunks"]))


def save(path, obj):
    data = sync.encode(obj)
    if not path.exists() or path.read_bytes() != data:
        sync.atomic(path, data)


def label(cfg):
    return cfg.get("device_name") or (platform.system() + " PC " + cfg["device"][:4])


def set_name(name, emoji=None):
    name = name.strip()
    if not name or len(name) > 60 or not name.isprintable():
        raise ValueError("Choose a readable device name of at most 60 characters.")
    if emoji is not None and emoji != "" and not valid_emoji(emoji):
        raise ValueError("Choose one emoji, or clear the field to remove it.")
    import projects

    with sync.lock(sync.STATE / "sync.lock"):
        cfg, _ = projects.config()
        cfg["device_name"] = name
        if emoji is not None:
            cfg["device_emoji"] = emoji
        sync.atomic(sync.CONFIG, sync.encode(cfg))
        # Update this desktop even when automatic transfers are paused.
        path = sync.STATE / "delivery.json"
        doc = sync.read_json(path, {})
        if doc.get("device") == cfg["device"]:
            for row in doc.get("chats", {}).values():
                origin = row.get("origin")
                if origin and origin["device"] == cfg["device"]:
                    origin.update(name=name, emoji=cfg.get("device_emoji", ""))
            save(path, doc)
    return {"device_name": name, "device_emoji": cfg.get("device_emoji", "")}


def build_label(value):
    if isinstance(value, dict) and type(value.get("version")) is str and type(value.get("build")) is str and re.fullmatch(r"[0-9a-f]{12}", str(value.get("build", ""))) and re.fullmatch(r"\d+\.\d+\.\d+", str(value.get("version", ""))):
        return value["version"] + " · " + value["build"]
    return None


def local_build():
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Capronicus/CodexDesktopMods"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "codex-sidebar-colours"
    running = sync.read_json(sync.STATE / "running-build.json", {})
    recent = isinstance(running, dict) and type(running.get("at")) is int and 0 <= time.time() - running["at"]/1000 <= 100
    return {"running": build_label(running) if recent else None,
            "ready": build_label(sync.read_json(root / "build-ready.json", {}))}


def exchange(vault, cfg, heads, accepted, busy, stats, state, private, visible, origins):
    directory = vault / "receipts-v1"
    dev = cfg["device"]
    path = directory / (dev + ".json")
    old = sync.read_json(path, {})
    ack = {k: v for k, v in old.get("received", {}).items() if k in visible and k not in private}
    waiting = {}
    for tid, peers in heads.items():
        if tid in private:
            continue
        revisions = {p: revision(m) for p, m in peers.items() if p != dev}
        if tid in accepted and revisions:
            ack[tid] = revisions
        elif tid in busy and revisions:
            waiting[tid] = revisions
    own = {
        "v": 1,
        "name": label(cfg),
        "emoji": cfg.get("device_emoji", ""),
        "received": ack,
        "busy": waiting,
        "origins": {tid: basis for tid, basis in origins.items() if tid not in private},
    }
    import bookmarks
    account = sync.digest(bookmarks.account_id().encode())
    if cfg.get("reading"):
        cursors = sync.read_json(sync.STATE / ("reading-"+dev+"-"+account+".json"), {})
        own["reading"] = {tid: row for tid,row in cursors.items() if tid in visible and tid not in private and tid in heads}
        own["readingAccount"] = account
    own["build"] = local_build()
    # Refresh the last-seen time periodically without churning cloud files each minute.
    previous = old.get("seen", 0)
    now = int(time.time())
    own["seen"] = previous if type(previous) is int and 0 <= now-previous < 300 and old.get("build")==own["build"] else now
    save(path, own)
    peers = {}
    for f in directory.glob("*.json"):
        if f == path or not sync.UUID.fullmatch(f.stem):
            continue
        try:
            d = sync.read_json(f)
            if (
                isinstance(d, dict)
                and d.get("v") == 1
                and isinstance(d.get("name"), str)
                and all(isinstance(d.get(k), dict) for k in ("received", "busy"))
            ):
                peers[f.stem] = d
        except (ValueError, OSError):
            continue
    claims = {}
    for device, doc in {**peers, dev: own}.items():
        observed = doc.get("origins", {})
        if isinstance(observed, dict):
            for tid, basis in observed.items():
                if isinstance(basis, str) and basis in ("started", "first_seen", "preexisting"):
                    claims.setdefault(tid, []).append(
                        {"device": device, "name": doc["name"][:60], "basis": basis,
                         "emoji": doc["emoji"] if valid_emoji(doc.get("emoji")) else ""}
                    )
    result = {}
    for tid in visible | busy | set(stats["conflicts"]):
        if tid in private:
            continue
        local = state.get(tid, {}).get("manifest")
        token = revision(local) if local else None
        status = (
            "conflict"
            if tid in stats["conflicts"]
            else "busy"
            if tid in busy
            else "waiting"
            if tid in stats["skipped"] or not local
            else "saved"
        )
        delivered = []
        blocked = []
        for device, peer in peers.items():
            for field, items in (("received", delivered), ("busy", blocked)):
                row = peer[field].get(tid)
                if token and isinstance(row, dict) and row.get(dev) == token:
                    items.append({"device": device, "name": peer["name"][:60]})
        if status == "saved" and delivered:
            status = "received"
        candidates = claims.get(tid, [])
        origin = (
            candidates[0]
            if len(candidates) == 1 and candidates[0]["basis"] != "preexisting"
            else None
        )
        cursors = []
        if cfg.get("reading") and tid in visible:
            for device, peer in peers.items():
                if peer.get("readingAccount") != account or not isinstance(peer.get("reading"), dict): continue
                row = peer["reading"].get(tid)
                if not isinstance(row,dict) or not isinstance(row.get("turn"),str) or not sync.UUID.fullmatch(row["turn"]): continue
                if type(row.get("at")) is not int or not 0 < row["at"] < 2**53: continue
                cursors.append({"device":device,"name":peer["name"][:60],"turn":row["turn"],"at":row["at"],
                                "role":"user" if row.get("role")=="user" else "assistant","item":str(row.get("item",""))[:160]})
        result[tid] = {
            "reading": sorted(cursors,key=lambda r:-r["at"]),
            "status": status,
            "received_by": delivered,
            "waiting_on": blocked,
            "origin": origin,
        }
    for tid in private:
        result[tid] = {"status": "local_only", "received_by": [], "waiting_on": []}
    save(sync.STATE / "delivery.json", {"device": dev, "peers": {device: row["name"][:60] for device, row in peers.items()}, "chats": result,
        "builds": [{"name": p["name"][:60], "build": p.get("build"), "seen": p.get("seen")} for p in peers.values()]})


def status(thread):
    if not sync.UUID.fullmatch(thread):
        raise ValueError("Choose a local chat UUID.")
    from projects import config

    cfg, _ = config()
    doc = sync.read_json(sync.STATE / "delivery.json", {})
    if thread in cfg.get("private_chats", []):
        return {"status": "local_only"}
    return (
        doc.get("chats", {}).get(thread, {"status": "awaiting_sync"})
        if doc.get("device") == cfg["device"]
        else {"status": "awaiting_sync"}
    )
