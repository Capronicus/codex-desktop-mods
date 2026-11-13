"""Compact a synthetic shared folder and retire a desktop, using two disposable profiles."""

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from mod_controls import Client, SERVER

sys.path.insert(0, str(SERVER.parent))
import maintenance
import sync


def append(path, text):
    with path.open("ab") as stream:
        stream.write(b'{"type":"event_msg","payload":{"type":"task_complete","text":"' + text.encode() + b'"}}\n')


def age(vault, days=30):
    past = time.time() - days * 86400
    for path in vault.rglob("*"):
        if path.is_file():
            os.utime(path, (past, past))


class FolderMaintenance(unittest.TestCase):
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

    def files(self, area):
        return {p.name for p in (self.vault / area).glob("*/*") if p.is_file() and not p.name.startswith(".")}

    def test_compaction_keeps_every_current_revision(self):
        # Three revisions of one chat. The second desktop stays one revision behind.
        append(self.local, os.urandom(sync.BLOCK).hex())
        self.a.tool("sync_chats")
        append(self.local, "second")
        self.a.tool("sync_chats")
        self.assertEqual(self.b.tool("sync_chats")["imported"], 1)
        self.b.tool("sync_chats")  # Publishes the second desktop's own head.
        append(self.local, "third")
        self.a.tool("sync_chats")
        versions, blocks = self.files("versions"), self.files("blocks")
        stale = self.vault / "blocks" / "ab" / ".writing-interrupted"
        stale.parent.mkdir(exist_ok=True)
        stale.write_bytes(b"partial")

        # Nothing is old enough yet, and a preview never deletes.
        preview = self.a.tool("compact_sync_folder")
        self.assertEqual((preview["revisions"], preview["blocks"], preview["applied"]), (0, 0, False))
        self.assertGreater(preview["kept_recent"], 0)
        age(self.vault)
        preview = self.a.tool("compact_sync_folder")
        # Only the first revision's tail block is unused. The lagging head keeps its own.
        self.assertEqual((preview["blocks"], preview["temporary_files"], preview["waiting_chats"]), (1, 1, 0))
        self.assertGreaterEqual(preview["revisions"], 1)
        self.assertEqual((self.files("versions"), self.files("blocks")), (versions, blocks))

        done = self.a.tool("compact_sync_folder", apply=True)
        self.assertEqual((done["revisions"], done["blocks"], done["failed"], done["applied"]),
                         (preview["revisions"], 1, 0, True))
        self.assertEqual(len(self.files("versions")), len(versions) - preview["revisions"])
        self.assertEqual(len(self.files("blocks")), len(blocks) - 1)
        self.assertFalse(stale.exists())
        # Both desktops still verify, and the lagging one can catch up.
        for client in (self.a, self.b):
            report = client.tool("check_sync_folder")
            self.assertEqual((report["missing"], report["damaged"], report["incomplete_chats"]), (0, 0, 0))
        self.assertEqual(self.b.tool("sync_chats")["updated"], 1)
        self.assertEqual(self.a.tool("compact_sync_folder")["revisions"], 0)

    def test_incomplete_head_protects_blocks(self):
        self.a.tool("sync_chats")
        append(self.local, "second")
        self.a.tool("sync_chats")
        age(self.vault)
        head = next((self.vault / "heads").glob("*/" + self.tid + ".json"))
        saved = head.read_bytes()
        head.write_bytes(b'"')  # Still arriving from the folder client.
        waiting = self.a.tool("compact_sync_folder", apply=True)
        self.assertEqual((waiting["revisions"], waiting["blocks"], waiting["waiting_chats"]), (0, 0, 1))
        head.write_bytes(saved)
        age(self.vault)
        self.assertEqual(self.a.tool("compact_sync_folder", apply=True)["revisions"], 1)
        self.assertEqual(self.a.tool("check_sync_folder")["missing"], 0)

    def test_retiring_a_desktop(self):
        self.a.tool("sync_chats")
        self.assertEqual(self.b.tool("sync_chats")["imported"], 1)
        shared = self.b.chat()   # Only on the desktop being retired.
        self.b.tool("sync_chats")
        own = sync.read_json(self.a.home / "chat-sync" / "config.json")["device"]
        other = sync.read_json(self.b.home / "chat-sync" / "config.json")["device"]
        listed = self.a.tool("list_sync_devices")
        self.assertEqual({d: row["chats"] for d, row in listed.items()}, {own: 1, other: 2})
        self.assertTrue(listed[own]["this_desktop"])
        with self.assertRaisesRegex(ValueError, "Disconnect"):
            self.a.tool("retire_sync_device", device=own)

        plan = self.a.tool("retire_sync_device", device=other)
        self.assertEqual((plan["chats"], plan["only_copy"], plan["unmatched_history"], plan["retired"]), (2, 1, 0, False))
        self.assertEqual(plan["affected_chats"], [shared])
        self.assertTrue((self.vault / "heads" / other).is_dir())
        with self.assertRaisesRegex(ValueError, "Preview this desktop again"):
            self.a.tool("retire_sync_device", device=other, review="0" * 64)
        # The first desktop takes its own copy before the other one is removed.
        self.assertEqual(self.a.tool("sync_chats")["imported"], 1)
        plan = self.a.tool("retire_sync_device", device=other)
        self.assertEqual((plan["only_copy"], plan["unmatched_history"]), (0, 0))
        done = self.a.tool("retire_sync_device", device=other, review=plan["review"])
        self.assertTrue(done["retired"])
        self.assertFalse((self.vault / "heads" / other).exists())
        self.assertFalse((self.vault / "receipts-v1" / (other + ".json")).exists())
        self.assertEqual(set(self.a.tool("list_sync_devices")), {own})
        # This desktop publishes the chat it imported on its next pass.
        self.a.tool("sync_chats")
        report = self.a.tool("check_sync_folder")
        self.assertEqual((report["chats"], report["missing"]), (2, 0))
        with self.assertRaisesRegex(ValueError, "no files"):
            self.a.tool("retire_sync_device", device=other)


if __name__ == "__main__":
    unittest.main()
