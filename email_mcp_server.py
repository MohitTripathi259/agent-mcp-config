"""
email_mcp_server.py
-------------------
Local stdio MCP server exposing the send_email tool.

Registered in .claude/settings.json as a stdio server so any agent using
setting_sources=["project"] picks it up automatically — no mcp_servers=
needed in code.

The tool calls the App Runner HTTP endpoint to send email via AWS SES.
"""

import json
import sys
import requests
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

APP_RUNNER_URL = "https://hm7z9pivmn.us-west-2.awsapprunner.com"

server = Server("email")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="send_email",
            description="Send an email via AWS SES. Both to_email and from_email must be SES-verified addresses.",
            inputSchema={
                "type": "object",
                "properties": {
                    "to_email":   {"type": "string", "description": "Recipient email address"},
                    "from_email": {"type": "string", "description": "Sender email address (must be SES-verified)"},
                    "subject":    {"type": "string", "description": "Email subject"},
                    "content":    {"type": "string", "description": "Email body content"},
                },
                "required": ["to_email", "from_email", "subject", "content"],
            },
        )
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name != "send_email":
        raise ValueError(f"Unknown tool: {name}")

    response = requests.post(
        APP_RUNNER_URL,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "send_email", "arguments": arguments},
        },
        timeout=30,
    )

    result = response.json()
    content = result.get("result", {}).get("content", [{}])
    text = content[0].get("text", json.dumps(result)) if content else json.dumps(result)

    return [types.TextContent(type="text", text=text)]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
