"""Encrypted chat histories travel between desktops that hold the folder key, and nobody else can read them."""

from contextlib import ExitStack
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

from mod_controls import Client, SERVER

sys.path.insert(0, str(SERVER.parent))
import sync
import vault_crypto

SECRET = "TOP-SECRET-MARKER-7f3a"


def append(path, text):
    with path.open("ab") as stream:
        stream.write(b'{"type":"event_msg","payload":{"type":"task_complete","text":"' + text.encode() + b'"}}\n')


class FolderEncryption(unittest.TestCase):
    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.root = Path(self.resources.enter_context(tempfile.TemporaryDirectory()))
        self.folder = self.root / "shared"
        self.folder.mkdir()
        self.vault = self.folder / "codex-chat-sync-v1"
        self.a, self.b = self.client("Home"), self.client("Work")

    def client(self, name):
        client = Client(self.root, name)
        self.resources.callback(client.close)
        client.tool("configure_chat_sync", folder=str(self.folder), automatic=False)
        return client

    def share_key(self, source, target):
        shutil.copy(source.home / "chat-sync" / "vault.key", target.home / "chat-sync" / "vault.key")

    def folder_bytes(self):
        return b"".join(p.read_bytes() for p in self.vault.rglob("*") if p.is_file())

    def local(self, client, tid):
        return next((client.home / "archived_sessions").glob("*" + tid + ".jsonl"))

    def test_round_trip_and_a_desktop_without_the_key(self):
        result = self.a.tool("set_folder_encryption", enabled=True)
        self.assertTrue(result["key_created"])
        key = Path(result["key_file"])
        # The profile path can be spelled with a short Windows name here and its long form there.
        self.assertTrue(key.samefile(self.a.home / "chat-sync" / "vault.key"))
        if os.name != "nt":
            self.assertEqual(key.stat().st_mode & 0o077, 0)
        self.share_key(self.a, self.b)
        self.assertFalse(self.b.tool("set_folder_encryption", enabled=True)["key_created"])

        tid = self.a.chat()
        append(self.local(self.a, tid), SECRET)
        self.a.tool("sync_chats")
        stored = self.folder_bytes()
        # A plain revision is readable JSON that names the history file. A sealed one is not.
        self.assertNotIn(self.local(self.a, tid).name.encode(), stored)
        self.assertNotIn(b'"chunks"', stored)
        self.assertTrue(all(p.read_bytes().startswith(vault_crypto.MAGIC) for p in self.vault.glob("versions/*/*.json")))
        blocks = list(self.vault.glob("blocks/*/*"))
        self.assertTrue(blocks and all(p.suffix == ".enc" and p.read_bytes().startswith(vault_crypto.MAGIC) for p in blocks))
        self.assertNotIn(b"\x1f\x8b\x08", b"".join(p.read_bytes()[:40] for p in blocks))
        self.assertNotIn(key.read_bytes().strip(), stored)

        self.assertEqual(self.b.tool("sync_chats")["imported"], 1)
        self.assertIn(SECRET, self.local(self.b, tid).read_text())
        self.b.tool("sync_chats")
        for client in (self.a, self.b):
            report = client.tool("check_sync_folder")
            self.assertEqual((report["chats"], report["missing"], report["damaged"], report["unreadable"]), (1, 0, 0, 0))

        # Further turns extend the encrypted history, and a lost block is restored in the same form.
        earlier = set(self.vault.glob("blocks/*/*.enc"))
        append(self.local(self.a, tid), "second turn")
        self.a.tool("sync_chats")
        self.assertEqual(self.b.tool("sync_chats")["updated"], 1)
        self.b.tool("sync_chats")
        current, = set(self.vault.glob("blocks/*/*.enc")) - earlier
        current.unlink()
        self.assertEqual(self.b.tool("check_sync_folder")["missing"], 1)
        self.assertEqual(self.b.tool("repair_sync_folder")["restored"], 1)
        self.assertEqual(self.a.tool("check_sync_folder")["missing"], 0)

        # A desktop without the key receives nothing, damages nothing and says why.
        c = self.client("Guest")
        self.assertEqual(c.tool("sync_chats")["imported"], 0)
        reason, = c.tool("chat_sync_status")["last_run"]["skipped"].values()
        self.assertIn("encrypted", reason)
        self.assertFalse((c.home / "archived_sessions").exists())
        self.assertEqual(c.tool("preview_incoming_chats")["counts"]["waiting"], 1)
        blocks = sorted(p.name for p in self.vault.glob("blocks/*/*"))
        self.assertEqual(c.tool("compact_sync_folder", apply=True)["blocks"], 0)
        self.assertEqual(c.tool("repair_sync_folder")["restored"], 0)
        self.assertEqual(blocks, sorted(p.name for p in self.vault.glob("blocks/*/*")))

        # A different key is reported as such and cannot read the chat either.
        c.tool("set_folder_encryption", enabled=True)
        c.tool("sync_chats")
        reason, = c.tool("chat_sync_status")["last_run"]["skipped"].values()
        self.assertIn("different folder key", reason)

    def test_switching_on_keeps_existing_chats_aligned(self):
        tid = self.a.chat()
        append(self.local(self.a, tid), SECRET)
        self.a.tool("sync_chats")
        self.assertEqual(self.b.tool("sync_chats")["imported"], 1)
        self.b.tool("sync_chats")
        self.assertIn(self.local(self.a, tid).name.encode(), self.folder_bytes())
        self.assertTrue(list(self.vault.glob("blocks/*/*.gz")))

        self.a.tool("set_folder_encryption", enabled=True)
        self.share_key(self.a, self.b)
        self.b.tool("set_folder_encryption", enabled=True)
        for client in (self.a, self.b, self.a, self.b):
            stats = client.tool("sync_chats")
            self.assertEqual((stats["imported"], stats["updated"], stats["conflicts"], stats["skipped"]), (0, 0, {}, {}))
        # Both heads now point to encrypted revisions. The plain ones are left for compaction.
        heads = {sync.read_json(p) for p in self.vault.glob("heads/*/*.json")}
        for revision in heads:
            self.assertTrue((self.vault / "versions" / tid / (revision + ".json")).read_bytes().startswith(vault_crypto.MAGIC))
        past = time.time() - 30 * 86400
        for path in self.vault.rglob("*"):
            if path.is_file():
                os.utime(path, (past, past))
        cleaned = self.a.tool("compact_sync_folder", apply=True)
        self.assertGreaterEqual(cleaned["revisions"], 1)
        self.assertGreaterEqual(cleaned["blocks"], 1)
        # Nothing readable is left: no plain blocks and no plain revision naming the file.
        self.assertFalse(list(self.vault.glob("blocks/*/*.gz")))
        self.assertNotIn(self.local(self.a, tid).name.encode(), self.folder_bytes())
        for client in (self.a, self.b):
            report = client.tool("check_sync_folder")
            self.assertEqual((report["missing"], report["damaged"], report["unreadable"]), (0, 0, 0))
        self.assertIn(SECRET, self.local(self.b, tid).read_text())

    def test_sealed_data_is_authenticated(self):
        key = os.urandom(32)
        sealed = vault_crypto.seal(key, b"block:1", b"history")
        self.assertEqual(vault_crypto.unseal(key, b"block:1", sealed), b"history")
        self.assertNotIn(b"history", sealed)
        for bad in (sealed[:-1] + bytes([sealed[-1] ^ 1]), sealed[:10] + b"\0" + sealed[11:], sealed[:20]):
            with self.assertRaises(ValueError):
                vault_crypto.unseal(key, b"block:1", bad)
        with self.assertRaises(ValueError):
            vault_crypto.unseal(key, b"block:2", sealed)
        with self.assertRaises(vault_crypto.Locked):
            vault_crypto.unseal(None, b"block:1", sealed)
        self.assertEqual(vault_crypto.unseal(key, b"n", vault_crypto.seal(key, b"n", b"")), b"")
        # Equal blocks share a name under one key and differ under another.
        self.assertEqual(vault_crypto.block_name(key, b"x"), vault_crypto.block_name(key, b"x"))
        self.assertNotEqual(vault_crypto.block_name(key, b"x"), vault_crypto.block_name(os.urandom(32), b"x"))


if __name__ == "__main__":
    unittest.main()
