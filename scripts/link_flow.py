"""One-shot Plaid Link onboarding.

Creates a link_token, serves a local page that runs Plaid Link,
captures the public_token on success, exchanges it, and stores
the encrypted access_token via the existing TokenManager.

Usage: python scripts/link_flow.py [--institution-name "First Tech FCU"]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tool_plaid.auth.tokens import TokenManager  # noqa: E402
from tool_plaid.config import Config  # noqa: E402
from tool_plaid.plaid.client import PlaidClient  # noqa: E402

PORT = 8765


PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Plaid Link</title>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<style>body{font:16px -apple-system,sans-serif;max-width:640px;margin:60px auto;padding:20px}
button{background:#111;color:#fff;border:0;padding:12px 24px;font-size:16px;border-radius:4px;cursor:pointer}
.ok{background:#e8f5e9;padding:12px;border-left:4px solid #4caf50;margin-top:20px;display:none}
.err{background:#ffebee;padding:12px;border-left:4px solid #f44336;margin-top:20px;display:none}
code{background:#f0f0f0;padding:2px 6px;border-radius:3px}</style></head>
<body>
<h1>Plaid Link (__ENV__)</h1>
<p>Credentials for sandbox test banks: <code>user_good</code> / <code>pass_good</code></p>
<button id="go">Open Plaid Link</button>
<div id="ok" class="ok">Linked. You can close this tab.</div>
<div id="err" class="err"></div>
<script>
const handler = Plaid.create({
  token: "__LINK_TOKEN__",
  onSuccess: async (public_token, metadata) => {
    await fetch("/callback", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({public_token, metadata})});
    document.getElementById("ok").style.display="block";
  },
  onExit: (err) => {
    if (err) {
      document.getElementById("err").textContent = "Link exited: " + JSON.stringify(err);
      document.getElementById("err").style.display="block";
    }
  }
});
document.getElementById("go").onclick = () => handler.open();
</script></body></html>
"""


async def create_link_token(plaid: PlaidClient, user_id: str) -> str:
    request = {
        "user": {"client_user_id": user_id},
        "client_name": "tool-plaid",
        "products": ["transactions"],
        "country_codes": ["US"],
        "language": "en",
    }
    resp = await asyncio.to_thread(plaid.api_client.link_token_create, request)
    return resp["link_token"]


def run_server(page_html: str, result: dict) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a, **k):  # silence
            pass

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = page_html.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/callback":
                length = int(self.headers.get("Content-Length", "0"))
                data = json.loads(self.rfile.read(length))
                result["public_token"] = data["public_token"]
                result["metadata"] = data.get("metadata", {})
                self.send_response(204)
                self.end_headers()
                # schedule shutdown after response
                import threading
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_response(404)
                self.end_headers()

    httpd = HTTPServer(("127.0.0.1", PORT), Handler)
    httpd.serve_forever()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", default="varun-local")
    args = parser.parse_args()

    config = Config.load()
    config.validate()
    plaid = PlaidClient(config)

    print(f"Creating link_token (env={config.PLAID_ENV})...")
    link_token = await create_link_token(plaid, args.user_id)
    print("link_token created.")

    page = PAGE_TEMPLATE.replace("__LINK_TOKEN__", link_token).replace("__ENV__", config.PLAID_ENV)

    result: dict = {}
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Opening {url} — complete the Link flow in your browser.")
    webbrowser.open(url)

    # Block on server until /callback shuts it down
    await asyncio.to_thread(run_server, page, result)

    if "public_token" not in result:
        print("No public_token received. Aborting.")
        return

    print("Exchanging public_token for access_token...")
    exchanged = await plaid.exchange_public_token(result["public_token"])
    access_token = exchanged["access_token"]
    item_id = exchanged["item_id"]

    meta = result.get("metadata") or {}
    inst = (meta.get("institution") or {}) if isinstance(meta, dict) else {}
    flat_meta = {
        "institution_name": str(inst.get("name", "")),
        "institution_id": str(inst.get("institution_id", "")),
    }

    tm = TokenManager(config.data_dir, config.ENCRYPTION_KEY)
    await tm.store_token(
        access_token=access_token,
        item_id=item_id,
        metadata=flat_meta,
    )
    print(f"Stored encrypted token for item_id={item_id}")
    print(f"Institution: {flat_meta['institution_name'] or 'n/a'}")


if __name__ == "__main__":
    asyncio.run(main())
