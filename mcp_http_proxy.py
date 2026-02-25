"""
mcp_http_proxy.py
-----------------
MCP stdio-to-HTTP proxy.

Claude CLI spawns this as a stdio MCP server. It reads JSON-RPC 2.0 messages
from stdin, forwards them to the real HTTP MCP server, and writes responses
back to stdout — correctly suppressing responses to notifications (no-id msgs).

Usage (configured in ClaudeAgentOptions mcp_servers):
    command: "python"
    args: ["mcp_http_proxy.py", "https://hm7z9pivmn.us-west-2.awsapprunner.com"]
"""

import json
import sys
import requests

MCP_URL = sys.argv[1] if len(sys.argv) > 1 else "https://hm7z9pivmn.us-west-2.awsapprunner.com"


def main() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")          # None for notifications
        method  = msg.get("method", "")

        try:
            resp = requests.post(MCP_URL, json=msg, timeout=30)
            resp_data = resp.json()
        except Exception as exc:
            # Only emit an error response if this was a request (has id)
            if msg_id is not None:
                resp_data = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32603, "message": str(exc)},
                }
            else:
                continue  # notifications get no response on failure either

        # JSON-RPC rule: NEVER respond to notifications (no id)
        if msg_id is None:
            continue

        sys.stdout.write(json.dumps(resp_data) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
