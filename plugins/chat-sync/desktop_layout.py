"""Project acknowledged sync changes into the desktop sidebar after Electron quits."""

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import sync

ITEM = "codex:thread:local:"
SECTIONS = "sidebar-custom-sections-v3"
QUEUE = "sidebar-layout-pending.json"
PINNED = "01984de2-8f74-7c91-a3b2-5c5e937cf318"


class Layout:
    def __init__(self, home):
        self.home = home
        self.path = home / ".codex-global-state.json"
        self.original = self.path.read_bytes() if self.path.is_file() else b"{}"
        self.data = json.loads(self.original)
        self.enabled = "local-projects" in self.data or "electron-persisted-atom-state" in self.data
        auth = sync.read_json(home / "auth.json", {}) if self.enabled else {}
        self.account = (auth.get("tokens") or {}).get("account_id") or ""
        self.host = "local:" + str(home)
        self.projects = self.data.get("local-projects", {})
        accounts = self.data.get("electron-persisted-atom-state", {}).get(SECTIONS, {})
        self.sections = accounts.get(self.account, {}).get("sections", [])
        self.project_ids = self.data.get(
            "app-server-project-id-by-legacy-project-id-by-host", {}
        ).get(self.host, {})
        self.section_ids = {
            s["id"]: s["hostSectionIds"]["local"]
            for s in self.sections
            if (s.get("hostSectionIds") or {}).get("local")
        }

    def subsection_projects(self):
        # A linked project is still a local parent for this account's sidebar.
        return {
            key: project for key, project in self.projects.items()
            if sync.UUID.fullmatch(key) and (
                not project.get("chatGptBacking")
                or self.account and project["chatGptBacking"].get("accountId") == self.account
            )
        }

    def observed(self, eligible):
        rows = {
            "project/" + k: v["name"]
            for k, v in self.projects.items()
            if sync.UUID.fullmatch(k) and v.get("name")
        }
        placements = self.data.get("thread-project-assignments", {})
        for tid in eligible:
            project = placements.get(tid) or {}
            if not project or project.get("projectKind") == "local":
                rows["chat-project/" + tid] = (
                    project.get("projectId") if project.get("projectId") in self.projects else None
                )
            if self.account:
                rows["chat-section/" + tid] = None
        for section in self.sections if self.account else []:
            sid = section["id"]
            if not sync.UUID.fullmatch(sid) or sid == PINNED:
                continue
            rows["section/" + sid] = section["name"]
            for item in section.get("itemKeys", []):
                if item.startswith(ITEM) and item[len(ITEM) :] in eligible:
                    rows["chat-section/" + item[len(ITEM) :]] = sid
        return rows

    def needs_mapping(self, kind, ident, native):
        if native is None:
            return False
        if kind == "project":
            return self.project_ids.get(ident) != native
        return kind == "section" and self.section_ids.get(ident) != native

    def apply(self, changes):
        """Use field-level preconditions so edits made after sync win on this device."""
        eligible = {k.split("/")[1] for k in changes if k.startswith("chat-")}
        import ordering

        for key in ("order/pins",):
            if key in changes:
                eligible.update(changes[key][1])
                eligible.update(changes[key][0] or [])
        current = self.observed(eligible)
        cached = set(
            self.data.get("electron-persisted-atom-state", {}).get(
                "app-server-pinned-thread-order-v1", []
            )
        )
        current.update(ordering.observe(self, eligible, cached))
        for key in changes:
            kind, ident = key.split("/")
            if kind == "checkout":
                current[key] = self.projects.get(ident, {}).get("rootPaths", [])
            if kind == "alias":
                current[key] = ident if ident in self.projects else changes[key][1]

        applied = {}
        projects = self.data.setdefault("local-projects", {})
        section_state = {}
        if self.account and any(k.startswith(("section/", "chat-section/")) for k in changes):
            atoms = self.data.setdefault("electron-persisted-atom-state", {})
            section_state = atoms.setdefault(SECTIONS, {}).setdefault(self.account, {})
            section_state.setdefault("collapsedSectionIds", [])
            section_state.setdefault("sectionOrder", [])
            self.sections = section_state.setdefault("sections", [])
        sections = self.sections
        self.projects = projects
        by_id = {s["id"]: s for s in sections}
        now = int(time.time() * 1000)
        # Groups exist before memberships are applied. Independent local ordering is retained.
        for key in sorted(
            changes,
            key=lambda k: 3
            if k.startswith("order/")
            else 2
            if k.startswith("alias/")
            else int(k.startswith(("chat-", "checkout/"))),
        ):
            before, value, native = changes[key]
            kind, ident = key.split("/")
            if kind == "parent": continue
            if kind == "chat-pin" and current.get(key) == value:
                ordering.project(self, key, value)
            if current.get(key) == value and not self.needs_mapping(kind, ident, native):
                applied[key] = value
                continue
            if current.get(key) != before and not (
                kind == "order" and ordering.can_apply_queued_order(before or [], current.get(key, []), value)
            ):
                continue
            if kind == "project":
                project = projects.setdefault(
                    ident, {"id": ident, "rootPaths": [], "createdAt": now}
                )
                project.update(name=value, updatedAt=now)
                maps = self.data.setdefault(
                    "app-server-project-id-by-legacy-project-id-by-host", {}
                )
                maps.setdefault(self.host, {})[ident] = native
                order = self.data.setdefault("project-order", [])
                if ident not in order:
                    order.append(ident)
            elif kind == "section":
                if ident not in by_id:
                    by_id[ident] = {"id": ident, "name": value, "itemKeys": []}
                    sections.append(by_id[ident])
                    order = section_state.setdefault("sectionOrder", [])
                    if "custom:" + ident not in order:
                        order.append("custom:" + ident)
                by_id[ident]["name"] = value
                by_id[ident].setdefault("hostSectionIds", {})["local"] = native
            elif kind in ("order", "chat-pin"):
                ordering.project(self, key, value)
            elif kind == "checkout":
                if ident not in projects or not Path(value).is_dir():
                    continue
                projects[ident]["rootPaths"] = [
                    value,
                    *[r for r in projects[ident].get("rootPaths", []) if r != value],
                ]
            elif kind == "alias":
                old = projects.get(ident)
                if (
                    old is None
                    or value not in projects
                    or old.get("rootPaths")
                    or old.get("chatGptBacking")
                ):
                    continue
                for row in self.data.get("thread-project-assignments", {}).values():
                    if row.get("projectKind") == "local" and row.get("projectId") == ident:
                        row["projectId"] = value
                del projects[ident]
                self.data.get("app-server-project-id-by-legacy-project-id-by-host", {}).get(
                    self.host, {}
                ).pop(ident, None)
                order = self.data.get("project-order", [])
                if ident in order:
                    order.remove(ident)
                atoms = self.data.get("electron-persisted-atom-state", {})
                sequence = atoms.get("unified-sidebar-project-order-v1", [])
                if "codex:project:" + ident in sequence:
                    sequence.remove("codex:project:" + ident)
            elif kind == "chat-project":
                assignments = self.data.setdefault("thread-project-assignments", {})
                if key not in current or (value and value not in projects):
                    continue
                projectless = self.data.setdefault("projectless-thread-ids", [])
                if value:
                    assignments[ident] = {"projectKind": "local", "projectId": value}
                    self.data.setdefault("thread-project-membership-host-ids", {})[ident] = "local"
                    if ident in projectless:
                        projectless.remove(ident)
                else:
                    assignments.pop(ident, None)
                    if ident not in projectless:
                        projectless.append(ident)
            elif kind == "chat-section":
                if value and value not in by_id:
                    continue
                item = ITEM + ident
                for section in sections:
                    if item in section["itemKeys"]:
                        section["itemKeys"].remove(item)
                if value:
                    by_id[value]["itemKeys"].append(item)
                    section_state.setdefault("threadHostIds", {})[ident] = "local"
            applied[key] = value
        if self.account and any(k.startswith("parent/") for k in changes):
            import section_tree
            applied.update(section_tree.apply(self, changes))
        return applied


