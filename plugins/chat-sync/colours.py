"""Exchange the sidebar mod's colour preferences through the private chat vault."""
from contextlib import closing
import json
import re
import sqlite3

ID = re.compile(r"[A-Za-z0-9:_@.-]{1,256}")
KEY = re.compile(r"(chat|project|section|override-project|override-section|swatch)/([A-Za-z0-9:_@.-]{1,256})")
HEX = re.compile(r"#[0-9a-fA-F]{6}")
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS colours (key TEXT PRIMARY KEY, value TEXT, rev INTEGER NOT NULL, author TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inherited (chat TEXT PRIMARY KEY, project TEXT, section TEXT);
"""


def valid(row):
    if not isinstance(row, (list, tuple)) or len(row) != 4:
        return False
    key, value, rev, author = row
    match = isinstance(key, str) and KEY.fullmatch(key)
    if not match or type(rev) is not int or not 0 <= rev < 2**52 or not isinstance(author, str) or not UUID.fullmatch(author):
        return False
    kind, item = match.groups()
    if kind == "swatch":
        return bool(re.fullmatch("[a-f0-9]{6}", item)) and value in (None, "#" + item)
    if value is None:
        return True
    return value == "on" if kind.startswith("override-") else isinstance(value, str) and bool(HEX.fullmatch(value))


def memberships(home, colours, index):
    # Read sidebar membership locally. The shared file contains only IDs of
    # chats and colour groups, including memberships of collapsed projects.
    path = home / ".codex-global-state.json"
    state = json.loads(path.read_bytes()) if path.is_file() else {}
    projects = state.get("local-projects", {})
    sections = state.get("electron-persisted-atom-state", {}).get("sidebar-custom-sections-v3", {})
    legacy = {}
    for host, aliases in state.get("app-server-project-id-by-legacy-project-id-by-host", {}).items():
        if host.startswith("local:") and isinstance(aliases, dict):
            legacy.update({new:old for old,new in aliases.items() if isinstance(new, str)})
    assignments = {tid: row.get("project_id") for tid, row in index.items()
                   if row.get("source") in ("cli", "vscode", "appServer", "app_server")}
    for tid, record in state.get("thread-project-assignments", {}).items():
        if isinstance(record, dict):
            assignments[tid] = record.get("projectId")
    links = {}
    for tid, group in assignments.items():
        if not isinstance(group, str) or not UUID.fullmatch(tid):
            continue
        old = legacy.get(group, group)
        linked = (projects.get(old, {}).get("chatGptBacking") or {}).get("projectId")
        owner = next((g for g in (linked, group, old) if g and "project/" + g in colours), None)
        if owner:
            links["local:" + tid] = [owner, None]
    for account in sections.values():
        for section in account.get("sections", []) if isinstance(account, dict) else []:
            sid = section.get("id")
            if not isinstance(sid, str) or "section/" + sid not in colours:
                continue
            for key in section.get("itemKeys", []):
                if isinstance(key, str):
                    chat = key.removeprefix("codex:thread:")
                    if ID.fullmatch(chat):
                        links.setdefault(chat, [None, None])[1] = sid
    return links


def exchange(home, vault, device, index, atomic, encode):
    directory = vault / "colours-v1"
    peers = []
    deferred = []
    for file in sorted(directory.glob("*.json")):
        if not UUID.fullmatch(file.stem) or file.stem == device:
            continue
        try:
            doc = json.loads(file.read_bytes())
            if doc["version"] != 1 or not all(valid(row) for row in doc["entries"]):
                raise ValueError("Invalid colour records")
            for key, pair in doc["members"].items():
                if not ID.fullmatch(key) or not isinstance(pair, list) or len(pair) != 2 or not all(
                    item is None or isinstance(item, str) and ID.fullmatch(item) for item in pair
                ):
                    raise ValueError("Invalid colour group references")
            peers.append(doc)
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            deferred.append(file.stem)

    with closing(sqlite3.connect(home / "sidebar-colours.sqlite", timeout=3)) as db:
        db.executescript(SCHEMA)
        # Only the compact preference merge holds the lock shared with Electron.
        with db:
            db.execute("BEGIN IMMEDIATE")
            updates = 0
            for peer in peers:
                result = db.executemany("""
                    INSERT INTO colours VALUES (?,?,?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value,rev=excluded.rev,author=excluded.author
                    WHERE (excluded.rev,excluded.author) > (colours.rev,colours.author)
                """, peer["entries"])
                updates += result.rowcount
            rows = db.execute("SELECT key,value,rev,author FROM colours ORDER BY key").fetchall()
        rows = [r for r in rows if valid(r)]
        active = {key for key, value, _, _ in rows if value is not None}
        own = memberships(home, active, index)
        hints = {}
        for peer in peers:
            for chat, (project, section) in peer["members"].items():
                current = hints.setdefault(chat, [None, None])
                for i, group in enumerate((project, section)):
                    if group and not own.get(chat, [None, None])[i] and not current[i]:
                        current[i] = group
        hints = {k:v for k,v in hints.items() if any(v)}
        old = {k:[p,s] for k,p,s in db.execute("SELECT chat,project,section FROM inherited")}
        if not deferred and hints != old:
            with db:
                db.execute("DELETE FROM inherited")
                db.executemany("INSERT INTO inherited VALUES (?,?,?)", ((k,*pair) for k,pair in hints.items()))
    head = directory / (device + ".json")
    content = encode({"version":1, "entries":rows, "members":own})
    if not head.is_file() or head.read_bytes() != content:
        atomic(head, content)
    return {"preferences":len(rows), "received":updates, "deferred_devices":deferred}
