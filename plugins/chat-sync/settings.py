"""Exchange portable Codex desktop appearance and chat preferences."""

from pathlib import Path
import re

from native_names import native_api
import sync

CHOICES = {
    "appearanceTheme": {"system", "light", "dark"},
    "appearanceDiffMarkerStyle": {"color", "symbols"},
    "reduced-motion-preference": {"system", "on", "off"},
    "conversationDetailMode": {"STEPS_PROSE", "STEPS_COMMANDS", "STEPS_EXECUTION"},
    "followUpQueueMode": {"queue", "steer", "interrupt"},
    "composerEnterBehavior": {"enter", "cmdIfMultiline", "cmdAlways"},
    "reviewDelivery": {"inline", "detached"},
    "notifications-turn-mode": {"off", "unfocused", "always"},
}
FLAGS = {
    "useFontSmoothing",
    "usePointerCursors",
    "pagesFullWidth",
    "pagesSmartPunctuationEnabled",
    "composerPlainTextMode",
    "show-context-window-usage",
    "sidebar-show-projects-in-recents",
    "notifications-dot-enabled",
}
SIZES = {"sansFontSize": (11, 16), "codeFontSize": (8, 24), "pagesFontSize": (12, 24)}
THEMES = {"appearanceDarkChromeTheme", "appearanceLightChromeTheme"}
CODE_THEMES = {"appearanceDarkCodeThemeId", "appearanceLightCodeThemeId"}
KEYS = FLAGS | CHOICES.keys() | SIZES.keys() | THEMES | CODE_THEMES
HEX = re.compile(r"#[a-fA-F0-9]{6}")
FONT = re.compile(r"""[\w ,."'\-]{1,192}""")
MAX_REV = 2**53 - 1


def native_number(value):
    # Some desktop app servers expose TOML numbers as serde_json's lossless-number object.
    if isinstance(value, dict) and set(value) == {"$serde_json::private::Number"}:
        number = value["$serde_json::private::Number"]
        if isinstance(number, str) and re.fullmatch(r"-?[0-9]{1,8}(?:\.[0-9]{1,12})?", number):
            return float(number) if "." in number else int(number)
    return value


def native_preferences(desktop):
    values = {k: v for k, v in desktop.items() if k in KEYS}
    for key in SIZES.keys() & values.keys():
        values[key] = native_number(values[key])
    for key in THEMES & values.keys():
        theme = values[key]
        if isinstance(theme, dict) and "contrast" in theme:
            values[key] = {**theme, "contrast": native_number(theme["contrast"])}
    return values


def valid_theme(theme):
    if not isinstance(theme, dict):
        return False
    required = {"accent", "ink", "surface", "contrast", "opaqueWindows", "semanticColors", "fonts"}
    if not required <= theme.keys() <= required | {"accentSource"}:
        return False
    semantic = theme["semanticColors"]
    if not isinstance(semantic, dict) or set(semantic) != {"diffAdded", "diffRemoved", "skill"}:
        return False
    if not all(
        isinstance(v, str) and HEX.fullmatch(v)
        for v in [theme["accent"], theme["ink"], theme["surface"], *semantic.values()]
    ):
        return False
    if (
        type(theme["contrast"]) is not int
        or not 0 <= theme["contrast"] <= 100
        or type(theme["opaqueWindows"]) is not bool
        or theme.get("accentSource") not in (None, "custom", "chatgpt")
    ):
        return False
    fonts = theme["fonts"]
    if not isinstance(fonts, dict) or not fonts.keys() <= {
        "ui",
        "code",
        "content",
        "uiFace",
        "codeFace",
        "contentFace",
    }:
        return False
    for key, value in fonts.items():
        if key.endswith("Face"):
            if not isinstance(value, dict) or set(value) != {
                "family",
                "fullName",
                "postscriptName",
            }:
                return False
            names = value.values()
        else:
            names = [value]
        if not all(isinstance(v, str) and FONT.fullmatch(v) for v in names):
            return False
    return True


def valid(key, value):
    if key not in KEYS:
        return False
    if value is None:
        return True
    if key in FLAGS:
        return type(value) is bool
    if key in CHOICES:
        return isinstance(value, str) and value in CHOICES[key]
    if key in SIZES:
        low, high = SIZES[key]
        return type(value) in (int, float) and low <= value <= high
    if key in CODE_THEMES:
        return isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value))
    return valid_theme(value)


