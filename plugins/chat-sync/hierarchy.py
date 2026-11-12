"""Convert local sidebar groups using Codex metadata and the desktop's saved layout."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import time
import uuid

import desktop_layout as desktop
from native_names import native_api
import projects
import sync

QUEUE = "sidebar-group-change.json"
STORE = "codex-mods-sidebar-groups-v1"
ITEM = desktop.ITEM


def group_state(layout):
    return layout.data.get(STORE, {}).get(layout.account, {})


def inventory():
    layout = desktop.Layout(sync.HOME)
    sections = {s["id"]: s["name"] for s in layout.sections}
    local = {i: p["name"] for i, p in layout.projects.items()
             if projects.valid_id(i) and not p.get("chatGptBacking")}
    job = sync.read_json(sync.STATE / QUEUE, {})
    return {"projects": local, "sections": sections, "parents": group_state(layout).get("parents", {}),
            "ready": bool(layout.enabled and layout.account), "pending": job.get("label", "") if job.get("account")==layout.account else "",
            "started": bool(job.get("started")), "error": job.get("error", "") if job.get("account")==layout.account else ""}


def change(action, source="", parent="", name="", folder=""):
    home, directory = sync.HOME, sync.STATE
    with sync.lock(directory / "sync.lock"):
        path = directory / QUEUE
        job = sync.read_json(path, {})
        layout = desktop.Layout(home)
        if not layout.account or not layout.enabled:
            raise ValueError("Sign in to the desktop to organize local projects and sections.")
        if job:
            if job["account"] != layout.account:
                raise ValueError("A group change is pending for another signed-in account.")
            if action == "cancel" and not job.get("started"):
                path.unlink()
                return {"cancelled": True}
            if action != "retry":
                raise ValueError("Finish the pending sidebar change first.")
        else:
            groups = inventory()
            if parent and parent not in groups["projects"]:
                raise ValueError("Choose a local parent project.")
            if action == "create":
                if not parent or not name.strip() or len(name.strip()) > 150:
                    raise ValueError("Choose a project and enter a subsection name (up to 150 characters).")
                label = name.strip()
            elif action in ("promote", "move") and source in groups["sections"]:
                label = groups["sections"][source]
            elif action == "demote" and source in groups["projects"] and source != parent:
                label = groups["projects"][source]
            else:
                raise ValueError("Choose an existing local sidebar group.")
            if folder and (action != "promote" or not Path(folder).is_absolute() or not Path(folder).is_dir()):
                raise ValueError("Choose an existing absolute folder for the new project.")
            job = {"id": str(uuid.uuid4()), "action": action, "source": source, "parent": parent,
                   "name": label, "label": label, "folder": folder, "account": layout.account}
            sync.atomic(path, sync.encode(job))
        start_worker(home)
        return {"queued": True}


def start_worker(home):
    options = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(home)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)


def thread_members(home, column, native):
    if not native:
        return set()
    with closing(sqlite3.connect((home / "state_5.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
        return {r[0] for r in db.execute("SELECT id FROM threads WHERE " + column + "=?", (native,))}


def native_rows(call, method):
    rows, cursor = {}, None
    while True:
        result = call(method, {"cursor": cursor} if cursor else {})
        rows.update((r["id"], r) for r in result["data"])
        cursor = result.get("nextCursor")
        if not cursor:
            return rows


def preserve_exclusions(home, members, source_project):
    directory = home / "chat-sync"
    config_path = directory / "config.json"
    cfg = sync.read_json(config_path, {})
    if cfg:
        vault = Path(cfg["folder"]) / "codex-chat-sync-v1"
        sharing = projects.Sharing(cfg, vault, sync.catalog(), home)
        protected = set(members) & sharing.private_chats
        if source_project in sharing.private_projects:
            protected.update(members)
        if protected.difference(cfg.get("private_chats", [])):
            cfg["private_chats"] = sorted(protected.union(cfg.get("private_chats", [])))
            sync.atomic(config_path, sync.encode(cfg))
    else:
        path = directory / "disconnected.json"
        saved = sync.read_json(path, {})
        exclusions = saved.get("exclusions", {})
        if source_project in exclusions.get("private_projects", []):
            exclusions["private_chats"] = sorted(set(members).union(exclusions.get("private_chats", [])))
            sync.atomic(path, sync.encode(saved))


def run_change(home, call):
    directory = home / "chat-sync"
    path = directory / QUEUE
    job = sync.read_json(path)
    if not job or desktop.desktop_running():
        return False
    layout = desktop.Layout(home)
    if layout.account != job["account"]:
        raise ValueError("Sign back in to the account that requested this change.")
    def save(**changes):
        job.update(changes)
        sync.atomic(path, sync.encode(job))

    local = layout.data.setdefault("local-projects", {})
    maps = layout.data.setdefault("app-server-project-id-by-legacy-project-id-by-host", {}).setdefault(layout.host, {})
    atoms = layout.data.setdefault("electron-persisted-atom-state", {})
    bucket = atoms.setdefault(desktop.SECTIONS, {}).setdefault(layout.account, {})
    bucket.setdefault("collapsedSectionIds", [])
    bucket.setdefault("sectionOrder", [])
    sections = bucket.setdefault("sections", [])
    state = layout.data.setdefault(STORE, {}).setdefault(layout.account, {})
    parents = state.setdefault("parents", {})
    retained = state.setdefault("retainedProjects", {})
    action, source, parent = (job[k] for k in ("action","source","parent"))
    native_projects = native_rows(call, "project/list")
    native_sections = native_rows(call, "threadSection/list")
    def project_id(key):
        mapped = maps.get(key, key)
        if mapped in native_projects:
            return mapped
        return next((i for i,p in native_projects.items() if p["metadata"].get("codex.localProjectId") == key), None)

    # Freeze membership before any RPC. A retry can finish after a partial move.
    if not job.get("started"):
        section = next((s for s in sections if s["id"] == source), {})
        project = local.get(source, {})
        if parent and (parent not in local or local[parent].get("chatGptBacking")):
            raise ValueError("The selected parent project changed. Cancel and choose it again.")
        if parent and "codex:project:"+parent in section.get("itemKeys", []):
            raise ValueError("Move the parent project out of this section first.")
        if action == "demote" and (not project or project.get("chatGptBacking")) or action in ("move","promote") and not section:
            raise ValueError("This group changed. Cancel and select its current sidebar entry.")
        pid = project_id(source) if action == "demote" else None
        sid = section.get("hostSectionIds", {}).get("local")
        if not sid and source in native_sections:
            sid = source
        if action == "demote":
            members = thread_members(home, "project_id", pid)
            members.update(t for t, row in layout.data.get("thread-project-assignments", {}).items()
                           if row.get("projectKind") == "local" and row.get("projectId") == source)
        else:
            members = thread_members(home, "thread_section_id", sid)
            members.update(k[len(ITEM):] for k in section.get("itemKeys", []) if k.startswith(ITEM))
        # Mixed sections keep their nonlocal members in a subsection of the new project.
        mixed = any(not k.startswith(ITEM) for k in section.get("itemKeys", [])) or any(k != "local" for k in section.get("hostSectionIds", {}))
        previous = retained.get(source, {}) if action == "promote" else {}
        preserve_exclusions(home, members, source if action=="demote" else None)
        target = previous.get("stable") or source or str(uuid.uuid4())
        if action == "promote" and target in local or action in ("create","demote") and any(s["id"] == target for s in sections):
            target = str(uuid.uuid4())
        old_sections = {k[len(ITEM):]: s["id"] for s in sections for k in s.get("itemKeys", [])
                        if k.startswith(ITEM) and k[len(ITEM):] in members}
        save(started=True, members=sorted(members), oldProject=pid, oldSection=sid, target=target, mixed=mixed,
             priorSections=old_sections if action == "demote" else previous.get("sections", {}),
             children=[s for s,p in parents.items() if p==source] if action=="demote" else previous.get("children", []),
             original=project if action == "demote" else previous.get("record", {}),
             nativeRecord=native_projects.get(pid, {}) if action == "demote" else previous.get("native", {}))

    target, members = job["target"], job["members"]
    # The atomic desktop layout commit precedes retiring the empty native group.
    if state.get("applied") != job["id"]:
        if parent and parent not in local:
            raise ValueError("The parent project is no longer available.")
        dest_project = project_id(parent) if parent else None
        if parent and not dest_project:
            record = local[parent]
            p = call("project/create", {"name":record["name"], "roots":[{"path":r} for r in record.get("rootPaths", [])],
                     "metadata":{"codex.localProjectId":parent}, "idempotencyKey":"chat-sync:"+parent})["project"]
            maps[parent] = dest_project = p["id"]
        dest_section = job["oldSection"] if action == "move" or action == "promote" and job["mixed"] else None
        now = int(time.time() * 1000)
        if action == "promote":
            record = dict(job["original"])
            roots = record.get("rootPaths", []) or [r["path"] for r in job["nativeRecord"].get("roots", [])]
            if job["folder"]:
                roots = list(dict.fromkeys([job["folder"], *roots]))
            metadata = {**job["nativeRecord"].get("metadata", {}), "codex.localProjectId":target}
            p = call("project/create", {"name":job["name"], "roots":[{"path":r} for r in roots],
                     "metadata":metadata, "idempotencyKey":"group-change:"+job["id"]})["project"]
            dest_project = maps[target] = p["id"]
            local[target] = {**record, "id":target, "name":job["name"], "rootPaths":roots,
                             "createdAt":record.get("createdAt",now), "updatedAt":now}
            order = layout.data.setdefault("project-order", [])
            if target not in order: order.append(target)
            if job["mixed"]: parents[source] = target
            else:
                sections[:] = [s for s in sections if s["id"] != source]
                parents.pop(source, None)
                bucket["sectionOrder"] = [k for k in bucket.get("sectionOrder", []) if k != "custom:"+source]
            retained.pop(source,None)
            for child in job.get("children", []):
                if any(s["id"] == child for s in sections): parents[child] = target
        elif action in ("create","demote"):
            # section/create has no idempotency key. Recover an interrupted create by its unique temporary name.
            temporary = "Sidebar change " + job["id"]
            dest_section = job.get("newSection") or next((i for i,s in native_sections.items() if s["name"] == temporary), None)
            if not dest_section:
                dest_section = call("threadSection/create", {"name":temporary})["section"]["id"]
            save(newSection=dest_section)
            call("threadSection/update", {"sectionId":dest_section,"name":job["name"]})
            sections.append({"id":target,"name":job["name"],"hostSectionIds":{"local":dest_section},"itemKeys":[]})
            order = bucket.setdefault("sectionOrder", [])
            if "custom:"+target not in order: order.append("custom:"+target)
            if action == "demote":
                retained[target] = {"stable":source,"record":job["original"],"native":job["nativeRecord"],
                                    "sections":job["priorSections"],"children":job["children"]}
                local.pop(source,None)
                maps.pop(source,None)
                layout.data["project-order"] = [k for k in layout.data.get("project-order", []) if k != source]
                for sid, previous in list(parents.items()):
                    if previous == source:
                        if parent: parents[sid] = parent
                        else: parents.pop(sid)
                for s in sections:
                    s["itemKeys"] = [k for k in s.get("itemKeys", []) if k != "codex:project:"+source]
        if action != "promote":
            if parent: parents[target] = parent
            else: parents.pop(target,None)
        assignments = layout.data.setdefault("thread-project-assignments", {})
        projectless = layout.data.setdefault("projectless-thread-ids", [])
        member_keys = {ITEM+t for t in members}
        if action != "move":
            for s in sections:
                s["itemKeys"] = [k for k in s.get("itemKeys", []) if k not in member_keys]
                if s["id"] == target and action=="demote" or s["id"]==source and action=="promote" and job["mixed"]:
                    s["itemKeys"].extend(ITEM+t for t in members)
        for tid in members:
            if action != "move" or parent:
                call("thread/metadata/update", {"threadId":tid, "projectId":dest_project or ""})
                if dest_project:
                    assignments[tid] = {"projectKind":"local","projectId":target if action=="promote" else parent}
                    layout.data.setdefault("thread-project-membership-host-ids", {})[tid] = "local"
                    if tid in projectless: projectless.remove(tid)
                else:
                    assignments.pop(tid,None)
                    if tid not in projectless: projectless.append(tid)
            if action != "move":
                sid = dest_section
                if action == "promote" and not job["mixed"]:
                    old = next((s for s in sections if s["id"] == job["priorSections"].get(tid)), None)
                    if old:
                        sid = old.get("hostSectionIds",{}).get("local")
                        if ITEM+tid not in old["itemKeys"]: old["itemKeys"].append(ITEM+tid)
                call("thread/section/move", {"threadId":tid, "sectionId":sid})
                if sid: bucket.setdefault("threadHostIds", {})[tid] = "local"
        state["applied"] = job["id"]
        if desktop.desktop_running() or layout.path.read_bytes() != layout.original:
            return False
        sync.atomic(layout.path, sync.encode(layout.data))
    for kind, ident, rows, column in [
        ("project", job["oldProject"] if action=="demote" else None, native_projects, "project_id"),
        ("threadSection", job["oldSection"] if action=="promote" and not job["mixed"] else None, native_sections, "thread_section_id")
    ]:
        if ident in rows and not thread_members(home, column, ident):
            call(kind+"/delete", {("projectId" if kind=="project" else "sectionId"):ident})
    path.unlink()
    return True


def finish(home):
    directory = home / "chat-sync"
    try:
        with sync.lock(directory / "sidebar-group-worker.lock"):
            while (directory / QUEUE).is_file():
                pid = desktop.desktop_pid()
                if pid:
                    desktop.wait_for_exit(pid)
                    continue
                try:
                    with sync.lock(directory / "sync.lock"):
                        with native_api(home) as call:
                            if run_change(home, call): return
                except sync.Busy:
                    pass
                except (ValueError, KeyError, OSError, EOFError, sqlite3.Error) as error:
                    with sync.lock(directory / "sync.lock"):
                        job = sync.read_json(directory / QUEUE)
                        if job: sync.atomic(directory / QUEUE, sync.encode({**job,"error":str(error)}))
                    return
                time.sleep(.5)
    except sync.Busy:
        pass


if __name__ == "__main__":
    finish(Path(sys.argv[1]))
