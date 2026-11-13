"""Two isolated desktops exercising reply handoffs, checkout links and exclusions."""
from contextlib import closing, contextmanager
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_actions as actions
import projects
import bookmarks
import sharing_rules
import sync


def ident():
    return str(uuid.uuid4())


class ChatActions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
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
            for ref in (r"c:\PROJECTS\Sample\src\test.txt:12:5-14:7","src/test.txt"):
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
        with self.on(self.home) as h:
            cfg,vault=projects.config()
            command=h/"chat-sync"/"desktop-command.json"
            # The first pass records the inbox. Nothing has arrived yet.
            self.assertEqual((actions.announce(cfg,vault),command.exists()),(0,False))
        with self.on(self.work):
            for _ in range(2):
                actions.handoff(self.thread,self.turn,self.home,nonce,"Test this","assistant-reply")
            with self.assertRaises(ValueError):actions.handoff(self.thread,self.turn,self.home,nonce,"different")
            self.assertEqual(actions.inbox(),[])
            projects.set_private("chat",self.thread,True)
            with self.assertRaises(ValueError):actions.handoff(self.thread,self.turn,self.home,ident())
        with self.on(self.home):
            self.assertEqual(actions.announce(cfg,vault),1)
            notice=sync.read_json(command)
            self.assertEqual((notice["action"],notice["count"]),("handoffs",1))
            command.unlink()
            # One notice per arrival, and none for a chat kept on this desktop.
            self.assertEqual((actions.announce(cfg,vault),command.exists()),(0,False))
            self.assertEqual(actions.announce(cfg,vault,{self.thread}),0)
            msg,=actions.inbox()
            self.assertEqual(msg["note"],"Test this")
            self.assertEqual(actions.reply(msg["thread"],msg["turn"],msg["item"])["text"],"Shared answer")
            with self.assertRaises(ValueError):actions.reply(self.thread,self.turn,"an-unrelated-item")
            actions.dismiss(self.work,nonce)
            self.assertEqual(actions.inbox(),[])

    def test_bookmarks_are_local_and_account_scoped(self):
        nonce = ident()
        anchor = json.dumps([json.dumps(["local",self.thread,self.turn,"assistant","reply"]),2,7,"hash"])
        with self.on(self.home) as h:
            req = {"action":"new-bookmark","id":nonce,"account":"first","at":int(time.time()*1000),
                   "thread":self.thread,"turn":self.turn,"item":"reply","anchor":anchor}
            sync.atomic(h/"auth.json",sync.encode({"tokens":{"account_id":"first"}}))
            sync.atomic(sync.STATE/"desktop-request.json",sync.encode(req))
            bookmarks.edit("add",nonce,"File decision")
            bookmarks.edit("rename",nonce,"Second occurrence")
            self.assertEqual(bookmarks.list_marks()[0]["label"],"Second occurrence")
            bookmarks.edit("open",nonce)
            self.assertEqual(sync.read_json(sync.STATE/"desktop-command.json")["anchor"],anchor)
            sync.atomic(h/"auth.json",sync.encode({"tokens":{"account_id":"other"}}))
            self.assertEqual(bookmarks.list_marks(),[])
            self.assertEqual(actions.desktop_context(),{})
            with self.assertRaises(ValueError): bookmarks.edit("add",nonce,"Other")
            sync.atomic(h/"auth.json",sync.encode({"tokens":{"account_id":"first"}}))
            bookmarks.edit("remove",nonce)
            self.assertEqual(bookmarks.list_marks(),[])
        self.assertFalse(list(self.vault.rglob("*bookmark*")))

    def test_reading_positions_and_build_receipts(self):
        import delivery
        head={"chunks":[]}
        stats={"conflicts":{},"skipped":[]}
        def exchange(device, reading, private=()):
            with self.on(device):
                cfg,_=projects.config();cfg["reading"]=reading
                delivery.exchange(self.vault,cfg,{self.thread:{self.home:head,self.work:head}},
                                  set(),set(),stats,{},set(private),{self.thread},{})
                return sync.read_json(sync.STATE/"delivery.json")
        with self.on(self.work):
            import bookmarks
            account=sync.digest(bookmarks.account_id().encode())
            sync.atomic(sync.STATE/("reading-"+self.work+"-"+account+".json"),sync.encode(
                {self.thread:{"turn":self.turn,"role":"user","item":"","at":10}}))
            sync.atomic(sync.STATE/"running-build.json",sync.encode({"version":"0.16.0","build":"a"*12,"at":int(time.time()*1000)}))
        exchange(self.work,True)
        home=exchange(self.home,True)
        self.assertEqual(home["chats"][self.thread]["reading"][0]["turn"],self.turn)
        self.assertEqual(home["builds"][0]["build"]["running"],"0.16.0 · "+"a"*12)
        self.assertEqual(exchange(self.home,False)["chats"][self.thread]["reading"],[])
        exchange(self.work,True,[self.thread])
        self.assertEqual(exchange(self.home,True)["chats"][self.thread]["reading"],[])
        exchange(self.work,False)
        self.assertNotIn("reading",sync.read_json(self.vault/"receipts-v1"/(self.work+".json")))


    def test_outline_and_git_handoff(self):
        import subprocess
        self.history(self.work,self.thread,"Try [the file](src/example.py).")
        request=ident()
        for device,branch in [(self.home,"home-review"),(self.work,"work-design")]:
            with self.on(device) as h:
                root=h/"checkout"
                def git(*args):
                    subprocess.run(["git","-C",str(root),*args],check=True,capture_output=True)
                git("init","-b",branch)
                (root/"file.txt").write_text("checked in")
                git("add","file.txt")
                git("-c","user.name=Fixture","-c","user.email=fixture@example.test","commit","-m","Fixture")
                if device==self.work:
                    (root/"file.txt").write_text("work in progress")
                    actions.handoff(self.thread,self.turn,self.home,request,reference="file.txt")
                    rows=actions.outline(self.thread)
                    self.assertEqual([r["kind"] for r in rows],["Prompt","Files"])
        with self.on(self.home):
            result=actions.compare_handoff_file(self.work,request)
            self.assertEqual(result["status"],"Changed here")
            self.assertEqual(result["senderGit"]["branch"],"work-design")
            self.assertTrue(result["senderGit"]["fileDirty"])
            self.assertEqual(result["localGit"]["branch"],"home-review")
            self.assertFalse(result["localGit"]["fileDirty"])


    def test_handoff_file_states(self):
        request=ident()
        with self.on(self.work) as h:
            file=h/"checkout"/"design.txt"
            file.write_bytes(b"Known fixture")
            actions.handoff(self.thread,self.turn,self.home,request,reference=str(file))
            payload=sync.read_json(self.vault/"handoffs-v1"/self.home/self.work/(request+".json"))
            self.assertEqual(payload["file"],"design.txt")
            self.assertNotIn(str(h),json.dumps(payload))
            self.assertNotIn("Known fixture",json.dumps(payload))
        with self.on(self.home) as h:
            file=h/"checkout"/"design.txt"
            for contents,status in [(None,"Missing here"),(b"another","Changed here"),(b"Known fixture","Same version")]:
                if contents: file.write_bytes(contents)
                self.assertEqual(actions.compare_handoff_file(self.work,request)["status"],status)
            projects.set_private("chat",self.thread,True)
            with self.assertRaises(ValueError): actions.compare_handoff_file(self.work,request)

    def test_handoff_file_ignores_checkout_line_endings(self):
        request=ident()
        with self.on(self.work) as h:
            file=h/"checkout"/"notes.md"
            file.write_bytes(b"first\nsecond\n")
            actions.handoff(self.thread,self.turn,self.home,request,reference=str(file))
        with self.on(self.home) as h:
            file=h/"checkout"/"notes.md"
            for contents,status in [(b"first\r\nsecond\r\n","Same version"),(b"first\r\nedited\r\n","Changed here")]:
                file.write_bytes(contents)
                self.assertEqual(actions.compare_handoff_file(self.work,request)["status"],status)
            # A line ending can straddle two read blocks. Binary files compare byte for byte.
            size=1024*1024-1
            file.write_bytes(b"x"*size+b"\r\ny")
            crlf=actions.fingerprint(file)
            file.write_bytes(b"x"*size+b"\ny")
            self.assertEqual(crlf[1],actions.fingerprint(file)[1])
            self.assertNotEqual(crlf[0],actions.fingerprint(file)[0])
            file.write_bytes(b"\0\r\n")
            self.assertIsNone(actions.fingerprint(file)[1])

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
