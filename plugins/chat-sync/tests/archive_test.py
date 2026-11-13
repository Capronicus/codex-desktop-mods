"""Archiving a chat on one desktop archives it on the other when both opt in."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_names
import sync

DATED = Path("sessions/2026/11/01")


class ArchivedChats(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "shared" / "codex-chat-sync-v1").mkdir(parents=True)
        self.devices = {name: str(uuid4()) for name in ("home", "work")}
        self.moves = []
        for name, device in self.devices.items():
            home = self.root / name
            (home / "thread-writer-locks").mkdir(parents=True)
            (home / "thread-writer-locks/.coordination.lock").touch()
            with closing(sqlite3.connect(home / "state_5.sqlite")) as db, db:
                db.execute("create table threads(id, rollout_path, name, archived, source, cwd)")
            sync.atomic(home / "chat-sync/config.json", sync.encode(
                {"folder": str(self.root / "shared"), "device": device, "automatic": False, "archive": True}))
        native = patch.object(native_names, "native_api", self.native_api)
        native.start()
        self.addCleanup(native.stop)

    @contextmanager
    def native_api(self, home):
        """Stand in for Codex: archiving moves the history between its two folders."""
        def call(method, params):
            if method in ("thread/archive", "thread/unarchive"):
                source = self.file(home, params["threadId"])
                target = home / ("archived_sessions" if method == "thread/archive" else DATED) / source.name
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
                self.moves.append((home.name, method))
            return {}
        yield call

    def file(self, home, tid):
        return next(p for p in home.rglob("rollout-*" + tid + ".jsonl"))

    def run_sync(self, name):
        home = self.root / name
        with patch.multiple(sync, HOME=home, STATE=home / "chat-sync", CONFIG=home / "chat-sync/config.json"):
            return sync.run()

    def filed(self, name, tid):
        home = self.root / name
        state = sync.read_json(home / "chat-sync" / ("state-" + self.devices[name] + ".json"))[tid]
        return (self.file(home, tid).parent.name == "archived_sessions", state["manifest"].get("archive_clock"),
                state.get("pending_archive"))

    def test_archive_and_restore_follow_the_latest_change(self):
        tid = str(uuid4())
        chat = self.root / "home" / DATED / ("rollout-2026-11-01T00-00-00-" + tid + ".jsonl")
        chat.parent.mkdir(parents=True)
        chat.write_text(json.dumps({"type": "session_meta", "payload": {"id": tid, "source": "cli"}}) + "\n"
                        + json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}) + "\n")
        self.run_sync("home")
        self.assertEqual(self.run_sync("work")["imported"], 1)
        self.run_sync("work")
        self.assertEqual(self.filed("work", tid), (False, None, None))

        # Archived on Home.
        archive = self.root / "home" / "archived_sessions"
        archive.mkdir()
        chat.rename(archive / chat.name)
        self.run_sync("home")
        first = [1, self.devices["home"]]
        self.assertEqual(self.filed("home", tid), (True, first, None))
        self.assertEqual(self.run_sync("work")["archive_moves"], 1)
        self.assertEqual(self.moves, [("work", "thread/archive")])
        # Work adopts Home's revision instead of recording an edit of its own.
        self.run_sync("work")
        self.assertEqual(self.filed("work", tid), (True, first, None))
        self.run_sync("home")
        self.run_sync("work")
        self.assertEqual((self.filed("home", tid), self.filed("work", tid), len(self.moves)),
                         ((True, first, None), (True, first, None), 1))

        # Restored on Work.
        restored = self.root / "work" / DATED
        restored.mkdir(parents=True, exist_ok=True)
        self.file(self.root / "work", tid).rename(restored / chat.name)
        self.run_sync("work")
        second = [2, self.devices["work"]]
        self.run_sync("home")
        self.run_sync("home")
        self.assertEqual((self.filed("home", tid), self.moves[-1]), ((False, second, None), ("home", "thread/unarchive")))

        # A desktop that has not opted in keeps its own filing.
        config = self.root / "work" / "chat-sync/config.json"
        sync.atomic(config, sync.encode({**sync.read_json(config), "archive": False}))
        self.file(self.root / "home", tid).rename(archive / chat.name)
        self.run_sync("home")
        self.run_sync("work")
        self.run_sync("work")
        self.assertEqual((self.filed("work", tid)[0], len(self.moves)), (False, 2))


if __name__ == "__main__":
    unittest.main()
