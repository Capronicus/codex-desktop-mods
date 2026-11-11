"""Two isolated desktops exercising reply handoffs, checkout links and exclusions."""
from contextlib import closing, contextmanager
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_actions as actions
import projects
import sharing_rules
import sync


def ident():
    return str(uuid.uuid4())


class ChatActions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.vault = root / "vault" / "codex-chat-sync-v1"
        self.vault.mkdir(parents=True)
        self.work, self.home, self.project, self.thread, self.turn = [ident() for _ in range(5)]
        self.profiles = {}
        for device in (self.work,self.home):
            home = root / device
            checkout = home / "checkout"
            checkout.mkdir(parents=True)
            self.profiles[device] = home
            (home/"thread-writer-locks").mkdir()
            (home/"thread-writer-locks/.coordination.lock").touch()
            with closing(sqlite3.connect(home / "state_5.sqlite")) as db, db:
                db.execute("create table threads(id,rollout_path,name,archived,source,cwd,project_id,title)")
            cfg = {"folder": str(self.vault.parent), "device": device, "project_links":
                   {self.project: {"shared": self.project, "folder": str(checkout)}}}
            sync.atomic(home / "chat-sync/config.json", sync.encode(cfg))
            sync.atomic(home / ".codex-global-state.json", sync.encode({"local-projects": {
                self.project: {"name": "Sample", "rootPaths": [str(checkout)]}}}))
            sync.atomic(self.vault / "receipts-v1" / (device+".json"),
                        sync.encode({"v": 1, "name": "Home" if device==self.home else "Work"}))
            self.history(device, self.thread, "Shared answer")

    @contextmanager
    def on(self, device):
        home=self.profiles[device]
        with patch.multiple(sync, HOME=home, STATE=home/"chat-sync", CONFIG=home/"chat-sync/config.json"):
            yield home

    def history(self, device, thread, answer, parent=None):
        home=self.profiles[device]
        dest=home/"sessions"/f"rollout-2026-11-01T00-00-00-{thread}.jsonl"
        records=[("session_meta", {"id":thread,"cwd":str(home/"checkout"),"forked_from_id":parent}),
                 ("turn_context",{"turn_id":self.turn}),
                 ("event_msg",{"type":"item_completed","turn_id":self.turn,"item":{"type":"UserMessage","content":[{"type":"text","text":"Check it"}]}}),
                 ("response_item",{"type":"message","id":"assistant-reply","role":"assistant",
                                   "content":[{"type":"output_text","text":answer}]})]
        sync.atomic(dest, b"".join(sync.encode({"type":k,"payload":v})+b"\n" for k,v in records))
        with closing(sqlite3.connect(home/"state_5.sqlite")) as db, db:
            db.execute("delete from threads where id=?",(thread,))
            db.execute("insert into threads values(?,?,?,?,?,?,?,?)",(thread,str(dest),"Sample",0,"cli",str(home/"checkout"),self.project,"Chat"))
        return dest

    def test_file_resolution(self):
        with self.on(self.home) as h:
            file=h/"checkout"/"src"/"test.txt"
            file.parent.mkdir(); file.write_text("from Home")
            cfg,_=projects.config()
            actions.publish_roots(cfg,self.vault)
            key=actions.root_key(actions.source_path(r"C:\Projects\Sample"))
            sync.atomic(self.vault/"file-roots-v1"/(self.work+".json"),sync.encode({self.project:[key]}))
            for ref in (r"c:\PROJECTS\Sample\src\test.txt:12:5","src/test.txt"):
                self.assertEqual(actions.open_file(self.thread,ref)["text"],"from Home")
            for ref in ("../secret","/unknown/test.txt","missing","//host/share"):
                with self.assertRaises(ValueError):actions.open_file(self.thread,ref)
            outside=h/"secret";outside.write_text("outside")
            try:(file.parent/"link").symlink_to(outside)
            except OSError:pass
            else:
                with self.assertRaises(ValueError):actions.open_file(self.thread,"src/link")
            projects.set_private("folder",str(file.parent),True)
            with self.assertRaises(ValueError):actions.open_file(self.thread,"src/test.txt")

    def test_handoff(self):
        nonce=ident()
        with self.on(self.work):
            for _ in range(2):
                actions.handoff(self.thread,self.turn,self.home,nonce,"Test this","assistant-reply")
            with self.assertRaises(ValueError):actions.handoff(self.thread,self.turn,self.home,nonce,"different")
            self.assertEqual(actions.inbox(),[])
            projects.set_private("chat",self.thread,True)
            with self.assertRaises(ValueError):actions.handoff(self.thread,self.turn,self.home,ident())
        with self.on(self.home):
            msg,=actions.inbox()
            self.assertEqual(msg["note"],"Test this")
            self.assertEqual(actions.reply(msg["thread"],msg["turn"],msg["item"])["text"],"Shared answer")
            with self.assertRaises(ValueError):actions.reply(self.thread,self.turn,"an-unrelated-item")
            actions.dismiss(self.work,nonce)
            self.assertEqual(actions.inbox(),[])

    def test_both_rule_scopes_and_resume(self):
        with self.on(self.work) as h:
            folder = h / "checkout" / "private"
            folder.mkdir()
            projects.set_private("folder",str(folder),True)
        with self.on(self.home) as h:
            self.assertFalse(projects.Sharing(projects.config()[0],self.vault,sync.catalog(),h).excluded_folders)
        with self.on(self.work):
            sharing_rules.set_rule("folder",str(folder),True)
        with self.on(self.home) as h:
            cfg,_=projects.config()
            self.assertIn(str(h/"checkout/private"),projects.Sharing(cfg,self.vault,sync.catalog(),h).excluded_folders)
            sharing_rules.set_rule("rule",self.project+"/private",False)
            sync.set_paused(True)
            sharing_rules.set_rule("project",self.project,True)
            self.assertIn(self.thread,projects.Sharing(projects.config()[0],self.vault,sync.catalog(),h).private_chats)
        with self.on(self.work) as h:
            self.assertNotIn(self.thread,projects.Sharing(projects.config()[0],self.vault,sync.catalog(),h).private_chats)
        with self.on(self.home):
            sync.set_paused(False)
            sharing_rules.exchange(projects.config()[0],self.vault)
        with self.on(self.work) as h:
            sharing=projects.Sharing(projects.config()[0],self.vault,sync.catalog(),h)
            self.assertIn(self.thread,sharing.private_chats)
            sharing_rules.set_rule("project",self.project,False)
            sharing=projects.Sharing(projects.config()[0],self.vault,sync.catalog(),h)
            self.assertNotIn(self.thread,sharing.private_chats)
            self.assertIn(str(folder),sharing.excluded_folders)

    def test_family_comparison_and_folder_rule(self):
        with self.on(self.home) as h:
            parent, sibling=ident(),ident()
            self.history(self.home,self.thread,"Home answer",parent)
            self.history(self.home,sibling,"Sibling",parent)
            self.assertEqual({(r["thread"],r["relation"]) for r in actions.family(self.thread)},
                             {(sibling,"Sibling"),(parent,"Parent")})
            for device in (self.home,self.work):
                with self.on(device):
                    entry=sync.streams()[self.thread]
                    man=sync.snapshot(self.vault,self.thread,entry,"sample",None)["manifest"]
                    sync.publish(self.vault,device,man)
            diff=actions.conflict(self.thread,self.home,self.work)
            self.assertEqual(diff["common"]["text"],"Check it")
            self.assertEqual([diff["left"][0]["text"],diff["right"][0]["text"]],["Home answer","Shared answer"])
            projects.set_private("folder",str(h/"checkout"),True)
            sharing=projects.Sharing(projects.config()[0],self.vault,sync.catalog(),h)
            self.assertIn(self.thread,sharing.folder_chats)
            self.assertIn(sibling,sharing.folder_chats)
            with self.assertRaises(ValueError):actions.conflict(self.thread,self.home,self.work)
            sync.disable();sync.setup(str(self.vault.parent))
            self.assertIn(str(h/"checkout"),projects.config()[0]["private_folders"])
            projects.set_private("folder",str(h/"checkout"),False)
            self.assertEqual(projects.config()[0]["private_folders"],[])


if __name__=="__main__":
    unittest.main()
