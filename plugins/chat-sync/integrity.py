"""Check blocks referenced by current device heads and restore matching local bytes."""

from collections import Counter, defaultdict
import gzip
import stat
import subprocess
import tempfile
import time

import projects
import sync


def inspect_block(vault, key):
    path = sync.block_path(vault, key)
    try:
        if path.resolve() != path or not stat.S_ISREG(path.stat().st_mode):
            return "unreadable"
        return len(sync.block_data(vault, key))
    except FileNotFoundError:
        return "missing"
    except ValueError:
        return "damaged"
    except (OSError, RuntimeError):
        return "unreadable"


def restore_chat(vault, tid, entry, targets, states, restored):
    path, _ = entry
    with tempfile.SpooledTemporaryFile(max_size=sync.BLOCK, dir=sync.STATE) as staged:
        if sync.stage_rollout(tid, path, path.stat(), staged) is None:
            return
        for offset, length, key in sorted(targets):
            if states[key] not in ("missing", "damaged"):
                continue
            staged.seek(offset)
            block = staged.read(length)
            if len(block) != length or sync.digest(block) != key:
                continue
            # A peer or the folder client may have restored it since our scan.
            current = inspect_block(vault, key)
            if current in ("missing", "damaged"):
                if not vault.is_dir() or vault.resolve() != vault:
                    raise OSError("Sync folder unavailable")
                sync.atomic(sync.block_path(vault, key), gzip.compress(block, compresslevel=3, mtime=0))
                current = inspect_block(vault, key)
                if current == length:
                    restored.add(key)
            states[key] = current


def inspect_folder(repair=False):
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = projects.config()
        if repair:
            sync.require_active(cfg)
        if not vault.is_dir() or vault.resolve() != vault:
            raise ValueError("Sync folder unavailable. Check the mount or desktop sync client.")
        needs_membership = cfg.get("private_projects") or cfg.get("project_links")
        index = sync.catalog() if needs_membership else {}
        private = projects.Sharing(cfg, vault, index, sync.HOME).private_chats
        incomplete = {}
        heads = sync.shared_heads(vault, incomplete)
        incomplete = set(incomplete) - private
        targets = defaultdict(set)
        chats = set(heads) - private
        for tid in chats:
            for man in heads[tid].values():
                for i, key in enumerate(man["chunks"]):
                    offset = i * sync.BLOCK
                    length = min(sync.BLOCK, man["size"] - offset)
                    targets[tid].add((offset, length, key))
        keys = {key for blocks in targets.values() for _, _, key in blocks}
        states = {key: inspect_block(vault, key) for key in keys}
        restored = set()
        busy = unavailable = 0
        if repair and any(value in ("missing", "damaged") for value in states.values()):
            local = sync.streams()
            if local and not needs_membership:
                index = sync.catalog()
            for tid, blocks in targets.items():
                if not local.get(tid) or not any(states[k] in ("missing", "damaged") for _, _, k in blocks):
                    continue
                if tid in index and index[tid]["source"] not in ("cli", "vscode", "appServer", "app_server"):
                    continue
                try:
                    restore_chat(vault, tid, local[tid], blocks, states, restored)
                except sync.Busy:
                    busy += 1
                except (OSError, ValueError, EOFError, subprocess.SubprocessError):
                    unavailable += 1
        for tid, blocks in targets.items():
            if any(isinstance(states[k], int) and states[k] != length for _, length, k in blocks):
                incomplete.add(tid)
        if not vault.is_dir() or vault.resolve() != vault:
            raise ValueError("Sync folder became unavailable. Reconnect it and check again.")
        counts = Counter(value for value in states.values() if isinstance(value, str))
        report = {"checked_at": time.time(), "chats": len(chats), "blocks": len(states),
                  "missing": counts["missing"], "damaged": counts["damaged"], "unreadable": counts["unreadable"],
                  "incomplete_chats": len(incomplete), "restored": len(restored), "busy": busy,
                  "unavailable_sources": unavailable, "operation": "repair" if repair else "check"}
        sync.atomic(sync.STATE / ("integrity-" + cfg["device"] + ".json"), sync.encode(report))
        return report