def read_rows(file):
    if file.stat().st_size > 128 * 1024:
        raise ValueError("Oversized settings file")
    doc = sync.read_json(file)
    if (
        not isinstance(doc, dict)
        or doc.get("version") != 1
        or not isinstance(doc.get("rows"), dict)
    ):
        raise ValueError("Invalid settings file")
    rows = {}
    for key, row in doc["rows"].items():
        if key not in KEYS:
            continue
        if (
            not isinstance(row, list)
            or len(row) != 3
            or type(row[0]) is not int
            or not 0 < row[0] <= MAX_REV
            or not isinstance(row[1], str)
            or not sync.UUID.fullmatch(row[1])
            or not valid(key, row[2])
        ):
            raise ValueError("Invalid settings record")
        rows[key] = row
    return rows


def save(path, doc):
    data = sync.encode(doc)
    if not path.exists() or path.read_bytes() != data:
        sync.atomic(path, data)


def exchange(home, vault, device, action="sync", approval=""):
    directory = vault / "settings-v1"
    preview_file = home / "chat-sync" / "settings-preview.json"
    checkpoint = home / "chat-sync" / ("settings-" + device + ".json")
    state = sync.read_json(checkpoint, {})
    previous = state.get("applied", {})
    pending = state.get("pending", {})
    before = state.get("before", {})
    winners = {**previous, **pending}
    deferred = []
    for file in sorted(directory.glob("*.json")):
        if not sync.UUID.fullmatch(file.stem):
            continue
        try:
            peer = read_rows(file)
        except (OSError, ValueError, RecursionError):
            deferred.append(file.stem)
            continue
        for key, row in peer.items():
            if key not in winners or row[:2] > winners[key][:2]:
                winners[key] = row
    with native_api(home) as call:
        layers = call("config/read", {"includeLayers": True})["layers"]
        layer = next(
            (
                entry
                for entry in layers
                if entry["name"]["type"] == "user"
                and Path(entry["name"]["file"]).resolve() == (home / "config.toml").resolve()
            ),
            None,
        )
        if layer is None or not layer.get("version"):
            raise ValueError("Codex could not identify this profile’s writable settings.")
        desktop = layer["config"].get("desktop", {})
        if not isinstance(desktop, dict):
            raise ValueError("Codex desktop preferences could not be read.")
        desktop = native_preferences(desktop)
        skipped = []
        edits = []
        changes = []
        observed = {}
        for key in sorted(KEYS):
            local = desktop.get(key)
            if not valid(key, local):
                skipped.append(key)
                continue
            prior = previous.get(key)
            # Recover an interrupted write without re-exporting the received value as a new edit.
            if key in pending and (
                local == pending[key][2] or key in before and local != before[key]
            ):
                prior = pending[key]
            changed = prior is not None and local != prior[2]
            if changed or (key not in winners and local is not None):
                revision = max(prior[0] if prior else 0, pending.get(key, [0])[0]) + 1
                if revision > MAX_REV:
                    raise ValueError("Settings revision limit reached.")
                candidate = [revision, device, local]
                if key not in winners or candidate[:2] >= winners[key][:2]:
                    winners[key] = candidate
            if key in winners and local != winners[key][2]:
                observed[key] = local
                changes.append({"key": key, "before": local, "after": winners[key][2], "device": winners[key][1]})
                edits.append(
                    {
                        "keyPath": "desktop." + key,
                        "value": winners[key][2],
                        "mergeStrategy": "replace",
                    }
                )
        review = sync.digest(sync.encode(changes))
        if action == "preview":
            return {"id": review, "changes": changes, "skipped_local": skipped, "deferred_devices": deferred}
        if action == "apply" and approval != review:
            raise ValueError("The settings preview changed. Request a fresh preview.")
        # The first settings a desktop receives replace its own. Show them before anything is written.
        if edits and action != "apply" and "applied" not in state:
            save(preview_file, {"id": review, "count": len(changes)})
            return {"preview_required": True, "id": review, "changes": len(changes), "deferred_devices": deferred}
        preview_file.unlink(missing_ok=True)
        # Record the intended changes before writing so a retry can recognise its own edits.
        if edits:
            save(checkpoint, {"applied": previous, "pending": winners, "before": observed})
            call("config/batchWrite", {"edits": edits, "expectedVersion": layer["version"]})
    # A future unsupported local representation must not become a reset or count as an applied change.
    applied = {k: v for k, v in winners.items() if k not in skipped}
    applied.update((k, previous[k]) for k in skipped if k in previous)
    save(checkpoint, {"applied": applied})
    save(directory / (device + ".json"), {"version": 1, "rows": winners})
    return {
        "active": sum(row[2] is not None for row in winners.values()),
        "applied": len(edits),
        "skipped_local": skipped,
        "deferred_devices": deferred,
    }
