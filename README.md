# agent-mcp-config

Email agent using **Claude Agent SDK** (`ClaudeAgentOptions` + `ClaudeSDKClient`) with an in-process MCP tool that sends emails via AWS SES.

## Architecture

```
POST /query  (or WS /ws)
    → main.py
        → agent.py  (ClaudeAgentOptions + ClaudeSDKClient)
            → Claude CLI subprocess
                → mcp__email__send_email  (tools.py, in-process)
                    → App Runner HTTP → SES → Email
```

## Files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI server — REST (`/query`) + WebSocket (`/ws`) |
| `agent.py` | ClaudeAgentOptions + ClaudeSDKClient agent runner |
| `tools.py` | `send_email` MCP tool + reasoning callback system |
| `claude_agent_sdk/` | Vendored SDK with Windows subprocess fix |

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# edit .env → set ANTHROPIC_API_KEY

# 3. Start server
uvicorn main:app --reload --port 8003
```

## Usage

**REST:**
```bash
curl -X POST http://localhost:8003/query \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Send an email to Mohit.Tripathi@quadranttechnologies.com from karrisindhuja26@gmail.com with subject Hello and content Test message"
  }'
```

**WebSocket** (streaming with real-time reasoning updates):
```js
const ws = new WebSocket("ws://localhost:8003/ws");
ws.send(JSON.stringify({ prompt: "Send an email to ..." }));
// receives: start → reasoning → response → done
```

## Windows Note

`claude.cmd` crashes on Windows usernames with parentheses. The agent uses `C:\Users\Public\claude_run.bat` which calls `node.exe` directly, bypassing `cmd.exe`.

```batch
@echo off
"C:\Program Files\nodejs\node.exe" "C:\Users\...\cli.js" %*
```
