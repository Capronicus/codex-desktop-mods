import argparse
import json
import sys
import sqlite3
import subprocess
import time
import sync


def main():
    parser = argparse.ArgumentParser(description=sync.__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    setup = sub.add_parser("setup")
    setup.add_argument("folder")
    setup.add_argument("--automatic", action="store_true")
    sub.add_parser("sync")
    sub.add_parser("disable")
    sub.add_parser("watch")
    for action in ("keep-branch", "adopt"):
        copy = sub.add_parser(action)
        copy.add_argument("thread")
        copy.add_argument("device")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            result = sync.setup(args.folder, args.automatic)
        elif args.command == "watch":
            while sync.read_json(sync.CONFIG):
                try:
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
        elif args.command == "sync":
            result = sync.run()
        else:
            result = getattr(sync, args.command)()
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, sync.Busy, sqlite3.Error, EOFError, subprocess.SubprocessError) as exc:
        parser.exit(1, (str(exc) or "Another sync is in progress") + "\n")
