"""
test_local.py
-------------
Local Windows test script using Anthropic API directly.

Use this for dev/testing on Windows where ClaudeSDKClient (subprocess)
has compatibility issues (parentheses in username, CLAUDECODE env var, etc.).

The production agent.py uses ClaudeAgentOptions + ClaudeSDKClient
which works correctly on Linux (Lambda / ECS / App Runner).

Usage:
    python test_local.py "Send an email to X from Y with subject Z and content ..."
    python test_local.py  # runs default email test
"""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx
import anthropic
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("TEST_LOCAL")


# ─────────────────────────────────────────────
# MCP client (reads .claude/settings.json)
# ─────────────────────────────────────────────

def load_mcp_servers(settings_path: str = ".claude/settings.json") -> Dict[str, str]:
    """Read enabled MCP server URLs from settings.json."""
    path = Path(settings_path)
    if not path.exists():
        return {}
    config = json.loads(path.read_text())
    servers = {}
    for name, cfg in config.get("mcpServers", {}).items():
        if not cfg.get("enabled", True):
            continue
        # Support both legacy httpUrl and standard Claude CLI url field
        url = cfg.get("httpUrl") or cfg.get("url")
        if url:
            servers[name] = url
    return servers


def list_tools(url: str) -> List[Dict]:
    """Get tools from an MCP server via JSON-RPC."""
    with httpx.Client(timeout=15) as client:
        resp = client.post(url, json={"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1})
        resp.raise_for_status()
        return resp.json().get("result", {}).get("tools", [])


def call_tool(url: str, tool_name: str, arguments: Dict) -> str:
    """Call a tool on an MCP server."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(url, json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}, "id": 1
        })
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("content", [])
        return content[0].get("text", str(content)) if content else ""


# ─────────────────────────────────────────────
# Agent loop (direct Anthropic API)
# ─────────────────────────────────────────────

async def run(prompt: str, max_turns: int = 10) -> Dict[str, Any]:
    """
    Local test runner using direct Anthropic API.
    Reads MCP servers from .claude/settings.json same as production.
    """
    # Discover MCP servers + tools
    servers = load_mcp_servers()
    server_tools: Dict[str, Dict] = {}   # tool_name → {url, schema}
    claude_tools: List[Dict] = []

    for name, url in servers.items():
        tools = list_tools(url)
        for t in tools:
            server_tools[t["name"]] = {"url": url, "schema": t}
            claude_tools.append({
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
            })
        logger.info(f"MCP '{name}': {[t['name'] for t in tools]}")

    client = anthropic.AsyncAnthropic()
    messages = [{"role": "user", "content": prompt}]
    tools_used: List[str] = []
    response_text = ""

    for turn in range(max_turns):
        logger.info(f"Turn {turn + 1}")
        resp = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system="You are a helpful AI agent. Use the available tools to complete the user's request.",
            messages=messages,
            tools=claude_tools,
        )

        if resp.stop_reason == "end_turn":
            response_text = "".join(b.text for b in resp.content if hasattr(b, "text"))
            break

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    tools_used.append(block.name)
                    logger.info(f"Calling tool: {block.name} | args: {block.input}")
                    info = server_tools.get(block.name)
                    if info:
                        result = call_tool(info["url"], block.name, block.input)
                    else:
                        result = f"Tool '{block.name}' not found"
                    logger.info(f"Tool result: {result}")
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": tool_results})

    return {"response": response_text, "tools_used": tools_used, "turns": turn + 1}


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Send an email to Mohit.Tripathi@quadranttechnologies.com "
        "from karrisindhuja26@gmail.com "
        "with subject 'Test from test_local.py' "
        "and content 'This is a local Windows test using direct Anthropic API.'"
    )

    print(f"\nPrompt: {prompt}\n")
    result = asyncio.run(run(prompt))

    print("\n" + "="*60)
    print(f"Response  : {result['response']}")
    print(f"Tools used: {result['tools_used']}")
    print(f"Turns     : {result['turns']}")
    print("="*60)
