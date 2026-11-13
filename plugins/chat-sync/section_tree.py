"""Portable edges between existing sections and local project IDs."""
from hashlib import sha256
from projects import valid_id

STORE = "codex-mods-sidebar-groups-v1"
PREFIX = "parent/"
CUSTOM = "custom:"


def directory(vault, account):
    return vault / "section-tree-v1" / sha256(account.encode()).hexdigest()


def valid(key, row):
    return (key.startswith(PREFIX) and valid_id(key[len(PREFIX):]) and isinstance(row, list) and len(row)==3
            and type(row[0]) is int and 0 <= row[0] < 2**53 and valid_id(row[1])
            and (row[2] is None or isinstance(row[2], str) and valid_id(row[2].removeprefix(CUSTOM))))


def observed(layout):
    parents = layout.data.get(STORE, {}).get(layout.account, {}).get("parents", {})
    return {PREFIX + row["id"]: parents.get(row["id"]) for row in layout.sections if valid_id(row["id"])}


def invalid_edges(edges, sections, projects):
    invalid = set()
    for sid, parent in edges.items():
        seen = {sid}
        while parent:
            if len(seen) >= 3:
                invalid.add(sid)
                break
            if not parent.startswith(CUSTOM):
                if parent not in projects: invalid.add(sid)
                break
            key = parent[len(CUSTOM):]
            if key in seen or key not in sections:
                invalid.add(sid)
                break
            seen.add(key)
            parent = edges.get(key)
    return invalid


def apply(layout, changes):
    current = observed(layout)
    edges = {k[len(PREFIX):]:v for k,v in current.items()}
    updates = {k[len(PREFIX):]:v for k,(old,v,_) in changes.items() if k.startswith(PREFIX)
               and k in current and current[k] in (old,v)}
    if not updates: return {}
    merged = {**edges, **updates}
    issues = invalid_edges(merged, set(edges), {k for k,p in layout.projects.items() if not p.get("chatGptBacking")})
    # Keep the previous tree if a concurrent move would break a branch.
    if issues - invalid_edges(edges, set(edges), set(layout.projects)) or issues.intersection(updates):
        return {}
    parents = layout.data.setdefault(STORE, {}).setdefault(layout.account, {}).setdefault("parents", {})
    for sid, parent in updates.items():
        if parent: parents[sid] = parent
        else: parents.pop(sid, None)
    return {PREFIX+sid:v for sid,v in updates.items()}
