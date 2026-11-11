"""Optional activation receipt for one independently installed marketplace plugin."""
import argparse
from contextlib import closing
from http.client import HTTPConnection, HTTPSConnection, HTTPException
import json
import os
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
PLUGIN = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))["name"]
CHOICE_ERRORS = (OSError, ValueError, sqlite3.Error, HTTPException)


def profile():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "capronicus-activations"


def configured_service():
    url = os.environ.get("CAPRONICUS_ACTIVATION_URL")
    if url is None:
        for path in (profile() / "endpoint.txt", ROOT / "activation-endpoint.txt"):
            if path.is_file():
                url = path.read_text(encoding="utf-8").strip()
                break
    if not url:
        return None
    return service_origin(url)


def service_origin(url):
    parsed = urlsplit(url)
    loopback = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1")
    if (not parsed.hostname or (parsed.scheme != "https" and not loopback)
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or any(c.isspace() for c in url)):
        raise ValueError("Configure an HTTPS origin, or a loopback HTTP origin for testing.")
    port = parsed.port
    if port == 0:
        raise ValueError("Invalid counter port.")
    host = parsed.hostname.encode("idna").decode("ascii")
    authority = "[" + host + "]" if ":" in host else host
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        authority += ":" + str(port)
    return parsed.scheme + "://" + authority


def database():
    directory = profile()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "state.sqlite3", timeout=5)
    db.execute("CREATE TABLE IF NOT EXISTS activations (plugin TEXT, service TEXT, receipt TEXT,"
               " counted INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(plugin,service))")
    return db


def offer():
    """Return the one-time invitation. Optional local bookkeeping cannot fail the primary action."""
    try:
        service = configured_service()
        if service:
            with closing(database()) as db, db:
                inserted = db.execute("INSERT OR IGNORE INTO activations(plugin,service) VALUES (?,?)",
                                      (PLUGIN, service)).rowcount
            if inserted:
                return {"plugin": PLUGIN, "destination": service,
                        "message": "Count this installation? This sends the plugin name and a random "
                                   "receipt to " + service + ". Choose Count this installation or Skip."}
    except CHOICE_ERRORS:
        pass
    return None


def status():
    service = configured_service()
    state = "unconfigured"
    if service:
        state = "available"
        if (profile() / "state.sqlite3").is_file():
            with closing(database()) as db:
                row = db.execute("SELECT counted,receipt IS NOT NULL FROM activations "
                                 "WHERE plugin=? AND service=?", (PLUGIN, service)).fetchone()
            if row:
                state = "counted" if row[0] else "pending" if row[1] else "offered"
    return {"plugin": PLUGIN, "destination": service, "state": state}


def report(destination):
    service = configured_service()
    if not service or destination != service:
        raise ValueError("Check the configured counter address and confirm that destination first.")
    with closing(database()) as db:
        with db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO activations(plugin,service) VALUES (?,?)", (PLUGIN, service))
            receipt, counted = db.execute("SELECT receipt,counted FROM activations WHERE plugin=? "
                                          "AND service=?", (PLUGIN, service)).fetchone()
            if counted:
                return {"plugin": PLUGIN, "state": "counted"}
            if receipt is None:
                receipt = secrets.token_hex(32)
                db.execute("UPDATE activations SET receipt=? WHERE plugin=? AND service=?",
                           (receipt, PLUGIN, service))
        # Commit the receipt before sending. A timeout or simultaneous retry uses the same receipt.
        parsed = urlsplit(service)
        connection = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
        with closing(connection(parsed.hostname, parsed.port, timeout=6)) as conn:
            body = json.dumps({"plugin": PLUGIN, "receipt": receipt}).encode()
            conn.request("POST", "/v1/activations", body, {"Content-Type": "application/json"})
            response = conn.getresponse()
            if response.status != 200 or json.loads(response.read(512)) != {"accepted": True}:
                raise ValueError("The counter did not confirm the activation. You can retry.")
        with db:
            db.execute("UPDATE activations SET counted=1 WHERE plugin=? AND service=?", (PLUGIN, service))
    return {"plugin": PLUGIN, "state": "counted"}


TOOLS = [
    {"name": "activation_status", "description": "Check this plugin's local opt-in status and counter address.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
    {"name": "report_activation", "description": "Count this plugin once after the user opts in to "
     "sending its name and a random receipt to the displayed counter address.",
     "inputSchema": {"type": "object", "properties": {"destination": {"type": "string"}},
                     "required": ["destination"], "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "idempotentHint": True, "openWorldHint": True}},
]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("offer")
    commands.add_parser("status")
    commands.add_parser("count").add_argument("destination")
    args = parser.parse_args()
    try:
        if args.action == "count":
            result = report(args.destination)
        else:
            result = offer() if args.action == "offer" else status()
        print(json.dumps(result))
    except CHOICE_ERRORS as exc:
        parser.exit(1, str(exc) + "\n")
