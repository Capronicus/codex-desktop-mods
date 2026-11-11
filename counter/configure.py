"""Set the public counter address and README badges for a marketplace distribution."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from activation import service_origin


def configure(root, url):
    origin = service_origin(url)
    if not origin.startswith("https://"):
        raise ValueError("Use the public HTTPS origin for a marketplace release.")
    directories = {"Codex Desktop Mods": root, "Chat Sync": root / "plugins/chat-sync",
                   "Chat Organizer": root / "plugins/chat-organizer"}
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8")
    start = "<!-- activation-badges -->"
    end = "<!-- /activation-badges -->"
    if text.count(start) != 1 or text.count(end) != 1 or text.index(end) < text.index(start):
        raise ValueError("The README badge markers are missing or out of order.")
    badges = []
    for label, package in directories.items():
        plugin = "codex-desktop-mods" if package == root else package.name
        badges.append(f"{label} [![{label} reported activations]({origin}/badges/{plugin}.svg)]({origin}/v1/counts)")
    replacement = start + "\n\n" + "\n\n".join(badges) + "\n\n" + end
    for directory in directories.values():
        (directory / "activation-endpoint.txt").write_text(origin + "\n", encoding="utf-8")
    readme.write_text(text[:text.index(start)] + replacement + text[text.index(end)+len(end):], encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("https_origin")
    args = parser.parse_args()
    try:
        configure(ROOT, args.https_origin)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
