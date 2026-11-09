"""Exchange the sidebar mod's colour preferences through the private chat vault."""

from contextlib import closing
import json
import re
import sqlite3

import desktop_layout
import projects
import sync

ID = re.compile(r"[A-Za-z0-9:_@.-]{1,256}")
KEY = re.compile(
    r"(chat|project|section|override-project|override-section|swatch)/([A-Za-z0-9:_@.-]{1,256})"
)
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
    if (
        not match
        or type(rev) is not int
        or not 0 <= rev < 2**52
        or not isinstance(author, str)
        or not UUID.fullmatch(author)
    ):
        return False
    kind, item = match.groups()
    if kind == "swatch":
        return bool(re.fullmatch("[a-f0-9]{6}", item)) and value in (None, "#" + item)
    if value is None:
        return True
    return (
        value == "on"
        if kind.startswith("override-")
        else isinstance(value, str) and bool(HEX.fullmatch(value))
    )


def memberships(state, colours, index):
    # Read sidebar membership locally. The shared file contains only IDs of
    # chats and colour groups, including memberships of collapsed projects.
    projects = state.get("local-projects", {})
    sections = state.get("electron-persisted-atom-state", {}).get("sidebar-custom-sections-v3", {})
    legacy = {}
    for host, aliases in state.get(
        "app-server-project-id-by-legacy-project-id-by-host", {}
    ).items():
        if host.startswith("local:") and isinstance(aliases, dict):
            legacy.update({new: old for old, new in aliases.items() if isinstance(new, str)})
    assignments = {
        tid: row.get("project_id")
        for tid, row in index.items()
        if row.get("source") in ("cli", "vscode", "appServer", "app_server")
    }
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


class GroupIds:
    def __init__(self, home, vault, layout, peers):
        cfg = sync.read_json(home / "chat-sync/config.json", {})
        local = {k: {"folders": v.get("rootPaths", [])} for k, v in layout.projects.items()}
        self.links = projects.Links(cfg, vault, local)
        self.native_projects = {v: k for k, v in layout.project_ids.items()}
        self.backing = {}
        self.own_backing = {}
        for doc in peers:
            for k, cloud in doc.get("project_backing", {}).items():
                self.backing.setdefault(self.links.wire(k), cloud)
        for k, project in layout.projects.items():
            cloud = (project.get("chatGptBacking") or {}).get("projectId")
            if (
                projects.valid_id(k)
                and isinstance(cloud, str)
                and cloud.startswith("g-p-")
                and ID.fullmatch(cloud)
            ):
                self.own_backing[k] = cloud
                self.backing[self.links.wire(k)] = cloud
        mapped = sync.read_json(home / "chat-sync/sidebar-section-ids.json", {}).get("aliases", {})
        self.sections = {v: k for k, v in {**mapped, **layout.section_ids}.items()}
        self.project_keys = {}
        for k in local:
            self.project_keys.setdefault(self.group("project", k), set()).add(k)

    def group(self, kind, group):
        if not group:
            return group
        if kind == "section":
            return self.sections.get(group, group)
        group = self.links.wire(self.native_projects.get(group, group))
        return self.backing.get(group, group)

    def key(self, key):
        kind, value = key.split("/")
        group = kind.removeprefix("override-")
        if group in ("project", "section"):
            return kind + "/" + self.group(group, value)
        return key

    def local_keys(self, key):
        kind, value = key.split("/")
        if kind.endswith("project"):
            ids = self.project_keys.get(value)
            if ids:
                keys = {kind + "/" + i for i in ids}
                if value.startswith("g-p-"):
                    keys.add(key)
                return keys
        return {key}

    def local_project(self, value):
        ids = self.project_keys.get(value)
        return self.links.local(min(ids)) if ids and not value.startswith("g-p-") else value


def read_peers(directory, device):
    peers, deferred = [], []
    for file in sorted(directory.glob("*.json")):
        if not UUID.fullmatch(file.stem) or file.stem == device:
            continue
        try:
            doc = json.loads(file.read_bytes())
            if doc["version"] != 1 or not all(valid(row) for row in doc["entries"]):
                raise ValueError("Invalid colour records")
            for key, pair in doc["members"].items():
                if (
                    not ID.fullmatch(key)
                    or not isinstance(pair, list)
                    or len(pair) != 2
                    or not all(
                        item is None or isinstance(item, str) and ID.fullmatch(item)
                        for item in pair
                    )
                ):
                    raise ValueError("Invalid colour group references")
            if not all(
                projects.valid_id(k)
                and isinstance(v, str)
                and v.startswith("g-p-")
                and ID.fullmatch(v)
                for k, v in doc.get("project_backing", {}).items()
            ):
                raise ValueError("Invalid project backing")
            peers.append(doc)
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            deferred.append(file.stem)
    return peers, deferred


