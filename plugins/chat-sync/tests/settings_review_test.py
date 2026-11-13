"""The first settings a desktop receives are shown for review before they are applied."""

from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings
import sync


class SettingsReview(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "profile"
        self.vault = Path(temp.name) / "vault"
        (self.home / "chat-sync").mkdir(parents=True)
        (self.vault / "settings-v1").mkdir(parents=True)
        self.device, self.peer = str(uuid4()), str(uuid4())
        self.desktop = {"appearanceTheme": "light", "sansFontSize": 13}
        self.writes = []
        native = patch.object(settings, "native_api", self.native_api)
        native.start()
        self.addCleanup(native.stop)

    @contextmanager
    def native_api(self, home):
        def call(method, params):
            if method == "config/read":
                return {"layers": [{"name": {"type": "user", "file": str(home / "config.toml")}, "version": "v1",
                                    "config": {"desktop": dict(self.desktop)}}]}
            self.assertEqual(method, "config/batchWrite")
            self.writes.append(params["edits"])
            for edit in params["edits"]:
                self.desktop[edit["keyPath"].removeprefix("desktop.")] = edit["value"]
        yield call

    def peer_rows(self, **rows):
        sync.atomic(self.vault / "settings-v1" / (self.peer + ".json"), sync.encode({"version": 1, "rows": rows}))

    def exchange(self, *args):
        return settings.exchange(self.home, self.vault, self.device, *args)

    def test_first_receipt_waits_for_review(self):
        self.peer_rows(appearanceTheme=[3, self.peer, "dark"])
        marker = self.home / "chat-sync" / "settings-preview.json"
        own = self.vault / "settings-v1" / (self.device + ".json")
        held = self.exchange()
        self.assertEqual((held["preview_required"], held["changes"]), (True, 1))
        self.assertTrue(marker.is_file())
        self.assertEqual((self.writes, own.exists(), self.desktop["appearanceTheme"]), ([], False, "light"))

        preview = self.exchange("preview")
        self.assertEqual(preview["changes"], [{"key": "appearanceTheme", "before": "light", "after": "dark", "device": self.peer}])
        self.assertEqual((preview["id"], self.writes), (held["id"], []))
        with self.assertRaisesRegex(ValueError, "fresh preview"):
            self.exchange("apply", "0" * 64)
        done = self.exchange("apply", preview["id"])
        self.assertEqual((done["applied"], self.desktop["appearanceTheme"]), (1, "dark"))
        self.assertFalse(marker.exists())
        # This desktop's own font size is now published beside the received theme.
        self.assertEqual(sync.read_json(own)["rows"]["sansFontSize"][1:], [self.device, 13])

        # Later changes from a desktop already reviewed apply without another prompt.
        self.peer_rows(appearanceTheme=[4, self.peer, "system"])
        self.assertEqual(self.exchange()["applied"], 1)
        self.assertEqual(self.desktop["appearanceTheme"], "system")

    def test_no_review_without_differences(self):
        self.peer_rows(appearanceTheme=[3, self.peer, "light"])
        self.assertEqual(self.exchange()["applied"], 0)
        self.assertFalse((self.home / "chat-sync" / "settings-preview.json").exists())
        self.assertEqual(self.exchange("preview")["changes"], [])


if __name__ == "__main__":
    unittest.main()
