"""Exercise conversions against real local projects, sections and a saved chat."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from native_fixture import App, ROOT, prepare
sys.path.insert(0, str(ROOT.parent))
import desktop_layout
import hierarchy
import sync


class SidebarGroups(unittest.TestCase):
    def test_native_round_trip(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            top = Path(tmp).resolve()
            home, checkout = top / "profile", top / "design files"
            checkout.mkdir()
            marker = checkout / "local.txt"
            marker.write_text("untouched")
            model = subprocess.Popen(["node","-e","require('./fake-model.cjs')().then(p=>{console.log(p);setInterval(()=>{},60000)})"],
                                     stdout=subprocess.PIPE,text=True,cwd=ROOT)
            try:
                prepare(home, int(model.stdout.readline()))
                auth = json.loads((home/"auth.json").read_bytes())
                auth["tokens"] = {"account_id":"fixture"}
                # Use API-key auth with the native server. The desktop layout separately reads the account id.
                with patch.dict(os.environ,{"CODEX_HOME":str(home), "HOME":str(top), "USERPROFILE":str(top)}), closing(App(home)) as app:
                    parent = app.call("project/create",{"name":"Studio","roots":[],"idempotencyKey":"studio"})["project"]["id"]
                    sec = app.call("threadSection/create",{"name":"Design"})["section"]["id"]
                    tid = app.call("thread/start",{"cwd":str(checkout)})["thread"]["id"]
                    app.turn(tid, "Synthetic design chat")
                    app.call("thread/section/move",{"threadId":tid,"sectionId":sec})
                    (home/"auth.json").write_text(json.dumps(auth))
                    key = str(uuid.uuid4())
                    layout = {"local-projects":{parent:{"id":parent,"name":"Studio","rootPaths":[]}},
                              "electron-persisted-atom-state":{desktop_layout.SECTIONS:{"fixture":{"sections":[
                                  {"id":key,"name":"Design","hostSectionIds":{"local":sec},"itemKeys":[desktop_layout.ITEM+tid]}]}}}}
                    sync.atomic(home/".codex-global-state.json",sync.encode(layout))
                    with patch.multiple(sync, HOME=home, STATE=home/"chat-sync"), patch.object(hierarchy,"start_worker"), patch.object(desktop_layout,"desktop_running",return_value=False):
                        def apply(action, **args):
                            hierarchy.change(action, **args)
                            self.assertTrue(hierarchy.run_change(home, app.call))
                            return desktop_layout.Layout(home)
                        lay = apply("promote",source=key,folder=str(checkout))
                        native = lay.project_ids[key]
                        self.assertEqual(app.call("project/read",{"projectId":native})["project"]["roots"][0]["path"],str(checkout))
                        self.assertEqual(app.call("thread/read",{"threadId":tid})["thread"]["projectId"],native)
                        self.assertFalse(lay.sections)
                        lay = apply("demote",source=key,parent=parent)
                        self.assertNotIn(key,lay.projects)
                        self.assertEqual(hierarchy.inventory()["parents"][key],parent)
                        row=app.call("thread/read",{"threadId":tid})["thread"]
                        self.assertEqual(row["projectId"],parent)
                        self.assertEqual(row["section"]["id"],lay.sections[0]["hostSectionIds"]["local"])
                        apply("move", source=key)
                        self.assertNotIn(key,hierarchy.inventory()["parents"])
                        lay=apply("promote",source=key)
                        self.assertEqual(lay.projects[key]["rootPaths"],[str(checkout)])
                        self.assertEqual(app.call("thread/read",{"threadId":tid})["thread"]["projectId"],lay.project_ids[key])
                        lay=apply("create",parent=key,name="References")
                        self.assertEqual(lay.sections[0]["name"],"References")
                        self.assertEqual(hierarchy.inventory()["parents"][lay.sections[0]["id"]],key)
                        middle=lay.sections[0]["id"]
                        lay=apply("create",parent="custom:"+middle,name="Seurat")
                        leaf=next(s for s in lay.sections if s["name"]=="Seurat")
                        deep="custom:"+leaf["id"]
                        self.assertEqual(hierarchy.inventory()["parents"][leaf["id"]],"custom:"+middle)
                        with self.assertRaisesRegex(ValueError,"three levels"):
                            hierarchy.change("create",parent=deep,name="Too deep")
                        with self.assertRaisesRegex(ValueError,"inside itself"):
                            hierarchy.change("move",source=middle,parent=deep)
                        with self.assertRaisesRegex(ValueError,"three levels"):
                            hierarchy.change("demote",source=key,parent=parent)
                        app.call("thread/section/move",{"threadId":tid,"sectionId":leaf["hostSectionIds"]["local"]})
                        lay=apply("move",source=middle,parent=parent)
                        row=app.call("thread/read",{"threadId":tid})["thread"]
                        self.assertEqual(row["projectId"],parent)
                        self.assertEqual(row["section"]["id"],leaf["hostSectionIds"]["local"])
                        lay=apply("demote",source=parent)
                        self.assertEqual(hierarchy.inventory()["parents"][middle],"custom:"+parent)
                        row=app.call("thread/read",{"threadId":tid})["thread"]
                        self.assertFalse(row["projectId"])
                        self.assertEqual(row["section"]["id"],leaf["hostSectionIds"]["local"])
                        lay=apply("promote",source=parent)
                        self.assertEqual(hierarchy.inventory()["parents"][middle],parent)
                        self.assertEqual(app.call("thread/read",{"threadId":tid})["thread"]["projectId"],lay.project_ids[parent])
                        apply("move",source=leaf["id"],parent=key)
                        lay=apply("move",source=leaf["id"],parent="custom:"+middle)
                        self.assertEqual(app.call("thread/read",{"threadId":tid})["thread"]["projectId"],lay.project_ids[parent])
                        lay=apply("promote",source=middle)
                        self.assertEqual(hierarchy.inventory()["parents"][leaf["id"]],middle)
                        self.assertEqual(app.call("thread/read",{"threadId":tid})["thread"]["projectId"],lay.project_ids[middle])
                        hierarchy.change("demote",source=key)
                        hierarchy.change("cancel")
                        self.assertFalse(hierarchy.inventory()["pending"])
                        self.assertEqual(marker.read_text(),"untouched")
            finally:
                model.terminate();model.wait();model.stdout.close()


if __name__ == "__main__":
    unittest.main()
