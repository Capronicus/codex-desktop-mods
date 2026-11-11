# Self-host the activation counter

The collector exposes per-plugin totals and SVG badges. It counts a profile once per plugin when its user chooses **Count this installation**. Chat Sync offers the choice after a successful manual sync, Desktop Mods after applying the patch, and Chat Organizer after a cleanup preview when Python 3.9+ is available. An installation, an automatic sync, a status check and **Skip** send zero requests.

## Run the collector

From this repository on a machine with Python 3.9+, venv and pip:

```sh
python3 -m venv .counter-venv
.counter-venv/bin/python -m pip install -r counter/requirements.txt
mkdir -p "$HOME/.local/share/capronicus-counter"
.counter-venv/bin/python counter/serve.py --database "$HOME/.local/share/capronicus-counter/counts.sqlite3"
```

The service listens on `127.0.0.1:8041`. On Windows use `.counter-venv\Scripts\python.exe` and a persistent database path. Keep the process running with your service manager. Put your HTTPS reverse proxy in front of it. For example, a Caddy site can serve all three routes from a dedicated hostname:

```caddyfile
counts.example.org {
    reverse_proxy 127.0.0.1:8041
}
```

Replace the example hostname with one you control. Open `/v1/counts` to see the three totals, initially zero. The JSON endpoint and `/badges/chat-sync.svg`, `/badges/chat-organizer.svg` and `/badges/codex-desktop-mods.svg` are public. `POST /v1/activations` accepts a plugin name and a random receipt. Keep the SQLite database on persistent storage. Stop the service before making a filesystem copy for backup, or use SQLite’s online backup command.

## Connect the plugins

For a marketplace release, run this from the source checkout with your actual public origin:

```sh
python3 counter/configure.py https://counts.example.org
```

This writes `activation-endpoint.txt` into each of the three independent packages and inserts the three badge links in the root README. Include those changes in the marketplace update. Each installed copy then knows the address without any discovery request.

To connect just your own desktop, put the origin on one line in `capronicus-activations/endpoint.txt` under the Codex profile directory (normally `~/.codex`). Alternatively set `CAPRONICUS_ACTIVATION_URL` in the desktop process environment. The environment takes precedence over the profile file, then the package file. For a single-machine trial you can use `http://127.0.0.1:8041`. An unconfigured plugin stays silent.

To check or revisit a choice, ask the plugin for its activation status. Every package also has the same optional CLI. From that plugin’s installed directory run `python3 activation.py status`, then `python3 activation.py count https://counts.example.org` if you want to be counted. Use the exact origin shown in status. On Windows the equivalent is `py -3 activation.py`.

## What the count means

The client generates a 256-bit random receipt when someone opts in. Receipts are separate for each plugin, profile and counter origin, survive plugin updates, and stay outside the Chat Sync vault. The client sends only the plugin name and receipt in the request body. A failed request can be retried explicitly with the same receipt, including if the server recorded the first request and its reply was lost. There are no background activation retries.

The collector stores the plugin name and a digest of the receipt. The public endpoints expose aggregate totals. As with any HTTPS endpoint the host sees incoming network connections. Leave access and request-body logging disabled on your proxy and hosting platform if you want to avoid retaining IP addresses and receipts. A fresh profile can opt in again. These are voluntary reports, so they do not measure unique people, current installations or fraud-resistant usage.

The copy of `activation.py` at the repository root is the client source. When changing it, copy it into both standalone plugin directories and run `python3 tests/activations.py` to verify package parity and the opt-in flow.
