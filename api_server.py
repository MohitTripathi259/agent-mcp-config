#!/usr/bin/env python3
"""
FastAPI Server for End-to-End Testing
Exposes DynamicAgent with S3 Skills + MCP Tools
"""

import os
import sys
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import uvicorn
import json

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    logger = logging.getLogger(__name__)
    logger.info("Loaded environment variables from .env file")
except ImportError:
    # dotenv not installed, will use system environment variables
    pass

# Add orchestrator to path
sys.path.insert(0, str(Path(__file__).parent / "orchestrator"))

from agent_runner import DynamicAgent

# Import S3 storage helpers
try:
    from s3_storage import upload_screenshot_to_s3
    S3_STORAGE_AVAILABLE = True
except ImportError:
    S3_STORAGE_AVAILABLE = False
    logger.warning("S3 storage not available")

# Configure logging with detailed formatting
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Console handler for terminal output
console = logging.StreamHandler()
console.setLevel(logging.INFO)
formatter = logging.Formatter(
    '\n' + '='*80 + '\n%(asctime)s | %(levelname)s | %(name)s\n%(message)s\n' + '='*80,
    datefmt='%Y-%m-%d %H:%M:%S'
)
console.setFormatter(formatter)
logging.getLogger().addHandler(console)

app = FastAPI(
    title="Computer Use + S3 Skills API",
    version="1.0.0",
    description="End-to-end testing API for DynamicAgent with MCP servers and S3 skills"
)

# Global agent instance
agent: Optional[DynamicAgent] = None


# ============================================================
# Pydantic Models
# ============================================================

class AgentRequest(BaseModel):
    """Request to execute agent task"""
    prompt: str = Field(..., description="User prompt/query")
    max_turns: int = Field(default=50, description="Maximum conversation turns")
    include_s3_skills: bool = Field(default=True, description="Use S3 skills")
    use_computer_tools: bool = Field(default=False, description="Enable computer use tools (bash, screenshot, text editor)")
    temperature: float = Field(default=1.0, description="Model temperature")

    class Config:
        json_schema_extra = {
            "example": {
                "prompt": "Take a screenshot and tell me what you see",
                "max_turns": 5,
                "include_s3_skills": True,
                "use_computer_tools": False,
                "temperature": 1.0
            }
        }


class AgentResponse(BaseModel):
    """Response from agent execution"""
    success: bool
    prompt: str
    response: str
    turns: int
    tools_used: List[str]
    mcp_servers_active: List[str]
    s3_skills_loaded: List[str]
    execution_time_seconds: float
    artifacts: Optional[Dict[str, List[str]]] = None
    error: Optional[str] = None


class SystemStatusResponse(BaseModel):
    """System status information"""
    status: str
    mcp_servers: Dict[str, Any]
    s3_skills: Dict[str, Any]
    total_tools: int
    anthropic_model: str


