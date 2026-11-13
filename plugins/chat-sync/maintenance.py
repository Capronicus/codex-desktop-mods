"""Reclaim space in the shared folder and remove a desktop that no longer syncs."""

import shutil
import time

import projects
import sync

# Folder clients deliver files in any order. Anything this recent may belong to a
# revision whose head has not arrived yet.
GRACE = 7 * 86400
LISTED = 50


def folder():
    cfg, vault = projects.config()
    if not vault.is_dir() or vault.resolve() != vault:
        raise ValueError("Sync folder unavailable. Check the mount or desktop sync client.")
    return cfg, vault


def settled(path, now):
    try:
        return now - path.stat().st_mtime > GRACE
    except OSError:
        return False


def head_files(vault):
    for path in (vault / "heads").glob("*/*.json"):
        if sync.UUID.fullmatch(path.parent.name) and sync.UUID.fullmatch(path.stem):
            yield path


def compact(apply=False):
    """Remove revisions no desktop points to, and the blocks only they used."""
    if type(apply) is not bool:
        raise ValueError("Choose whether to apply the cleanup.")
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = folder()
        if apply:
            sync.require_active(cfg)
        now = time.time()
        waiting = {}
        heads = sync.shared_heads(vault, waiting)
        current, keep = set(), set()
        for path in head_files(vault):
            try:
                revision = sync.read_json(path)
            except (OSError, ValueError):
                revision = None
            if isinstance(revision, str) and sync.HEX.fullmatch(revision):
                current.add((path.stem, revision))
            else:
                waiting.setdefault(path.stem, "Waiting for a complete device head")
        for copies in heads.values():
            for man in copies.values():
                keep.update(man["chunks"])
        versions, recent = [], 0
        for path in (vault / "versions").glob("*/*.json"):
            tid, revision = path.parent.name, path.stem
            if not sync.UUID.fullmatch(tid) or not sync.HEX.fullmatch(revision) or (tid, revision) in current:
                continue
            if tid not in waiting and settled(path, now):
                versions.append(path)
                continue
            # A revision that stays must keep its blocks, even with no head yet.
            recent += 1
            try:
                keep.update(sync.valid_manifest(sync.read_json(path), tid)["chunks"])
            except (OSError, ValueError, TypeError):
                pass
        blocks = []
        # Blocks carry no chat ID. While any head is incomplete its blocks are unknown,
        # so only revisions of complete chats are removed in that pass.
        for path in () if waiting else (vault / "blocks").glob("*/*.gz"):
            key = path.name[:-3]
            if not sync.HEX.fullmatch(key) or path.parent.name != key[:2] or key in keep:
                continue
            if settled(path, now):
                blocks.append(path)
            else:
                recent += 1
        # An interrupted write leaves a temporary file beside its destination.
        leftovers = [
            path
            for area in ("blocks", "versions", "heads")
            for path in (vault / area).glob("*/.writing-*")
            if path.is_file() and settled(path, now)
        ]
        size = failed = 0
        for path in (*versions, *blocks, *leftovers):
            try:
                size += path.stat().st_size
                if apply:
                    path.unlink()
            except OSError:
                failed += 1
        if apply:
            for area in ("versions", "blocks"):
                for directory in (vault / area).iterdir():
                    try:
                        directory.rmdir()  # Only succeeds once the directory is empty.
                    except OSError:
                        pass
        return {
            "applied": apply,
            "revisions": len(versions),
            "blocks": len(blocks),
            "temporary_files": len(leftovers),
            "bytes": size,
            "kept_recent": recent,
            "waiting_chats": len(waiting),
            "failed": failed,
        }


def devices():
    cfg, vault = folder()
    counts = {}
    for path in head_files(vault):
        counts[path.parent.name] = counts.get(path.parent.name, 0) + 1
    result = {}
    receipts = {p.stem: p for p in (vault / "receipts-v1").glob("*.json") if sync.UUID.fullmatch(p.stem)}
    for device in sorted(set(counts) | set(receipts)):
        receipt = receipts.get(device)
        name = projects.peer_file(receipt).get("name") if receipt else None
        try:
            seen = receipt.stat().st_mtime if receipt else None
        except OSError:
            seen = None
        result[device] = {
            "name": name[:60] if isinstance(name, str) else None,
            "this_desktop": device == cfg["device"],
            "chats": counts.get(device, 0),
            "last_receipt": seen,
        }
    return result


def retire(device, review=""):
    """Preview, then remove one other desktop's heads, receipt and handoffs."""
    if not projects.valid_id(device) or not isinstance(review, str):
        raise ValueError("Choose a desktop ID from list_sync_devices.")
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = folder()
        if device == cfg["device"]:
            raise ValueError("This is the desktop you are using. Disconnect it instead.")
        if review:
            sync.require_active(cfg)
        mine = vault / "heads" / device
        heads = sorted(p for p in head_files(vault) if p.parent.name == device)
        receipt = vault / "receipts-v1" / (device + ".json")
        handoffs = [vault / "handoffs-v1" / device, *sorted((vault / "handoffs-v1").glob("*/" + device))]
        handoffs = [p for p in handoffs if p.is_dir() and not p.is_symlink()]
        if not heads and not receipt.is_file() and not handoffs:
            raise ValueError("That desktop has no files in this sync folder.")
        # Removing a head is safe when another desktop holds the same or a longer history.
        waiting = {}
        copies = sync.shared_heads(vault, waiting)
        # A chat imported here is published under this desktop's head on its next pass.
        state = sync.read_json(sync.STATE / ("state-" + cfg["device"] + ".json"), {})
        only, unmatched = [], []
        for path in heads:
            tid = path.stem
            others = {d: m for d, m in copies.get(tid, {}).items() if d != device}
            imported = state.get(tid, {}).get("manifest")
            if isinstance(imported, dict) and isinstance(imported.get("chunks"), list) and "size" in imported:
                others.setdefault(cfg["device"], imported)
            own = copies.get(tid, {}).get(device)
            if not others:
                only.append(tid)
                continue
            try:
                covered = own is not None and any(
                    sync.latest_history(vault, [own, other], other)[1] for other in others.values()
                )
            except (OSError, ValueError, TypeError, AttributeError, KeyError):
                covered = False
            if not covered:
                unmatched.append(tid)
        name = projects.peer_file(receipt).get("name") if receipt.is_file() else None
        digest = sync.digest(sync.encode([device, [p.name for p in heads], only, unmatched]))
        plan = {
            "device": device,
            "name": name[:60] if isinstance(name, str) else None,
            "chats": len(heads),
            "only_copy": len(only),
            "unmatched_history": len(unmatched),
            "affected_chats": (only + unmatched)[:LISTED],
            "handoff_folders": len(handoffs),
        }
        if not review:
            return {**plan, "retired": False, "review": digest}
        if review != digest:
            raise ValueError("The sync folder changed. Preview this desktop again.")
        if mine.is_dir() and not mine.is_symlink():
            shutil.rmtree(mine)
        receipt.unlink(missing_ok=True)
        for path in handoffs:
            shutil.rmtree(path)
        return {**plan, "retired": True}
