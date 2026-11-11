"""Exercise the panel's real MCP server with isolated profiles and a synthetic private folder."""
from contextlib import closing
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
        with closing(sqlite3.connect(self.home / "state_5.sqlite")) as db, db:
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
    def test_outgoing_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with closing(Client(root, "Home")) as a:
                with self.assertRaisesRegex(ValueError, "Configure"):
                    a.tool("preview_chat_sharing")
                self.assertFalse((a.home / "chat-sync").exists())
                folder = root / "shared"
                folder.mkdir()
                a.tool("configure_chat_sync", folder=str(folder), automatic=False)
                a.tool("set_sync_paused", paused=True)
                ids = [a.chat() for _ in range(27)]
                excluded, unknown, duplicate, unsupported = [a.chat() for _ in range(4)]
                missing = str(uuid.uuid4())
                archive = a.home / "archived_sessions"
                (archive / ("rollout-2026-11-07T12-00-00-" + duplicate + ".jsonl")).touch()
                with closing(sqlite3.connect(a.home / "state_5.sqlite")) as db, db:
                    db.execute("ALTER TABLE threads ADD title TEXT")
                    db.executemany("INSERT INTO threads(id,title,source) VALUES(?,?,?)",
                                   [(tid, f"Fixture {i:02d}", "cli") for i, tid in enumerate(ids)]
                                   + [(unsupported, "Automation fixture", "subagent"), (missing, "Missing", "vscode")])
                a.tool("set_local_only", kind="chat", ident=excluded, enabled=True)
                vault = folder / "codex-chat-sync-v1"
                local = a.home / "chat-sync"
                before = {p.name: p.read_bytes() for p in local.iterdir() if p.is_file()}
                result = a.tool("preview_chat_sharing")
                self.assertEqual(result["counts"], {"pending":27,"check":1,"excluded":1,"unavailable":2,"unchanged":0})
                self.assertEqual(len(result["chats"]), 25)
                last = a.tool("preview_chat_sharing", search="FIXTURE", group="pending", page=999)
                self.assertEqual([r["name"] for r in last["chats"]], ["Fixture 25", "Fixture 26"])
                self.assertEqual(last["page"], 1)
                for tid, reason in [(excluded, "chat_rule"), (unknown, "unindexed"), (duplicate, "multiple_files"), (missing, "missing_file")]:
                    row, = a.tool("preview_chat_sharing", search=tid)["chats"]
                    self.assertEqual(row["reason"], reason)
                self.assertEqual(a.tool("preview_chat_sharing", search=unsupported)["matched"], 0)
                self.assertEqual(before, {p.name:p.read_bytes() for p in local.iterdir() if p.is_file()})
                self.assertFalse(list(vault.iterdir()))
                a.tool("set_sync_paused", paused=False)
                self.assertEqual(a.tool("sync_chats")["exported"], 28)
                self.assertEqual(a.tool("preview_chat_sharing",group="unchanged")["matched"], 28)
                with next(archive.glob("*" + ids[0] + ".jsonl")).open("ab") as stream:
                    stream.write(b'{"type":"event_msg","payload":{"type":"task_complete"}}\n')
                self.assertEqual(a.tool("preview_chat_sharing",search=ids[0])["chats"][0]["reason"], "changed")
                vault.rename(root / "offline")
                with self.assertRaisesRegex(ValueError, "unavailable"):
                    a.tool("preview_chat_sharing")
                self.assertFalse(vault.exists())

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
        a.tool("set_sync_device_label", name="Home", emoji="🏠")
        with self.assertRaisesRegex(ValueError, "one emoji"):
            a.tool("set_sync_device_label", name="Home", emoji="Home!")
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
        self.assertEqual(a.tool("mod_controls_status")["deviceEmoji"], "🏠")
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
        origin = b.tool("chat_delivery_status", thread=public)["origin"]
        self.assertEqual((origin["name"], origin["emoji"], origin["basis"]), ("Home", "🏠", "first_seen"))
        a.tool("set_sync_device_label", name="Workshop", emoji="👩🏽‍💻")
        self.assertEqual(a.tool("chat_delivery_status", thread=public)["origin"]["emoji"], "👩🏽‍💻")
        a.tool("mod_controls_sync")
        b.tool("mod_controls_sync")
        self.assertEqual(b.tool("chat_delivery_status", thread=public)["origin"]["emoji"], "👩🏽‍💻")
        a.tool("set_sync_device_label", name="Home", emoji="")
        a.tool("mod_controls_sync")
        b.tool("mod_controls_sync")
        self.assertEqual(b.tool("chat_delivery_status", thread=public)["origin"]["emoji"], "")
        s = a.tool("mod_controls_status")
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