# ============================================================
# Startup & Initialization
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Initialize agent on startup"""
    global agent

    logger.info("="*80)
    logger.info("🚀 STARTING API SERVER")
    logger.info("="*80)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("❌ ANTHROPIC_API_KEY not set!")
        raise RuntimeError("ANTHROPIC_API_KEY environment variable required")

    logger.info("📋 Initializing DynamicAgent...")
    logger.info(f"   Settings: .claude/settings.json")
    logger.info(f"   S3 Skills: Enabled")
    logger.info(f"   S3 Bucket: cerebricks-studio-agent-skills")
    logger.info(f"   S3 Prefix: marketplace_skills/")

    try:
        agent = DynamicAgent(
            anthropic_api_key=api_key,
            settings_path=".claude/settings.json",
            model="claude-sonnet-4-20250514",
            load_s3_skills=True,
            s3_skills_bucket="cerebricks-studio-agent-skills",
            s3_skills_prefix="marketplace_skills/"
        )

        logger.info("="*80)
        logger.info("✅ AGENT INITIALIZED SUCCESSFULLY")
        logger.info("="*80)
        logger.info(f"   MCP Servers: {len(agent.mcp_client.servers)}")
        for server_name, server in agent.mcp_client.servers.items():
            logger.info(f"      • {server_name}: {len(server.tools or [])} tools")

        logger.info(f"   S3 Skills Loaded: {agent.skills_loaded}")
        if agent.skill_loader:
            skills = agent.skill_loader.get_skills()
            logger.info(f"      • Skills: {list(skills.keys())}")

        logger.info(f"   Total Tools: {len(agent.tools)}")
        logger.info("="*80)

    except Exception as e:
        logger.error(f"❌ Failed to initialize agent: {e}")
        raise


# ============================================================
# API Endpoints
# ============================================================

@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "Computer Use + S3 Skills API",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "status": "GET /status",
            "execute": "POST /execute",
            "health": "GET /health"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "agent_initialized": agent is not None,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/status", response_model=SystemStatusResponse)
async def get_status():
    """Get system status"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    logger.info("\n" + "="*80)
    logger.info("📊 STATUS CHECK REQUESTED")
    logger.info("="*80)

    # MCP servers info
    mcp_servers_info = {}
    for server_name, server in agent.mcp_client.servers.items():
        mcp_servers_info[server_name] = {
            "url": server.url,
            "enabled": server.enabled,
            "tools_count": len(server.tools or []),
            "tools": [tool["name"] for tool in (server.tools or [])]
        }

    # S3 skills info
    s3_skills_info = {
        "enabled": agent.skills_loaded,
        "skills": []
    }

    if agent.skill_loader:
        skills = agent.skill_loader.get_skills()
        for skill_name, skill_data in skills.items():
            s3_skills_info["skills"].append({
                "name": skill_name,
                "description": skill_data.get("description", ""),
                "version": skill_data.get("metadata", {}).get("version", ""),
                "scripts": list(skill_data.get("scripts", {}).keys())
            })

    status_response = SystemStatusResponse(
        status="operational",
        mcp_servers=mcp_servers_info,
        s3_skills=s3_skills_info,
        total_tools=len(agent.tools),
        anthropic_model=agent.model
    )

    logger.info(f"MCP Servers: {len(mcp_servers_info)}")
    logger.info(f"S3 Skills: {len(s3_skills_info['skills'])}")
    logger.info(f"Total Tools: {len(agent.tools)}")
    logger.info("="*80 + "\n")

    return status_response


