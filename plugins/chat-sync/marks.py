"""Exchange bookmarks, saved views and text animations between desktops that opt in.

Bookmarks are stored by this plugin. Saved views and text animations live in the
desktop window, which hands over a snapshot and later applies what it receives.
Every item is one row, and the newest edit of a row wins on each desktop.
"""

import hashlib
import json

import bookmarks
import delivery
import projects
import sync

KINDS = ("bookmark", "view", "word")
AGES = ("", "1", "7", "30")


def scope():
    return hashlib.sha256(bookmarks.account_id().encode()).hexdigest()


def text(value, limit):
    return isinstance(value, str) and len(value) <= limit


def clean(key, value):
    """Return the stored form of one mark, or None when it cannot be accepted."""
    kind, _, ident = key.partition("/")
    if kind == "word":
        try:
            fields = json.loads(ident)
        except ValueError:
            return None
        valid = (len(ident) <= 2048 and isinstance(fields, list) and len(fields) == 4 and text(fields[0], 1024)
                 and all(type(n) is int and n >= 0 for n in fields[1:3]) and fields[2] > fields[1]
                 and text(fields[3], 16))
        return 1 if valid and value == 1 else None
    if not projects.valid_id(ident) or not isinstance(value, dict):
        return None
    if kind == "view":
        links = {k: value.get(k) or "" for k in ("project", "origin", "waiting")}
        if (not text(value.get("name"), 80) or not value["name"].strip() or (value.get("age") or "") not in AGES
                or not all(v == "" or projects.valid_id(v) for v in links.values())):
            return None
        return {"name": value["name"], "age": value.get("age") or "", **links}
    if kind == "bookmark":
        try:
            address = json.loads(json.loads(value["anchor"])[0])
        except (KeyError, ValueError, TypeError, IndexError):
            return None
        if (value.get("id") != ident or not text(value["anchor"], 2048) or not isinstance(address, list)
                or not address or address[0] not in ("local", "cloud") or not text(value.get("label"), 160)
                or not value["label"].strip() or type(value.get("created")) is not int
                or not all(text(value.get(k, ""), 256) for k in ("thread", "turn", "item"))):
            return None
        return {"id": ident, "anchor": value["anchor"], "thread": value.get("thread", ""),
                "turn": value.get("turn", ""), "item": value.get("item", ""), "cloud": address[0] == "cloud",
                "created": value["created"], "label": value["label"]}
    return None


def observe(account, links):
    """Read this desktop's marks. Window storage counts only while its snapshot is for this account."""
    current = {}
    for ident, mark in sync.read_json(bookmarks.location(), {}).items():
        value = clean("bookmark/" + ident, mark)
        if value:
            current["bookmark/" + ident] = value
    kinds = {"bookmark"}
    shown = sync.read_json(sync.STATE / "renderer-marks.json", {})
    if isinstance(shown, dict) and shown.get("account") == account:
        kinds.update(("view", "word"))
        for ident, view in (shown.get("views") or {}).items():
            value = clean("view/" + ident, view)
            if value:
                # Each desktop has its own project IDs. Shared rows name the linked project.
                current["view/" + ident] = {**value, "project": value["project"] and links.wire(value["project"])}
        for tag in shown.get("words") or []:
            if isinstance(tag, str) and clean("word/" + tag, 1):
                current["word/" + tag] = 1
    return current, kinds


def exchange(cfg, vault):
    device, account = cfg["device"], scope()
    directory = vault / "marks-v1" / account
    state_path = sync.STATE / ("marks-" + account[:16] + ".json")
    # Hold the bookmark file still between reading it and writing the merged result.
    with sync.lock(sync.STATE / "bookmark.lock"):
        state = sync.read_json(state_path, {})
        rows, seen = state.get("rows", {}), state.get("seen", {})
        links = projects.Links(cfg, vault, projects.local_projects(sync.HOME))
        current, kinds = observe(account, links)
        clock = 1 + max((row[0] for row in rows.values()), default=0)
        for key in current.keys() | seen.keys():
            if key.partition("/")[0] not in kinds:
                continue
            value, prior = current.get(key), rows.get(key)
            # A value equal to the shared row is that row arriving here, not a new edit.
            if value != seen.get(key) and value != (prior[2] if prior else None):
                rows[key] = [clock, device, value]

        deferred = []
        for path in sorted(directory.glob("*.json")):
            if not projects.valid_id(path.stem) or path.stem == device:
                continue
            try:
                doc = sync.read_json(path)
                if doc.get("v") != 1 or not isinstance(doc.get("rows"), dict):
                    raise ValueError("Invalid shared marks")
                for key, row in doc["rows"].items():
                    if (not isinstance(row, list) or len(row) != 3 or type(row[0]) is not int
                            or not 0 <= row[0] < 2**53 or not projects.valid_id(row[1])):
                        raise ValueError("Invalid shared mark")
                    if key.partition("/")[0] not in KINDS or len(key) > 2100:
                        continue
                    value = None if row[2] is None else clean(key, row[2])
                    if row[2] is not None and value is None:
                        continue
                    if key not in rows or row[:2] > rows[key][:2]:
                        rows[key] = [row[0], row[1], value]
            except (OSError, ValueError, TypeError, AttributeError):
                deferred.append(path.stem)

        # Bookmarks belong to this plugin and are written at once. Other entries stay untouched.
        saved = sync.read_json(bookmarks.location(), {})
        merged = dict(saved)
        for key, row in rows.items():
            if key.startswith("bookmark/"):
                if row[2] is None:
                    merged.pop(key[9:], None)
                else:
                    merged[key[9:]] = row[2]
        if merged != saved:
            sync.atomic(bookmarks.location(), sync.encode(merged))

        # The window applies its own storage. Until its next snapshot shows the result,
        # the same instructions are offered again.
        waiting = 0
        instructions = sync.STATE / "renderer-marks-apply.json"
        if "view" in kinds:
            put, drop = {}, []
            for key, row in rows.items():
                if key.partition("/")[0] not in ("view", "word") or current.get(key) == row[2]:
                    continue
                if row[2] is None:
                    drop.append(key)
                elif key.startswith("view/"):
                    put[key] = {**row[2], "project": row[2]["project"] and links.local(row[2]["project"])}
                else:
                    put[key] = 1
            if put or drop:
                doc = {"account": account, "set": put, "remove": sorted(drop)}
                delivery.save(instructions, {**doc, "id": sync.digest(sync.encode(doc))})
                waiting = len(put) + len(drop)
            else:
                instructions.unlink(missing_ok=True)

        observed = {k: v for k, v in seen.items() if k.partition("/")[0] not in kinds}
        observed.update((k, v) for k, v in current.items() if not k.startswith("bookmark/"))
        for ident, mark in merged.items():
            value = clean("bookmark/" + ident, mark)
            if value:
                observed["bookmark/" + ident] = value
        delivery.save(state_path, {"rows": rows, "seen": observed})
        delivery.save(directory / (device + ".json"), {"v": 1, "rows": rows})
        live = [key.partition("/")[0] for key, row in rows.items() if row[2] is not None]
        return {"bookmarks": live.count("bookmark"), "views": live.count("view"), "animations": live.count("word"),
                "waiting_for_desktop": waiting, "deferred_devices": deferred}
