"""Review received sidebar changes and undo the last applied local batch."""

import copy

import sync

JOURNAL = "sidebar-undo.json"


def summarize(changes):
    totals = {}
    details = []
    for key, (old, new) in sorted(changes.items()):
        kind, ident = key.split("/")
        if kind in ("project", "section"):
            action = kind + ("_created" if old is None else "_renamed")
        elif kind == "order":
            action = "lists_reordered"
        elif kind == "chat-pin":
            action = "pins_changed"
        else:
            action = "project_moves" if kind == "chat-project" else "section_moves"
        totals[action] = totals.get(action, 0) + 1
        details.append({"action": action, "id": ident, "before": old, "after": new})
    return {"id": sync.digest(sync.encode(changes)), "counts": totals, "changes": details}


def begin(home, device, review, changes, native, pinned, rows):
    if not changes:
        return
    p = home / "chat-sync" / JOURNAL
    old = sync.read_json(p, {})
    if old.get("device") == device and old.get("id") == review["id"]:
        return review["id"]
    # Consecutive passes before quit form one visible batch.
    pending = (home / "chat-sync" / "sidebar-layout-pending.json").exists()
    undo = old if pending and old.get("device") == device else {"device": device, "native": {}}
    targets = {k: v[1] for k, v in changes.items()}
    # Codex uses a single native section field for a custom section or a pin.
    # The renderer can display both independently. Journal the native placement as a pair.
    for tid in {k.split("/")[1] for k in changes if k.startswith(("chat-section/", "chat-pin/"))}:
        pin = "chat-pin/" + tid
        section = "chat-section/" + tid
        native[pin] = tid in pinned
        targets[pin] = rows.get(pin, [0, None, native[pin]])[2]
        targets[section] = (
            None if targets[pin] else rows.get(section, [0, None, native.get(section)])[2]
        )
    for key, after in targets.items():
        if key.startswith("order/"):
            continue
        original = undo["native"].get(key, [native.get(key)])[0]
        undo["native"][key] = [original, after]
    undo["id"] = review["id"]
    sync.atomic(p, sync.encode(undo))
    return review["id"]


def capture(home, device, batch_id, before, after):
    p = home / "chat-sync" / JOURNAL
    doc = sync.read_json(p, {})
    if not batch_id or doc.get("device") != device or doc.get("id") != batch_id:
        return
    # Record only changed fields. Traversing dictionaries preserves unrelated preferences.
    diff = []

    def walk(a, b, path):
        if a == b:
            return
        if isinstance(a, dict) and isinstance(b, dict):
            for k in a.keys() | b.keys():
                walk(a.get(k), b.get(k), [*path, k])
        else:
            diff.append([path, a, b])

    walk(before, after, [])
    if diff:
        from desktop_layout import Layout

        doc["account"] = Layout(home).account
        doc["desktop"] = diff
        sync.atomic(p, sync.encode(doc))


def restore_desktop(data, changes):
    result = copy.deepcopy(data)
    for keys, before, after in changes:
        target = result
        for key in keys[:-1]:
            target = target.get(key) if isinstance(target, dict) else None
        if not isinstance(target, dict) or target.get(keys[-1]) not in (before, after):
            raise ValueError(
                "A sidebar field changed after this batch. Its newer edit has been preserved."
            )
        if before is None:
            target.pop(keys[-1], None)
        else:
            target[keys[-1]] = before
    return result


