# Tool-Plaid: MCP Server for Plaid

MCP (Model Context Protocol) server tool that exposes two tools for interacting with Plaid's financial data API:
- `get_transactions_by_date` - Fetch transactions for an explicit date range
- `get_balance` - Get account balances

## Installation

### Development Installation

```bash
# Clone the repository
git clone https://github.com/reilly3000/tool-plaid.git
cd tool-plaid

# Install in development mode
pip install -e .

# Or with uv (recommended)
uv pip install -e .
```

### Production Installation

```bash
pip install tool-plaid
# or with uv
uv pip install tool-plaid
```

## Configuration

### Environment Variables

Create a `.env.agent` file in the project root (ignored by git) or set environment variables:

```bash
# Plaid Configuration
PLAID_ENV=sandbox|production
PLAID_CLIENT_ID=<your_client_id>
PLAID_SECRET=<your_secret>

# Security
ENCRYPTION_KEY=<your_32_byte_encryption_key>

# Storage
STORAGE_MODE=file|postgres
DATABASE_URL=postgresql://user:pass@host:port/dbname  # required if STORAGE_MODE=postgres

# MCP Server
MCP_TRANSPORT=stdio|streamable-http
MCP_PORT=8000

# Caching (optional)
BALANCE_CACHE_TTL=300  # seconds, default: 300 (5 minutes)

# Self-serve connect (#146, optional -- only needed if mcp-auth-gateway's
# hosted Plaid Link page is in use). Gates two localhost-only FastMCP
# custom routes, POST /internal/link-token and POST /internal/exchange,
# that the gateway calls to mint a Link token and complete an exchange
# without going through the MCP protocol at all (a plain browser POST has
# no MCP Context/X-User-Id header). Never exposed via nginx; this header
# check is defense-in-depth on top of that network boundary. See
# imessage-bot's SELF_SERVE_AUTH_PLAN.md for the full flow.
PLAID_INTERNAL_SECRET=<shared secret, generate with: openssl rand -base64 32>
```

### Example .env File

```bash
# Sandbox Environment
PLAID_ENV=sandbox
PLAID_CLIENT_ID=62eacf714206f30013d6e722
PLAID_SECRET=6f85aa5808d484246313470945c515

# Generate a random 32-byte key
ENCRYPTION_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Start with file storage
STORAGE_MODE=file
```

## Usage

### Running the Server

#### Local Development (STDIO)

```bash
# Set environment
export PLAID_ENV=sandbox
export PLAID_CLIENT_ID=<your_client_id>
export PLAID_SECRET=<your_secret>
export STORAGE_MODE=file

# Run with uv
uv run plaid-tool

# Or directly
python -m tool_plaid
```

#### Production (Streamable HTTP)

```bash
export PLAID_ENV=production
export DATABASE_URL=postgresql://...
export STORAGE_MODE=postgres
export MCP_TRANSPORT=streamable-http

# Run HTTP server
plaid-tool
```

### Configuration with Claude Desktop

Add this to your MCP configuration file (e.g., `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "plaid": {
      "command": "plaid-tool",
      "env": {
        "PLAID_ENV": "sandbox"
      }
    }
  }
}
```

## Tools

### get_transactions_by_date

Fetch transactions for an explicit date range via Plaid's `/transactions/get`. Stateless — no server-side cursor or bookkeeping; re-querying any past window (even one already fetched) always returns the same data, which makes it safe to re-check for retroactively-settled/backdated transactions. Pagination is handled internally.

**Parameters:**
- `item_id` (required): Plaid item identifier
- `start_date` (required): Start date, inclusive (YYYY-MM-DD)
- `end_date` (required): End date, inclusive (YYYY-MM-DD)

**Returns:**
```json
{
  "transactions": [
    {
      "transaction_id": "string",
      "account_id": "string",
      "amount": 100.50,
      "date": "2025-01-15",
      "merchant_name": "Merchant Name",
      "category": "Food and Drink",
      "pending": false
    }
  ],
  "total_transactions": 42,
  "item_status": "OK",
  "summary": "42 of 42 transactions (2025-01-01..2025-01-31)",
  "skipped_count": 0
}
```

### get_balance

Get account balances with intelligent caching.

**Parameters:**
- `item_id` (required): Plaid item identifier
- `account_ids` (optional, default=None): Filter specific accounts
- `force_refresh` (optional, default=False): Bypass cache

**Returns:**
```json
{
  "balances": [
    {
      "account_id": "string",
      "name": "Plaid Checking",
      "mask": "0000",
      "type": "depository",
      "available": 1000.00,
      "current": 1250.00,
      "iso_currency_code": "USD"
    }
  ],
  "cached": true,
  "timestamp": "2025-01-15T10:30:00Z"
}
```

## Architecture

### Storage Modes

**File Storage (Development)**
- JSONL files for transactions
- Simple key-value cursor tracking
- Text-based balance snapshots
- Directory: `data/items/{item_id}/`

**PostgreSQL (Production)**
- Async PostgreSQL with asyncpgres
- Tables: `access_tokens`, `transactions`, `balances`, `cursors`
- Connection pooling
- Migrations via alembic

### Security

- AES-256 encryption for access tokens at rest
- Encryption key from environment variable
- No logging of sensitive data (tokens, raw responses)
- Audit logging without sensitive data

## Development

### Running Tests

```bash
# Unit tests
pytest tests/ -v

# With coverage
pytest --cov=tool_plaid --cov-report=html
```

### End-to-End Testing (Sandbox)

1. Open `tests/plaid_link_test.html` in a browser
2. Link a test bank using credentials: `user_good` / `pass_good`
3. Copy the public token and run:
```bash
python tests/test_e2e.py <PUBLIC_TOKEN>
```

Note: Public tokens expire after ~30 minutes.

### Quick Verification

```bash
# Check config loads
python -c "from tool_plaid.config import Config; c = Config.load(); print(c.PLAID_CLIENT_ID)"

# Test encryption
python -c "from tool_plaid.utils.encryption import Encryptor; from tool_plaid.config import Config; e = Encryptor(Config.load().ENCRYPTION_KEY); print(e.decrypt(e.encrypt('test')))"
```

### Code Quality

```bash
# Format code
black tool_plaid

# Lint
ruff check tool_plaid

# Type check
mypy tool_plaid
```

## License

MIT

## Support

For issues and questions, please open an issue on GitHub.
