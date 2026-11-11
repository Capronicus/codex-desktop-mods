"""Verify linking, delivery, privacy and reversible ordering on two disposable desktops."""

from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

from native_fixture import App, ROOT, prepare
from cross_platform import start

sys.path.insert(0, str(ROOT.parent))
import sync
import projects
import delivery
import colours
import batches
import desktop_layout

ACCOUNT = "aaaa0000-0000-4000-8000-000000000001"


@contextmanager
def profile(home):
    with (
        patch.dict(
            os.environ,
            {"HOME": str(home.parent), "USERPROFILE": str(home.parent), "CODEX_HOME": str(home)},
        ),
        patch.multiple(
            sync, HOME=home, STATE=home / "chat-sync", CONFIG=home / "chat-sync/config.json"
        ),
        patch.object(desktop_layout, "desktop_running", return_value=False),
    ):
        yield


def install_desktop(home, groups, assignments, section, tid=None):
    s = {
        "local-projects": {
            k: {"id": k, "name": name, "rootPaths": roots} for k, name, roots in groups
        },
        "project-order": [k for k, _, _ in groups],
        "thread-project-assignments": {
            k: {"projectKind": "local", "projectId": v} for k, v in assignments.items()
        },
        "app-server-project-id-by-legacy-project-id-by-host": {
            "local:" + str(home): {k: k for k, _, _ in groups}
        },
        "electron-persisted-atom-state": {
            desktop_layout.SECTIONS: {
                ACCOUNT: {
                    "sections": (
                        [
                            {
                                "id": section,
                                "name": "Later",
                                "hostSectionIds": {"local": section},
                                "itemKeys": [desktop_layout.ITEM + tid],
                            }
                        ]
                        if section
                        else []
                    ),
                    "sectionOrder": (["custom:" + section] if section else []),
                    "collapsedSectionIds": [],
                }
            }
        },
        "unrelated": "preserve",
    }
    sync.atomic(home / ".codex-global-state.json", sync.encode(s))
    # Native API-key fixture, plus a synthetic desktop account identifier used by the sidebar.
    auth = sync.read_json(home / "auth.json")
    auth["tokens"] = {
        "account_id": ACCOUNT,
        "id_token": "e30.e30.eA",
        "access_token": "offline",
        "refresh_token": "offline",
    }
    sync.atomic(home / "auth.json", sync.encode(auth))


def create(app, name, root):
    return app.call(
        "project/create", {"name": name, "roots": [{"path": str(root)}], "idempotencyKey": name}
    )["project"]["id"]


