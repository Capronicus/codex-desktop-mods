"""Preview outgoing local chat histories without opening or publishing transcripts."""

import projects
import sync

GROUPS = ("pending", "check", "unchanged", "excluded", "unavailable")
PAGE_SIZE = 25


def history_status(entry, row, previous, name):
    try:
        token = sync.snapshot_token(entry, entry[0].stat(), name)
    except OSError:
        return "unavailable", "unreadable"
    if previous.get("token") == token:
        return "unchanged", "unchanged"
    if not row and not previous:
        return "check", "unindexed"
    if previous and not previous.get("token"):
        return "check", "no_fingerprint"
    return "pending", "changed" if previous else "new"


def preview(group="all", search="", page=0):
    if group not in ("all", *GROUPS) or len(search) > 256 or type(page) is not int or page < 0:
        raise ValueError("Select a preview group, a search of up to 256 characters and a nonnegative page.")
    if not sync.CONFIG.is_file():
        raise ValueError("Configure a private sync folder first.")
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = projects.config()
        if not vault.is_dir() or vault.resolve() != vault:
            raise ValueError("Sync folder unavailable. Reconnect it to check current sharing rules.")
        index = sync.catalog(include_titles=True)
        files = sync.streams()
        explicit = set(cfg.get("private_chats", []))
        sharing = projects.Sharing(cfg, vault, index, sync.HOME)
        private = sharing.private_chats
        known = sync.read_json(sync.STATE / ("state-" + cfg["device"] + ".json"), {})
        recovery = {p.name for p in (sync.STATE / "pending-projection").glob("*")}
        counts = dict.fromkeys(GROUPS, 0)
        matches = []
        query = search.strip().casefold()
        for tid in index.keys() | files.keys():
            row = index.get(tid)
            if row and row["source"] not in sync.LOCAL_SOURCES:
                continue
            previous = known.get(tid, {})
            name = sync.export_name(row, previous)
            entry = files.get(tid)
            if tid in private:
                status, reason = "excluded", "chat_rule" if tid in explicit else ("folder_rule" if tid in sharing.folder_chats else "project_rule")
            elif not entry:
                status, reason = "unavailable", "multiple_files" if tid in files else "missing_file"
            elif tid in recovery:
                status, reason = "check", "recovery"
            else:
                status, reason = history_status(entry, row, previous, name)
            title = name or (row or {}).get("title")
            counts[status] += 1
            if group not in ("all", status) or (query not in (title or "").casefold() and query not in tid):
                continue
            label = title[:199] + "…" if title and len(title) > 200 else title
            matches.append({"id": tid, "name": label, "group": status, "reason": reason,
                            "archived": entry[1] if entry else bool(row and row["archived"])})
        matches.sort(key=lambda r: (GROUPS.index(r["group"]), (r["name"] or r["id"]).casefold(), r["id"]))
        page = min(page, max(0, (len(matches) - 1) // PAGE_SIZE))
        start = page * PAGE_SIZE
        return {"counts": counts, "matched": len(matches), "page": page, "pageSize": PAGE_SIZE,
                "chats": matches[start:start + PAGE_SIZE]}
