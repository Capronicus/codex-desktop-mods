"""Exchange receipts for verified history revisions. Device labels are user-editable."""

import platform
import sync


def revision(man):
    return sync.digest(sync.encode(man["chunks"]))


def save(path, obj):
    data = sync.encode(obj)
    if not path.exists() or path.read_bytes() != data:
        sync.atomic(path, data)


def label(cfg):
    return cfg.get("device_name") or (platform.system() + " PC " + cfg["device"][:4])


def set_name(name):
    name = name.strip()
    if not name or len(name) > 60 or not name.isprintable():
        raise ValueError("Choose a readable device name of at most 60 characters.")
    import projects

    with sync.lock(sync.STATE / "sync.lock"):
        cfg, _ = projects.config()
        cfg["device_name"] = name
        sync.atomic(sync.CONFIG, sync.encode(cfg))
    return {"device_name": name}


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
        "received": ack,
        "busy": waiting,
        "origins": {tid: basis for tid, basis in origins.items() if tid not in private},
    }
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
                        {"device": device, "name": doc["name"][:60], "basis": basis}
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
        result[tid] = {
            "status": status,
            "received_by": delivered,
            "waiting_on": blocked,
            "origin": origin,
        }
    for tid in private:
        result[tid] = {"status": "local_only", "received_by": [], "waiting_on": []}
    save(sync.STATE / "delivery.json", {"device": dev, "chats": result})


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
