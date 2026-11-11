"""Portable project identities, device-local checkouts and sharing preferences."""

from contextlib import closing
from pathlib import Path
import sqlite3

import sync


def config():
    cfg = sync.read_json(sync.CONFIG)
    if not cfg:
        raise ValueError("Configure a private sync folder first.")
    return cfg, Path(cfg["folder"]) / "codex-chat-sync-v1"


def valid_id(value):
    return isinstance(value, str) and sync.UUID.fullmatch(value) is not None


def local_projects(home):
    import desktop_layout

    layout = desktop_layout.Layout(home)
    if layout.enabled:
        return {
            k: {"name": v["name"], "folders": v.get("rootPaths", [])}
            for k, v in layout.projects.items()
            if valid_id(k)
        }
    catalogue = home / "state_5.sqlite"
    if not catalogue.is_file():
        return {}
    with closing(sqlite3.connect(catalogue.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'").fetchone():
            return {}
        import json

        return {
            json.loads(meta).get("codex.localProjectId", k): {"name": name, "folders": []}
            for k, name, meta in db.execute("SELECT id,name,metadata FROM projects")
        }


def peer_file(path):
    try:
        data = sync.read_json(path)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def sidebar_rows(vault):
    import sidebar

    merged = {}
    for head in (vault / "sidebar-v1").glob("*.json"):
        if not valid_id(head.stem):
            continue
        doc = peer_file(head)
        if doc.get("v") != 1 or not isinstance(doc.get("rows"), dict):
            continue
        for key, rec in doc.get("rows", {}).items():
            if sidebar.valid_record(key, rec) and (key not in merged or rec[:2] > merged[key][:2]):
                merged[key] = rec
    return merged


class Links:
    """Union explicit ID pairs. Different device labels never imply equivalence."""

    def __init__(self, cfg, vault, local):
        self.parent = {}
        self.preferred = {}
        self.checkouts = {}
        pairs = []
        for f in (vault / "project-links-v1").glob("*.json"):
            if valid_id(f.stem):
                values = peer_file(f).get("links", [])
                if isinstance(values, list):
                    pairs.extend(values)
        bindings = cfg.get("project_links", {})
        pairs.extend([k, b["shared"]] for k, b in bindings.items())
        for pair in pairs:
            if isinstance(pair, list) and len(pair) == 2 and all(valid_id(i) for i in pair):
                a, b = (self.wire(i) for i in pair)
                if a != b:
                    self.parent[max(a, b)] = min(a, b)
        # Prefer a local checkout over a previously imported rootless shell.
        for k in sorted(local, key=lambda k: (not bool(local[k].get("folders")), k)):
            self.preferred.setdefault(self.wire(k), k)
        for k, b in bindings.items():
            self.preferred[self.wire(k)] = k
            if b.get("folder"):
                self.checkouts[self.wire(k)] = b["folder"]

    def wire(self, key):
        start = key
        while key in self.parent:
            key = self.parent[key]
        if start != key:
            self.parent[start] = key
        return key

    def local(self, key):
        return self.preferred.get(self.wire(key), self.wire(key))

    def rows(self, records, *, portable):
        resolve = self.wire if portable else self.local
        result = {}
        for key, rec in records.items():
            kind, ident = key.split("/")
            if kind == "project":
                key = kind + "/" + resolve(ident)
            elif kind == "chat-project" and rec[2]:
                rec = [*rec[:2], resolve(rec[2])]
            elif key == "order/projects":
                rec = [*rec[:2], list(dict.fromkeys(resolve(i) for i in rec[2]))]
            if key not in result or rec[:2] > result[key][:2]:
                result[key] = rec
        return result


def inventory():
    cfg, vault = config()
    local = local_projects(sync.HOME)
    rows = sidebar_rows(vault)
    return {
        "local": local,
        "shared": {k.split("/")[1]: v[2] for k, v in rows.items() if k.startswith("project/")},
        "links": cfg.get("project_links", {}),
    }


def link(local_project, shared_project, folder):
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = config()
        sync.require_active(cfg)
        inv = inventory()
        if local_project not in inv["local"] or shared_project not in inv["shared"]:
            raise ValueError("Choose a local project and a shared project from list_sync_projects.")
        checkout = Path(folder).expanduser()
        if not checkout.is_absolute() or not checkout.is_dir():
            raise ValueError("Choose an existing absolute checkout folder on this computer.")
        bindings = cfg.setdefault("project_links", {})
        links = Links(cfg, vault, inv["local"])
        if local_project in bindings and links.wire(
            bindings[local_project]["shared"]
        ) != links.wire(shared_project):
            raise ValueError("This local project is already linked to a different project.")
        if any(
            k != local_project and links.wire(v["shared"]) == links.wire(shared_project)
            for k, v in bindings.items()
        ):
            raise ValueError("This shared project already has a local checkout binding.")
        if local_project in cfg.get("private_projects", []) or shared_project in cfg.get(
            "private_projects", []
        ):
            raise ValueError("Remove the local-only project rule before linking it across devices.")
        bindings[local_project] = {"shared": shared_project, "folder": str(checkout.resolve())}
        sync.atomic(sync.CONFIG, sync.encode(cfg))
        publish_links(cfg, vault)
        return {"local_project": local_project, **bindings[local_project]}


def publish_links(cfg, vault):
    bindings = cfg.get("project_links", {})
    if bindings:
        blocked = set(cfg.get("private_projects", []))
        data = sync.encode(
            {
                "links": [
                    [k, v["shared"]]
                    for k, v in sorted(bindings.items())
                    if k not in blocked and v["shared"] not in blocked
                ]
            }
        )
        f = vault / "project-links-v1" / (cfg["device"] + ".json")
        if not f.exists() or f.read_bytes() != data:
            sync.atomic(f, data)


class Sharing:
    def __init__(self, cfg, vault, index, home):
        self.private_chats = set(cfg.get("private_chats", []))
        self.private_projects = set(cfg.get("private_projects", []))
        self.members = {}
        self.links = None
        self.roots = {}
        self.source_roots = {}
        import sharing_rules
        rules = sharing_rules.records(cfg,vault) if cfg.get("device") else {}
        self.shared_rules = {}
        self.excluded_folders = list(cfg.get("private_folders", []))
        # Ordinary transcript-only profiles need no desktop or sidebar schema.
        if self.private_projects or cfg.get("project_links") or rules:
            import desktop_layout

            layout = desktop_layout.Layout(home)
            projects = local_projects(home)
            links = self.links = Links(cfg, vault, projects)
            self.shared_rules = sharing_rules.canonical(rules,links)
            roots = self.roots = sharing_rules.project_roots(cfg,projects,links)
            for file in (vault / "file-roots-v1").glob("*.json"):
                if not valid_id(file.stem):continue
                for pid, hashes in peer_file(file).items():
                    if valid_id(pid) and isinstance(hashes, list):
                        for key in hashes:
                            if isinstance(key,str) and sync.HEX.fullmatch(key):
                                self.source_roots.setdefault(key,set()).add(links.wire(pid))
            for key, (_,enabled,_,threads) in self.shared_rules.items():
                if not enabled:continue
                pid,relative = sharing_rules.parse(key)
                self.private_chats.update(threads)
                if not relative:self.private_projects.add(pid)
                if pid in roots:self.excluded_folders.append(str(roots[pid]/relative))
            aliases = {v: k for k, v in layout.project_ids.items()}
            self.private_projects = {links.wire(i) for i in self.private_projects}
            shared = {
                k.split("/")[1]: r[2]
                for k, r in sidebar_rows(vault).items()
                if k.startswith("chat-project/")
            }
            self.members.update(shared)
            for tid, row in index.items():
                if row.get("project_id"):
                    self.members[tid] = aliases.get(row["project_id"], row["project_id"])
            for tid, row in layout.data.get("thread-project-assignments", {}).items():
                if row.get("projectKind") == "local":
                    self.members[tid] = row.get("projectId")
            for tid, project in self.members.items():
                if links.wire(project) in self.private_projects:
                    self.private_chats.add(tid)
            self.private_projects.update(
                k
                for k in set(links.parent) | set(links.preferred.values()) | set(aliases)
                if links.wire(aliases.get(k, k)) in self.private_projects
            )

        self.folder_chats = set()
        if self.excluded_folders:
            cfg = {**cfg, "private_folders":self.excluded_folders}
            self.folder_chats.update(t for t, row in index.items() if folder_excluded(row.get("cwd"), cfg))
            self.folder_chats.update(t for t in self.members if folder_excluded(self.checkout(t), cfg))
            for tid, entry in sync.streams().items():
                if tid in self.folder_chats or (tid in index and index[tid].get("cwd")):
                    continue
                # A just-created chat can reach disk before its catalogue row.
                try:
                    import json
                    with sync.open_rollout(entry[0]) as stream:
                        cwd = json.loads(stream.readline(2 * 1024 * 1024))["payload"]["cwd"]
                    if not cwd or folder_excluded(cwd, cfg):
                        self.folder_chats.add(tid)
                except (OSError, ValueError, TypeError, KeyError, EOFError):
                    self.folder_chats.add(tid)
            self.private_chats.update(self.folder_chats)

    def checkout(self, tid):
        return (
            self.links.checkouts.get(self.links.wire(self.members.get(tid))) if self.links else None
        )

    def blocks_incoming(self, vault, tid, manifests):
        if tid in self.private_chats:
            return True
        if not self.excluded_folders and not self.private_projects:
            return False
        from chat_actions import source_path, root_key
        import json
        folders = {"private_folders": self.excluded_folders}
        for man in manifests:
            header = bytearray()
            for key in man["chunks"]:
                head, sep, _ = sync.block_data(vault,key).partition(b"\n")
                header.extend(head)
                if len(header)>8*sync.BLOCK:
                    raise ValueError("Cannot check the workspace of this chat yet.")
                if sep:break
            p=json.loads(header).get("payload", {})
            cwd=p.get("cwd")
            if not isinstance(cwd,str) or not cwd or cwd.startswith(("//","\\")):continue
            if folder_excluded(cwd,folders):return True
            source=source_path(cwd)
            for base in (source,*source.parents):
                for pid in self.source_roots.get(root_key(base),()):
                    if pid in self.private_projects:return True
                    if pid in self.roots:
                        relative=source.relative_to(base)
                        if ".." not in relative.parts and folder_excluded(str(self.roots[pid].joinpath(*relative.parts)),folders):
                            return True
        return False

    def allows_row(self, key, rec):
        kind, ident = key.split("/")
        return (
            ident not in self.private_chats
            and ident not in self.private_projects
            and not (kind == "chat-project" and rec[2] in self.private_projects)
        )

    def allows_colour(self, key):
        kind, _, value = key.partition("/")
        return (
            value.removeprefix("local:") not in self.private_chats
            and value not in self.private_projects
        )


def folder_excluded(value, cfg):
    folders = cfg.get("private_folders", [])
    if not folders or not isinstance(value, str) or not value or not Path(value).is_absolute():
        return False
    path = Path(value).resolve()
    return any(path == Path(root) or Path(root) in path.parents for root in folders)


def set_private(kind, ident, enabled):
    if kind == "folder":
        folder = Path(ident).expanduser()
        if not folder.is_absolute() or (enabled and not folder.is_dir()):
            raise ValueError("Choose an existing absolute local folder.")
        ident = str(folder.resolve())
    elif kind not in ("chat", "project") or not valid_id(ident):
        raise ValueError("Choose a chat or project UUID.")
    with sync.lock(sync.STATE / "sync.lock"):
        cfg, vault = config()
        key = {"chat": "private_chats", "project": "private_projects", "folder": "private_folders"}[kind]
        entries = set(cfg.get(key, []))
        prior = sync.read_json(sync.STATE / ("state-" + cfg["device"] + ".json"), {})
        if kind == "chat":
            previously = ident in prior or (vault / "versions" / ident).is_dir()
        elif kind == "folder":
            membership = Sharing({**cfg, "private_folders": [ident]}, vault, sync.catalog(), sync.HOME)
            previously = any(t in prior or (vault / "versions" / t).is_dir() for t in membership.folder_chats)
        else:
            rows = sidebar_rows(vault)
            membership = Sharing(
                {**cfg, "private_projects": [ident]}, vault, sync.catalog(), sync.HOME
            )
            previously = "project/" + ident in rows or any(
                t in prior or (vault / "versions" / t).is_dir()
                for t, p in membership.members.items()
                if membership.links.wire(p) == membership.links.wire(ident)
            )
        if enabled:
            entries.add(ident)
        else:
            entries.discard(ident)
        cfg[key] = sorted(entries)
        sync.atomic(sync.CONFIG, sync.encode(cfg))
        # Do not apply a queued membership from before the sharing decision.
        if enabled:
            queue = sync.STATE / "sidebar-layout-pending.json"
            if not sync.read_json(queue, {}).get("undo"):
                queue.unlink(missing_ok=True)
            doc = sync.read_json(sync.STATE / "delivery.json", {})
            if doc.get("device") == cfg["device"]:
                private = Sharing(cfg, vault, sync.catalog(), sync.HOME).private_chats
                for thread in private:
                    doc["chats"][thread] = {
                        "status": "local_only",
                        "received_by": [],
                        "waiting_on": [],
                    }
                sync.atomic(sync.STATE / "delivery.json", sync.encode(doc))
        return {"kind": kind, "id": ident, "local_only": enabled, "previously_shared": previously}
