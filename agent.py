"""
agent.py
--------
Claude Agent with MCP (Model Context Protocol) support.

Reads .claude/settings.json to discover MCP servers dynamically.
Any MCP server added to settings.json is automatically available.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

import httpx
import anthropic

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# MCP Client
# ─────────────────────────────────────────────

@dataclass
class MCPServer:
    name: str
    url: str
    description: str
    enabled: bool
    tools: List[Dict[str, Any]] = field(default_factory=list)


class MCPClient:
    """Connects to all enabled MCP servers listed in .claude/settings.json."""

    def __init__(self, settings_path: str = ".claude/settings.json"):
        self.settings_path = Path(settings_path)
        self.servers: Dict[str, MCPServer] = {}
        self.all_tools: List[Dict[str, Any]] = []

    def load_settings(self) -> Dict[str, Any]:
        if not self.settings_path.exists():
            logger.warning(f"Settings not found: {self.settings_path}")
            return {"mcpServers": {}}
        with open(self.settings_path) as f:
            return json.load(f)

    def connect(self):
        """Discover tools from all enabled MCP servers."""
        config = self.load_settings()
        for name, cfg in config.get("mcpServers", {}).items():
            if not cfg.get("enabled", True):
                continue
            url = cfg.get("httpUrl")
            if not url:
                continue
            try:
                tools = self._list_tools(url)
                server = MCPServer(
                    name=name, url=url,
                    description=cfg.get("description", ""),
                    enabled=True, tools=tools
                )
                self.servers[name] = server
                for t in tools:
                    t["_mcp_server"] = name
                    self.all_tools.append(t)
                logger.info(f"Connected to '{name}': {len(tools)} tools")
            except Exception as e:
                logger.error(f"Failed to connect to '{name}': {e}")

    def _rpc(self, url: str, method: str, params: dict = None) -> dict:
        """Send a JSON-RPC 2.0 request."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json={
                "jsonrpc": "2.0", "method": method,
                "params": params or {}, "id": 1
            }, headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            return resp.json()

    def _list_tools(self, url: str) -> List[Dict]:
        result = self._rpc(url, "tools/list")
        return result.get("result", {}).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict = None) -> str:
        """Call a tool on the appropriate MCP server."""
        for server in self.servers.values():
            if tool_name in [t["name"] for t in server.tools]:
                resp = self._rpc(server.url, "tools/call", {
                    "name": tool_name, "arguments": arguments or {}
                })
                content = resp.get("result", {}).get("content", [])
                if content and content[0].get("type") == "text":
                    return content[0].get("text", "")
                return str(content)
        raise ValueError(f"Tool '{tool_name}' not found in any MCP server")

    def get_tools_for_claude(self) -> List[Dict]:
        """Return tools in Anthropic Claude API format."""
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {
                    "type": "object", "properties": {}, "required": []
                })
            }
            for t in self.all_tools
        ]


# ─────────────────────────────────────────────
# Dynamic Agent
# ─────────────────────────────────────────────

class DynamicAgent:
    """
    Claude agent that uses MCP tools discovered from .claude/settings.json.

    Usage:
        agent = DynamicAgent()
        result = await agent.run("Send an email to ...")
    """

    def __init__(
        self,
        api_key: str = None,
        settings_path: str = ".claude/settings.json",
        model: str = "claude-sonnet-4-20250514",
    ):
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=self.api_key)

        # Connect to all MCP servers
        self.mcp = MCPClient(settings_path)
        self.mcp.connect()
        self.tools = self.mcp.get_tools_for_claude()

        logger.info(f"Agent ready | model={model} | tools={len(self.tools)}")
        for name, srv in self.mcp.servers.items():
            logger.info(f"  • {name}: {[t['name'] for t in srv.tools]}")

    def _system_prompt(self) -> str:
        lines = ["You are an AI agent with access to the following tools:\n"]
        for name, srv in self.mcp.servers.items():
            lines.append(f"**{name}** — {srv.description}")
            for t in srv.tools:
                lines.append(f"  - {t['name']}: {t.get('description', '')}")
        lines.append("\nUse tools as needed to complete the user's request.")
        return "\n".join(lines)

    async def run(self, prompt: str, max_turns: int = 10) -> Dict[str, Any]:
        """Run the agent on a prompt and return the result."""
        messages = [{"role": "user", "content": prompt}]
        tools_used = []

        for turn in range(max_turns):
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=self._system_prompt(),
                messages=messages,
                tools=self.tools,
            )

            if response.stop_reason == "end_turn":
                text = "".join(b.text for b in response.content if hasattr(b, "text"))
                return {"response": text, "tools_used": tools_used, "turns": turn + 1}

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tools_used.append(block.name)
                        try:
                            result = self.mcp.call_tool(block.name, block.input)
                        except Exception as e:
                            result = f"Error: {e}"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result)
                        })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

        return {"response": "Max turns reached", "tools_used": tools_used, "turns": max_turns}
