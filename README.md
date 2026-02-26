# agent-mcp-config

Email agent using **Claude Agent SDK** (`ClaudeAgentOptions` + `query()`) with an HTTP MCP server deployed on AWS App Runner that sends emails via AWS SES.

## Architecture

```
POST /query  (or WS /ws)
    → main.py  (FastAPI)
        → agent.py  (reads .claude/settings.json → ClaudeAgentOptions)
            → bundled claude.exe subprocess  (--print mode, no streaming)
                → HTTP MCP call  →  App Runner (email_mcp_lambda.py)
                                 →  AWS Lambda (sendEmailAlert)
                                 →  AWS SES  →  Email
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI server — REST (`/query`) + WebSocket (`/ws`) |
| `agent.py` | Reads `settings.json`, builds `ClaudeAgentOptions`, runs `query()` |
| `email_mcp_lambda.py` | HTTP MCP server deployed on AWS App Runner — bridges agent to SES Lambda |
| `test_local.py` | Windows-friendly test script using Anthropic API directly (bypasses subprocess) |
| `.claude/settings.json` | **Single source of truth** — defines all MCP servers |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variable template |

## How it works

- `query()` (non-streaming) is used instead of `ClaudeSDKClient`.
  Streaming mode requires an `initialize` handshake that times out on Windows in a uvicorn context.
- `agent.py` reads `.claude/settings.json` at runtime and passes MCP servers to `ClaudeAgentOptions` as `--mcp-config`. The bundled `claude.exe` (v2.1.1) does not auto-discover HTTP MCP from `settings.json` directly, so the config is forwarded via code.
- The SDK auto-discovers its bundled `claude.exe` — no `cli_path`, no `.bat` workaround needed.
- `env={"CLAUDECODE": ""}` prevents nested-session detection when running inside a Claude Code session.

## Adding a new tool

1. Deploy an HTTP MCP server anywhere (App Runner, Lambda, ECS, etc.) that handles:
   - `POST /` with `initialize`, `tools/list`, `tools/call` (JSON-RPC 2.0)
   - `GET /` health check
2. Add it to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "email": {
      "type": "http",
      "url": "https://hm7z9pivmn.us-west-2.awsapprunner.com"
    },
    "slack": {
      "type": "http",
      "url": "https://your-slack-mcp-server.example.com"
    }
  }
}
```

No code changes needed — `agent.py` picks it up automatically.

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables (optional — CLI uses its own auth)
cp .env.example .env

# 3. Start server
uvicorn main:app --reload --port 8004
```

## Usage

**REST:**
```bash
curl -X POST http://localhost:8004/query \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Send an email to Mohit.Tripathi@quadranttechnologies.com from karrisindhuja26@gmail.com with subject Hello and content Test message"
  }'
```

**Response:**
```json
{
  "success": true,
  "response": "Email sent successfully to Mohit.Tripathi@quadranttechnologies.com with subject 'Hello'.",
  "tools_used": ["mcp__email__send_email"],
  "turns": 2,
  "cost_usd": 0.09,
  "elapsed_seconds": 20.1
}
```

**WebSocket** (streaming with real-time tool-call events):
```js
const ws = new WebSocket("ws://localhost:8004/ws");
ws.send(JSON.stringify({ prompt: "Send an email to ..." }));
// receives: start → reasoning → response → done
```

**Windows local test (no uvicorn needed):**
```bash
python test_local.py "Send an email to Mohit.Tripathi@quadranttechnologies.com from karrisindhuja26@gmail.com with subject Hello and content Test message"
```

## MCP Server (App Runner)

Deployed at `https://hm7z9pivmn.us-west-2.awsapprunner.com`

Source: `email_mcp_lambda.py`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /` | — | Health check |
| `POST /` | `initialize` | MCP handshake |
| `POST /` | `tools/list` | List available tools |
| `POST /` | `tools/call` | Execute a tool |

| Tool | Parameters |
|------|-----------|
| `send_email` | `to_email`, `from_email`, `subject`, `content`, `cc` (optional array) |

## SES Sender Requirements

The `from_email` address must be **SES-verified**. Two ways:

- **Individual address** (e.g. `user@gmail.com`): verify via SES console — simple, works immediately
- **Domain** (e.g. `example.com`): add a TXT verification record + 3 DKIM CNAME records to DNS

> **Corporate domains (Office 365 / Exchange)**: If the domain SPF uses `-all` and does not include `amazonses.com`, SES emails will be silently dropped by the receiving mail server even though SES reports success (0 bounces). Fix: add `include:amazonses.com` to the SPF record and add the 3 DKIM CNAME records from SES to your DNS. Contact your IT/DNS admin to apply these.

## Lambda Change (sendEmailAlert)

The `sendEmailAlert` Lambda previously ignored the `from_email` in the request body and always used the `FROM_EMAIL` environment variable. The following fix was applied so the caller controls the sender address:

```python
# After parsing body:
if body.get("from_email"):
    from_email = body["from_email"]   # caller-supplied address takes precedence
```
