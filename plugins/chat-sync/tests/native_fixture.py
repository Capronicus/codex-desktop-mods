from pathlib import Path
import json, subprocess, os

ROOT = Path(__file__).resolve().parent


def prepare(home, port):
    home.mkdir(exist_ok=True, parents=True)
    (home / "config.toml").write_text(
        f'model_provider="local-qa"\nmodel="offline-test"\n[model_providers.local-qa]\nname="Offline"\nbase_url="http://127.0.0.1:{port}/v1"\nwire_api="responses"\n'
    )
    (home / "auth.json").write_text(
        json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "offline-fixture"})
    )


class App:
    def __init__(self, home):
        self.log = (home / "qa.log").open("a", encoding="utf-8")
        self.p = subprocess.Popen(
            [os.environ.get("CODEX_CHAT_SYNC_CLI", "codex"), "app-server", "--listen", "stdio://"],
            stdin=-1,
            stdout=-1,
            stderr=self.log,
            text=True,
            encoding="utf-8",
            env={**os.environ, "CODEX_HOME": str(home)},
        )
        self.i = 0
        self.call(
            "initialize",
            {
                "clientInfo": {"name": "codex_vscode", "version": "1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )

    def call(self, method, args):
        self.i += 1
        self.p.stdin.write(json.dumps({"id": self.i, "method": method, "params": args}) + "\n")
        self.p.stdin.flush()
        while line := self.p.stdout.readline():
            msg = json.loads(line)
            if msg.get("id") == self.i:
                if "error" in msg:
                    raise ValueError(msg["error"])
                return msg["result"]
        raise RuntimeError("server exited")

    def turn(self, tid, prompt):
        self.call("turn/start", {"threadId": tid, "input": [{"type": "text", "text": prompt}]})
        for line in self.p.stdout:
            m = json.loads(line)
            if m.get("method") == "turn/completed":
                assert m["params"]["turn"]["status"] == "completed", m
                return
        raise RuntimeError("Server exited before turn completion")

    def close(self):
        self.p.stdin.close()
        try:
            self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.terminate()
            self.p.wait()
        finally:
            self.p.stdout.close()
            self.log.close()
