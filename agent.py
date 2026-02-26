"""
agent.py
--------
Email agent using ClaudeAgentOptions + query().

Uses the one-shot query() function (non-streaming) so the prompt is passed
directly via --print, bypassing the streaming-mode initialize handshake.

The send_email tool is registered via mcp_servers= (passed as --mcp-config).

Flow: run_agent(prompt)
        → ClaudeAgentOptions (mcp_servers with HTTP endpoint)
        → query()
        → bundled claude.exe subprocess (--print mode)
        → mcp__email__send_email tool (via HTTP → App Runner → SES)
"""

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

ALLOWED_TOOLS = ["mcp__email__send_email"]

# HTTP MCP server — App Runner endpoint exposing send_email via AWS SES.
# Passed via mcp_servers= so the SDK serialises it as --mcp-config for the CLI.
EMAIL_MCP_SERVER = {
    "type": "http",
    "url": "https://hm7z9pivmn.us-west-2.awsapprunner.com",
}

SYSTEM_PROMPT = """
You are an intelligent AI email assistant.

## Your Capabilities

You have access to an email tool for sending emails via AWS SES.

## Available MCP Tools

- **send_email**: Send an email via SES. Requires to_email, from_email, subject, and content.
  Both to_email and from_email must be SES-verified addresses.

## How to Process Requests

1. Extract recipient (to_email), sender (from_email), subject, and content from the user's request
2. Use the send_email tool with all four required fields
3. Confirm success or failure clearly in your response

## Response Format

Always respond with a clear confirmation, for example:
- "Email sent successfully to <to_email> with subject '<subject>'."
- "Failed to send email: <reason>"

## Important Guidelines

- Both to_email and from_email must be SES-verified addresses
- Default from_email: karrisindhuja26@gmail.com
- Default to_email: Mohit.Tripathi@quadranttechnologies.com
- Always confirm the result of the email operation clearly
"""


async def run_agent(
    prompt: str,
    max_turns: int = 10,
    callback: Optional[Callable[[str, str], Coroutine]] = None,
) -> Dict[str, Any]:
    options = ClaudeAgentOptions(
        cwd=project_root,
        setting_sources=["project"],
        allowed_tools=ALLOWED_TOOLS,
        mcp_servers={"email": EMAIL_MCP_SERVER},
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
                        await callback(f"Calling {block.name}", "📧")
                elif isinstance(block, ToolResultBlock):
                    logger.info(f"[AGENT] Tool result: {block.content}")

        elif isinstance(message, ResultMessage):
            turns = message.num_turns
            cost  = message.total_cost_usd or 0.0
            logger.info(f"[AGENT] Done — turns={turns}, cost=${cost:.4f}")

    return {"response": response_text, "tools_used": tools_used, "turns": turns, "cost_usd": cost}
