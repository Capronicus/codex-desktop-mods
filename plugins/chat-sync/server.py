"""Local, newline-delimited MCP server for Codex Chat Sync."""

import json
from http.client import HTTPException
import sys
import sqlite3
import subprocess
import threading
import sync
import projects
import delivery
import batches
import activation
import controls
import integrity
import outgoing
import chat_actions
import sharing_rules

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
STOP = threading.Event()


def tool(name, description, fields=None, readonly=False, optional=()):
    fields = fields or {}
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": fields,
            "required": [key for key in fields if key not in optional],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": readonly},
    }


TOOLS = [
    {**tool("open_mod_controls", "Open Mod Controls to see sync health, pause transfers and change appearance.", readonly=True),
     "title": "Mod Controls", "_meta": {"ui": {"resourceUri": controls.URI},
               "openai/ui": {"entrypoints": [{"type": "global"}]}}},
    tool("set_shared_exclusion","Stop or resume project or linked-folder transfers across updated desktops. Existing copies are retained.",
         {"kind":{"type":"string","enum":["project","folder","rule"]},"ident":{"type":"string"},"enabled":{"type":"boolean"}}),
    tool("mod_controls_status", "Read local sync health, receipt counts and visual preferences.", readonly=True),
    tool("preview_chat_sharing", "Preview outgoing local chats and exclusions without transferring histories. Pages start at zero.",
         {"group": {"type": "string", "enum": ["all", *outgoing.GROUPS]},
          "search": {"type": "string", "maxLength": 256}, "page": {"type": "integer", "minimum": 0}},
         readonly=True, optional=("group", "search", "page")),
    {**tool("undo_sidebar_batch", "Undo the sidebar batch displayed in Mod Controls.",
            {"batch": {"type": "string"}}), "_meta": {"ui": {"visibility": ["app"]}}},
    tool("set_sync_device_label", "Name this desktop and choose one origin emoji. An empty emoji removes it.",
         {"name": {"type": "string"}, "emoji": {"type": "string"}}),
    tool("check_sync_folder", "Verify the blocks referenced by current shared chat revisions and save a local report.", readonly=True),
    tool("repair_sync_folder", "Restore missing or damaged blocks of current shared chat revisions from eligible matching local history."),
    {**tool("mod_controls_sync", "Exchange chats with this desktop's configured folder."),
     "_meta": {"ui": {"visibility": ["app"]}}},
    tool("set_sync_paused", "Pause or resume chat sync on this desktop, keeping its sharing rules.",
         {"paused": {"type": "boolean"}}),
    tool("set_mod_visual", "Change a visual preference on this desktop.",
         {"feature": {"type": "string", "enum": list(controls.FEATURES)}, "enabled": {"type": "boolean"}}),
    tool(
        "list_sync_projects",
        "List local project IDs, checkout folders, shared projects and explicit links.",
        readonly=True,
    ),
    tool(
        "link_sync_project",
        "Link one existing local project to a shared project by ID and select its checkout on this computer.",
        {key: {"type": "string"} for key in ("local_project", "shared_project", "folder")},
    ),
    tool(
        "chat_delivery_status",
        "Check the last synced chat revision and the desktops that acknowledged it.",
        {"thread": {"type": "string"}},
        readonly=True,
    ),
    tool(
        "name_sync_device",
        "Choose this computer's display name for receipts.",
        {"name": {"type": "string"}},
    ),
    tool(
        "set_local_only",
        "Exclude or include a local chat or project in future sync on this device. Reports previous sharing.",
        {
            "kind": {"type": "string", "enum": ["chat", "project", "folder"]},
            "ident": {"type": "string"},
            "enabled": {"type": "boolean"},
        },
    ),
    tool(
        "preview_sidebar_sync",
        "Preview received sidebar changes without applying them.",
        readonly=True,
    ),
    tool(
        "apply_sidebar_preview",
        "Apply the reviewed batch using its exact preview ID.",
        {"batch": {"type": "string"}},
    ),
    tool(
        "undo_sidebar_sync",
        "Undo the most recent received sidebar batch locally and pause sidebar sync.",
    ),
    tool(
        "set_pin_order_sync",
        "Opt in or out of local chat pins and sidebar ordering on this device.",
        {"enabled": {"type": "boolean"}},
    ),
    tool(
        "set_settings_sync",
        "Enable or disable syncing desktop appearance and chat preferences through this device’s configured private folder.",
        {"enabled": {"type": "boolean"}},
    ),
    tool("sync_settings_now", "Exchange only supported desktop appearance and chat preferences."),
    tool("chat_sync_status", "Show local configuration and the last sync result.", readonly=True),
    tool(
        "configure_chat_sync",
        "Use an existing private synced folder selected by the user. Enabling automatic mode exchanges transcripts while this plugin runs.",
        {"folder": {"type": "string"}, "automatic": {"type": "boolean"}},
    ),
    tool(
        "set_sidebar_sync",
        "Enable or disable exchanging local project and custom section names and chat membership through the configured private folder.",
        {"enabled": {"type": "boolean"}},
    ),
    tool(
        "sync_colours_now",
        "Exchange only sidebar colours, saved swatches and colour memberships with the configured folder.",
    ),
    tool(
        "set_colour_sync",
        "Enable or disable exchanging sidebar colours and saved swatches in the configured private folder.",
        {"enabled": {"type": "boolean"}},
    ),
    tool(
        "sync_chats",
        "Exchange completed local Codex conversations with the configured private folder.",
    ),
    tool(
        "keep_conflict_branch",
        "Keep one divergent device history as a separate chat. Both original histories are preserved.",
        {"thread": {"type": "string"}, "device": {"type": "string"}},
    ),
    tool(
        "adopt_chat_history",
        "Keep this desktop’s divergent chat as a separate branch and use the selected device’s history for the original chat here.",
        {"thread": {"type": "string"}, "device": {"type": "string"}},
    ),
    tool(
        "disable_chat_sync",
        "Stop participating in the configured folder; existing copies stay in place.",
    ),
] + activation.TOOLS
ACTIONS = {
    "open_mod_controls": controls.status,
    "mod_controls_status": controls.status,
    "preview_chat_sharing": outgoing.preview,
    "mod_controls_sync": controls.sync_now,
    "check_sync_folder": integrity.inspect_folder,
    "repair_sync_folder": lambda: integrity.inspect_folder(repair=True),
    "set_sync_paused": sync.set_paused,
    "set_mod_visual": controls.set_visual,
    "activation_status": activation.status,
    "report_activation": activation.report,
    "list_sync_projects": projects.inventory,
    "link_sync_project": projects.link,
    "chat_delivery_status": delivery.status,
    "name_sync_device": delivery.set_name,
    "set_sync_device_label": delivery.set_name,
    "set_local_only": projects.set_private,
    "set_shared_exclusion": sharing_rules.set_rule,
    "preview_sidebar_sync": batches.review,
    "apply_sidebar_preview": lambda batch: batches.review("apply", batch),
    "undo_sidebar_sync": batches.undo,
    "undo_sidebar_batch": batches.undo,
    "set_pin_order_sync": lambda enabled: sync.set_exchange_option("order", enabled),
    "chat_sync_status": sync.status,
    "set_settings_sync": lambda enabled: sync.set_exchange_option("settings", enabled),
    "sync_settings_now": sync.sync_settings_now,
    "configure_chat_sync": sync.setup,
    "sync_chats": sync.run,
    "sync_colours_now": sync.sync_colours_now,
    "set_colour_sync": lambda enabled: sync.set_exchange_option("colours", enabled),
    "set_sidebar_sync": lambda enabled: sync.set_exchange_option("sidebar", enabled),
    "keep_conflict_branch": lambda thread, device: sync.keep_branch(thread, device),
    "adopt_chat_history": lambda thread, device: sync.adopt_history(thread, device),
    "disable_chat_sync": sync.disable,
}


