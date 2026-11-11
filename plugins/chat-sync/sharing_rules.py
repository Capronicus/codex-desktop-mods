"""Device-owned revisions of shared project and checkout-relative exclusions."""
from pathlib import Path

import projects
import sync


def cache(cfg):
    return sync.STATE / ("shared-exclusions-" + cfg["device"] + ".json")


def parse(key):
    pid, _, relative = key.partition("/")
    if not projects.valid_id(pid) or len(key) > 4096:
        raise ValueError("Invalid shared project rule.")
    if relative and (relative.startswith("/") or any(part in ("", ".", "..") or ":" in part or "\\" in part
                                                    for part in relative.split("/"))):
        raise ValueError("Invalid project-relative folder.")
    return pid, relative


def records(cfg, vault):
    result = sync.read_json(cache(cfg), {})
    # Preserve the last complete rules during delayed provider delivery.
    docs = [(cfg["device"], cfg.get("shared_exclusions", {}))]
    for file in (vault/"sharing-rules-v1").glob("*.json"):
        if projects.valid_id(file.stem):
            docs.append((file.stem, sync.read_json(file)))
    for writer, doc in docs:
        if not isinstance(doc, dict):
            raise ValueError("Waiting for complete project sharing rules.")
        for key, row in doc.items():
            parse(key)
            if not (isinstance(row,list) and len(row)==4 and type(row[0]) is int and row[0]>0 and
                    type(row[1]) is bool and row[2]==writer and isinstance(row[3],list) and
                    all(projects.valid_id(i) for i in row[3])):
                raise ValueError("Waiting for a valid project sharing rule.")
            if key not in result or row[:3] > result[key][:3]:
                result[key]=row
    return result


def canonical(rows, links):
    result = {}
    for key,row in rows.items():
        project,relative=parse(key)
        key=links.wire(project)+("/"+relative if relative else "")
        if key not in result or row[:3] > result[key][:3]:
            result[key]=row
    return result


def project_roots(cfg, local, links):
    roots={}
    for pid, p in local.items():
        root = links.checkouts.get(links.wire(pid))
        if not root and len(p.get("folders",[]))==1:
            root=p["folders"][0]
        if root and Path(root).is_absolute():
            roots[links.wire(pid)] = Path(root).resolve()
    return roots


def exchange(cfg, vault):
    own=cfg.get("shared_exclusions",{})
    if own:
        path=vault/"sharing-rules-v1"/(cfg["device"]+".json")
        if sync.read_json(path,{})!=own:
            sync.atomic(path,sync.encode(own))
    rows=records(cfg,vault)
    if rows and rows!=sync.read_json(cache(cfg),{}):
        sync.atomic(cache(cfg),sync.encode(rows))


def set_rule(kind, ident, enabled):
    if type(enabled) is not bool or kind not in ("project","folder","rule"):
        raise ValueError("Choose a project or a folder in a linked checkout.")
    with sync.lock(sync.STATE/"sync.lock"):
        cfg,vault=projects.config()
        if not vault.is_dir():
            raise ValueError("Reconnect your sync folder before changing a shared rule.")
        local=projects.local_projects(sync.HOME)
        links=projects.Links(cfg,vault,local)
        rows=canonical(records(cfg,vault),links)
        roots=project_roots(cfg,local,links)
        if kind=="rule":
            if enabled or ident not in rows:
                raise ValueError("Choose an existing shared exclusion.")
            key=ident
        elif kind=="project":
            if ident not in local:raise ValueError("Choose a local project.")
            key=links.wire(ident)
        else:
            folder=Path(ident).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError("Choose an existing absolute folder.")
            folder=folder.resolve()
            choices=[(len(root.parts),pid,root) for pid,root in roots.items() if folder==root or root in folder.parents]
            if not choices:raise ValueError("For all PCs, choose a folder inside a project with a known checkout.")
            choices.sort(reverse=True)
            if len(choices)>1 and choices[0][0]==choices[1][0]:
                raise ValueError("This folder belongs to multiple projects. Choose the project explicitly.")
            _,pid,root=choices[0]
            key=pid if folder==root else pid+"/"+folder.relative_to(root).as_posix()
        pid,relative=parse(key)
        index=sync.catalog()
        scope=projects.Sharing(cfg,vault,index,sync.HOME)
        sealed=set()
        state=sync.read_json(sync.STATE/("state-"+cfg["device"]+".json"),{})
        if enabled:
            for tid,row in index.items():
                same_project=links.wire(scope.members.get(tid) or row.get("project_id"))==pid
                under=pid in roots and projects.folder_excluded(row.get("cwd"),{"private_folders":[str(roots[pid]/relative)]})
                if (under or (not relative and same_project)) and (tid in state or (vault/"versions"/tid).is_dir()):
                    sealed.add(tid)
        if enabled and rows.get(key,[0,False])[1]:
            sealed.update(rows[key][3])
        rev=rows.get(key,[0])[0]+1
        cfg.setdefault("shared_exclusions",{})[key]=[rev,enabled,cfg["device"],sorted(sealed)]
        sync.atomic(sync.CONFIG,sync.encode(cfg))
        if not cfg.get("paused"):exchange(cfg,vault)
        if enabled:
            queue=sync.STATE/"sidebar-layout-pending.json"
            if not sync.read_json(queue,{}).get("undo"):queue.unlink(missing_ok=True)
        return {"key":key,"excluded":enabled,"queued":bool(cfg.get("paused"))}
