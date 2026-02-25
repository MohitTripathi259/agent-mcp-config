"""
main.py
-------
FastAPI server — takes a prompt as input, runs the agent, returns the response.

Start:
    uvicorn main:app --reload --port 8003

Test:
    POST http://localhost:8003/execute
    { "prompt": "Send an email to X from Y with subject Z and content ..." }
"""

import logging
import time
from dotenv import load_dotenv

load_dotenv()  # Load .env before anything else

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent import DynamicAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Dynamic Agent API", version="1.0.0")

# Initialise agent once at startup
_agent: DynamicAgent = None


@app.on_event("startup")
async def startup():
    global _agent
    logger.info("Initialising agent...")
    _agent = DynamicAgent()
    logger.info("Agent ready.")


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
    elapsed_seconds: float
    error: str = None


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "agent_ready": _agent is not None}


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest):
    """Run the agent with the given prompt."""
    logger.info(f"Prompt: {req.prompt}")
    start = time.time()

    try:
        result = await _agent.run(req.prompt, max_turns=req.max_turns)
        return ExecuteResponse(
            success=True,
            prompt=req.prompt,
            response=result["response"],
            tools_used=result["tools_used"],
            turns=result["turns"],
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
            elapsed_seconds=round(time.time() - start, 2),
            error=str(e)
        )