# Actions called by the interactive Mod Controls view.
for name, fn, required, optional, readonly in (
    ("chat_action_dashboard", chat_actions.dashboard, (), (), True),
    ("open_linked_file", chat_actions.open_file, ("thread","reference"), (), True),
    ("edit_linked_file", chat_actions.edit_file, ("thread","reference"), (), False),
    ("reveal_linked_file", chat_actions.reveal_file, ("thread","reference"), (), False),
    ("send_reply_handoff", chat_actions.handoff, ("thread","turn","device","request"), ("note","item"), False),
    ("read_handoff_reply", chat_actions.reply, ("thread","turn"), ("item",), True),
    ("dismiss_reply_handoff", chat_actions.dismiss, ("sender","request"), (), False),
    ("list_chat_family", chat_actions.family, ("thread",), (), True),
    ("compare_chat_histories", chat_actions.conflict, ("thread","left","right"), (), True),
    ("desktop_chat_command", chat_actions.desktop_command, ("action",), ("thread","turn","item"), False),
):
    TOOLS.append({**tool(name, name.replace("_"," ").capitalize(), {k: {"type":"string"} for k in (*required,*optional)},
                         readonly=readonly, optional=optional), "_meta":{"ui":{"visibility":["app"]}}})
    ACTIONS[name] = fn


