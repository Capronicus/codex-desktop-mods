"""Local state exposed by the Mod Controls view."""

from collections import Counter
from pathlib import Path

import delivery
import sync

FEATURES = ("wideChat", "petDock", "petMotion", "timestamps", "tokens", "branchLines")
URI = "ui://chat-sync/mod-controls.html"
MIME = "text/html;profile=mcp-app"


def visuals():
    saved = sync.read_json(sync.HOME / "desktop-mod-controls.json", {})
    return {key: saved.get(key) is not False for key in FEATURES}


def set_visual(feature, enabled):
    if feature not in FEATURES or type(enabled) is not bool:
        raise ValueError("Unknown appearance setting")
    with sync.lock(sync.STATE / "visuals.lock"):
        values = visuals()
        values[feature] = enabled
        sync.atomic(sync.HOME / "desktop-mod-controls.json", sync.encode(values))
    return values


def status():
    cfg = sync.read_json(sync.CONFIG, {})
    device = cfg.get("device")
    folder = cfg.get("folder")
    health = sync.read_json(sync.STATE / ("health-" + device + ".json"), {}) if device else {}
    receipts = sync.read_json(sync.STATE / "delivery.json", {}) if device else {}
    rows = receipts.get("chats", {}).values() if device and receipts.get("device") == device else ()
    counts = Counter(row["status"] for row in rows)
    peers = {}
    for row in rows:
        if row["status"] != "received":
            continue
        for peer in row["received_by"]:
            item = peers.setdefault(peer["device"], {"name": peer["name"], "received": 0})
            item["received"] += 1
    rules = cfg or sync.read_json(sync.STATE / "disconnected.json", {}).get("exclusions", {})
    return {
        "configured": bool(device), "device": delivery.label(cfg) if device else None,
        "folder": folder, "available": bool(folder and (Path(folder) / "codex-chat-sync-v1").is_dir()),
        "paused": bool(cfg.get("paused")), "automatic": bool(cfg.get("automatic")),
        "options": {k: bool(cfg.get(k)) for k in ("colours", "settings", "sidebar", "order")},
        "health": health, "receipts": counts, "peers": list(peers.values()),
        "privateChats": rules.get("private_chats", []), "privateProjects": rules.get("private_projects", []),
        "visuals": visuals(),
    }


def resource():
    html = Path(__file__).with_name("mod-controls.html").read_text(encoding="utf-8")
    return {"uri": URI, "mimeType": MIME, "text": html,
            "_meta": {"openai/ui": {"availableDisplayModes": ["inline", "fullscreen"],
                                    "preferredDisplayMode": "fullscreen"}}}


def sync_now():
    stats = sync.run()
    return {key: stats[key] for key in ("exported", "imported", "updated")}
