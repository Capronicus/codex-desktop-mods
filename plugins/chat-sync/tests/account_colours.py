"""Exercise account switching with the installed SQLite bridge and two disposable desktops."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from mod_controls import Client, SERVER


def account(client, name):
    (client.home / "auth.json").write_text(json.dumps({"tokens": {"account_id": name}}))


def bridge(client, method, *args):
    script = """
const handlers = new Map();
const {readFileSync} = require('node:fs');
new Function('require','process',readFileSync(process.argv[1],'utf8'))(
  name=>name==='electron'?{ipcMain:{handle:(name,fn)=>handlers.set(name,fn)}}:require(name),process);
const frame={url:'app://-/index.html'},event={senderFrame:frame,sender:{mainFrame:frame}};
Promise.resolve(handlers.get('local-sidebar-colours:'+process.argv[2])(event,...JSON.parse(process.argv[3])))
  .then(result=>console.log(JSON.stringify(result)));
"""
    return json.loads(subprocess.check_output(
        ["node", "-e", script, str(SERVER.parents[2] / "chat-details-main.cjs"), method, json.dumps(args)],
        env={**os.environ, "CODEX_HOME": str(client.home), "NODE_NO_WARNINGS": "1"}, text=True))


class AccountColours(unittest.TestCase):
    def test_sections_follow_active_account(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            folder = root / "folder"
            folder.mkdir()
            home, work = Client(root, "home"), Client(root, "work")
            for pc in (home, work):
                stack.callback(pc.close)
                pc.tool("configure_chat_sync", folder=str(folder), automatic=False)
                pc.tool("set_colour_sync", enabled=True)
            atoms = {"sidebar-custom-sections-v3": {
                "account-a": {"sections": [{"id":"a-only","itemKeys":["codex:thread:local:from-a"]},
                                            {"id":"same","itemKeys":["codex:thread:local:common-a"]}]},
                "account-b": {"sections": [{"id":"b-only","itemKeys":["codex:thread:local:from-b"]},
                                            {"id":"same","itemKeys":["codex:thread:local:common-b"]}]}
            }}
            for k,v in {"section/a-only":"#990000", "section/b-only":"#009900", "section/same":"#999999",
                        "override-section/b-only":"on", "swatch/ffeedd":"#ffeedd"}.items():
                atoms["codex.sidebar-colours.v1/"+k] = v
            (home.home/".codex-global-state.json").write_text(json.dumps({"electron-persisted-atom-state":atoms}))
            (work.home/".codex-global-state.json").write_text(json.dumps({"electron-persisted-atom-state":{}}))
            account(home,"account-a")
            home.tool("sync_colours_now")
            colour_dir = folder/"codex-chat-sync-v1"/"colours-v1"
            public_head = next(colour_dir.glob("*.json"))
            self.assertNotIn("section/",public_head.read_text())
            account_a_head = next((colour_dir/"sections").glob("*/*.json"))
            self.assertIn("from-a",account_a_head.read_text())
            self.assertNotIn("b-only",account_a_head.read_text())
            self.assertNotIn("from-b",account_a_head.read_text())
            self.assertNotIn("section/same",account_a_head.read_text())
            # Old clients wrote section colours and membership hints to an untagged file.
            # Receiving that file must not import or relay those entries on either account.
            (colour_dir/"cccccccc-1111-4111-8111-111111111111.json").write_text(json.dumps({
                "version":1,"entries":[["section/legacy-foreign","#abcdef",80,"cccccccc-1111-4111-8111-111111111111"]],
                "members":{"local:legacy-foreign":[None,"legacy-foreign"]}}))
            self.assertEqual(bridge(home,"members","section","same")["chats"],["local:common-a"])
            bridge(home,"preferences","write",[["section/same","#aa0000"]],None)
            home.tool("sync_colours_now")
            account(work,"account-a")
            work.tool("sync_colours_now")
            snap=bridge(work,"preferences","read",[],None)
            self.assertEqual(dict(snap["entries"])["section/same"],"#aa0000")
            self.assertNotIn("section/legacy-foreign",dict(snap["entries"]))
            self.assertNotIn("local:legacy-foreign",[r["chat"] for r in snap["inherited"]])
            self.assertIn("local:from-a",[r["chat"] for r in snap["inherited"]])
            account(work,"account-b")
            other=bridge(work,"preferences","read",[],snap["version"])
            self.assertNotIn("section/same",dict(other["entries"]))
            self.assertEqual(other["inherited"],[])
            account(home,"account-b")
            bridge(home,"preferences","write",[["section/same","#00aa00"]],None)
            home.tool("sync_colours_now")
            work.tool("sync_colours_now")
            other=bridge(work,"preferences","read",[],None)
            self.assertEqual(dict(other["entries"])["section/same"],"#00aa00")
            self.assertNotIn("section/a-only",dict(other["entries"]))
            self.assertEqual(bridge(home,"members","section","same")["chats"],["local:common-b"])
            account(work,"")
            work.tool("sync_colours_now")
            snap=bridge(work,"preferences","read",[],None)
            self.assertEqual(dict(snap["entries"]),{"swatch/ffeedd":"#ffeedd"})
            self.assertEqual(snap["inherited"],[])
            account(work,"account-a")
            work.tool("sync_colours_now")
            self.assertEqual(dict(bridge(work,"preferences","read",[],None)["entries"])["section/same"],"#aa0000")


if __name__=="__main__":
    unittest.main()