def background():
    while not STOP.wait(10):
        try:
            cfg = sync.read_json(sync.CONFIG)
            if cfg and cfg["automatic"] and not cfg.get("paused"):
                sync.run(automatic=True)
        except sync.Busy:
            pass
        except Exception as exc:
            print("Chat sync: " + str(exc), file=sys.stderr, flush=True)
            STOP.wait(50)


def handle(req):
    if "id" not in req:
        return
    method = req.get("method")
    params = req.get("params", {})
    if method == "initialize":
        version = params.get("protocolVersion")
        return {
            "protocolVersion": version if version in PROTOCOLS else PROTOCOLS[0],
            "serverInfo": {"name": "chat-sync", "version": "0.12.0"},
            "capabilities": {"tools": {}, "resources": {}},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "resources/list":
        return {"resources": [{"uri": controls.URI, "name": "Mod Controls", "mimeType": controls.MIME}]}
    if method == "resources/templates/list":
        return {"resourceTemplates": []}
    if method == "resources/read" and params.get("uri") == controls.URI:
        return {"contents": [controls.resource()]}
    if method != "tools/call" or params.get("name") not in ACTIONS:
        raise ValueError("Unknown method or tool")
    name = params["name"]
    args = params.get("arguments", {})
    schema = next(t["inputSchema"] for t in TOOLS if t["name"] == name)
    fields = schema["properties"]
    types = {"string": str, "boolean": bool, "integer": int}
    if (
        not isinstance(args, dict)
        or not set(schema.get("required", ())) <= args.keys() <= fields.keys()
        or any(type(value) is not types[fields[key]["type"]] for key, value in args.items())
    ):
        raise ValueError("Invalid tool arguments")
    try:
        result = ACTIONS[name](**args)
        if name in ("open_mod_controls", "mod_controls_status"):
            return {"structuredContent": result,
                    "content": [{"type": "text", "text": "Mod Controls" if name == "open_mod_controls" else json.dumps(result)}]}
        content = [{"type": "text", "text": json.dumps(result)}]
        if name == "sync_chats":
            invitation = activation.offer()
            if invitation:
                content.append({"type": "text", "text": json.dumps({"activation_offer": invitation})})
        return {"content": content}
    except (
        OSError,
        ValueError,
        sync.Busy,
        sqlite3.Error,
        EOFError,
        subprocess.SubprocessError,
        HTTPException,
    ) as exc:
        return {
            "isError": True,
            "content": [{"type": "text", "text": str(exc) or "Busy; retry shortly."}],
        }


if __name__ == "__main__":
    threading.Thread(target=background, daemon=True).start()
    try:
        for line in sys.stdin.buffer:
            req = {}
            try:
                req = json.loads(line)
                result = handle(req)
                if result is None:
                    continue
                reply = {"result": result}
            except Exception as exc:
                reply = {"error": {"code": -32602, "message": str(exc)}}
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": req.get("id") if isinstance(req, dict) else None,
                        **reply,
                    }
                ),
                flush=True,
            )
    finally:
        STOP.set()
