"""Install the marketplace into an empty profile and discover both servers with the desktop CLI."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins/chat-sync/tests"))
from native_fixture import App, prepare


def install_and_check_servers(cli, home):
    with patch.dict(os.environ, {"CODEX_HOME": str(home), "CODEX_CHAT_SYNC_CLI": str(cli)}):
        for args in (["marketplace", "add", str(ROOT)],
                     ["add", "chat-sync@capronicus-desktop-mods"],
                     ["add", "codex-desktop-mods@capronicus-desktop-mods"]):
            subprocess.run([str(cli), "plugin", *args], check=True)
        app = App(home)
        try:
            result = app.call("mcpServerStatus/list", {"detail": "full", "limit": 100})
            servers = {item["name"]: item for item in result["data"]}
            assert "desktop_mods_status" in servers["desktop-mods"]["tools"]
            tool = servers["chat-sync"]["tools"]["open_mod_controls"]
            meta = tool["_meta"]
            assert {"type": "global"} in meta["openai/ui"]["entrypoints"]
            thread = app.call("thread/start", {"ephemeral": True, "cwd": str(ROOT)})["thread"]["id"]
            params = {"threadId": thread, "server": "chat-sync"}
            panel = app.call("mcpServer/tool/call", {**params, "tool": tool["name"], "arguments": {}})
            assert not panel.get("isError") and panel["content"][0]["text"] == "Mod Controls"
            ui = app.call("mcpServer/resource/read", {**params, "uri": meta["ui"]["resourceUri"]})
            assert "Desktop appearance" in ui["contents"][0]["text"]
            print("Fresh marketplace installation served Mod Controls and discovered Desktop Mods.")
        finally:
            app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cli", type=Path)
    parser.add_argument('profile', type=Path, help='New directory for the disposable test profile')
    args = parser.parse_args()
    home = args.profile.resolve()
    home.mkdir(parents=True, exist_ok=False)
    prepare(home, 1)
    install_and_check_servers(args.cli.resolve(), home)
