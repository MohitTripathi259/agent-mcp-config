"""
agent.py
--------
Claude Agent using ClaudeAgentOptions + ClaudeSDKClient.

Connects to MCP servers defined in .claude/settings.json via setting_sources=["project"].
The email MCP server (App Runner) is discovered automatically from settings.json.
"""

import os
import sys
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
)

logger = logging.getLogger("AGENT")

# Project root (where mcp_http_proxy.py lives)
project_root = str(Path(__file__).parent)

# Proxy script that bridges Claude CLI stdio ↔ HTTP MCP server.
# Root cause: Claude CLI's HTTP MCP transport fails on the `initialized`
# notification because the App Runner server incorrectly returns an error
# response for notifications. The stdio proxy suppresses those responses.
MCP_PROXY_SCRIPT = str(Path(__file__).parent / "mcp_http_proxy.py")
MCP_SERVER_URL = "https://hm7z9pivmn.us-west-2.awsapprunner.com"

# On Windows, the claude.cmd wrapper breaks when the username contains special
# characters like parentheses. We use a wrapper batch file at a safe path.
# Wrapper at C:\Users\Public\claude_run.bat calls node.exe with cli.js directly.
CLAUDE_CLI_PATH = r"C:\Users\Public\claude_run.bat"

# Tools allowed for this agent (must match tool names exposed by MCP servers)
ALLOWED_TOOLS = ["send_email"]

# System prompt
SYSTEM_PROMPT = """You are a helpful AI agent with access to MCP tools.

Available tools come from MCP servers configured in .claude/settings.json.

Use tools when needed to complete the user's request.
Always confirm success or failure clearly in your response."""


async def run_agent(query: str, max_turns: int = 10) -> Dict[str, Any]:
    """
    Run the agent on a query using ClaudeAgentOptions + ClaudeSDKClient.

    This is the same pattern used across all agents in this codebase:
      1. Build ClaudeAgentOptions (model, MCP servers, tools, system prompt)
      2. Create ClaudeSDKClient with those options
      3. Connect → query → stream response

    Args:
        query: The user's prompt
        max_turns: Max agent turns

    Returns:
        Dict with response, tools_used, turns, cost
    """

    # ── ClaudeAgentOptions ──────────────────────────────────────────────────
    # We pass the MCP server as a stdio proxy instead of using Claude CLI's
    # HTTP transport. Root cause: the App Runner server returns JSON-RPC error
    # responses for `initialized` notifications, which Claude CLI's HTTP
    # transport interprets as a connection failure. The stdio proxy correctly
    # suppresses responses to notifications, so MCP init succeeds.
    options = ClaudeAgentOptions(
        cwd=project_root,
        setting_sources=[],                   # don't read settings files
        mcp_servers={
            "email": {
                "type": "stdio",
                "command": sys.executable,    # current Python interpreter
                "args": [MCP_PROXY_SCRIPT, MCP_SERVER_URL],
            }
        },
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="bypassPermissions",
        system_prompt=SYSTEM_PROMPT,
        max_turns=max_turns,
        # Windows fix: bypass claude.cmd (breaks on usernames with parentheses)
        # Uses C:\Users\Public\claude_run.bat which calls node + cli.js with proper quoting
        cli_path=CLAUDE_CLI_PATH,
        # Unset CLAUDECODE so the subprocess doesn't think it's nested inside Claude Code
        env={"CLAUDECODE": ""},
    )

    # ── ClaudeSDKClient ──────────────────────────────────────────────────────
    client = ClaudeSDKClient(options=options)

    response_text = ""
    tools_used: List[str] = []
    turns = 0
    cost = 0.0

    logger.info(f"[AGENT] Connecting to Claude SDK...")
    await client.__aenter__()

    try:
        logger.info(f"[AGENT] Sending query: {query[:100]}...")
        await client.query(query)

        # Stream messages until ResultMessage
        async for message in client.receive_response():

            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text
                        logger.info(f"[AGENT] Text: {block.text[:100]}...")
                    elif isinstance(block, ThinkingBlock):
                        logger.info(f"[AGENT] Thinking: {block.thinking[:80]}...")
                    elif isinstance(block, ToolUseBlock):
                        tools_used.append(block.name)
                        logger.info(f"[AGENT] Tool call: {block.name} | input={block.input}")
                    elif isinstance(block, ToolResultBlock):
                        logger.info(f"[AGENT] Tool result: {block.content}")

            elif isinstance(message, ResultMessage):
                turns = message.num_turns
                cost = message.total_cost_usd or 0.0
                logger.info(f"[AGENT] Done — turns={turns}, cost=${cost:.4f}")

        # tools_used is populated directly from ToolUseBlock messages above

    finally:
        logger.info("[AGENT] Closing connection...")
        await client.__aexit__(None, None, None)

    return {
        "response": response_text,
        "tools_used": tools_used,
        "turns": turns,
        "cost_usd": cost,
    }