def desktop_pid():
    # Electron owns its cached global state while open. Check again immediately
    # before replacement. Headless Codex app-server processes do not own this file.
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ChatGPT.exe", "/FO", "CSV", "/NH"],
            stdout=subprocess.PIPE,
            check=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return next(
            (
                int(r[1])
                for r in csv.reader(result.stdout.decode(errors="replace").splitlines())
                if r and r[0].lower() == "chatgpt.exe"
            ),
            None,
        )
    if not sys.platform.startswith("linux"):
        raise ValueError("Sidebar projection supports the Windows and Linux desktops.")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            if (
                proc.stat().st_uid != os.getuid()
                or (proc / "comm").read_text().strip().lower() != "chatgpt"
            ):
                continue
            # Zombie processes have already released their files.
            if (proc / "stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                continue
            return int(proc.name)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            return int(proc.name)
    return None


def desktop_running():
    return desktop_pid() is not None


def wait_for_exit(pid):
    # Wake promptly when the app quits without repeatedly launching tasklist or
    # scanning /proc during a long desktop session. Recheck opt-in periodically.
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            try:
                kernel.WaitForSingleObject(wintypes.HANDLE(handle), 30000)
            finally:
                kernel.CloseHandle(wintypes.HANDLE(handle))
            return
    elif hasattr(os, "pidfd_open"):
        import select

        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return
        except OSError:
            pass
        else:
            try:
                select.select([fd], [], [], 30)
            finally:
                os.close(fd)
            return
    time.sleep(2)


def apply_pending(home):
    directory = home / "chat-sync"
    path = directory / QUEUE
    cfg = sync.read_json(directory / "config.json", {})
    plan = sync.read_json(path)
    if not plan:
        return True
    undo = plan.get("undo", False)
    if cfg.get("device") != plan["device"] or not (undo or cfg.get("sidebar")):
        path.unlink(missing_ok=True)
        return True
    if cfg.get("paused") and not undo:
        return False
    if desktop_running():
        return False
    if undo:
        import batches

        if plan.get("account") != Layout(home).account:
            return False
        if plan.get("batch_id") and plan["batch_id"] != sync.read_json(directory / batches.JOURNAL, {}).get("id"):
            path.unlink(missing_ok=True)
            return True
        batches.undo_locked(home, cfg)
        return True
    layout = Layout(home)
    if not layout.enabled or layout.account != plan["account"]:
        return False
    acknowledged = layout.apply(plan["changes"])
    if layout.path.read_bytes() != layout.original or desktop_running():
        return False
    updated = layout.observed({k.split("/")[1] for k in acknowledged if k.startswith("chat-")})
    if any(
        updated.get(k) != v
        for k, v in acknowledged.items()
        if k.startswith(("project/", "section/", "chat-project/", "chat-section/"))
    ):
        raise ValueError("The desktop sidebar layout could not be verified")
    if acknowledged:
        if layout.data != json.loads(layout.original):
            sync.atomic(directory / "sidebar-layout-backup.json", layout.original)
            if layout.path.read_bytes() != layout.original or desktop_running():
                return False
            import batches

            batches.capture(
                home, cfg["device"], plan.get("batch_id"), json.loads(layout.original), layout.data
            )
            sync.atomic(layout.path, sync.encode(layout.data))
        state_path = directory / ("sidebar-" + cfg["device"] + ".json")
        state = sync.read_json(state_path)
        state["seen"].update(acknowledged)
        sync.atomic(state_path, sync.encode(state))
    path.unlink()
    return True


def queue(home, device, layout, changes, batch_id=None):
    directory = home / "chat-sync"
    path = directory / QUEUE
    if not changes:
        path.unlink(missing_ok=True)
        return False
    sync.atomic(
        path,
        sync.encode(
            {"device": device, "account": layout.account, "changes": changes, "batch_id": batch_id}
        ),
    )
    if apply_pending(home):
        return False
    start_worker(home)
    return True


def start_worker(home):
    directory = home / "chat-sync"
    # A single short-lived helper finishes the pending local layout when the app exits.
    try:
        with sync.lock(directory / "sidebar-exit.lock"):
            pass
    except sync.Busy:
        return True
    kwargs = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), str(home)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    return True


def finish_on_exit(home):
    directory = home / "chat-sync"
    with sync.lock(directory / "sidebar-exit.lock"):
        while (directory / QUEUE).is_file():
            cfg = sync.read_json(directory / "config.json", {})
            plan = sync.read_json(directory / QUEUE, {})
            if not plan.get("undo") and (not cfg.get("sidebar") or cfg.get("paused")):
                return
            pid = desktop_pid()
            if pid is not None:
                wait_for_exit(pid)
                continue
            try:
                with sync.lock(directory / "sync.lock"):
                    if apply_pending(home):
                        return
                    if sync.read_json(directory / QUEUE, {}).get("account") != Layout(home).account:
                        return
            except sync.Busy:
                pass
            time.sleep(1.5)


if __name__ == "__main__":
    try:
        finish_on_exit(Path(sys.argv[1]))
    except sync.Busy:
        pass