def exchange(home, vault, device, index, atomic, encode, sharing=None):
    directory = vault / "colours-v1"
    peers, deferred = read_peers(directory, device)
    layout = desktop_layout.Layout(home)
    ids = GroupIds(home, vault, layout, peers)
    private_groups = (
        {ids.group("project", k) for k in sharing.private_projects} if sharing else set()
    )

    def allowed(key):
        return not sharing or (
            sharing.allows_colour(key)
            and not (
                key.split("/")[0].endswith("project")
                and ids.key(key).split("/")[1] in private_groups
            )
        )

    prefix = "codex.sidebar-colours.v1/"
    legacy = [
        (k[len(prefix) :], v, 0, device)
        for k, v in layout.data.get("electron-persisted-atom-state", {}).items()
        if k.startswith(prefix)
    ]
    with closing(sqlite3.connect(home / "sidebar-colours.sqlite", timeout=3)) as db:
        db.executescript(SCHEMA)
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.executemany(
                "INSERT OR IGNORE INTO colours VALUES (?,?,?,?)", (r for r in legacy if valid(r))
            )
            local = {
                r[0]: r for r in db.execute("SELECT key,value,rev,author FROM colours") if valid(r)
            }
            rows, aliases = {}, {}

            def merge(row):
                key, value, rev, author = row
                if allowed(key):
                    wire = ids.key(key)
                    record = (wire, value, rev, author)
                    if wire not in rows or record[2:] > rows[wire][2:]:
                        rows[wire] = record

            for key, row in local.items():
                aliases.setdefault(ids.key(key), set()).add(key)
                merge(row)
            for peer in peers:
                for row in peer["entries"]:
                    merge(row)
            edits = [
                (k, value, rev, author)
                for wire, value, rev, author in rows.values()
                for k in ids.local_keys(wire) | aliases.get(wire, set())
                if local.get(k) != (k, value, rev, author)
            ]
            db.executemany("INSERT OR REPLACE INTO colours VALUES (?,?,?,?)", edits)
        active = {
            key
            for wire, value, _, _ in rows.values()
            if value is not None
            for key in ids.local_keys(wire) | aliases.get(wire, set()) | {wire}
        }
        own = {
            chat: [ids.group("project", p), ids.group("section", s)]
            for chat, (p, s) in memberships(layout.data, active, index).items()
            if allowed("chat/" + chat) and ids.group("project", p) not in private_groups
        }
        hints = {}
        for peer in peers:
            for chat, (project, section) in peer["members"].items():
                project = ids.group("project", project)
                if not allowed("chat/" + chat) or project in private_groups:
                    continue
                current = hints.setdefault(chat, [None, None])
                for i, group in enumerate(
                    (ids.local_project(project), ids.group("section", section))
                ):
                    if group and not own.get(chat, [None, None])[i] and not current[i]:
                        current[i] = group
        hints = {k: v for k, v in hints.items() if any(v)}
        old = {k: [p, s] for k, p, s in db.execute("SELECT chat,project,section FROM inherited")}
        if not deferred and hints != old:
            with db:
                db.execute("DELETE FROM inherited")
                db.executemany(
                    "INSERT INTO inherited VALUES (?,?,?)",
                    ((k, *pair) for k, pair in hints.items()),
                )
    head = directory / (device + ".json")
    backing = {
        k: v
        for k, v in ids.own_backing.items()
        if allowed("project/" + k) and "project/" + v in rows
    }
    content = encode(
        {
            "version": 1,
            "entries": [rows[k] for k in sorted(rows)],
            "members": own,
            "project_backing": backing,
        }
    )
    if not head.is_file() or head.read_bytes() != content:
        atomic(head, content)
    return {
        "preferences": len(rows),
        "active": sum(r[1] is not None for r in rows.values()),
        "saved_swatches": sum(
            k.startswith("swatch/") and r[1] is not None for k, r in rows.items()
        ),
        "received": len(edits),
        "deferred_devices": deferred,
    }
