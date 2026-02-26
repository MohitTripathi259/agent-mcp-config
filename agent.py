"""
agent.py
--------
Email agent using ClaudeAgentOptions + query().

MCP servers are defined in .claude/settings.json — nothing is hardcoded here.
To add a new tool: deploy an HTTP MCP server and add it to settings.json.

Flow: run_agent(prompt)
        → load mcp_servers from .claude/settings.json
        → ClaudeAgentOptions(mcp_servers=...)   # passed as --mcp-config to CLI
        → query()
        → bundled claude.exe subprocess (--print mode)
        → HTTP MCP call to App Runner → AWS SES
"""

import json
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
)

logger = logging.getLogger("AGENT")

project_root = str(Path(__file__).parent)

SYSTEM_PROMPT = (
    "You are a helpful AI assistant with access to various tools. "
    "Use them when needed to fulfil the user's request. "
    "When calling email tools, always use EXACTLY the email addresses specified by the user. "
    "Never substitute, change, or default to any other email address."
)


def _load_settings() -> dict:
    """Load .claude/settings.json from the project root."""
    settings_path = Path(project_root) / ".claude" / "settings.json"
    with settings_path.open(encoding="utf-8") as f:
        return json.load(f)


def _mcp_servers(settings: dict) -> dict:
    """Extract mcpServers from settings, stripping the description field."""
    servers = {}
    for name, cfg in settings.get("mcpServers", {}).items():
        servers[name] = {k: v for k, v in cfg.items() if k != "description"}
    return servers


def _allowed_tools(settings: dict) -> List[str]:
    """Build allowed-tools list from server names: mcp__<server>__*."""
    return [f"mcp__{name}__*" for name in settings.get("mcpServers", {})]


async def run_agent(
    prompt: str,
    max_turns: int = 10,
    callback: Optional[Callable[[str, str], Coroutine]] = None,
) -> Dict[str, Any]:
    settings = _load_settings()

    options = ClaudeAgentOptions(
        cwd=project_root,
        mcp_servers=_mcp_servers(settings),    # HTTP MCP servers from settings.json
        allowed_tools=_allowed_tools(settings),
        permission_mode="bypassPermissions",
        system_prompt=SYSTEM_PROMPT,
        max_turns=max_turns,
        max_thinking_tokens=10000,
        # SDK auto-discovers the bundled claude.exe (no cli_path needed).
        # Clear CLAUDECODE so the subprocess doesn't detect a nested session.
        env={"CLAUDECODE": ""},
    )

    response_text = ""
    tools_used: List[str] = []
    turns = 0
    cost  = 0.0

    logger.info(f"[AGENT] Query: {prompt[:120]}")

    async for message in query(prompt=prompt, options=options):

        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_text += block.text
                    logger.info(f"[AGENT] Text: {block.text[:100]}")
                elif isinstance(block, ThinkingBlock):
                    logger.info(f"[AGENT] Thinking: {block.thinking[:80]}")
                elif isinstance(block, ToolUseBlock):
                    tools_used.append(block.name)
                    logger.info(f"[AGENT] Tool call: {block.name} | {block.input}")
                    if callback:
                        await callback(f"Calling {block.name}", "⚙️")
                elif isinstance(block, ToolResultBlock):
                    logger.info(f"[AGENT] Tool result: {block.content}")

        elif isinstance(message, ResultMessage):
            turns = message.num_turns
            cost  = message.total_cost_usd or 0.0
            logger.info(f"[AGENT] Done — turns={turns}, cost=${cost:.4f}")

    return {"response": response_text, "tools_used": tools_used, "turns": turns, "cost_usd": cost}
