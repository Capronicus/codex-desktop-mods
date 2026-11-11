import argparse
import json
import sys
import sqlite3
import subprocess
import time
import sync
import projects, delivery, batches
import integrity


def main():
    parser = argparse.ArgumentParser(description=sync.__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    setup = sub.add_parser("setup")
    setup.add_argument("folder")
    setup.add_argument("--automatic", action="store_true")
    sub.add_parser("sync")
    sub.add_parser("check-folder")
    sub.add_parser("repair-folder")
    sub.add_parser("sync-colours")
    sub.add_parser("sync-settings")
    sub.add_parser("settings").add_argument("mode", choices=("on", "off"))
    sub.add_parser("disable")
    sub.add_parser("pause")
    sub.add_parser("resume")
    sub.add_parser("watch")
    sub.add_parser("sidebar").add_argument("mode", choices=("on", "off"))
    sub.add_parser("colours").add_argument("mode", choices=("on", "off"))
    sub.add_parser("order").add_argument("mode", choices=("on", "off"))
    for command, fields in {
        "projects": [],
        "link": ["local_project", "shared_project", "folder"],
        "receipt": ["thread"],
        "name": ["name"],
        "preview": [],
        "apply-preview": ["batch"],
        "undo": [],
    }.items():
        command_parser = sub.add_parser(command)
        for field in fields:
            command_parser.add_argument(field)
    privacy = sub.add_parser("local-only")
    privacy.add_argument("kind", choices=("chat", "project"))
    privacy.add_argument("ident")
    privacy.add_argument("mode", choices=("on", "off"))
    for action in ("keep-branch", "adopt"):
        copy = sub.add_parser(action)
        copy.add_argument("thread")
        copy.add_argument("device")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            result = sync.setup(args.folder, args.automatic)
        elif args.command in ("sidebar", "colours", "order", "settings"):
            result = sync.set_exchange_option(args.command, args.mode == "on")
        elif args.command in ("pause", "resume"):
            result = sync.set_paused(args.command == "pause")
        elif args.command == "local-only":
            result = projects.set_private(args.kind, args.ident, args.mode == "on")
        elif args.command in (
            "projects",
            "link",
            "receipt",
            "name",
            "preview",
            "apply-preview",
            "undo",
        ):
            actions = {
                "projects": projects.inventory,
                "link": projects.link,
                "receipt": delivery.status,
                "name": delivery.set_name,
                "preview": batches.review,
                "apply-preview": lambda batch: batches.review("apply", batch),
                "undo": batches.undo,
            }
            result = actions[args.command](
                **{k: v for k, v in vars(args).items() if k != "command"}
            )
        elif args.command == "watch":
            while cfg := sync.read_json(sync.CONFIG):
                try:
                    if not cfg.get("paused"):
                        print(json.dumps(sync.run()), flush=True)
                except (
                    sync.Busy,
                    OSError,
                    ValueError,
                    sqlite3.Error,
                    EOFError,
                    subprocess.SubprocessError,
                ) as exc:
                    print(str(exc) or "Busy", file=sys.stderr)
                time.sleep(60)
            return
        elif args.command == "keep-branch":
            result = sync.keep_branch(args.thread, args.device)
        elif args.command == "adopt":
            result = sync.adopt_history(args.thread, args.device)
        elif args.command == "sync-settings":
            result = sync.sync_settings_now()
        elif args.command == "sync-colours":
            result = sync.sync_colours_now()
        elif args.command in ("check-folder", "repair-folder"):
            result = integrity.inspect_folder(repair=args.command == "repair-folder")
        elif args.command == "sync":
            result = sync.run()
        else:
            result = getattr(sync, args.command)()
        print(json.dumps(result, indent=2))
    except (
        ValueError,
        OSError,
        sync.Busy,
        sqlite3.Error,
        EOFError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(1, (str(exc) or "Another sync is in progress") + "\n")
