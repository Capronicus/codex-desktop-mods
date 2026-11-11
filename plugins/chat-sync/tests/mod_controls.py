"""Exercise the panel's real MCP server with isolated profiles and a synthetic private folder."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid

SERVER = Path(__file__).resolve().parents[1] / "server.py"


class Client:
    def __init__(self, root, name):
        self.home = root / name
        directory = self.home / "thread-writer-locks"
        directory.mkdir(parents=True)
        (directory / ".coordination.lock").touch()
        with sqlite3.connect(self.home / "state_5.sqlite") as db:
            db.execute("create table threads(id, rollout_path, name, archived, source, cwd)")
        self.proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=-1, stdout=-1,
                                     env={**os.environ, "CODEX_HOME": str(self.home)}, text=True)
        self.seq = 0

    def request(self, method, **params):
        self.seq += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())["result"]

    def tool(self, tool_name, **args):
        reply = self.request("tools/call", name=tool_name, arguments=args)
        if reply.get("isError"):
            raise ValueError(reply["content"][0]["text"])
        return reply.get("structuredContent") or json.loads(reply["content"][0]["text"])

    def chat(self):
        tid = str(uuid.uuid4())
        entry = self.home / "archived_sessions" / ("rollout-2026-11-08T12-00-00-" + tid + ".jsonl")
        entry.parent.mkdir(exist_ok=True)
        entry.write_text(json.dumps({"type": "session_meta", "payload": {"id": tid, "source": "cli"}}) + "\n")
        return tid

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(10)
        self.proc.stdout.close()


class ModControls(unittest.TestCase):
    def test_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = Client(root, "a"), Client(root, "b")
            try:
                self.exercise(root, a, b)
            finally:
                a.close()
                b.close()

    def exercise(self, root, a, b):
        uri = "ui://chat-sync/mod-controls.html"
        tool = next(t for t in a.request("tools/list")["tools"] if t["name"] == "open_mod_controls")
        self.assertEqual(tool["_meta"]["ui"]["resourceUri"], uri)
        self.assertEqual(tool["_meta"]["openai/ui"]["entrypoints"], [{"type": "global"}])
        self.assertIn("resources", a.request("initialize")["capabilities"])
        self.assertIn("Mod Controls", a.request("resources/read", uri=uri)["contents"][0]["text"])
        state = a.tool("open_mod_controls")
        self.assertFalse(state["configured"])
        self.assertTrue(all(state["visuals"].values()))
        self.assertFalse((a.home / "chat-sync").exists())
        self.assertFalse(a.tool("set_mod_visual", feature="petMotion", enabled=False)["petMotion"])
        with self.assertRaisesRegex(ValueError, "Unknown appearance"):
            a.tool("set_mod_visual", feature="unknown", enabled=False)
        self.assertTrue(b.tool("mod_controls_status")["visuals"]["petMotion"])
        folder = root / "shared"
        folder.mkdir()
        device = a.tool("configure_chat_sync", folder=str(folder), automatic=False)["device"]
        a.tool("name_sync_device", name="Home")
        private = a.chat()
        a.tool("set_local_only", kind="chat", ident=private, enabled=True)
        a.tool("set_sync_paused", paused=True)
        public = a.chat()
        with self.assertRaisesRegex(ValueError, "paused"):
            a.tool("mod_controls_sync")
        self.assertFalse(any((folder / "codex-chat-sync-v1").iterdir()))
        for name in ("sync_settings_now", "sync_colours_now"):
            with self.assertRaisesRegex(ValueError, "paused"):
                a.tool(name)
        a.tool("disable_chat_sync")
        self.assertEqual(a.tool("mod_controls_status")["privateChats"], [private])
        self.assertEqual(a.tool("configure_chat_sync", folder=str(folder), automatic=False)["device"], device)
        self.assertEqual(a.tool("mod_controls_status")["device"], "Home")
        self.assertEqual(a.tool("mod_controls_sync")["exported"], 1)
        s = a.tool("mod_controls_status")
        self.assertEqual(s["receipts"], {"saved": 1, "local_only": 1})
        self.assertEqual(s["health"]["outcome"], "completed")
        self.assertFalse(s["visuals"]["petMotion"])
        b.tool("configure_chat_sync", folder=str(folder), automatic=False)
        b.tool("name_sync_device", name="Work")
        b.tool("mod_controls_sync")
        a.tool("mod_controls_sync")
        s = a.tool("mod_controls_status")
        self.assertEqual(s["peers"], [{"name": "Work", "received": 1}])
        self.assertEqual(s["receipts"]["received"], 1)
        folder.rename(root / "offline")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            a.tool("mod_controls_sync")
        after = a.tool("mod_controls_status")
        self.assertFalse(after["available"])
        self.assertEqual(after["health"]["outcome"], "failed")
        self.assertEqual(after["health"]["success"], s["health"]["success"])
        self.assertFalse(list(b.home.rglob("*" + private + ".jsonl")))
        self.assertTrue(list(b.home.rglob("*" + public + ".jsonl")))
        a.tool("disable_chat_sync")
        folder.mkdir()
        other = root / "elsewhere"
        other.mkdir()
        self.assertNotEqual(a.tool("configure_chat_sync", folder=str(other), automatic=False)["device"], device)
        self.assertEqual(a.tool("mod_controls_sync")["exported"], 1)
        self.assertFalse(list(other.rglob(private + ".json")))


if __name__ == "__main__":
    unittest.main()
