"""Account-local message bookmarks; never written to the sync folder."""
import hashlib
import json
import time
import uuid

import projects
import sync


def account_id():
    return (sync.read_json(sync.HOME / "auth.json", {}).get("tokens") or {}).get("account_id") or ""


def location():
    account = account_id()
    scope = hashlib.sha256(account.encode()).hexdigest()
    return sync.STATE / ("bookmarks-" + scope + ".json")


def list_marks():
    return sorted(sync.read_json(location(), {}).values(), key=lambda b:-b["created"])


def edit(action, ident, label=""):
    if not projects.valid_id(ident): raise ValueError("Invalid bookmark.")
    with sync.lock(sync.STATE / "bookmark.lock"):
        path = location()
        marks = sync.read_json(path, {})
        if action == "add":
            from chat_actions import desktop_context
            ctx = desktop_context()
            if ctx.get("action") != "new-bookmark" or ctx.get("id") != ident or ctx.get("account") != account_id():
                raise ValueError("Select the message again to bookmark it.")
            key, start, end, fingerprint = json.loads(ctx["anchor"])
            address = json.loads(key)
            if not isinstance(address,list) or address[0] not in ("local","cloud"):
                raise ValueError("Invalid message reference.")
            marks.setdefault(ident, {"id":ident,"anchor":ctx["anchor"],"thread":ctx.get("thread",""),
                                     "turn":ctx.get("turn",""),"item":ctx.get("item",""),"cloud":address[0]=="cloud",
                                     "created":int(time.time()*1000)})
        if ident not in marks: raise ValueError("This bookmark is unavailable.")
        if action in ("add","rename"):
            if not label.strip() or len(label)>160: raise ValueError("Name the bookmark using up to 160 characters.")
            marks[ident]["label"]=label.strip()
        elif action=="remove": del marks[ident]
        elif action=="open":
            b=marks[ident]
            sync.atomic(sync.STATE / "desktop-command.json",sync.encode({**b,"id":str(uuid.uuid4()),"account":account_id(),
                        "action":"bookmark","at":int(time.time()*1000)}))
            return {"requested":True}
        else: raise ValueError("Unknown bookmark action.")
        sync.atomic(path,sync.encode(marks))
        return {"saved":True}
