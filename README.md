# agent-mcp-config

Email agent using **Claude Agent SDK** (`ClaudeAgentOptions` + `query()`) with an HTTP MCP server that sends emails via AWS SES.

## Architecture

```
POST /query  (or WS /ws)
    → main.py  (FastAPI)
        → agent.py  (ClaudeAgentOptions + query())
            → bundled claude.exe subprocess  (--print mode, no streaming)
                → mcp__email__send_email  (HTTP MCP via --mcp-config)
                    → App Runner  →  AWS SES  →  Email
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI server — REST (`/query`) + WebSocket (`/ws`) |
| `agent.py` | `ClaudeAgentOptions` + `query()` agent runner |
| `requirements.txt` | Python dependencies |
| `.claude/settings.json` | MCP server description (informational) |
| `.env.example` | Environment variable template |

## How it works

- `query()` (non-streaming) is used instead of `ClaudeSDKClient`.
  Streaming mode requires an `initialize` handshake that times out on Windows in a uvicorn context.
- The MCP email server is registered via `mcp_servers=` in `ClaudeAgentOptions`, which passes it as `--mcp-config` to the CLI subprocess. The bundled `claude.exe` (v2.1.1) does not support HTTP MCP via `settings.json` directly.
- The SDK auto-discovers its bundled `claude.exe` — no `cli_path`, no `.bat` workaround needed.
- `env={"CLAUDECODE": ""}` prevents nested-session detection when running inside a Claude Code session.

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
  "cost_usd": 0.112,
  "elapsed_seconds": 26.3
}
```

**WebSocket** (streaming with real-time tool-call events):
```js
const ws = new WebSocket("ws://localhost:8004/ws");
ws.send(JSON.stringify({ prompt: "Send an email to ..." }));
// receives: start → reasoning → response → done
```

## MCP Server

The email MCP server runs on AWS App Runner and exposes a single tool:

| Tool | Parameters |
|------|-----------|
| `send_email` | `to_email`, `from_email`, `subject`, `content` |

Both `to_email` and `from_email` must be SES-verified addresses.

- Default `from_email`: `karrisindhuja26@gmail.com`
- Default `to_email`: `Mohit.Tripathi@quadranttechnologies.com`