@app.post("/execute", response_model=AgentResponse)
async def execute_agent(request: AgentRequest):
    """Execute agent with user prompt"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    logger.info("\n" + "="*80)
    logger.info("🤖 NEW AGENT EXECUTION REQUEST")
    logger.info("="*80)
    logger.info(f"Prompt: {request.prompt}")
    logger.info(f"Max Turns: {request.max_turns}")
    logger.info(f"S3 Skills: {request.include_s3_skills}")
    logger.info(f"Computer Tools: {request.use_computer_tools}")
    logger.info(f"Temperature: {request.temperature}")
    logger.info("="*80 + "\n")

    start_time = datetime.now()
    tools_used = []
    artifacts = {"screenshots": [], "files": []}
    session_id = f"session_{int(start_time.timestamp())}"

    try:
        # Reset tools to base (MCP tools only) for each request
        agent.tools = agent.mcp_client.get_tools_for_anthropic()
        logger.info(f"🔄 Base tools (MCP): {len(agent.tools)} tools\n")

        # Enable S3 skills if requested
        if request.include_s3_skills:
            logger.info("📦 Enabling S3 skills...")
            agent.enable_s3_skills()
            logger.info(f"   S3 skills added to tools\n")

        # Enable computer tools if requested
        if request.use_computer_tools:
            logger.info("🖥️  Enabling computer use tools...")
            agent.enable_computer_tools()
            logger.info(f"   Computer tools added\n")

        logger.info(f"   Total tools available: {len(agent.tools)}\n")

        # Build system prompt
        system_prompt = agent._build_system_prompt()

        logger.info("📝 System Prompt Generated")
        logger.info(f"   Length: {len(system_prompt)} characters")
        logger.info(f"   Contains S3 Skills: {agent.skills_loaded}")
        logger.info(f"   MCP Tools Available: {len(agent.tools)}\n")

        # Execute agent loop
        messages = [{"role": "user", "content": request.prompt}]
        turn = 0
        final_response = ""

        logger.info("🔄 Starting Multi-Turn Execution Loop\n")

        while turn < request.max_turns:
            turn += 1
            logger.info(f"{'='*80}")
            logger.info(f"TURN {turn}/{request.max_turns}")
            logger.info(f"{'='*80}")

            # Call Claude API
            logger.info("📡 Calling Claude API...")
            response = await agent.anthropic_client.messages.create(
                model=agent.model,
                max_tokens=4096,
                temperature=request.temperature,
                system=system_prompt,
                messages=messages,
                tools=agent.tools
            )

            logger.info(f"✅ Response received (stop_reason: {response.stop_reason})")

            # Process response
            if response.stop_reason == "end_turn":
                # Final response
                for block in response.content:
                    if hasattr(block, 'text'):
                        final_response = block.text
                        logger.info(f"\n📄 FINAL RESPONSE:\n{final_response}\n")
                break

            elif response.stop_reason == "tool_use":
                # Tool calls
                logger.info(f"\n🔧 TOOL CALLS REQUESTED:")

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_id = block.id

                        logger.info(f"\n   Tool: {tool_name}")
                        logger.info(f"   Input: {tool_input}")

                        tools_used.append(tool_name)

                        # Execute tool (native or MCP)
                        try:
                            # Check if this is a native Anthropic tool
                            native_tools = ["bash", "str_replace_based_edit_tool", "computer"]

                            if tool_name in native_tools:
                                # Execute via native handler
                                logger.info(f"   🖥️  Executing native tool...")
                                from orchestrator.native_tool_handlers import get_handler
                                handler = get_handler()

                                if tool_name == "bash":
                                    result = handler.handle_bash(tool_input)
                                elif tool_name == "str_replace_based_edit_tool":
                                    result = handler.handle_text_editor(tool_input)
                                elif tool_name == "computer":
                                    result = handler.handle_computer(tool_input)
                                else:
                                    result = {"error": f"Unknown native tool: {tool_name}"}

                                # Convert result to string
                                if isinstance(result, dict):
                                    import json
                                    result = json.dumps(result, indent=2)

                                logger.info(f"   ✅ Native tool executed")
                            elif agent.skill_loader and agent.skills_loaded:
                                # Check if this is an S3 skill
                                logger.info(f"   🔍 Checking if '{tool_name}' is an S3 skill...")
                                logger.info(f"      skill_loader exists: {agent.skill_loader is not None}")
                                logger.info(f"      skills_loaded: {agent.skills_loaded}")
                                skills = agent.skill_loader.get_skills()
                                logger.info(f"      Available skills: {list(skills.keys())}")
                                logger.info(f"      Tool name: '{tool_name}'")
                                logger.info(f"      Match: {tool_name in skills}")

                                if tool_name in skills:
                                    # Execute S3 skill
                                    logger.info(f"   📦 Executing S3 skill...")
                                    from orchestrator.s3_skill_executor import execute_s3_skill
                                    result = execute_s3_skill(agent.skill_loader, tool_name, tool_input)
                                    logger.info(f"   ✅ S3 skill executed")
                                else:
                                    # Not an S3 skill, try MCP
                                    logger.info(f"   ⚙️  Executing via MCP...")
                                    result = agent.mcp_client.call_tool(tool_name, tool_input)
                                    logger.info(f"   ✅ MCP tool executed")
                            else:
                                # Execute via MCP
                                logger.info(f"   ⚙️  Executing via MCP...")
                                result = agent.mcp_client.call_tool(tool_name, tool_input)
                                logger.info(f"   ✅ MCP tool executed")

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": str(result)
                            })

                            # Upload screenshots to S3 if computer tool was used
                            # Computer tool can come from MCP with various names (computer, computer_20250124, etc.)
                            if "computer" in tool_name.lower() and S3_STORAGE_AVAILABLE:
                                # Check if action was screenshot
                                action = tool_input.get("action")
                                if action == "screenshot":
                                    try:
                                        # Extract base64 image from result
                                        import base64
                                        import json
                                        result_dict = json.loads(result) if isinstance(result, str) else result
                                        if "base64_image" in result_dict:
                                            image_bytes = base64.b64decode(result_dict["base64_image"])
                                            s3_url = upload_screenshot_to_s3(
                                                image_bytes,
                                                session_id,
                                                f"screenshot_{turn}.png"
                                            )
                                            if s3_url:
                                                artifacts["screenshots"].append(s3_url)
                                                logger.info(f"   📸 Screenshot uploaded to S3: {s3_url}")
                                    except Exception as upload_error:
                                        logger.warning(f"   ⚠️  Screenshot upload failed: {upload_error}")

                        except Exception as e:
                            logger.error(f"   ❌ Error: {e}")
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": f"Error: {str(e)}",
                                "is_error": True
                            })

                # Add assistant message and tool results
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

                logger.info(f"\n{'='*80}\n")

            else:
                # Unexpected stop reason
                logger.warning(f"⚠️  Unexpected stop_reason: {response.stop_reason}")
                break

        execution_time = (datetime.now() - start_time).total_seconds()

        # Get active servers and skills
        mcp_servers_active = list(agent.mcp_client.servers.keys())
        s3_skills_loaded = []
        if agent.skill_loader:
            s3_skills_loaded = list(agent.skill_loader.get_skills().keys())

        logger.info("="*80)
        logger.info("✅ EXECUTION COMPLETE")
        logger.info("="*80)
        logger.info(f"Total Turns: {turn}")
        logger.info(f"Tools Used: {tools_used}")
        logger.info(f"Execution Time: {execution_time:.2f}s")
        logger.info("="*80 + "\n")

        return AgentResponse(
            success=True,
            prompt=request.prompt,
            response=final_response or "No final response (max turns reached)",
            turns=turn,
            tools_used=list(set(tools_used)),
            mcp_servers_active=mcp_servers_active,
            s3_skills_loaded=s3_skills_loaded,
            execution_time_seconds=execution_time,
            artifacts=artifacts if (artifacts["screenshots"] or artifacts["files"]) else None
        )

    except Exception as e:
        execution_time = (datetime.now() - start_time).total_seconds()
        logger.error("="*80)
        logger.error("❌ EXECUTION FAILED")
        logger.error("="*80)
        logger.error(f"Error: {str(e)}")
        logger.error("="*80 + "\n")

        return AgentResponse(
            success=False,
            prompt=request.prompt,
            response="",
            turns=turn,
            tools_used=tools_used,
            mcp_servers_active=[],
            s3_skills_loaded=[],
            execution_time_seconds=execution_time,
            artifacts=None,
            error=str(e)
        )


@app.post("/execute/stream")
async def execute_agent_stream(request: AgentRequest):
    """Execute agent with streaming responses (Server-Sent Events)"""
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    async def event_generator():
        """Generate Server-Sent Events for streaming"""
        try:
            # Send initial event
            yield f"data: {json.dumps({'type': 'start', 'prompt': request.prompt})}\n\n"

            start_time = datetime.now()
            tools_used = []

            # Reset tools to base (MCP tools only) for each request
            agent.tools = agent.mcp_client.get_tools_for_anthropic()
            yield f"data: {json.dumps({'type': 'status', 'message': f'Base tools (MCP): {len(agent.tools)} tools'})}\n\n"

            # Enable S3 skills if requested
            if request.include_s3_skills:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Enabling S3 skills...'})}\n\n"
                agent.enable_s3_skills()
                yield f"data: {json.dumps({'type': 'status', 'message': 'S3 skills enabled'})}\n\n"

            # Enable computer tools if requested
            if request.use_computer_tools:
                yield f"data: {json.dumps({'type': 'status', 'message': 'Enabling computer use tools...'})}\n\n"
                agent.enable_computer_tools()
                yield f"data: {json.dumps({'type': 'status', 'message': 'Computer tools enabled'})}\n\n"

            yield f"data: {json.dumps({'type': 'status', 'message': f'Total tools available: {len(agent.tools)}'})}\n\n"

            # Build system prompt
            system_prompt = agent._build_system_prompt()
            yield f"data: {json.dumps({'type': 'system_prompt_ready', 'length': len(system_prompt)})}\n\n"

            # Execute agent loop
            messages = [{"role": "user", "content": request.prompt}]
            turn = 0

            while turn < request.max_turns:
                turn += 1
                yield f"data: {json.dumps({'type': 'turn_start', 'turn': turn, 'max_turns': request.max_turns})}\n\n"

                # Call Claude API
                yield f"data: {json.dumps({'type': 'api_call', 'message': 'Calling Claude API...'})}\n\n"

                response = await agent.anthropic_client.messages.create(
                    model=agent.model,
                    max_tokens=4096,
                    temperature=request.temperature,
                    system=system_prompt,
                    messages=messages,
                    tools=agent.tools
                )

                yield f"data: {json.dumps({'type': 'api_response', 'stop_reason': response.stop_reason})}\n\n"

                # Process response
                if response.stop_reason == "end_turn":
                    # Final response
                    for block in response.content:
                        if hasattr(block, 'text'):
                            final_response = block.text
                            yield f"data: {json.dumps({'type': 'final_response', 'text': final_response})}\n\n"
                    break

                elif response.stop_reason == "tool_use":
                    # Tool calls
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            tool_name = block.name
                            tool_input = block.input
                            tool_id = block.id

                            yield f"data: {json.dumps({'type': 'tool_call', 'tool': tool_name, 'input': tool_input})}\n\n"

                            tools_used.append(tool_name)

                            # Execute tool
                            try:
                                native_tools = ["bash", "str_replace_based_edit_tool", "computer"]

                                if tool_name in native_tools:
                                    from orchestrator.native_tool_handlers import get_handler
                                    handler = get_handler()
                                    result = await handler.execute_tool(tool_name, tool_input)
                                    result_str = str(result)
                                elif agent.skill_loader and agent.skills_loaded:
                                    skills = agent.skill_loader.get_skills()
                                    if tool_name in skills:
                                        from orchestrator.s3_skill_executor import execute_s3_skill
                                        result_str = execute_s3_skill(agent.skill_loader, tool_name, tool_input)
                                    else:
                                        result_str = agent.mcp_client.call_tool(tool_name, tool_input)
                                else:
                                    result_str = agent.mcp_client.call_tool(tool_name, tool_input)

                                yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': result_str[:500]})}\n\n"

                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": result_str
                                })

                            except Exception as e:
                                error_msg = str(e)
                                yield f"data: {json.dumps({'type': 'tool_error', 'tool': tool_name, 'error': error_msg})}\n\n"

                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": f"Error: {error_msg}",
                                    "is_error": True
                                })

                    # Add assistant message with tool results
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})

                else:
                    break

            # Send completion event
            execution_time = (datetime.now() - start_time).total_seconds()
            yield f"data: {json.dumps({'type': 'complete', 'turns': turn, 'tools_used': tools_used, 'execution_time': execution_time})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ============================================================
# WebSocket Endpoint (Real-time streaming)
# ============================================================

@app.websocket("/execute/ws")
async def websocket_execute(websocket: WebSocket):
    """
    WebSocket endpoint for real-time agent execution streaming

    Client sends JSON: {
        "prompt": "...",
        "max_turns": 10,
        "include_s3_skills": false,
        "use_computer_tools": false
    }

    Server sends JSON events:
    - {"type": "status", "message": "..."}
    - {"type": "system_prompt_ready", "length": 1234}
    - {"type": "content", "text": "..."}
    - {"type": "tool_call", "tool": "...", "input": {...}}
    - {"type": "tool_result", "tool": "...", "result": "..."}
    - {"type": "final_response", "text": "..."}
    - {"type": "complete"}
    """
    await websocket.accept()
    logger.info("WebSocket connection established")

    # Check if agent is initialized
    if not agent:
        await websocket.send_json({"type": "error", "message": "Agent not initialized"})
        await websocket.close()
        return

    try:
        # Receive request from client
        data = await websocket.receive_json()

        prompt = data.get("prompt", "")
        max_turns = data.get("max_turns", 10)
        include_s3_skills = data.get("include_s3_skills", False)
        use_computer_tools = data.get("use_computer_tools", False)

        if not prompt:
            await websocket.send_json({"type": "error", "message": "Prompt is required"})
            await websocket.close()
            return

        await websocket.send_json({"type": "status", "message": "Agent ready"})

        # Reset tools to base (MCP tools only) for each request
        agent.tools = agent.mcp_client.get_tools_for_anthropic()
        await websocket.send_json({"type": "status", "message": f"Base tools (MCP): {len(agent.tools)} tools"})

        # Enable S3 skills if requested
        if include_s3_skills:
            await websocket.send_json({"type": "status", "message": "Enabling S3 skills..."})
            agent.enable_s3_skills()
            await websocket.send_json({"type": "status", "message": "S3 skills enabled"})

        # Enable computer tools if requested
        if use_computer_tools:
            await websocket.send_json({"type": "status", "message": "Enabling computer use tools..."})
            agent.enable_computer_tools()
            await websocket.send_json({"type": "status", "message": "Computer tools enabled"})

        await websocket.send_json({"type": "status", "message": f"Total tools available: {len(agent.tools)}"})

        # Build system prompt
        system_prompt = agent._build_system_prompt()
        await websocket.send_json({"type": "system_prompt_ready", "length": len(system_prompt)})

        # Start execution
        await websocket.send_json({"type": "status", "message": f"Starting agent execution (max {max_turns} turns)"})

        # Run agent loop
        messages = [{"role": "user", "content": prompt}]
        tools_used = []

        for turn in range(max_turns):
            await websocket.send_json({"type": "status", "message": f"Turn {turn + 1}/{max_turns}"})

            try:
                # Call Claude
                response = await agent.anthropic_client.messages.create(
                    model=agent.model,
                    max_tokens=4096,
                    system=system_prompt,
                    messages=messages,
                    tools=agent.tools
                )

                # Handle content
                for block in response.content:
                    if block.type == "text":
                        await websocket.send_json({"type": "content", "text": block.text})

                # Check stop reason
                if response.stop_reason == "end_turn":
                    final_text = "".join([b.text for b in response.content if b.type == "text"])
                    await websocket.send_json({"type": "final_response", "text": final_text})
                    await websocket.send_json({"type": "complete"})
                    break

                if response.stop_reason != "tool_use":
                    await websocket.send_json({"type": "complete"})
                    break

                # Tool calls
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_id = block.id

                        await websocket.send_json({"type": "tool_call", "tool": tool_name, "input": tool_input})

                        tools_used.append(tool_name)

                        # Execute tool
                        try:
                            native_tools = ["bash", "str_replace_based_edit_tool", "computer"]

                            if tool_name in native_tools:
                                from orchestrator.native_tool_handlers import get_handler
                                handler = get_handler()
                                result = await handler.execute_tool(tool_name, tool_input)
                                result_str = str(result)
                            elif agent.skill_loader and agent.skills_loaded:
                                skills = agent.skill_loader.get_skills()
                                if tool_name in skills:
                                    from orchestrator.s3_skill_executor import execute_s3_skill
                                    result_str = execute_s3_skill(agent.skill_loader, tool_name, tool_input)
                                else:
                                    result_str = agent.mcp_client.call_tool(tool_name, tool_input)
                            else:
                                result_str = agent.mcp_client.call_tool(tool_name, tool_input)

                            await websocket.send_json({"type": "tool_result", "tool": tool_name, "result": result_str[:500]})

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": result_str
                            })

                        except Exception as e:
                            error_msg = str(e)
                            await websocket.send_json({"type": "tool_error", "tool": tool_name, "error": error_msg})

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tool_id,
                                "content": f"Error: {error_msg}",
                                "is_error": True
                            })

                # Add assistant response + tool results to conversation
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            except Exception as e:
                logger.error(f"Error in agent execution: {e}", exc_info=True)
                await websocket.send_json({"type": "error", "message": str(e)})
                break

        await websocket.send_json({"type": "status", "message": "Execution complete"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass
    finally:
        try:
            await websocket.close()
        except:
            pass


# ============================================================
# Error Handlers
# ============================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"❌ Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc)
        }
    )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    logger.info("="*80)
    logger.info("🚀 Starting FastAPI Server - E2E Testing API")
    logger.info("="*80)
    logger.info("Endpoints:")
    logger.info("  - http://localhost:8003/")
    logger.info("  - http://localhost:8003/status")
    logger.info("  - http://localhost:8003/execute")
    logger.info("  - http://localhost:8003/execute/stream (SSE)")
    logger.info("  - ws://localhost:8003/execute/ws (WebSocket)")
    logger.info("  - http://localhost:8003/health")
    logger.info("="*80 + "\n")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8003,
        log_level="info"
    )
