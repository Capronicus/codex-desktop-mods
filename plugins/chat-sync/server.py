"""Local, newline-delimited MCP server for Codex Chat Sync."""

import json
import sys
import sqlite3
import subprocess
import threading
import sync

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
STOP = threading.Event()


def tool(name, description, fields=None, readonly=False):
    fields = fields or {}
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": fields,
            "required": list(fields),
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": readonly},
    }


TOOLS = [
    tool("chat_sync_status", "Show local configuration and the last sync result.", readonly=True),
    tool(
        "configure_chat_sync",
        "Use an existing private synced folder selected by the user. Enabling automatic mode exchanges transcripts while this plugin runs.",
        {"folder": {"type": "string"}, "automatic": {"type": "boolean"}},
    ),
    tool("set_sidebar_sync", "Enable or disable exchanging local project and custom section names and chat membership through the configured private folder.",
         {"enabled": {"type": "boolean"}}),
    tool("set_colour_sync", "Enable or disable exchanging sidebar colours and saved swatches in the configured private folder.",
         {"enabled": {"type": "boolean"}}),
    tool(
        "sync_chats", "Exchange completed local Codex conversations with the configured private folder."
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
]
ACTIONS = {
    "chat_sync_status": sync.status,
    "configure_chat_sync": sync.setup,
    "sync_chats": sync.run,
    "set_colour_sync": lambda enabled: sync.set_exchange_option("colours", enabled),
    "set_sidebar_sync": lambda enabled: sync.set_exchange_option("sidebar", enabled),
    "keep_conflict_branch": lambda thread, device: sync.keep_branch(thread, device),
    "adopt_chat_history": lambda thread, device: sync.adopt_history(thread, device),
    "disable_chat_sync": sync.disable,
}


def background():
    while not STOP.wait(10):
        try:
            cfg = sync.read_json(sync.CONFIG)
            if cfg and cfg["automatic"]:
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
            "serverInfo": {"name": "chat-sync", "version": "0.3.0"},
            "capabilities": {"tools": {}},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method != "tools/call" or params.get("name") not in ACTIONS:
        raise ValueError("Unknown method or tool")
    name = params["name"]
    args = params.get("arguments", {})
    fields = next(t["inputSchema"]["properties"] for t in TOOLS if t["name"] == name)
    if (
        not isinstance(args, dict)
        or args.keys() != fields.keys()
        or any(
            not isinstance(args[k], bool if f["type"] == "boolean" else str) for k, f in fields.items()
        )
    ):
        raise ValueError("Invalid tool arguments")
    try:
        result = ACTIONS[name](**args)
        return {"content": [{"type": "text", "text": json.dumps(result)}]}
    except (OSError, ValueError, sync.Busy, sqlite3.Error, EOFError, subprocess.SubprocessError) as exc:
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
                    {"jsonrpc": "2.0", "id": req.get("id") if isinstance(req, dict) else None, **reply}
                ),
                flush=True,
            )
    finally:
        STOP.set()
