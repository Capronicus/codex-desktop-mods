"""Exchange local chat pins and relative order without copying cloud sidebar entries."""

from desktop_layout import ITEM, SECTIONS
from projects import valid_id

LISTS = {"projects", "sections", "pins"}


def valid(key, record):
    kind, _, item = key.partition("/")
    if not isinstance(record, list) or len(record) != 3:
        return False
    clock, author, value = record
    if type(clock) is not int or not 0 <= clock < 2**53 or not valid_id(author):
        return False
    if kind == "chat-pin":
        return valid_id(item) and type(value) is bool
    return (
        kind == "order"
        and item in LISTS
        and isinstance(value, list)
        and all(valid_id(i) for i in value)
        and len(value) == len(set(value))
    )


def can_apply_queued_order(before, current, incoming):
    """Allow membership changes while preserving an intervening local reorder."""
    previous_ids = set(before)
    current_ids = set(current)
    if current_ids.difference(previous_ids, incoming):
        return False
    return [item for item in current if item in previous_ids] == [
        item for item in before if item in current_ids
    ]


def reorder(existing, subset, encode=lambda i: i):
    """Replace only participating slots. Keep all unrelated items in their relative positions."""
    desired = list(dict.fromkeys(map(encode, subset)))
    relevant = set(desired)
    it = iter(desired)
    result = [next(it) if item in relevant else item for item in dict.fromkeys(existing)]
    result.extend(it)
    return result


def ordered(ids, allowed, prefix=""):
    return list(
        dict.fromkeys(
            i[len(prefix) :]
            for i in ids
            if isinstance(i, str) and i.startswith(prefix) and i[len(prefix) :] in allowed
        )
    )


def observe(layout, eligible, native_pins):
    data = layout.data
    atoms = data.get("electron-persisted-atom-state", {})
    pins = set(data.get("pinned-thread-ids", [])) | native_pins
    rows = {"chat-pin/" + tid: tid in pins for tid in eligible}
    if not layout.enabled:
        return rows
    projects = set(layout.projects)
    porder = ordered(
        atoms.get("unified-sidebar-project-order-v1", []), projects, "codex:project:"
    ) + ordered(data.get("project-order", []), projects)
    rows["order/projects"] = list(dict.fromkeys(porder + list(layout.projects)))
    if layout.account:
        sections = [s["id"] for s in layout.sections]
        account = atoms.get(SECTIONS, {}).get(layout.account, {})
        rows["order/sections"] = list(
            dict.fromkeys(
                ordered(account.get("sectionOrder", []), set(sections), "custom:") + sections
            )
        )
    pins &= eligible
    porder = ordered(atoms.get("unified-sidebar-pinned-order-v1", []), pins, ITEM) + ordered(
        atoms.get("app-server-pinned-thread-order-v1", []), pins
    )
    rows["order/pins"] = list(
        dict.fromkeys(porder + ordered(data.get("pinned-thread-ids", []), pins) + sorted(pins))
    )
    return rows


def project(layout, key, value):
    data = layout.data
    atoms = data.setdefault("electron-persisted-atom-state", {})
    kind, ident = key.split("/")
    if kind == "chat-pin":
        for store, name, entry in [
            (data, "pinned-thread-ids", ident),
            (atoms, "app-server-pinned-thread-order-v1", ident),
            (atoms, "unified-sidebar-pinned-order-v1", ITEM + ident),
        ]:
            items = store.setdefault(name, [])
            if value and entry not in items:
                items.append(entry)
            if not value and entry in items:
                items.remove(entry)
    elif ident == "projects":
        items = [k for k in value if k in layout.projects]
        data["project-order"] = reorder(data.get("project-order", []), items)
        name = "unified-sidebar-project-order-v1"
        atoms[name] = reorder(atoms.get(name, []), items, lambda k: "codex:project:" + k)
    elif ident == "sections" and layout.account:
        account = atoms.setdefault(SECTIONS, {}).setdefault(
            layout.account, {"sections": [], "collapsedSectionIds": [], "sectionOrder": []}
        )
        visible = {s["id"] for s in account["sections"]}
        account["sectionOrder"] = reorder(
            account["sectionOrder"], [k for k in value if k in visible], lambda k: "custom:" + k
        )
    elif ident == "pins":
        for store, name, prefix in [
            (data, "pinned-thread-ids", ""),
            (atoms, "app-server-pinned-thread-order-v1", ""),
            (atoms, "unified-sidebar-pinned-order-v1", ITEM),
        ]:
            items = store.get(name, [])
            # Pin membership is resolved independently for each chat.
            store[name] = reorder(
                items, [k for k in value if prefix + k in items], lambda k: prefix + k
            )
