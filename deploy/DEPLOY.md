# Deploy plaid-mcp-server to Azure VM

Target: `assistant-vm.eastus.cloudapp.azure.com`. Port **7777**. Route `/plaid/mcp`.

## 1. Create remote repo and push

From local:

```bash
cd /Users/varunsashidharan/Assistant/mcp-servers/plaid-mcp-server
git remote add origin git@github.com:<user>/plaid-mcp-server.git   # create the repo in the GitHub UI first
git push -u origin main
```

## 2. Clone + install on VM

```bash
ssh ubuntu@assistant-vm.eastus.cloudapp.azure.com
mkdir -p ~/assistant/mcp-servers && cd ~/assistant/mcp-servers
git clone git@github.com:<user>/plaid-mcp-server.git
cd plaid-mcp-server
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e .
```

If `python3.12` is missing: `sudo apt install -y python3.12 python3.12-venv`.

## 3. Create `.env.agent` on VM

```bash
cat > .env.agent <<'EOF'
PLAID_ENV=sandbox
PLAID_CLIENT_ID=<plaid-client-id>
PLAID_SECRET=<plaid-secret>           # from Plaid dashboard, matching PLAID_ENV
ENCRYPTION_KEY=<fernet-key>           # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
STORAGE_MODE=file
MCP_TRANSPORT=streamable-http
MCP_PORT=7777
EOF
chmod 600 .env.agent
```

Flip `PLAID_ENV=production` + production secret once Plaid approves production access, then re-run `python scripts/link_flow.py` locally against production (cannot run headless on VM) and copy the resulting `data/items/<item_id>/` directory over.

## 4. Install systemd unit

```bash
sudo cp deploy/mcp-plaid.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mcp-plaid
sudo systemctl status mcp-plaid
journalctl -u mcp-plaid -f --since "1 minute ago"
```

## 5. Nginx route

Append the contents of `deploy/nginx-plaid.conf` to the main `server {}` block in `/etc/nginx/sites-enabled/assistant` (or wherever the existing MCP routes live — mirror them).

```bash
sudo nginx -t && sudo systemctl reload nginx
```

## 6. Register with auth gateway

The existing `mcp-auth-gateway` (port 8000) needs to know about the new resource. How this is done depends on the gateway's config format — check the gateway's repo for how `splitwise`, `garmin`, etc. are registered and follow the same pattern. Typically a `resources.yaml` or similar listing `plaid` with its URL.

Restart gateway after edit:
```bash
sudo systemctl restart mcp-auth-gateway
```

## 7. Client side: add to `.mcp.json`

Only after verifying the health endpoint responds through nginx. Add to `/Users/varunsashidharan/Assistant/.mcp.json`:

```json
"plaid": {
  "type": "http",
  "url": "https://assistant-vm.eastus.cloudapp.azure.com/plaid/mcp"
}
```

## 8. Smoke test

From Claude Code client after restart:

```
mcp__plaid__get_balance(item_id="<item_id>")
```

(Will fail with "no items" until `link_flow.py` has been run against this environment.)

## Running the Plaid Link flow

`scripts/link_flow.py` must be run **locally** (not on the VM) because it opens a browser. The flow:

1. `PLAID_ENV=production` in a local `.env.agent`
2. `python scripts/link_flow.py`
3. Complete the real bank login in the browser
4. Script writes `data/items/<item_id>/token.json` (AES-encrypted)
5. `scp -r data ubuntu@assistant-vm.eastus.cloudapp.azure.com:~/assistant/mcp-servers/plaid-mcp-server/`
6. `sudo systemctl restart mcp-plaid`

The `ENCRYPTION_KEY` must match between local and VM for the token to decrypt — hence the same key in both `.env.agent` files.