def run():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        base = Path(tmp).resolve()
        src, dst = [base / n / ".codex" for n in ("source", "receiver")]
        vault = base / "vault"
        vault.mkdir()
        a, b = base / "source-checkout", base / "receiver-checkout"
        a.mkdir()
        b.mkdir()
        model = subprocess.Popen(
            [
                "node",
                "-e",
                "require('./fake-model.cjs')().then(p=>{console.log(p);setInterval(()=>{},60000)})",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            port = int(model.stdout.readline())
            for home in (src, dst):
                prepare(home, port)
            with profile(src):
                sync.setup(str(vault))
                delivery.set_name("Home")
                with closing(App(src)) as app:
                    p = create(app, "Linked", a)
                    q = create(app, "Private work", a)
                    extra = create(app, "Empty project", a)
                    section = app.call("threadSection/create", {"name": "Later"})["section"]["id"]
                    tid = start(app)
                    secret = start(app)
                    individual = start(app)
                    for chat, prompt, group in [
                        (tid, "Share this QA chat", p),
                        (secret, "PRIVATE BODY SENTINEL", q),
                        (individual, "PRIVATE CHAT SENTINEL", p),
                    ]:
                        app.turn(chat, prompt)
                        app.call("thread/metadata/update", {"threadId": chat, "projectId": group})
                    app.call(
                        "thread/section/move", {"threadId": tid, "sectionId": desktop_layout.PINNED}
                    )
                install_desktop(
                    src,
                    [
                        (p, "Linked", [str(a)]),
                        (q, "Private work", [str(a)]),
                        (extra, "Empty project", [str(a)]),
                    ],
                    {tid: p, secret: q, individual: p},
                    section,
                    tid,
                )
                sync.setup(str(vault))
                sync.set_exchange_option("sidebar", True)
                sync.set_exchange_option("order", True)
                assert not projects.set_private("project", q, True)["previously_shared"]
                assert not projects.set_private("chat", individual, True)["previously_shared"]
                # Upgrade an existing installation with Electron colours and no SQLite seed.
                state = sync.read_json(src / ".codex-global-state.json")
                prefix = "codex.sidebar-colours.v1/"
                expected = {
                    "project/" + p: "#bb2100",
                    "section/" + section: "#9922bb",
                    "chat/local:" + tid: "#44ccdd",
                    "swatch/12ab34": "#12ab34",
                    "override-project/" + p: "on",
                }
                private_colour = {"project/" + q: "#123456", "chat/local:" + secret: "#998877"}
                state["electron-persisted-atom-state"].update(
                    {prefix + k: v for k, v in (expected | private_colour).items()}
                )
                state["project-order"] = [extra, p, q]
                sync.atomic(src / ".codex-global-state.json", sync.encode(state))
                sync.set_exchange_option("colours", True)
                sent = sync.sync_colours_now()
                assert sent["active"] == len(expected) and sent["saved_swatches"] == 1, sent
                assert {d.name for d in (vault / "codex-chat-sync-v1").iterdir()} == {"colours-v1"}
                stats = sync.run()
                assert stats["exported"] == 1 and "error" not in stats["sidebar"], stats
                assert delivery.status(tid)["status"] == "saved"
                shared = vault / "codex-chat-sync-v1"
                for f in shared.rglob("*.json"):
                    body = f.read_text()
                    assert all(
                        token not in body for token in [secret, individual, q, "Private work"]
                    ), (f, body)
            with profile(dst):
                with closing(App(dst)) as app:
                    start(app)
                    local = create(app, "Linked", b)
                install_desktop(dst, [(local, "Linked", [str(b)])], {}, None)
                sync.setup(str(vault))
                sync.set_exchange_option("sidebar", True)
                sync.set_exchange_option("order", True)
                delivery.set_name("Windows QA")
                assert p in projects.inventory()["shared"]
                projects.link(local, p, str(b))
                sync.set_exchange_option("colours", True)
                synced = sync.sync_colours_now()
                assert synced["active"] == len(expected) and synced["saved_swatches"] == 1, synced
                with closing(sqlite3.connect(dst / "sidebar-colours.sqlite")) as db:
                    imported = dict(db.execute("SELECT key,value FROM colours"))
                    for key, value in expected.items():
                        db_key = colours.account_prefix(ACCOUNT) + key if colours.section_key(key) else key.replace("/" + p, "/" + local)
                        assert imported[db_key] == value, (key, imported)
                    db.execute(
                        "UPDATE colours SET value=NULL,rev=10 WHERE key IN (?,?)",
                        ("swatch/12ab34", "project/" + local),
                    )
                    db.commit()
                sync.sync_colours_now()
                stats = sync.run()
                assert (
                    delivery.status(tid)["origin"]["name"] == "Home"
                    and delivery.status(tid)["origin"]["basis"] == "started"
                ), delivery.status(tid)
                assert stats["imported"] == 1 and stats["sidebar"].get("preview_required"), stats
                pre = batches.review()
                assert (
                    pre["counts"]["project_created"] == 1 and pre["counts"]["section_created"] == 1
                ), pre
                assert pre["names"]["project/" + local] == "Linked", pre["names"]
                assert pre["names"]["section/" + section] == "Later", pre["names"]
                assert q not in str(pre) and secret not in str(pre) and individual not in str(pre)
                try:
                    batches.review("apply", "stale")
                except ValueError:
                    pass
                else:
                    raise AssertionError("Accepted a stale preview")
                batches.review("apply", pre["id"])
                layout = desktop_layout.Layout(dst)
                assert set(layout.projects) == {local, extra} and layout.projects[local][
                    "rootPaths"
                ] == [str(b)], layout.projects
                assert layout.observed({tid})["chat-project/" + tid] == local
                assert tid in layout.data["pinned-thread-ids"]
                assert layout.data["project-order"] == [extra, local]
                assert layout.sections[0]["itemKeys"] == [desktop_layout.ITEM + tid]
                with sync.open_rollout(sync.streams()[tid][0]) as f:
                    assert json.loads(f.readline())["payload"]["cwd"] == str(b)
                with closing(App(dst)) as app:
                    resumed = app.call("thread/resume", {"threadId": tid})["thread"]
                    assert Path(resumed["cwd"]).resolve() == b.resolve(), resumed["cwd"]
                try:
                    batches.undo("stale")
                except ValueError as exc:
                    assert "newer sidebar batch" in str(exc)
                else:
                    raise AssertionError("Undid a different batch")
                result = batches.undo(pre["id"])
                assert result["sidebar"] is False
                restored = desktop_layout.Layout(dst)
                assert set(restored.projects) == {local} and not restored.sections
                assert (
                    tid not in restored.data.get("pinned-thread-ids", [])
                    and restored.data["unrelated"] == "preserve"
                )
                assert tid in sync.streams()
            with profile(src):
                sync.sync_colours_now()
                with closing(sqlite3.connect(src / "sidebar-colours.sqlite")) as db:
                    imported = dict(db.execute("SELECT key,value FROM colours"))
                    assert imported["swatch/12ab34"] is None and imported["project/" + p] is None, (
                        imported
                    )
                sync.run()
                assert [p["name"] for p in delivery.status(tid)["received_by"]] == ["Windows QA"]
                with closing(App(src)) as app:
                    app.call("thread/resume", {"threadId": tid})
                    app.turn(tid, "Second metadata check from Windows")
                sync.run()
                assert (
                    delivery.status(tid)["status"] == "saved"
                    and not delivery.status(tid)["received_by"]
                )
                assert projects.set_private("chat", tid, True)["previously_shared"]
                before = {f: f.read_bytes() for f in (shared / "heads").glob("*/*.json")}
                sync.run()
                assert before == {f: f.read_bytes() for f in before}
            print(
                "Verified linked checkout, exact peer receipt, chat and project exclusion, preview, undo, pins and order."
            )
        finally:
            model.terminate()
            model.wait()
            model.stdout.close()


if __name__ == "__main__":
    run()
