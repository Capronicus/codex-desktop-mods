"""Merge project and custom section names and the two independent chat assignments."""

from contextlib import closing, ExitStack
import json
import re
from pathlib import Path
import sqlite3
import uuid

from native_names import native_api
import desktop_layout
import projects
import ordering
import batches

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
    if (kind == "section" and obj == desktop_layout.PINNED) or (
        kind == "chat-section" and value == desktop_layout.PINNED
    ):
        return False
    if kind in GROUPS:
        return valid_name(value)
    return kind in PLACEMENT and (value is None or uid(value))


def load(path, default):
    return json.loads(path.read_bytes()) if path.is_file() else default


def catalogue(home, aliases, eligible, pending, layout):
    """Read one consistent native snapshot. The desktop API owns all mutations."""
    groups = {}
    seen = {}
    pinned = set()
    project_aliases = {v: k for k, v in layout.project_ids.items() if uid(k)}
    uri = (home / "state_5.sqlite").as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("BEGIN")
        native = {kind: {} for kind in GROUPS}
        for ident, name, metadata in db.execute("SELECT id,name,metadata FROM projects"):
            meta = json.loads(metadata)
            original = meta.get("codex.localProjectId")
            stable = project_aliases.get(ident, original if uid(original) else ident)
            if not uid(stable) or not valid_name(name):
                continue
            native["project"][ident] = stable
            groups["project/" + stable] = ident
            seen["project/" + stable] = name
        inverse = {v: k for k, v in {**aliases, **layout.section_ids}.items() if uid(k)}
        for ident, name in db.execute("SELECT id,name FROM thread_sections"):
            if ident == desktop_layout.PINNED or not uid(ident) or not valid_name(name):
                continue
            if name in pending.values() and ident not in inverse:
                continue
            stable = inverse.get(ident, ident)
            native["section"][ident] = stable
            groups["section/" + stable] = ident
            if name not in pending.values():
                seen["section/" + stable] = name
        for tid, project, section in db.execute(
            "SELECT id,project_id,thread_section_id FROM threads"
        ):
            if tid in eligible:
                if section == desktop_layout.PINNED:
                    pinned.add(tid)
                seen["chat-project/" + tid] = native["project"].get(project)
                seen["chat-section/" + tid] = native["section"].get(section)
    return groups, seen, pinned


