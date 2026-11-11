"""Exercise shared-folder checks and repair with two disposable desktops."""

from contextlib import ExitStack
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mod_controls import Client, SERVER

sys.path.insert(0, str(SERVER.parent))
import sync


def append(path, kind, text=""):
    with path.open("ab") as stream:
        stream.write(json.dumps({"type": "event_msg", "payload": {"type": kind, "text": text}}).encode() + b"\n")


class FolderIntegrity(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        root = Path(self.resources.enter_context(tempfile.TemporaryDirectory()))
        folder = root / "shared"
        folder.mkdir()
        self.vault = folder / "codex-chat-sync-v1"
        self.a, self.b = Client(root, "Home"), Client(root, "Work")
        for client in (self.a, self.b):
            self.resources.callback(client.close)
            client.tool("configure_chat_sync", folder=str(folder), automatic=False)
        self.tid = self.a.chat()
        self.local = next((self.a.home / "archived_sessions").glob("*.jsonl"))
        self.writer = self.a.home / "thread-writer-locks" / (self.tid + ".lock")

    def test_busy_snapshot_and_repair(self):
        with sync.lock(self.writer):
            append(self.local, "task_started")
            append(self.local, "agent_message", "a" * (2 * sync.BLOCK))
            self.assertEqual(self.a.tool("sync_chats")["busy"], 1)
            self.assertFalse((self.vault / "blocks").exists())
            self.assertFalse((self.vault / "heads").exists())
            append(self.local, "task_complete")
            self.assertEqual(self.a.tool("sync_chats")["exported"], 1)
        head = next((self.vault / "heads").rglob("*.json"))
        revision = json.loads(head.read_bytes())
        manifest_path = self.vault / "versions" / self.tid / (revision + ".json")
        manifest = json.loads(manifest_path.read_bytes())
        files = [sync.block_path(self.vault, k) for k in manifest["chunks"]]
        files[0].unlink()
        files[-1].write_bytes(gzip.compress(b"different bytes"))
        self.assertEqual(self.b.tool("repair_sync_folder")["restored"], 0)
        self.assertFalse((self.b.home / "archived_sessions").exists())
        self.a.tool("set_sync_paused", paused=True)
        status = self.a.tool("check_sync_folder")
        self.assertEqual((status["missing"], status["damaged"]), (1, 1))
        self.assertEqual(self.a.tool("mod_controls_status")["integrity"], status)
        with self.assertRaisesRegex(ValueError, "paused"):
            self.a.tool("repair_sync_folder")
        self.a.tool("set_sync_paused", paused=False)
        old_local = self.local.read_bytes()
        with sync.lock(self.writer):
            append(self.local, "task_started")
            result = self.a.tool("repair_sync_folder")
            self.assertEqual((result["busy"], result["restored"]), (1, 0))
        # The newer local revision extends the old final, partial block.
        # Repair must restore exactly that old prefix without publishing a new head.
        append(self.local, "task_complete")
        repaired = self.a.tool("repair_sync_folder")
        self.assertEqual((repaired["restored"], repaired["missing"], repaired["damaged"]), (2, 0, 0))
        self.assertEqual(json.loads(head.read_bytes()), revision)
        self.assertEqual(self.a.tool("repair_sync_folder")["restored"], 0)
        self.assertEqual(self.b.tool("sync_chats")["imported"], 1)
        self.assertEqual(next((self.b.home / "archived_sessions").glob("*.jsonl")).read_bytes(), old_local)
        manifest_path.unlink()
        self.assertEqual(self.b.tool("check_sync_folder")["incomplete_chats"], 1)

    def test_private_and_changed_sources(self):
        self.a.tool("sync_chats")
        block = next((self.vault / "blocks").rglob("*.gz"))
        block.unlink()
        self.a.tool("set_local_only", kind="chat", ident=self.tid, enabled=True)
        self.assertEqual(self.a.tool("repair_sync_folder")["blocks"], 0)
        self.assertFalse(block.exists())
        self.a.tool("set_local_only", kind="chat", ident=self.tid, enabled=False)
        self.local.write_bytes(self.local.read_bytes().replace(b'"cli"', b'"vscode"'))
        result = self.a.tool("repair_sync_folder")
        self.assertEqual((result["missing"], result["restored"]), (1, 0))

    def test_file_changes_during_snapshot(self):
        pieces = sync.workspace_paths.pieces
        def change_during_read(stream):
            yield from pieces(stream)
            append(self.local, "task_started")
        with (patch.object(sync, "STATE", self.a.home / "chat-sync"),
              patch.object(sync.workspace_paths, "pieces", change_during_read)):
            with self.assertRaises(sync.Busy):
                sync.snapshot(self.vault, self.tid, (self.local, True), None, None)
        self.assertFalse((self.vault / "blocks").exists())


if __name__ == "__main__":
    unittest.main()
