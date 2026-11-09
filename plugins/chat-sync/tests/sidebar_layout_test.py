"""Round-trip real desktop layout records separately from native app-server fixtures."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_layout
import sidebar
import sync


def ident():
    return str(uuid4())


class DesktopSidebar(unittest.TestCase):
    def test_helper_waits_for_desktop_exit(self):
        if desktop_layout.desktop_running():
            self.skipTest("This OS user already has a desktop running")
        with TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "profile"
            plugin = home / "chat-sync"
            plugin.mkdir(parents=True)
            device, project, native = ident(), ident(), ident()
            state_path = home / ".codex-global-state.json"
            sync.atomic(state_path, sync.encode({"local-projects": {}}))
            sync.atomic(plugin / "config.json", sync.encode({"device": device, "sidebar": True}))
            sync.atomic(plugin / ("sidebar-" + device + ".json"), sync.encode({"seen": {}}))
            if os.name == "nt":
                binary = root / "ChatGPT.exe"
                shutil.copy2(Path(os.environ["SystemRoot"]) / "System32/ping.exe", binary)
                args = [str(binary), "-t", "127.0.0.1"]
            else:
                args = [
                    sys.executable,
                    "-c",
                    "import ctypes,time; ctypes.CDLL(None).prctl(15,b'ChatGPT',0,0,0); time.sleep(60)",
                ]
            with subprocess.Popen(args, stdout=subprocess.DEVNULL) as owner:
                try:
                    for _ in range(40):
                        if desktop_layout.desktop_running():
                            break
                        time.sleep(0.05)
                    self.assertTrue(desktop_layout.desktop_running())
                    self.assertTrue(
                        desktop_layout.queue(
                            home,
                            device,
                            desktop_layout.Layout(home),
                            {"project/" + project: [None, "On exit", native]},
                        )
                    )
                    self.assertEqual(sync.read_json(state_path)["local-projects"], {})
                finally:
                    owner.terminate()
            for _ in range(100):
                if not (plugin / desktop_layout.QUEUE).exists():
                    break
                time.sleep(0.1)
            self.assertFalse((plugin / desktop_layout.QUEUE).exists())
            self.assertEqual(
                sync.read_json(state_path)["local-projects"][project]["name"], "On exit"
            )

    def test_offline_projection_and_concurrent_edits(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            home, vault = root / "profile", root / "vault"
            plugin = home / "chat-sync"
            plugin.mkdir(parents=True)
            dev, peer, account, project, section, tid = (ident() for _ in range(6))
            native_project, native_section = ident(), ident()
            sync.atomic(home / "auth.json", sync.encode({"tokens": {"account_id": account}}))
            sync.atomic(plugin / "config.json", sync.encode({"sidebar": True, "device": dev}))
            statefile = home / ".codex-global-state.json"
            sync.atomic(
                statefile,
                sync.encode(
                    {
                        "electron-persisted-atom-state": {
                            desktop_layout.SECTIONS: {"other-account": {"untouched": True}}
                        },
                        "unrelated": 123,
                    }
                ),
            )
            with sqlite3.connect(home / "state_5.sqlite") as db:
                db.executescript("""
                    CREATE TABLE projects(id TEXT, name TEXT, metadata TEXT);
                    CREATE TABLE thread_sections(id TEXT, name TEXT, appearance TEXT);
                    CREATE TABLE threads(id TEXT, project_id TEXT, thread_section_id TEXT);
                """)
                db.execute(
                    "INSERT INTO projects VALUES(?,?,?)",
                    (native_project, "Work", json.dumps({"codex.localProjectId": project})),
                )
                db.execute(
                    "INSERT INTO thread_sections VALUES(?,?,NULL)", (native_section, "Later")
                )
                db.execute(
                    "INSERT INTO thread_sections VALUES(?,?,NULL)",
                    (desktop_layout.PINNED, "Pinned"),
                )
                db.execute(
                    "INSERT INTO threads VALUES(?,?,?)", (tid, native_project, native_section)
                )
            sync.atomic(
                plugin / "sidebar-section-ids.json",
                sync.encode({"aliases": {section: native_section}, "pending": {}}),
            )
            head = vault / "sidebar-v1" / (peer + ".json")
            values = {
                "project/" + project: "Work",
                "section/" + section: "Later",
                "chat-project/" + tid: project,
                "chat-section/" + tid: section,
            }
            rows = {k: [0, peer, v] for k, v in values.items()}
            sync.atomic(head, sync.encode({"v": 1, "rows": rows}))

            def exchange():
                return sidebar.exchange(home, vault, dev, {tid}, sync.atomic, sync.encode)

            with (
                patch.object(desktop_layout, "desktop_running", return_value=True),
                patch.object(desktop_layout.subprocess, "Popen"),
            ):
                self.assertTrue(exchange()["desktop_refresh_on_quit"])
                self.assertNotIn("local-projects", sync.read_json(statefile))
            with patch.object(desktop_layout, "desktop_running", return_value=False):
                self.assertTrue(desktop_layout.apply_pending(home))
                layout = desktop_layout.Layout(home)
                self.assertEqual(layout.observed({tid}), values)
                self.assertEqual(layout.project_ids[project], native_project)
                self.assertEqual(layout.section_ids[section], native_section)
                self.assertEqual(layout.projects[project]["rootPaths"], [])
                self.assertEqual(layout.data["unrelated"], 123)
                self.assertEqual(
                    layout.data["electron-persisted-atom-state"][desktop_layout.SECTIONS][
                        "other-account"
                    ],
                    {"untouched": True},
                )
                self.assertFalse(exchange()["desktop_refresh_on_quit"])
                self.assertNotIn(desktop_layout.PINNED, (head.parent / (dev + ".json")).read_text())
                # An edit made between sync and quitting must survive the queued rename.
                key = "project/" + project
                change = {key: ["Work", "From peer", native_project]}
                sync.atomic(
                    plugin / desktop_layout.QUEUE,
                    sync.encode({"device": dev, "account": account, "changes": change}),
                )
                layout.projects[project]["name"] = "Edited here"
                sync.atomic(statefile, sync.encode(layout.data))
                self.assertTrue(desktop_layout.apply_pending(home))
                self.assertEqual(
                    desktop_layout.Layout(home).projects[project]["name"], "Edited here"
                )
                # Turning sync off while the app is open cancels the queued layout.
                sync.atomic(
                    plugin / desktop_layout.QUEUE,
                    sync.encode({"device": dev, "account": account, "changes": change}),
                )
                sync.atomic(plugin / "config.json", sync.encode({"sidebar": False, "device": dev}))
                self.assertTrue(desktop_layout.apply_pending(home))
                self.assertFalse((plugin / desktop_layout.QUEUE).exists())


if __name__ == "__main__":
    unittest.main()
