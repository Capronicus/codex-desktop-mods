"""Preview the chats another desktop would add or extend here, without receiving anything."""

import projects
import sync

GROUPS = ("new", "update", "conflict", "waiting", "blocked", "current")
PAGE_SIZE = 25


def classify(vault, tid, peers, entry, known, previous, row, sharing):
    """Mirror the choices of a sync pass using only the published snapshots."""
    if tid in known and entry is None:
        return "waiting", "multiple_files", None
    local = previous.get("manifest") if isinstance(previous.get("manifest"), dict) else None
    try:
        if entry is None:
            if previous:
                return "blocked", "removed_here", None
            if sharing.blocks_incoming(vault, tid, peers.values()):
                return "blocked", "sharing_rule", None
            best, _ = sync.latest_history(vault, list(peers.values()), None)
            return ("conflict", "diverged", None) if best is None else ("new", "new", best)
        # A local edit is sent first. Its result decides what, if anything, is received.
        token = sync.snapshot_token(entry, entry[0].stat(), sync.export_name(row, previous))
        if not local or previous.get("token") != token:
            return "waiting", "local_changes", None
        best, up_to_date = sync.latest_history(vault, [*peers.values(), local], local)
    except (OSError, ValueError):
        return "waiting", "missing_blocks", None
    if best is None:
        return "conflict", "diverged", None
    return ("current", "current", None) if up_to_date else ("update", "longer_history", best)


def preview(group="all", search="", page=0):
    if group not in ("all", *GROUPS) or len(search) > 256 or type(page) is not int or page < 0:
        raise ValueError("Select a preview group, a search of up to 256 characters and a nonnegative page.")
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = projects.config()
        if not vault.is_dir() or vault.resolve() != vault:
            raise ValueError("Sync folder unavailable. Check the mount or desktop sync client.")
        from chat_actions import peers as device_names

        index = sync.catalog(include_titles=True)
        files = sync.streams()
        sharing = projects.Sharing(cfg, vault, index, sync.HOME)
        state = sync.read_json(sync.STATE / ("state-" + cfg["device"] + ".json"), {})
        labels = device_names(vault)
        incomplete = {}
        heads = sync.shared_heads(vault, incomplete)
        counts = dict.fromkeys(GROUPS, 0)
        matches = []
        query = search.strip().casefold()
        for tid in heads.keys() | incomplete.keys():
            peers = {d: m for d, m in heads.get(tid, {}).items() if d != cfg["device"]}
            row = index.get(tid)
            previous = state.get(tid, {})
            best = None
            if tid in sharing.private_chats:
                status, reason = "blocked", "local_only"
            elif tid in incomplete:
                status, reason = "waiting", "incomplete_snapshot"
            elif not peers:
                continue  # Only this desktop has published it.
            else:
                status, reason, best = classify(vault, tid, peers, files.get(tid), files, previous, row, sharing)
            named = max((m for m in peers.values() if m.get("name")),
                        key=lambda m: tuple(m.get("name_clock", [0, ""])), default=None)
            title = (named or {}).get("name") or sync.export_name(row, previous) or (row or {}).get("title")
            counts[status] += 1
            if group not in ("all", status) or (query not in (title or "").casefold() and query not in tid):
                continue
            label = title[:199] + "…" if title and len(title) > 200 else title
            matches.append({"id": tid, "name": label, "group": status, "reason": reason,
                            "size": best["size"] if best else None,
                            "devices": sorted(labels.get(d, d[:8]) for d in peers)})
        matches.sort(key=lambda r: (GROUPS.index(r["group"]), (r["name"] or r["id"]).casefold(), r["id"]))
        page = min(page, max(0, (len(matches) - 1) // PAGE_SIZE))
        start = page * PAGE_SIZE
        return {"counts": counts, "matched": len(matches), "page": page, "pageSize": PAGE_SIZE,
                "chats": matches[start:start + PAGE_SIZE]}