def exchange(
    home,
    vault,
    device,
    eligible,
    atomic,
    encode,
    *,
    cfg=None,
    sharing=None,
    action="sync",
    approval="",
):
    directory = vault / "sidebar-v1"
    state_path = home / "chat-sync" / ("sidebar-" + device + ".json")
    map_path = home / "chat-sync" / "sidebar-section-ids.json"
    mapping = load(map_path, {"aliases": {}, "pending": {}})
    aliases, pending = mapping["aliases"], mapping["pending"]
    state = load(state_path, {"rows": {}, "seen": {}, "hidden": []})
    rows, last = state["rows"], state["seen"]
    hidden = set(state["hidden"])
    layout = desktop_layout.Layout(home)
    cfg = cfg if cfg is not None else load(home / "chat-sync" / "config.json", {})
    queued = load(home / "chat-sync" / desktop_layout.QUEUE, {})
    if queued.get("undo"):
        return {"undo_on_quit": True}
    ordering_on = bool(cfg.get("order"))
    links = projects.Links(
        cfg, vault, layout.projects if layout.enabled else projects.local_projects(home)
    )
    section_access = not layout.enabled or bool(layout.account)
    if (
        layout.enabled
        and state.get("account")
        and layout.account
        and state["account"] != layout.account
    ):
        raise ValueError(
            "This sidebar belongs to another signed-in account. Switch back to sync its layout."
        )
    groups, native, pinned = catalogue(home, aliases, eligible, pending, layout)
    raw_groups = dict(groups)
    for key, ident in raw_groups.items():
        if key.startswith("project/"):
            target = links.local(key.split("/")[1])
            groups.setdefault("project/" + target, ident)
    raw_current = layout.observed(eligible) if layout.enabled else dict(native)
    if ordering_on:
        raw_current.update(ordering.observe(layout, eligible, pinned))
        native.update({"chat-pin/" + tid: tid in pinned for tid in eligible})

    def local_values(values):
        # Prefer this computer's chosen project when both physical rows already exist.
        result = {}
        for key, val in sorted(
            values.items(), key=lambda kv: kv[0].split("/")[-1] in links.preferred.values()
        ):
            if key.startswith("project/"):
                key = "project/" + links.local(key.split("/")[1])
            elif key.startswith("chat-project/") and val:
                val = links.local(val)
            if key == "order/projects":
                val = list(dict.fromkeys(links.local(i) for i in val))
            result[key] = val
        return result

    rows = links.rows(rows, portable=False)
    last = local_values(last)
    current = local_values(raw_current)

    def permitted(k, r):
        if sharing and not sharing.allows_row(k, r):
            return False
        return ordering_on or not k.startswith(("chat-pin/", "order/"))

    def filtered_order(key, record):
        if key.startswith("order/") and sharing:
            return [
                *record[:2],
                [
                    i
                    for i in record[2]
                    if i not in sharing.private_chats and i not in sharing.private_projects
                ],
            ]
        return record

    rows = {k: filtered_order(k, r) for k, r in rows.items() if permitted(k, r)}
    current = {
        k: filtered_order(k, [0, device, v])[2]
        for k, v in current.items()
        if permitted(k, [0, device, v])
    }
    current = {k: v for k, v in current.items() if k.split("/")[0] not in GROUPS or valid_name(v)}
    # Only an object previously present on this desktop can have been deleted here.
    hidden.update(
        key
        for key in last
        if key.split("/")[0] in GROUPS
        and key not in current
        and (section_access or not key.startswith("section/"))
    )
    hidden.difference_update(current)
    new_orders = {k for k in current if k.startswith("order/") and k not in last and k not in rows}
    clock = 1 + max((r[0] for r in rows.values()), default=0)
    for key, value in current.items():
        old = last.get(key, MISSING)
        prior = rows.get(key)
        if old is MISSING:
            if prior is None and value is not None and value is not False and value != []:
                rows[key] = [0, device, value]
        elif old != value and (not prior or prior[2] != value):
            kind = key.split("/")[0]
            if kind in PLACEMENT and value is None and PLACEMENT[kind] + "/" + str(old) in hidden:
                continue
            rows[key] = [clock, device, value]

    deferred = []
    folders = [(directory, valid_record)]
    if ordering_on:
        folders.append((vault / "order-v1", ordering.valid))
    for folder, validator in folders:
        for path in sorted(folder.glob("*.json")):
            if not uid(path.stem):
                continue
            try:
                head = load(path, None)
                if (
                    head["v"] != 1
                    or not isinstance(head["rows"], dict)
                    or not all(validator(k, r) for k, r in head["rows"].items())
                ):
                    raise ValueError("Invalid shared sidebar records")
                for key, record in links.rows(head["rows"], portable=False).items():
                    first_remote = key in new_orders and path.stem != device
                    if permitted(key, record) and (first_remote or key not in rows or record[:2] > rows[key][:2]):
                        if first_remote:
                            new_orders.discard(key)
                        rows[key] = filtered_order(key, record)
            except (AttributeError, KeyError, OSError, TypeError, ValueError):
                deferred.append(path.stem)

    changes = {}
    for key, (_, _, value) in rows.items():
        kind, obj = key.split("/")
        if key in hidden or (kind in ("section", "chat-section") and not section_access):
            continue
        if kind.startswith("chat-") and (obj not in eligible or key not in current):
            continue
        if kind in PLACEMENT and value and PLACEMENT[kind] + "/" + value in hidden:
            continue
        if kind == "order":
            if not layout.enabled or obj == "sections" and not section_access:
                continue
            if obj == "pins":
                value = [
                    i
                    for i in value
                    if i in eligible
                    and rows.get("chat-pin/" + i, [0, None, current.get("chat-pin/" + i)])[2]
                ]
            else:
                group = "project" if obj == "projects" else "section"
                value = [
                    i for i in value if group + "/" + i in rows and group + "/" + i not in hidden
                ]
            existing = current.get(key, [])
            value = ordering.reorder(existing + list(i for i in value if i not in existing), value)
        if current.get(key) != value:
            changes[key] = [current.get(key), value]
    review = batches.summarize(changes)
    preview_file = home / "chat-sync" / "sidebar-preview.json"
    reviewed = state.get("reviewed", False)
    if action == "preview":
        return review
    if action == "apply" and approval != review["id"]:
        raise ValueError("The sidebar preview changed. Request a fresh preview.")
    if (
        changes
        and layout.enabled
        and not reviewed
        and action != "apply"
        and cfg.get("review_first", True)
    ):
        atomic(preview_file, encode(review))
        return {"preview_required": True, **review}
    if action == "apply":
        reviewed = True
    preview_file.unlink(missing_ok=True)
    batch_id = batches.begin(home, device, review, changes, native)

    def checkpoint():
        observed = dict(current)
        if not section_access:
            observed.update(
                (k, v) for k, v in last.items() if k.startswith(("section/", "chat-section/"))
            )
        saved = encode(
            {
                "rows": rows,
                "reviewed": reviewed,
                "seen": observed,
                "hidden": sorted(hidden),
                "account": layout.account or state.get("account"),
            }
        )
        if not state_path.is_file() or state_path.read_bytes() != saved:
            atomic(state_path, saved)

    # The intent precedes any native mutation. On retry, a matching value is
    # acknowledged as our own applied change instead of creating a new revision.
    checkpoint()
    for folder, validator in folders:
        ordered = validator is ordering.valid
        head_path = folder / (device + ".json")
        outgoing = {
            k: r for k, r in rows.items() if k.startswith(("chat-pin/", "order/")) == ordered
        }
        content = encode({"v": 1, "rows": links.rows(outgoing, portable=True)})
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
            if (
                kind not in GROUPS
                or key in hidden
                or native.get(key) == record[2]
                or (kind == "section" and not section_access)
            ):
                continue
            name = record[2]
            local = groups.get(key)
            if local is None and kind == "project":
                project = rpc(
                    "project/create",
                    {
                        "name": name,
                        "roots": [],
                        "idempotencyKey": "chat-sync:" + stable,
                        "metadata": {"codex.localProjectId": stable},
                    },
                )["project"]
                local = project["id"]
                if project["name"] != name:
                    rpc("project/update", {"projectId": local, "name": name})
            elif local is None:
                # Section creation has no idempotency key. A journaled unique
                # staging name lets a retry identify a successful first request.
                if stable not in pending:
                    pending[stable] = "Chat Sync " + str(uuid.uuid4())
                    atomic(map_path, encode(mapping))
                with closing(
                    sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True)
                ) as db:
                    staged = db.execute(
                        "SELECT id FROM thread_sections WHERE name=?", (pending[stable],)
                    ).fetchall()
                if len(staged) > 1:
                    raise ValueError("Ambiguous pending section import")
                local = (
                    staged[0][0]
                    if staged
                    else rpc("threadSection/create", {"name": pending[stable]})["section"]["id"]
                )
                aliases[stable] = local
                atomic(map_path, encode(mapping))
                rpc("threadSection/update", {"sectionId": local, "name": name})
            elif kind == "project":
                rpc("project/update", {"projectId": local, "name": name})
            else:
                # The API accepts an appearance on rename. Retain this device's theme.
                with closing(
                    sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True)
                ) as db:
                    row = db.execute(
                        "SELECT appearance FROM thread_sections WHERE id=?", (local,)
                    ).fetchone()
                rpc(
                    "threadSection/update",
                    {
                        "sectionId": local,
                        "name": name,
                        "appearance": json.loads(row[0]) if row and row[0] else None,
                    },
                )
            if kind == "section" and stable in pending:
                del pending[stable]
                atomic(map_path, encode(mapping))
            if key in groups:
                renamed += 1
            else:
                created += 1
            groups[key], native[key] = local, name

        for ident, binding in cfg.get("project_links", {}).items():
            local = groups.get("project/" + ident)
            if local and Path(binding["folder"]).is_dir():
                project = rpc("project/read", {"projectId": local})["project"]
                roots = project["roots"]
                if not roots or roots[0]["path"] != binding["folder"]:
                    rpc(
                        "project/update",
                        {
                            "projectId": local,
                            "roots": [
                                {"path": binding["folder"]},
                                *[r for r in roots if r["path"] != binding["folder"]],
                            ],
                        },
                    )
        for key, record in rows.items():
            if key.startswith("chat-pin/"):
                tid = key.split("/")[1]
                if tid in eligible and bool(tid in pinned) != record[2]:
                    if "chat-section/" + tid not in native:
                        try:
                            rpc("thread/read", {"threadId": tid})
                        except ValueError:
                            waiting.add(tid)
                            continue
                    section = (
                        desktop_layout.PINNED
                        if record[2]
                        else groups.get(
                            "section/" + str(rows.get("chat-section/" + tid, [0, None, None])[2])
                        )
                    )
                    rpc("thread/section/move", {"threadId": tid, "sectionId": section})
                    (pinned.add if record[2] else pinned.discard)(tid)
                    native[key] = record[2]
        for key, record in rows.items():
            kind, tid = key.split("/")
            if kind not in PLACEMENT:
                continue
            target = record[2]
            group = PLACEMENT[kind] + "/" + str(target)
            if layout.enabled and kind == "chat-project" and key not in current and tid in eligible:
                continue
            if kind == "chat-section" and (tid in pinned or not section_access):
                continue
            if group in hidden or native.get(key, MISSING) == target:
                continue
            if tid not in eligible or (target is not None and group not in groups):
                waiting.add(tid)
                continue
            if key not in native:
                # The transcript can arrive before the desktop indexes its first message.
                try:
                    rpc("thread/read", {"threadId": tid})
                except ValueError:
                    waiting.add(tid)
                    continue
            local = groups.get(group)
            if kind == "chat-project":
                rpc("thread/metadata/update", {"threadId": tid, "projectId": local or ""})
            else:
                rpc("thread/section/move", {"threadId": tid, "sectionId": local})
            native[key] = target
            moved += 1
    if not layout.enabled:
        current = local_values(native)
    checkpoint()
    pending_layout = False
    if layout.enabled:
        projection = {}
        cached_pins = set(
            layout.data.get("electron-persisted-atom-state", {}).get(
                "app-server-pinned-thread-order-v1", []
            )
        )
        cached_order = ordering.observe(layout, eligible, cached_pins) if ordering_on else {}
        for key, (_, _, value) in rows.items():
            if key.startswith(("chat-pin/", "order/")):
                desired = changes.get(key, [None, value])[1]
                if key.startswith("chat-pin/") and key.split("/")[1] not in eligible:
                    continue
                if key in cached_order and cached_order[key] != desired:
                    projection[key] = [cached_order[key], desired, None]
                continue
            kind, obj = key.split("/")
            native_id = groups.get(key)
            if key in hidden or (kind in ("section", "chat-section") and not section_access):
                continue
            if current.get(key) == value and not layout.needs_mapping(kind, obj, native_id):
                continue
            if kind in PLACEMENT and (
                key not in current
                or obj not in eligible
                or (value and PLACEMENT[kind] + "/" + value in hidden)
            ):
                continue
            if kind in GROUPS and not native_id:
                continue
            projection[key] = [raw_current.get(key), value, native_id]
        for local, binding in cfg.get("project_links", {}).items():
            if local in layout.projects and binding["folder"] not in layout.projects[local].get(
                "rootPaths", []
            ):
                projection["checkout/" + local] = [
                    layout.projects[local].get("rootPaths", []),
                    binding["folder"],
                    groups.get("project/" + local),
                ]
        for old, project in layout.projects.items():
            primary = links.local(old)
            if (
                old != primary
                and primary in layout.projects
                and not project.get("rootPaths")
                and not project.get("chatGptBacking")
                and (primary in cfg.get("project_links", {}) or "project/" + old in last)
            ):
                projection["alias/" + old] = [old, primary, None]
        pending_layout = desktop_layout.queue(home, device, layout, projection, batch_id=batch_id)
    return {
        "desktop_refresh_on_quit": pending_layout,
        "sections_need_sign_in": not section_access,
        "created": created,
        "renamed": renamed,
        "assignments_updated": moved,
        "pending_chats": len(waiting),
        "deferred_devices": deferred,
    }
