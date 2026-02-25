"""
main.py
-------
FastAPI server — takes a prompt as input, runs the Claude agent, returns the response.

Start:
    uvicorn main:app --reload --port 8003

Test:
    POST http://localhost:8003/execute
    { "prompt": "Send an email to X from Y with subject Z and content ..." }
"""

import logging
import time
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent import run_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Dynamic Agent API", version="1.0.0")


# ─────────────────────────────────────────────
# Request / Response models
# ─────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    prompt: str = Field(..., description="What you want the agent to do")
    max_turns: int = Field(default=10, description="Max agent turns")

    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "Send an email to Mohit.Tripathi@quadranttechnologies.com from karrisindhuja26@gmail.com with subject 'Hello' and content 'Test message'",
                "max_turns": 5
            }
        }


class ExecuteResponse(BaseModel):
    success: bool
    prompt: str
    response: str
    tools_used: list
    turns: int
    cost_usd: float
    elapsed_seconds: float
    error: str = None


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    """Run the agent with the given prompt."""
    logger.info(f"Prompt: {req.prompt}")
    start = time.time()

    try:
        result = await run_agent(req.prompt, max_turns=req.max_turns)
        return ExecuteResponse(
            success=True,
            prompt=req.prompt,
            response=result["response"],
            tools_used=result["tools_used"],
            turns=result["turns"],
            cost_usd=result["cost_usd"],
            elapsed_seconds=round(time.time() - start, 2)
        )
    except Exception as e:
        logger.error(f"Agent error: {e}")
        return ExecuteResponse(
            success=False,
            prompt=req.prompt,
            response="",
            tools_used=[],
            turns=0,
            cost_usd=0.0,
            elapsed_seconds=round(time.time() - start, 2),
            error=str(e)
        )
