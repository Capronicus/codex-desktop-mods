"""Exercise the shipped activation clients against a disposable local collector."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import activation
from counter.serve import create_app
from counter.configure import configure

PACKAGES = [ROOT, ROOT / "plugins/chat-sync", ROOT / "plugins/chat-organizer"]


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class ActivationTests(unittest.TestCase):
    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        temp = Path(resources.enter_context(TemporaryDirectory()))
        self.home = temp / "profile"
        self.database = temp / "counts.db"
        self.requests = []
        self.fail_response = False
        app = create_app(self.database)

        def record(env, start_response):
            self.requests.append(env["REQUEST_METHOD"])
            if self.fail_response:
                # Commit a real activation, then simulate loss of its acknowledgement.
                app(env, lambda *args: None)
                start_response("503 Unavailable", [("Content-Length", "0")])
                return [b""]
            return app(env, start_response)

        self.http = make_server("127.0.0.1", 0, record, handler_class=Quiet)
        self.origin = "http://127.0.0.1:" + str(self.http.server_port)
        thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02})
        thread.start()
        resources.callback(self.http.server_close)
        resources.callback(thread.join)
        resources.callback(self.http.shutdown)
        resources.enter_context(patch.dict(os.environ, CODEX_HOME=str(self.home),
                                            CAPRONICUS_ACTIVATION_URL=self.origin))

    def cli(self, package, *args):
        out = subprocess.check_output([sys.executable, str(package / "activation.py"), *args])
        return json.loads(out)

    def test_packages_and_voluntary_choices(self):
        for package in PACKAGES:
            name = json.loads((package / "plugin.json").read_bytes())["name"]
            self.assertEqual((package / "activation.py").read_bytes(), (ROOT / "activation.py").read_bytes())
            self.assertEqual(self.cli(package, "status")["state"], "available")
            self.assertEqual(self.cli(package, "offer")["destination"], self.origin)
            self.assertIsNone(self.cli(package, "offer"))
            if name == "chat-organizer":
                continue  # This profile chose Skip.
            self.assertEqual(self.cli(package, "count", self.origin)["state"], "counted")
            self.cli(package, "count", self.origin)
        self.assertEqual(self.requests, ["POST", "POST"])
        with urlopen(self.origin + "/v1/counts") as response:
            counts = json.load(response)
        self.assertEqual(counts, {"codex-desktop-mods": 1, "chat-sync": 1, "chat-organizer": 0})
        with urlopen(self.origin + "/badges/chat-sync.svg") as response:
            self.assertEqual(response.headers.get_content_type(), "image/svg+xml")
            self.assertIn(b"chat-sync: 1 reported activations", response.read())
        with closing(sqlite3.connect(activation.profile() / "state.sqlite3")) as local:
            receipts = local.execute("SELECT plugin,receipt FROM activations WHERE receipt IS NOT NULL").fetchall()
        self.assertEqual(len({r[1] for r in receipts}), 2)
        with closing(sqlite3.connect(self.database)) as db:
            stored = set(db.execute("SELECT plugin,digest FROM activations"))
        self.assertEqual(stored, {(p, hashlib.sha256((p + "/" + r).encode()).digest()) for p,r in receipts})
        with patch.dict(os.environ, CODEX_HOME=str(self.home.parent / "other-profile")):
            self.cli(ROOT, "count", self.origin)
        with urlopen(self.origin + "/v1/counts") as response:
            self.assertEqual(json.load(response)["codex-desktop-mods"], 2)

    def test_lost_response_concurrent_retry_and_restart(self):
        self.fail_response = True
        with self.assertRaises(ValueError):
            activation.report(self.origin)
        self.assertEqual(activation.status()["state"], "pending")
        self.assertIsNone(activation.offer())
        self.fail_response = False
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda _: self.cli(ROOT, "count", self.origin), range(4)))
        self.assertTrue(all(r["state"] == "counted" for r in results))
        create_app(self.database)
        with urlopen(self.origin + "/v1/counts") as response:
            self.assertEqual(json.load(response)["codex-desktop-mods"], 1)

    def test_offer_offline_and_destination_bound(self):
        with patch.dict(os.environ, CAPRONICUS_ACTIVATION_URL=""):
            self.assertIsNone(activation.offer())
            self.assertEqual(activation.status()["state"], "unconfigured")
            self.assertFalse(self.home.exists())
        with self.assertRaises(ValueError):
            activation.report(self.origin + "/other")
        self.assertFalse(self.home.exists())
        with patch.dict(os.environ, CAPRONICUS_ACTIVATION_URL="http://public.example.org"):
            self.assertIsNone(activation.offer())
        self.assertEqual(self.requests, [])

    def test_distribution_configuration(self):
        tree = self.home
        for directory in ("plugins/chat-sync", "plugins/chat-organizer"):
            (tree / directory).mkdir(parents=True)
        shutil.copy(ROOT / "README.md", tree)
        configure(tree, "https://counts.example.org/")
        configure(tree, "https://counts.example.org")
        for p in tree.rglob("activation-endpoint.txt"):
            self.assertEqual(p.read_text().strip(), "https://counts.example.org")
        self.assertEqual(len(list(tree.rglob("activation-endpoint.txt"))), 3)
        self.assertEqual((tree / "README.md").read_text(encoding="utf-8").count("https://counts.example.org/badges/"), 3)

    def test_mcp_first_use_and_report(self):
        for package, action in [(ROOT, "apply_desktop_mods"), (PACKAGES[1], "sync_chats")]:
            script = """
import json, sys
from unittest.mock import patch
import server
action, origin = sys.argv[1:]
def call(name, arguments):
    req = {"jsonrpc":"2.0", "id":1, "method":"tools/call","params":{"name":name,"arguments":arguments}}
    return server.respond(json.dumps(req))["result"] if hasattr(server,"respond") else server.handle(req)
if action == "apply_desktop_mods":
    target = "windows_mods.apply" if sys.platform == "win32" else "manager.apply"
    stub = patch(target, return_value="fixture apply completed")
else:
    stub = patch.dict(server.ACTIONS, {action:lambda:{"imported":1}})
with stub:
    assert len(call(action,{})["content"]) == 2
    assert len(call(action,{})["content"]) == 1
assert json.loads(call("activation_status",{})["content"][0]["text"])["state"] == "offered"
assert call("report_activation",{"destination":origin + "/changed"})["isError"]
assert json.loads(call("report_activation",{"destination":origin})["content"][0]["text"])["state"] == "counted"
"""
            subprocess.run([sys.executable, "-c", script, action, self.origin], cwd=package, check=True)
        self.assertEqual(self.requests, ["POST", "POST"])


if __name__ == "__main__":
    unittest.main()