def undo_locked(home, cfg):
    import desktop_layout
    import sidebar
    from native_names import native_api

    directory = home / "chat-sync"
    doc = sync.read_json(directory / JOURNAL, {})
    if doc.get("device") != cfg["device"]:
        raise ValueError("There is no sidebar batch to undo on this device.")
    layout = desktop_layout.Layout(home)
    if doc.get("account") and layout.account != doc["account"]:
        raise ValueError("Switch back to the account that applied this batch.")
    restored = restore_desktop(layout.data, doc.get("desktop", []))
    # Refuse to remove a newly imported project that now has a checkout or new members.
    original = doc["native"]
    if original:
        aliases = sync.read_json(directory / "sidebar-section-ids.json", {}).get("aliases", {})
        from contextlib import closing
        import sqlite3

        with closing(
            sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True)
        ) as db:
            tids = {r[0] for r in db.execute("select id from threads")}
        groups, values, pins = sidebar.catalogue(home, aliases, tids, {}, layout)
        values.update({"chat-pin/" + t: t in pins for t in tids})
        renderer = layout.observed(tids)
        for key, (before, after) in original.items():
            if before and key.startswith(("chat-project/", "chat-section/")):
                group = key.split("/")[0].removeprefix("chat-") + "/" + before
                if group not in groups:
                    raise ValueError("The chat’s former group was removed. Undo was skipped.")
            if values.get(key) not in (before, after):
                raise ValueError(
                    "A synced group or chat has a newer local change. Undo was skipped."
                )
            if before is None and key.startswith(("project/", "section/")):
                kind, ident = key.split("/")
                if kind == "project" and layout.projects.get(ident, {}).get("rootPaths"):
                    raise ValueError(
                        "The imported project now has a local folder. Undo was skipped."
                    )
                if any(
                    k.startswith("chat-" + kind + "/")
                    and v == ident
                    and (k not in original or original[k][0] == ident)
                    for state in (values, renderer)
                    for k, v in state.items()
                ):
                    raise ValueError("An imported group now has other chats. Undo was skipped.")
        with native_api(home) as call:
            for key, (before, after) in original.items():
                if before is None and key.startswith("project/") and key in groups:
                    if call("project/read", {"projectId": groups[key]})["project"]["roots"]:
                        raise ValueError(
                            "The imported project now has a local folder. Undo was skipped."
                        )
            restored_placements = set()
            for key, (before, after) in sorted(
                original.items(), key=lambda kv: 0 if kv[0].startswith("chat-") else 1
            ):
                kind, ident = key.split("/")
                if values.get(key) == before:
                    continue
                if kind == "chat-project":
                    call(
                        "thread/metadata/update",
                        {"threadId": ident, "projectId": groups.get("project/" + str(before), "")},
                    )
                elif kind in ("chat-section", "chat-pin"):
                    if ident in restored_placements:
                        continue
                    restored_placements.add(ident)
                    was_pinned = original.get("chat-pin/" + ident, [False])[0]
                    previous = original.get("chat-section/" + ident, [None])[0]
                    target = (
                        desktop_layout.PINNED
                        if was_pinned
                        else groups.get("section/" + str(previous))
                    )
                    call("thread/section/move", {"threadId": ident, "sectionId": target})
                elif kind in ("project", "section") and key in groups:
                    method, field = (
                        ("project", "projectId")
                        if kind == "project"
                        else ("threadSection", "sectionId")
                    )
                    params = {field: groups[key]}
                    if before is not None:
                        params["name"] = before
                    call(method + ("/delete" if before is None else "/update"), params)
    if layout.enabled and restored != layout.data:
        if layout.path.read_bytes() != layout.original or desktop_layout.desktop_running():
            raise ValueError("The desktop reopened while undo was running. Quit it and retry.")
        sync.atomic(layout.path, sync.encode(restored))
    cfg.update(sidebar=False, order=False)
    sync.atomic(directory / "config.json", sync.encode(cfg))
    for name in (JOURNAL, desktop_layout.QUEUE, "sidebar-preview.json"):
        (directory / name).unlink(missing_ok=True)
    return {"undone": doc["id"], "sidebar": False}


def undo():
    import desktop_layout
    import projects

    with sync.lock(sync.STATE / "sync.lock"):
        cfg, _ = projects.config()
        doc = sync.read_json(sync.STATE / JOURNAL, {})
        if doc.get("device") != cfg["device"]:
            raise ValueError("There is no sidebar batch to undo on this device.")
        if desktop_layout.desktop_running():
            sync.atomic(
                sync.STATE / desktop_layout.QUEUE,
                sync.encode(
                    {
                        "device": cfg["device"],
                        "undo": True,
                        "account": desktop_layout.Layout(sync.HOME).account,
                    }
                ),
            )
            desktop_layout.start_worker(sync.HOME)
            return {"undo_on_quit": True, "batch": doc["id"]}
        return undo_locked(sync.HOME, cfg)


def review(action="preview", batch=""):
    import projects
    import sidebar

    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = projects.config()
        if not cfg.get("sidebar"):
            raise ValueError("Enable sidebar sync on this computer first.")
        index = sync.catalog()
        sharing = projects.Sharing(cfg, vault, index, sync.HOME)
        known = sync.read_json(sync.STATE / ("state-" + cfg["device"] + ".json"), {})
        eligible = set(known) & set(sync.streams()) - sharing.private_chats
        return sidebar.exchange(
            sync.HOME,
            vault,
            cfg["device"],
            eligible,
            sync.atomic,
            sync.encode,
            cfg=cfg,
            sharing=sharing,
            action=action,
            approval=batch,
        )
