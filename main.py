"""
main.py
-------
FastAPI server with REST and WebSocket endpoints.

Start:
    uvicorn main:app --reload --port 8004

Endpoints:
    GET  /           → service info
    GET  /status     → health check
    POST /query      → run agent (REST)
    WS   /ws         → run agent with real-time streaming
"""

import json
import logging
import time

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent import run_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Email Agent API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    prompt: str = Field(..., description="What you want the agent to do")
    max_turns: int = Field(default=10, description="Max agent turns")

    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "Send an email to Mohit.Tripathi@quadranttechnologies.com from karrisindhuja26@gmail.com with subject 'Hello' and content 'Test message'",
                "max_turns": 5
            }
        }


class QueryResponse(BaseModel):
    success: bool
    prompt: str
    response: str
    tools_used: list
    turns: int
    cost_usd: float
    elapsed_seconds: float
    error: str = None


# ── REST Endpoints ────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "Email Agent API",
        "version": "1.0.0",
        "endpoints": ["/query", "/ws", "/status"],
        "tools": ["mcp__email__send_email"],
    }


@app.get("/status")
def status():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Run the agent with the given prompt (REST)."""
    logger.info(f"Prompt: {req.prompt}")
    start = time.time()

    try:
        result = await run_agent(req.prompt, max_turns=req.max_turns)
        return QueryResponse(
            success=True,
            prompt=req.prompt,
            response=result["response"],
            tools_used=result["tools_used"],
            turns=result["turns"],
            cost_usd=result["cost_usd"],
            elapsed_seconds=round(time.time() - start, 2),
        )
    except Exception as e:
        logger.error(f"Agent error: {e}")
        return QueryResponse(
            success=False,
            prompt=req.prompt,
            response="",
            tools_used=[],
            turns=0,
            cost_usd=0.0,
            elapsed_seconds=round(time.time() - start, 2),
            error=str(e),
        )


# ── WebSocket Endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time streaming endpoint.

    Message types sent to client:
      start     → query received, processing started
      reasoning → live update from a tool (icon + message)
      response  → final agent response
      done      → processing complete
      error     → something went wrong
    """
    await websocket.accept()
    logger.info("[WS] Client connected")

    try:
        while True:
            data = await websocket.receive_text()

            # Accept JSON { "prompt": "...", "max_turns": 10 } or plain text
            try:
                payload   = json.loads(data)
                prompt    = payload.get("prompt") or payload.get("query", data)
                max_turns = payload.get("max_turns", 10)
            except json.JSONDecodeError:
                prompt    = data
                max_turns = 10

            await websocket.send_json({"type": "start", "query": prompt})

            # Callback streams live tool-call events to the WebSocket client
            async def reasoning_callback(action: str, icon: str = "⚙️"):
                await websocket.send_json({
                    "type": "reasoning",
                    "message": action,
                    "icon": icon,
                })

            start = time.time()

            try:
                result = await run_agent(prompt, max_turns=max_turns, callback=reasoning_callback)
                await websocket.send_json({
                    "type": "response",
                    "response": result["response"],
                    "tools_used": result["tools_used"],
                    "turns": result["turns"],
                    "cost_usd": result["cost_usd"],
                    "elapsed_seconds": round(time.time() - start, 2),
                })
            except Exception as e:
                logger.error(f"[WS] Agent error: {e}")
                await websocket.send_json({"type": "error", "message": str(e)})
            finally:
                await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected")
