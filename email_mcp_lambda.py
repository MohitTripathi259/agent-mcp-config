import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

EMAIL_API_URL = "https://bss2gd3mbj.execute-api.us-west-2.amazonaws.com/dev/sendEmailAlert"

app = FastAPI(title="Email MCP Server")

# -------------------------------------------------------
# Tool schema
# -------------------------------------------------------
_TOOL_SCHEMA = {
    "name": "send_email",
    "description": "Send an email. Optionally include cc as a list of addresses.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "to_email":   {"type": "string", "description": "Recipient email address"},
            "from_email": {"type": "string", "description": "Sender email address"},
            "subject":    {"type": "string", "description": "Email subject line"},
            "content":    {"type": "string", "description": "Email body (HTML supported)"},
            "cc":         {"type": "array", "items": {"type": "string"}, "description": "CC recipients (optional)"}
        },
        "required": ["to_email", "from_email", "subject", "content"]
    }
}


# -------------------------------------------------------
# Health check (App Runner probes GET /)
# -------------------------------------------------------
@app.get("/")
async def health():
    return {"status": "ok", "service": "email-mcp-server"}


# -------------------------------------------------------
# MCP JSON-RPC 2.0 handler
# -------------------------------------------------------
@app.post("/")
async def mcp_handler(request: Request):
    body = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id")

    # --- initialize ---
    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "email-mcp-server", "version": "2.0.0"},
                "capabilities": {"tools": {}}
            }
        })

    # --- tools/list ---
    elif method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": [_TOOL_SCHEMA]}
        })

    # --- tools/call ---
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name != "send_email":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Tool not found: {tool_name}"}
            })

        try:
            payload = {
                "to_email":   arguments.get("to_email"),
                "from_email": arguments.get("from_email"),
                "subject":    arguments.get("subject"),
                "content":    arguments.get("content"),
            }
            if arguments.get("cc"):
                payload["cc"] = arguments["cc"]
            resp = requests.post(EMAIL_API_URL, json=payload, timeout=30)
            resp.raise_for_status()
            cc_note = f", cc: {arguments['cc']}" if arguments.get("cc") else ""
            text = f"Email sent from {arguments.get('from_email')} to {arguments.get('to_email')}{cc_note} — status {resp.status_code}"
        except Exception as e:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": f"Email send failed: {str(e)}"}
            })

        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"content": [{"type": "text", "text": text}]}
        })

    # --- notifications (no id) → never respond ---
    elif request_id is None:
        return Response(status_code=204)

    # --- unknown method ---
    else:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"}
        })
