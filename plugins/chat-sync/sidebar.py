"""Merge project and custom section names and the two independent chat assignments."""

from contextlib import closing, ExitStack
import json
import re
import sqlite3
import uuid

from native_names import native_api

ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
GROUPS = ("project", "section")
PLACEMENT = {"chat-project": "project", "chat-section": "section"}
MISSING = object()


def uid(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def valid_name(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096


def valid_record(key, record):
    kind, _, obj = key.partition("/")
    if not uid(obj) or not isinstance(record, list) or len(record) != 3:
        return False
    rev, author, value = record
    if type(rev) is not int or not 0 <= rev < 2**53 or not uid(author):
        return False
    if kind in GROUPS:
        return valid_name(value)
    return kind in PLACEMENT and (value is None or uid(value))


def load(path, default):
    return json.loads(path.read_bytes()) if path.is_file() else default


def catalogue(home, aliases, eligible, pending):
    """Read one consistent native snapshot. The desktop API owns all mutations."""
    groups = {}
    seen = {}
    uri = (home / "state_5.sqlite").as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("BEGIN")
        native = {kind: {} for kind in GROUPS}
        for ident, name, metadata in db.execute("SELECT id,name,metadata FROM projects"):
            meta = json.loads(metadata)
            original = meta.get("codex.localProjectId")
            stable = original if uid(original) else ident
            native["project"][ident] = stable
            groups["project/" + stable] = ident
            seen["project/" + stable] = name
        inverse = {v:k for k,v in aliases.items()}
        for ident, name in db.execute("SELECT id,name FROM thread_sections"):
            if name in pending.values() and ident not in inverse:
                continue
            stable = inverse.get(ident, ident)
            native["section"][ident] = stable
            groups["section/" + stable] = ident
            seen["section/" + stable] = name
        for tid, project, section in db.execute("SELECT id,project_id,thread_section_id FROM threads"):
            if tid in eligible:
                seen["chat-project/" + tid] = native["project"].get(project)
                seen["chat-section/" + tid] = native["section"].get(section)
    return groups, seen


def exchange(home, vault, device, eligible, atomic, encode):
    directory = vault / "sidebar-v1"
    state_path = home / "chat-sync" / ("sidebar-" + device + ".json")
    map_path = home / "chat-sync" / "sidebar-section-ids.json"
    mapping = load(map_path, {"aliases": {}, "pending": {}})
    aliases, pending = mapping["aliases"], mapping["pending"]
    state = load(state_path, {"rows": {}, "seen": {}, "hidden": []})
    rows, last = state["rows"], state["seen"]
    hidden = set(state["hidden"])
    groups, current = catalogue(home, aliases, eligible, pending)
    # Only an object previously present on this desktop can have been deleted here.
    hidden.update(key for key in last if key.split("/")[0] in GROUPS and key not in current)
    hidden.difference_update(groups)
    clock = 1 + max((r[0] for r in rows.values()), default=0)
    for key, value in current.items():
        old = last.get(key, MISSING)
        prior = rows.get(key)
        if old is MISSING:
            if prior is None and value is not None:
                rows[key] = [0, device, value]
        elif old != value and (not prior or prior[2] != value):
            kind = key.split("/")[0]
            if kind in PLACEMENT and value is None and PLACEMENT[kind] + "/" + str(old) in hidden:
                continue
            rows[key] = [clock, device, value]

    deferred = []
    for path in sorted(directory.glob("*.json")):
        if not uid(path.stem):
            continue
        try:
            head = load(path, None)
            if head["v"] != 1 or not isinstance(head["rows"], dict) or not all(
                    valid_record(k,r) for k,r in head["rows"].items()):
                raise ValueError("Invalid sidebar records")
            for key, record in head["rows"].items():
                if key not in rows or record[:2] > rows[key][:2]:
                    rows[key] = record
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            deferred.append(path.stem)

    def checkpoint():
        saved = encode({"rows":rows, "seen":current, "hidden":sorted(hidden)})
        if not state_path.is_file() or state_path.read_bytes() != saved:
            atomic(state_path, saved)

    # The intent precedes any native mutation. On retry, a matching value is
    # acknowledged as our own applied change instead of creating a new revision.
    checkpoint()
    head_path = directory / (device + ".json")
    content = encode({"v":1, "rows":rows})
    if not head_path.is_file() or head_path.read_bytes() != content:
        atomic(head_path, content)

    created = renamed = moved = 0
    waiting = set()
    with ExitStack() as stack:
        call = None

        def rpc(method, params):
            nonlocal call
            if call is None:
                call = stack.enter_context(native_api(home))
            return call(method, params)

        for key, record in rows.items():
            kind, stable = key.split("/")
            if kind not in GROUPS or key in hidden or current.get(key) == record[2]:
                continue
            name = record[2]
            local = groups.get(key)
            if local is None and kind == "project":
                project = rpc("project/create", {"name":name, "roots":[],
                    "idempotencyKey":"chat-sync:" + stable, "metadata":{"codex.localProjectId":stable}})["project"]
                local = project["id"]
                if project["name"] != name:
                    rpc("project/update", {"projectId":local, "name":name})
            elif local is None:
                # Section creation has no idempotency key. A journaled unique
                # staging name lets a retry identify a successful first request.
                if stable not in pending:
                    pending[stable] = "Chat Sync " + str(uuid.uuid4())
                    atomic(map_path, encode(mapping))
                with closing(sqlite3.connect((home / "state_5.sqlite").as_uri()+"?mode=ro", uri=True)) as db:
                    staged = db.execute("SELECT id FROM thread_sections WHERE name=?", (pending[stable],)).fetchall()
                if len(staged) > 1:
                    raise ValueError("Ambiguous pending section import")
                local = staged[0][0] if staged else rpc("threadSection/create", {"name":pending[stable]})["section"]["id"]
                aliases[stable] = local
                atomic(map_path, encode(mapping))
                rpc("threadSection/update", {"sectionId":local, "name":name})
            elif kind == "project":
                rpc("project/update", {"projectId":local, "name":name})
            else:
                # The API accepts an appearance on rename. Retain this device's theme.
                with closing(sqlite3.connect((home / "state_5.sqlite").as_uri()+"?mode=ro", uri=True)) as db:
                    row = db.execute("SELECT appearance FROM thread_sections WHERE id=?", (local,)).fetchone()
                rpc("threadSection/update", {"sectionId":local, "name":name,
                                             "appearance":json.loads(row[0]) if row and row[0] else None})
            if kind == "section" and stable in pending:
                del pending[stable]
                atomic(map_path, encode(mapping))
            if key in groups:
                renamed += 1
            else:
                created += 1
            groups[key], current[key] = local, name

        for key, record in rows.items():
            kind, tid = key.split("/")
            if kind not in PLACEMENT:
                continue
            target = record[2]
            group = PLACEMENT[kind] + "/" + str(target)
            if group in hidden or current.get(key, MISSING) == target:
                continue
            if tid not in eligible or (target is not None and group not in groups):
                waiting.add(tid)
                continue
            if key not in current:
                # The transcript can arrive before the desktop indexes its first message.
                try:
                    rpc("thread/read", {"threadId":tid})
                except ValueError:
                    waiting.add(tid)
                    continue
            local = groups.get(group)
            if kind == "chat-project":
                rpc("thread/metadata/update", {"threadId":tid, "projectId":local or ""})
            else:
                rpc("thread/section/move", {"threadId":tid, "sectionId":local})
            current[key] = target
            moved += 1
    checkpoint()
    return {"created":created, "renamed":renamed, "assignments_updated":moved,
            "pending_chats":len(waiting), "deferred_devices":deferred}
