"""
agent.py
--------
Email agent using ClaudeAgentOptions + ClaudeSDKClient (SDK MCP server pattern).

The send_email tool runs in-process via create_sdk_mcp_server(), which calls
the App Runner HTTP endpoint. This matches the pattern used in other agents
(CI-agent, scout-lambda) and avoids Claude CLI's HTTP MCP transport issues.

Flow: run_agent(prompt)
        → ClaudeAgentOptions (sdk MCP server in-process)
        → ClaudeSDKClient
        → Claude CLI subprocess
        → mcp__email__send_email tool
        → App Runner HTTP → SES → Email
"""

import sys
import logging
from pathlib import Path
from typing import Any, Dict, List

import httpx
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
    tool,
    create_sdk_mcp_server,
)

logger = logging.getLogger("AGENT")

# ── Constants ────────────────────────────────────────────────────────────────

project_root    = str(Path(__file__).parent)
MCP_SERVER_URL  = "https://hm7z9pivmn.us-west-2.awsapprunner.com"
CLAUDE_CLI_PATH = r"C:\Users\Public\claude_run.bat"   # Windows: bypasses claude.cmd

SYSTEM_PROMPT = (
    "You are a helpful AI agent with access to an email tool. "
    "Use the send_email tool to send emails as requested. "
    "Always confirm success or failure clearly in your response."
)


# ── In-process MCP tool ──────────────────────────────────────────────────────

@tool(
    name="send_email",
    description=(
        "Send an email via SES. Both to_email and from_email must be "
        "SES-verified addresses."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "to_email":   {"type": "string", "description": "Recipient (SES-verified)"},
            "from_email": {"type": "string", "description": "Sender (SES-verified)"},
            "subject":    {"type": "string", "description": "Email subject"},
            "content":    {"type": "string", "description": "Email body (HTML supported)"},
        },
        "required": ["to_email", "from_email", "subject", "content"],
    },
)
async def send_email(args: Dict[str, Any]) -> Dict[str, Any]:
    """Call the App Runner MCP HTTP endpoint to send an email via SES."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(MCP_SERVER_URL, json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "send_email", "arguments": args},
            "id": 1,
        })
        resp.raise_for_status()
        content = resp.json().get("result", {}).get("content", [])
        text = content[0].get("text", str(content)) if content else "done"
    return {"content": [{"type": "text", "text": text}]}


EMAIL_MCP_SERVER = create_sdk_mcp_server(name="email", tools=[send_email])
ALLOWED_TOOLS    = ["mcp__email__send_email"]


# ── Agent runner ─────────────────────────────────────────────────────────────

async def run_agent(prompt: str, max_turns: int = 10) -> Dict[str, Any]:
    """
    Run the agent using ClaudeAgentOptions + ClaudeSDKClient.

    Pattern mirrors other agents in this codebase (CI-agent, scout-lambda):
      1. Build ClaudeAgentOptions with in-process SDK MCP server
      2. Create ClaudeSDKClient
      3. connect → query → stream response
    """
    options = ClaudeAgentOptions(
        cwd=project_root,
        mcp_servers={"email": EMAIL_MCP_SERVER},
        allowed_tools=ALLOWED_TOOLS,
        permission_mode="bypassPermissions",
        system_prompt=SYSTEM_PROMPT,
        max_turns=max_turns,
        # Windows fix: claude.cmd breaks on usernames with parentheses.
        # claude_run.bat calls node.exe + cli.js directly (safe path).
        cli_path=CLAUDE_CLI_PATH,
        # Clear CLAUDECODE so the subprocess doesn't detect a nested session.
        env={"CLAUDECODE": ""},
    )

    client = ClaudeSDKClient(options=options)
    response_text = ""
    tools_used: List[str] = []
    turns = 0
    cost  = 0.0

    logger.info("[AGENT] Connecting...")
    await client.__aenter__()

    try:
        logger.info(f"[AGENT] Query: {prompt[:120]}")
        await client.query(prompt)

        async for message in client.receive_response():

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
                    elif isinstance(block, ToolResultBlock):
                        logger.info(f"[AGENT] Tool result: {block.content}")

            elif isinstance(message, ResultMessage):
                turns = message.num_turns
                cost  = message.total_cost_usd or 0.0
                logger.info(f"[AGENT] Done — turns={turns}, cost=${cost:.4f}")

    finally:
        logger.info("[AGENT] Closing...")
        await client.__aexit__(None, None, None)

    return {"response": response_text, "tools_used": tools_used, "turns": turns, "cost_usd": cost}
