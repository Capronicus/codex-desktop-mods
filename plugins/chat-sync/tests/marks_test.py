"""Exchange bookmarks, saved views and text animations between two disposable profiles."""

from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bookmarks
import marks
import sync


def bookmark(label):
    ident = str(uuid4())
    address = json.dumps(["local", str(uuid4()), str(uuid4()), "assistant", "item"])
    return {"id": ident, "anchor": json.dumps([address, 0, 0, ""]), "thread": str(uuid4()), "turn": "", "item": "",
            "cloud": False, "created": 1700000000000, "label": label}


class SharedMarks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.devices = {name: str(uuid4()) for name in ("home", "work")}
        self.account = marks.hashlib.sha256(b"").hexdigest()

    @contextmanager
    def on(self, name):
        home = self.root / name
        with patch.multiple(sync, HOME=home, STATE=home / "chat-sync", CONFIG=home / "chat-sync/config.json"):
            yield {"device": self.devices[name], "folder": str(self.root)}, home / "chat-sync"

    def window(self, state, views, words):
        sync.atomic(state / "renderer-marks.json", sync.encode({"account": self.account, "views": views, "words": words}))

    def test_marks_travel_and_deletions_stay_deleted(self):
        mark = bookmark("Design note")
        view_id = str(uuid4())
        view = {"name": "This week", "project": "", "origin": "", "age": "7", "waiting": ""}
        word = json.dumps([json.dumps(["cloud", "message"]), 3, 9, "1a2b"])
        with self.on("home") as (cfg, state):
            sync.atomic(bookmarks.location(), sync.encode({mark["id"]: mark}))
            self.window(state, {view_id: view}, [word])
            sent = marks.exchange(cfg, self.vault)
            self.assertEqual((sent["bookmarks"], sent["views"], sent["animations"], sent["waiting_for_desktop"]), (1, 1, 1, 0))

        with self.on("work") as (cfg, state):
            # Without a window snapshot only bookmarks are applied. Nothing is treated as deleted.
            marks.exchange(cfg, self.vault)
            self.assertEqual(sync.read_json(bookmarks.location()), {mark["id"]: mark})
            self.assertFalse((state / "renderer-marks-apply.json").exists())
            self.window(state, {}, [])
            received = marks.exchange(cfg, self.vault)
            self.assertEqual(received["waiting_for_desktop"], 2)
            todo = sync.read_json(state / "renderer-marks-apply.json")
            self.assertEqual((todo["set"], todo["remove"]), ({"view/" + view_id: view, "word/" + word: 1}, []))
            # The same instructions are offered until the window reports the result.
            marks.exchange(cfg, self.vault)
            self.assertEqual(sync.read_json(state / "renderer-marks-apply.json")["id"], todo["id"])
            self.window(state, {view_id: view}, [word])
            self.assertEqual(marks.exchange(cfg, self.vault)["waiting_for_desktop"], 0)
            self.assertFalse((state / "renderer-marks-apply.json").exists())
            rows = sync.read_json(self.vault / "marks-v1" / self.account / (cfg["device"] + ".json"))["rows"]
            self.assertTrue(all(row[1] == self.devices["home"] for row in rows.values()))

            # This desktop renames the bookmark and removes the animation.
            sync.atomic(bookmarks.location(), sync.encode({mark["id"]: {**mark, "label": "Renamed"}}))
            self.window(state, {view_id: view}, [])
            marks.exchange(cfg, self.vault)

        with self.on("home") as (cfg, state):
            result = marks.exchange(cfg, self.vault)
            self.assertEqual(sync.read_json(bookmarks.location())[mark["id"]]["label"], "Renamed")
            self.assertEqual(sync.read_json(state / "renderer-marks-apply.json")["remove"], ["word/" + word])
            # Its window still shows the animation. That must not bring the deleted row back.
            again = marks.exchange(cfg, self.vault)
            self.assertEqual((result["animations"], again["animations"], again["waiting_for_desktop"]), (0, 0, 1))
            self.window(state, {view_id: view}, [])
            self.assertEqual(marks.exchange(cfg, self.vault)["waiting_for_desktop"], 0)

    def test_unacceptable_rows_are_ignored(self):
        good = bookmark("Kept")
        peer = str(uuid4())
        rows = {"bookmark/" + good["id"]: [1, peer, good],
                "bookmark/" + str(uuid4()): [1, peer, {"id": "other", "label": "x"}],
                "view/" + str(uuid4()): [1, peer, {"name": "x" * 200}],
                "word/not-json": [1, peer, 1], "script/" + str(uuid4()): [1, peer, {"run": True}]}
        sync.atomic(self.vault / "marks-v1" / self.account / (peer + ".json"), sync.encode({"v": 1, "rows": rows}))
        sync.atomic(self.vault / "marks-v1" / self.account / (str(uuid4()) + ".json"), b'{"v":1,"rows":[')
        with self.on("home") as (cfg, state):
            local = bookmark("Mine")
            unusual = {"legacy": True}
            sync.atomic(bookmarks.location(), sync.encode({local["id"]: local, "old-entry": unusual}))
            result = marks.exchange(cfg, self.vault)
            self.assertEqual((result["bookmarks"], result["views"], result["animations"], len(result["deferred_devices"])), (2, 0, 0, 1))
            self.assertEqual(sync.read_json(bookmarks.location()), {local["id"]: local, good["id"]: good, "old-entry": unusual})


if __name__ == "__main__":
    unittest.main()
