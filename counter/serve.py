"""Self-hosted opt-in activation counter for the Capronicus desktop plugins."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3


PLUGINS = ("codex-desktop-mods", "chat-sync", "chat-organizer")


def create_app(database):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS activations (plugin TEXT NOT NULL, digest BLOB NOT NULL,"
                   " PRIMARY KEY(plugin,digest)) WITHOUT ROWID")

    def application(env, start_response):
        def respond(status, data, mime="application/json", public=False):
            body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            start_response(status, [("Content-Type", mime), ("Content-Length", str(len(body))),
                                    ("Cache-Control", "public, max-age=60" if public else "no-store"),
                                    ("X-Content-Type-Options", "nosniff")])
            return [body]

        path = env["PATH_INFO"]
        if path == "/v1/activations" and env["REQUEST_METHOD"] == "POST":
            if env.get("CONTENT_TYPE", "").partition(";")[0].strip().lower() != "application/json":
                return respond("415 Unsupported Media Type", {"error": "Expected application/json"})
            try:
                size = int(env.get("CONTENT_LENGTH") or 0)
                if not 0 < size <= 512:
                    return respond("400 Bad Request", {"error": "Expected a small JSON object"})
                data = json.loads(env["wsgi.input"].read(size))
                if (not isinstance(data, dict) or set(data) != {"plugin", "receipt"}
                        or data["plugin"] not in PLUGINS or not isinstance(data["receipt"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", data["receipt"])):
                    raise ValueError
            except (UnicodeError, ValueError, RecursionError):
                return respond("400 Bad Request", {"error": "Invalid activation"})
            digest = hashlib.sha256((data["plugin"] + "/" + data["receipt"]).encode()).digest()
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("INSERT OR IGNORE INTO activations(plugin,digest) VALUES (?,?)",
                           (data["plugin"], digest))
            return respond("200 OK", {"accepted": True})

        badge = re.fullmatch(r"/badges/([a-z-]+)\.svg", path)
        if env["REQUEST_METHOD"] == "GET" and (path == "/v1/counts" or badge and badge[1] in PLUGINS):
            with closing(sqlite3.connect(database)) as db:
                counts = dict.fromkeys(PLUGINS, 0)
                counts.update(db.execute("SELECT plugin,count(*) FROM activations GROUP BY plugin"))
            if badge:
                plugin, number = badge[1], counts[badge[1]]
                label = "reported activations"
                right = max(32, 12 + len(str(number)) * 8)
                svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{126+right}" height="20" '
                       f'role="img" aria-label="{plugin}: {number} {label}"><title>{plugin}: {number} {label}</title>'
                       f'<rect width="126" height="20" fill="#394454"/><rect x="126" width="{right}" height="20" fill="#2f6464"/>'
                       '<g fill="white" text-anchor="middle" font-family="Verdana,sans-serif" font-size="11">'
                       f'<text x="63" y="14">{label}</text><text x="{126+right/2}" y="14">{number}</text></g></svg>')
                return respond("200 OK", svg, "image/svg+xml", public=True)
            return respond("200 OK", counts, public=True)
        return respond("404 Not Found", {"error": "Unknown route"})

    return application


if __name__ == "__main__":
    from waitress import serve

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8041)
    args = parser.parse_args()
    serve(create_app(args.database), host=args.listen, port=args.port,
          max_request_body_size=512, max_request_header_size=8192)
