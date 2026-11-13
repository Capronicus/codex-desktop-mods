"""Preview what a sync would receive, using two disposable profiles."""

from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest

from mod_controls import Client, SERVER

sys.path.insert(0, str(SERVER.parent))


def append(path, text):
    with path.open("ab") as stream:
        stream.write(b'{"type":"event_msg","payload":{"type":"task_complete","text":"' + text.encode() + b'"}}\n')


class IncomingPreview(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        root = Path(self.resources.enter_context(tempfile.TemporaryDirectory()))
        folder = root / "shared"
        folder.mkdir()
        self.a, self.b = Client(root, "Home"), Client(root, "Work")
        for client in (self.a, self.b):
            self.resources.callback(client.close)
            client.tool("configure_chat_sync", folder=str(folder), automatic=False)

    def status(self, client, tid):
        row, = client.tool("preview_incoming_chats", search=tid)["chats"]
        return row["group"], row["reason"]

    def test_groups_follow_a_chat_through_sync(self):
        tid = self.a.chat()
        source = next((self.a.home / "archived_sessions").glob("*.jsonl"))
        self.a.tool("sync_chats")
        # The sender has nothing to receive. The other desktop sees one new chat.
        self.assertEqual(self.a.tool("preview_incoming_chats")["matched"], 0)
        before = sorted(p.name for p in (self.b.home / "chat-sync").iterdir())
        preview = self.b.tool("preview_incoming_chats")
        self.assertEqual(preview["counts"]["new"], 1)
        self.assertEqual((preview["chats"][0]["id"], preview["chats"][0]["devices"]), (tid, [self.a.tool("mod_controls_status")["device"]]))
        self.assertGreater(preview["chats"][0]["size"], 0)
        self.assertEqual(before, sorted(p.name for p in (self.b.home / "chat-sync").iterdir()))
        self.assertFalse((self.b.home / "archived_sessions").exists())

        self.assertEqual(self.b.tool("sync_chats")["imported"], 1)
        self.b.tool("sync_chats")
        self.assertEqual(self.status(self.b, tid), ("current", "current"))
        append(source, "more")
        self.a.tool("sync_chats")
        self.assertEqual(self.status(self.b, tid), ("update", "longer_history"))
        self.assertEqual(self.b.tool("preview_incoming_chats", group="update")["matched"], 1)

        # An unsent local edit is exported first, so the preview waits for it.
        copy = next((self.b.home / "archived_sessions").glob("*.jsonl"))
        append(copy, "elsewhere")
        self.assertEqual(self.status(self.b, tid), ("waiting", "local_changes"))
        self.b.tool("sync_chats")
        self.assertEqual(self.status(self.b, tid), ("conflict", "diverged"))
        self.assertEqual(self.status(self.a, tid), ("conflict", "diverged"))

        self.b.tool("set_local_only", kind="chat", ident=tid, enabled=True)
        self.assertEqual(self.status(self.b, tid), ("blocked", "local_only"))

    def test_removed_and_incomplete_chats(self):
        tid = self.a.chat()
        self.a.tool("sync_chats")
        self.b.tool("sync_chats")
        next((self.b.home / "archived_sessions").glob("*.jsonl")).unlink()
        self.assertEqual(self.status(self.b, tid), ("blocked", "removed_here"))
        other = self.a.chat()
        self.a.tool("sync_chats")
        head = next((self.b.home.parent / "shared" / "codex-chat-sync-v1" / "heads").glob("*/" + other + ".json"))
        head.write_bytes(b'"')
        self.assertEqual(self.status(self.b, other), ("waiting", "incomplete_snapshot"))


if __name__ == "__main__":
    unittest.main()
