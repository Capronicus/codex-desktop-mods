"""Adapt native workspace metadata locally while retaining exact portable history bytes."""

import json
import os
import re
from pathlib import Path

MARK = "_chat_sync_wire_record_v1"
PREFIX = ('{"' + MARK + '":').encode()
NATIVE = re.compile(rb'^\{"timestamp":"[^"]+","type":"([^"]+)","payload":')
LIMIT = 8 * 1024 * 1024
PART = 1024 * 1024


def fields(record):
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    if record.get("type") in ("session_meta", "turn_context"):
        return payload
    if record.get("type") == "event_msg" and payload.get("type") == "thread_settings_applied":
        return payload.get("thread_settings")


def local_dir(value):
    if not isinstance(value, str) or not value:
        return False
    # Do not probe network shares named by another computer's transcript.
    if os.name == "nt" and value.startswith(("//", "\\\\")):
        if not value.startswith("\\\\?\\") or len(value) < 7 or value[5:7] != ":\\":
            return False
    path = Path(value)
    try:
        return path.is_absolute() and path.is_dir()
    except (OSError, ValueError):
        return False


def local_record(raw, fallback, known_dirs):
    if raw.startswith(PREFIX):
        raise ValueError("Unexpected local workspace marker in shared history")
    record = json.loads(raw)
    if not isinstance(record, dict) or MARK in record:
        raise ValueError("Invalid shared history record")
    target = fields(record)
    if not isinstance(target, dict):
        return raw

    def available(value):
        if not isinstance(value, str):
            return False
        if value not in known_dirs:
            known_dirs[value] = local_dir(value)
        return known_dirs[value]

    changed = False
    if target.get("cwd") and not available(target["cwd"]):
        fallback.mkdir(parents=True, exist_ok=True)
        target["cwd"] = str(fallback)
        changed = True
    roots = target.get("runtime_workspace_roots")
    if isinstance(roots, list):
        valid = [r for r in roots if available(r)]
        if valid != roots:
            target["runtime_workspace_roots"] = valid
            changed = True
    if not changed:
        return raw
    # Unknown top-level fields are ignored by Codex. Keep the exact source line
    # alongside this local view so a subsequent reply still extends the same bytes.
    return json.dumps({MARK: raw.decode("utf-8"), **record}, ensure_ascii=False,
                      separators=(",", ":")).encode() + b"\n"


def wire_record(raw):
    if not raw.startswith(PREFIX):
        return raw
    record = json.loads(raw)
    original = record.pop(MARK).encode("utf-8")
    source = json.loads(original)
    restored, native = fields(record), fields(source)
    if not isinstance(restored, dict) or not isinstance(native, dict):
        raise ValueError("Invalid local workspace record")
    for key in ("cwd", "runtime_workspace_roots"):
        if key in native:
            restored[key] = native[key]
    if record != source or not original.endswith(b"\n") or original.startswith(PREFIX):
        raise ValueError("Local workspace metadata has changed unexpectedly")
    return original


def pieces(stream, fallback=None):
    """Pass long tool records in bounded pieces. Only buffer structured metadata."""
    at_start = True
    known_dirs = {}
    first = True
    while part := stream.readline(PART):
        local = part.startswith(PREFIX)
        candidate = False
        if at_start and fallback is not None:
            native = NATIVE.match(part)
            candidate = (native is None or native[1] in (b"session_meta", b"turn_context") or
                         (native[1] == b"event_msg" and b'"thread_settings_applied"' in part[:512]))
        if at_start and (local or candidate or first):
            limit = LIMIT * 4 if local else LIMIT
            if not part.endswith(b"\n"):
                part += stream.readline(limit + 1 - len(part))
            if len(part) > limit or not part.endswith(b"\n"):
                if first or local:
                    raise ValueError("Oversized or incomplete workspace metadata record")
            elif fallback is not None:
                part = local_record(part, fallback, known_dirs)
            else:
                part = wire_record(part)
        yield part
        first = False
        at_start = part.endswith(b"\n")
