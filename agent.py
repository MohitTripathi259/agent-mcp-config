"""
agent.py
--------
Email agent using Anthropic API directly + MCP HTTP server for send_email tool.

Flow: run_agent(prompt) → Anthropic API (tool loop) → MCP HTTP server → SES
"""

import json
import logging
from typing import Any, Dict, List

import httpx
import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("AGENT")

MCP_SERVER_URL = "https://hm7z9pivmn.us-west-2.awsapprunner.com"


def _get_tools() -> List[Dict]:
    """Fetch available tools from the MCP server."""
    with httpx.Client(timeout=15) as client:
        resp = client.post(MCP_SERVER_URL, json={
            "jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1
        })
        resp.raise_for_status()
        tools = resp.json().get("result", {}).get("tools", [])
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _call_tool(tool_name: str, arguments: Dict) -> str:
    """Call a tool on the MCP server and return the text result."""
    with httpx.Client(timeout=30) as client:
        resp = client.post(MCP_SERVER_URL, json={
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments}, "id": 1
        })
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("content", [])
        return content[0].get("text", str(content)) if content else ""


async def run_agent(prompt: str, max_turns: int = 10) -> Dict[str, Any]:
    """
    Run the agent on a prompt.

    Uses Anthropic API directly with an agentic tool-use loop.
    MCP tools are discovered from the HTTP MCP server and called over HTTP.
    """
    tools = _get_tools()
    logger.info(f"Tools: {[t['name'] for t in tools]}")

    client = anthropic.AsyncAnthropic()
    messages = [{"role": "user", "content": prompt}]
    tools_used: List[str] = []
    response_text = ""

    for turn in range(max_turns):
        logger.info(f"Turn {turn + 1}")
        resp = await client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system="You are a helpful AI agent. Use the available tools to complete the user's request.",
            messages=messages,
            tools=tools,
        )

        if resp.stop_reason == "end_turn":
            response_text = "".join(b.text for b in resp.content if hasattr(b, "text"))
            break

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    tools_used.append(block.name)
                    logger.info(f"Tool call: {block.name} | args={block.input}")
                    result = _call_tool(block.name, block.input)
                    logger.info(f"Tool result: {result}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": tool_results})

    return {"response": response_text, "tools_used": tools_used, "turns": turn + 1}
