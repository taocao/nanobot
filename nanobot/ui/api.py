"""FastAPI application for nanobot web UI."""

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from nanobot.config.loader import load_config, save_config, get_config_path
from nanobot.config.schema import Config
from nanobot.session.manager import SessionManager
from nanobot.bus.queue import MessageBus
from nanobot.bus.events import InboundMessage
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.agent.loop import AgentLoop
from nanobot.ui.execution_log import ExecutionLog


class ChatMessage(BaseModel):
    """Chat message request."""
    message: str
    session_id: str = "ui:default"


class ConfigUpdate(BaseModel):
    """Configuration update request."""
    config: dict[str, Any]


def create_app(config: Config | None = None) -> FastAPI:
    """Create the FastAPI application.
    
    Args:
        config: Optional config to use. Loads from disk if not provided.
    
    Returns:
        Configured FastAPI application.
    """
    if config is None:
        config = load_config()
    
    app = FastAPI(
        title="nanobot UI",
        description="Web interface for nanobot",
        version="0.1.0"
    )
    
    # Store config and services in app state
    app.state.config = config
    app.state.sessions = SessionManager(config.workspace_path)
    app.state.bus = MessageBus()
    app.state.agent = None
    app.state.agent_lock = asyncio.Lock()
    from nanobot.config.paths import get_logs_dir
    app.state.execution_log = ExecutionLog(log_dir=get_logs_dir())
    
    # Static files directory
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    # Routes
    @app.get("/")
    async def index():
        """Serve the main UI page."""
        index_path = static_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {"error": "UI not found. Run from installed package."}
    
    @app.get("/api/config")
    async def get_config():
        """Get current configuration."""
        config_path = get_config_path()
        if config_path.exists():
            with open(config_path) as f:
                return json.load(f)
        return {}
    
    @app.put("/api/config")
    async def update_config(update: ConfigUpdate):
        """Update configuration."""
        try:
            # Validate by parsing through schema
            validated = Config.model_validate(update.config)
            save_config(validated)
            app.state.config = validated
            # Reset agent so it picks up new config
            app.state.agent = None
            return {"status": "ok", "message": "Configuration saved"}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    @app.get("/api/sessions")
    async def list_sessions():
        """List all conversation sessions."""
        sessions = app.state.sessions.list_sessions()
        return {"sessions": sessions}
    
    @app.get("/api/sessions/{session_key:path}")
    async def get_session(session_key: str):
        """Get a specific session's history."""
        session = app.state.sessions.get_or_create(session_key)
        return {
            "key": session.key,
            "messages": session.messages,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }
    
    @app.delete("/api/sessions/{session_key:path}")
    async def delete_session(session_key: str):
        """Delete a session."""
        deleted = app.state.sessions.delete(session_key)
        if deleted:
            return {"status": "ok", "message": "Session deleted"}
        raise HTTPException(status_code=404, detail="Session not found")
    
    # ========================================
    # Execution Log Endpoints
    # ========================================
    @app.get("/api/logs")
    async def list_logs(session_id: str | None = None, limit: int = 50):
        """List execution logs, optionally filtered by session."""
        logs = app.state.execution_log.list_logs(session_id=session_id, limit=limit)
        return {"logs": logs}
    
    @app.get("/api/logs/{session_id}/{log_id}")
    async def get_log(session_id: str, log_id: str):
        """Get a specific execution log."""
        log = app.state.execution_log.get_log(session_id, log_id)
        if log is None:
            raise HTTPException(status_code=404, detail="Log not found")
        return log
    
    @app.delete("/api/logs/{session_id}/{log_id}")
    async def delete_log(session_id: str, log_id: str):
        """Delete a specific execution log."""
        deleted = app.state.execution_log.delete_log(session_id, log_id)
        if deleted:
            return {"status": "ok", "message": "Log deleted"}
        raise HTTPException(status_code=404, detail="Log not found")
    
    # ========================================
    # Dashboard Stats Endpoint
    # ========================================
    @app.get("/api/dashboard/stats")
    async def dashboard_stats(limit: int = 100):
        """Aggregate stats from execution logs for the governance dashboard."""
        logs = app.state.execution_log.list_logs(limit=limit)
        
        if not logs:
            return {
                "total_requests": 0, "avg_iterations": 0,
                "total_tool_calls": 0, "estimated_cost": 0,
                "tool_stats": {}, "recent_requests": [], "model_usage": {}
            }
        
        total_requests = len(logs)
        total_iterations = 0
        total_tool_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0
        tool_stats: dict[str, dict[str, int]] = {}  # name -> {success, fail, total}
        model_usage: dict[str, int] = {}
        recent_requests = []
        
        for log_summary in logs:
            session_id = log_summary.get("session_id", "")
            log_id = log_summary.get("log_id", "")
            model = log_summary.get("model", "unknown")
            iterations = log_summary.get("iterations", 0)
            tool_count = log_summary.get("tool_count", 0)
            
            total_iterations += iterations
            total_tool_calls += tool_count
            model_usage[model] = model_usage.get(model, 0) + 1
            
            # Fetch full log for detailed analysis
            full_log = app.state.execution_log.get_log(session_id, log_id)
            if not full_log:
                continue
            
            # Token metrics
            metrics = full_log.get("metrics", {})
            total_input_tokens += metrics.get("context_tokens", 0)
            total_output_tokens += metrics.get("output_tokens", 0)
            
            # Tool-level stats
            request_tools = []
            for te in full_log.get("tool_executions", []):
                name = te.get("tool_name", "unknown")
                result = te.get("result", "")
                elapsed = te.get("elapsed_seconds", 0)
                is_error = result.startswith("Error:") or result.startswith("❌")
                
                if name not in tool_stats:
                    tool_stats[name] = {"success": 0, "fail": 0, "total": 0, "total_time": 0}
                tool_stats[name]["total"] += 1
                tool_stats[name]["total_time"] += elapsed
                if is_error:
                    tool_stats[name]["fail"] += 1
                else:
                    tool_stats[name]["success"] += 1
                
                request_tools.append({
                    "name": name,
                    "elapsed": round(elapsed, 2),
                    "success": not is_error,
                    "result_preview": result[:120]
                })
            
            recent_requests.append({
                "log_id": log_id,
                "session_id": session_id,
                "started_at": full_log.get("started_at", ""),
                "message_preview": full_log.get("user_message", "")[:100],
                "model": model,
                "iterations": iterations,
                "tool_count": tool_count,
                "tools": request_tools,
                "input_tokens": metrics.get("context_tokens", 0),
                "output_tokens": metrics.get("output_tokens", 0),
                "has_error": bool(full_log.get("error")),
                "wasted_calls": sum(1 for t in request_tools if not t["success"]),
            })
        
        # Cost estimation (rough, per 1M tokens)
        # Using average pricing: ~$3/1M input, ~$15/1M output
        estimated_cost = (total_input_tokens * 3 / 1_000_000) + (total_output_tokens * 15 / 1_000_000)
        
        # Add success rate to tool_stats
        for name, stats in tool_stats.items():
            stats["success_rate"] = round(
                stats["success"] / stats["total"] * 100, 1
            ) if stats["total"] > 0 else 0
            stats["avg_time"] = round(
                stats["total_time"] / stats["total"], 2
            ) if stats["total"] > 0 else 0
        
        return {
            "total_requests": total_requests,
            "avg_iterations": round(total_iterations / total_requests, 1) if total_requests else 0,
            "total_tool_calls": total_tool_calls,
            "estimated_cost": round(estimated_cost, 4),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "tool_stats": tool_stats,
            "model_usage": model_usage,
            "recent_requests": recent_requests[:20],  # Last 20
        }
    
    @app.get("/api/status")
    async def get_status():
        """Get nanobot status."""
        cfg = app.state.config
        return {
            "model": cfg.agents.defaults.model,
            "workspace": str(cfg.workspace_path),
            "has_api_key": bool(cfg.get_api_key()),
        }
    
    @app.post("/api/chat")
    async def chat(msg: ChatMessage):
        """Send a message and get response (non-streaming)."""
        agent = await _get_or_create_agent(app)
        response = await agent.process_direct(
            content=msg.message,
            session_key=msg.session_id,
            channel="ui",
            chat_id=msg.session_id.split(":")[-1]
        )
        return {"response": response}
    
    @app.websocket("/ws/chat")
    async def websocket_chat(websocket: WebSocket):
        """WebSocket endpoint for streaming chat."""
        await websocket.accept()
        
        try:
            while True:
                # Receive message
                data = await websocket.receive_json()
                message = data.get("message", "")
                session_id = data.get("session_id", "ui:default")
                
                if not message:
                    await websocket.send_json({"type": "error", "content": "Empty message"})
                    continue
                
                # Get or create agent
                agent = await _get_or_create_agent(app)
                
                # Send status
                await websocket.send_json({"type": "status", "content": "Processing..."})
                
                # Process with streaming updates
                try:
                    response = await _process_with_updates(
                        agent, message, session_id, websocket,
                        execution_log=app.state.execution_log
                    )
                    await websocket.send_json({"type": "response", "content": response})
                except Exception as e:
                    await websocket.send_json({"type": "error", "content": str(e)})
                    
        except WebSocketDisconnect:
            pass
    
    return app


async def _get_or_create_agent(app: FastAPI) -> AgentLoop:
    """Get or create the agent loop instance."""
    async with app.state.agent_lock:
        if app.state.agent is None:
            config = app.state.config
            model = config.agents.defaults.model
            
            # Use the same provider factory as the CLI
            from nanobot.cli.commands import _make_provider
            provider = _make_provider(config)
            
            # Create agent loop (matching upstream constructor)
            app.state.agent = AgentLoop(
                bus=app.state.bus,
                provider=provider,
                workspace=config.workspace_path,
                model=model,
                temperature=config.agents.defaults.temperature,
                max_tokens=config.agents.defaults.max_tokens,
                max_iterations=config.agents.defaults.max_tool_iterations,
                memory_window=config.agents.defaults.memory_window,
                reasoning_effort=config.agents.defaults.reasoning_effort,
                brave_api_key=config.tools.web.search.api_key or None,
                web_proxy=config.tools.web.proxy or None,
                exec_config=config.tools.exec,
                restrict_to_workspace=config.tools.restrict_to_workspace,
                session_manager=app.state.sessions,
                mcp_servers=config.tools.mcp_servers,
                channels_config=config.channels,
            )
        
        return app.state.agent


async def _process_with_updates(
    agent: AgentLoop,
    message: str,
    session_id: str,
    websocket: WebSocket,
    execution_log: ExecutionLog | None = None
) -> str:
    """Process a message and send detailed debug updates via WebSocket.
    
    This function provides educational insight into how the agent works:
    - Step-by-step processing visualization
    - Token usage metrics
    - System prompt display
    - Tool execution details
    - Persistent execution logging
    """
    import json
    import time
    
    # Create execution record for persistent logging
    record = None
    if execution_log:
        record = execution_log.create_record(session_id, message, agent.model)
    
    # ========================================
    # Step 1: Receive Input
    # ========================================
    step_detail = {
        "full_message": message,
        "message_length": len(message),
        "timestamp": time.strftime("%H:%M:%S")
    }
    await websocket.send_json({
        "type": "debug_step",
        "step": "receive_input",
        "status": "complete",
        "details": f"Received: {message[:50]}...",
        "step_detail": step_detail
    })
    if record:
        record.add_step("receive_input", "complete", f"Received {len(message)} chars", **step_detail)
    
    # ========================================
    # Step 2: Build Context
    # ========================================
    await websocket.send_json({
        "type": "debug_step",
        "step": "build_context",
        "status": "start",
        "details": "Building context with history and system prompt..."
    })
    
    # Get session
    session = agent.sessions.get_or_create(session_id)
    
    # Build context message
    from nanobot.bus.events import InboundMessage
    msg = InboundMessage(
        channel="ui",
        sender_id="user",
        chat_id=session_id.split(":")[-1],
        content=message
    )
    
    # Update tool contexts
    from nanobot.agent.tools.message import MessageTool
    from nanobot.agent.tools.spawn import SpawnTool
    from nanobot.agent.tools.cron import CronTool
    
    message_tool = agent.tools.get("message")
    if isinstance(message_tool, MessageTool):
        message_tool.set_context(msg.channel, msg.chat_id)
    
    spawn_tool = agent.tools.get("spawn")
    if isinstance(spawn_tool, SpawnTool):
        spawn_tool.set_context(msg.channel, msg.chat_id)
    
    cron_tool = agent.tools.get("cron")
    if isinstance(cron_tool, CronTool):
        cron_tool.set_context(msg.channel, msg.chat_id)
    
    # Build messages (this includes system prompt)
    messages = agent.context.build_messages(
        history=session.get_history(),
        current_message=message,
        channel="ui",
        chat_id=msg.chat_id,
    )
    
    # Extract and send system prompt for educational display
    system_prompt = ""
    for m in messages:
        if m.get("role") == "system":
            system_prompt = m.get("content", "")
            break
    
    if system_prompt:
        await websocket.send_json({
            "type": "debug_prompt",
            "content": system_prompt
        })
    
    # Estimate token counts (rough: ~4 chars per token)
    context_tokens = sum(len(str(m.get("content", ""))) // 4 for m in messages)
    
    # Send metrics
    await websocket.send_json({
        "type": "debug_metrics",
        "content": {
            "model": agent.model,
            "contextTokens": context_tokens,
            "contextLimit": 128000,  # Default assumption
            "inputTokens": len(message) // 4,
            "outputTokens": 0
        }
    })
    
    step_detail = {
        "message_count": len(messages),
        "history_count": len(session.get_history()),
        "context_tokens": context_tokens,
        "system_prompt": system_prompt,
        "messages_summary": [{
            "role": m.get("role", "unknown"),
            "content_length": len(str(m.get("content", "")))
        } for m in messages]
    }
    await websocket.send_json({
        "type": "debug_step",
        "step": "build_context",
        "status": "complete",
        "details": f"Context built: {len(messages)} messages, ~{context_tokens} tokens",
        "step_detail": step_detail
    })
    if record:
        record.add_step("build_context", "complete", f"{len(messages)} messages, ~{context_tokens} tokens", **step_detail)
        record.system_prompt = system_prompt
    
    # ========================================
    # Step 3: Call LLM (loop)
    # ========================================
    iteration = 0
    final_content = None
    total_output_tokens = 0
    
    while iteration < agent.max_iterations:
        iteration += 1
        
        await websocket.send_json({
            "type": "debug_step",
            "step": "call_llm",
            "status": "start",
            "details": f"Iteration {iteration}: Calling {agent.model}..."
        })
        
        start_time = time.time()
        
        # Call LLM
        response = await agent.provider.chat(
            messages=messages,
            tools=agent.tools.get_definitions(),
            model=agent.model
        )
        
        elapsed = time.time() - start_time
        
        # Update output tokens from response
        output_tokens = response.usage.get("completion_tokens", 0)
        if output_tokens == 0 and response.content:
            output_tokens = len(response.content) // 4
        total_output_tokens += output_tokens
        
        step_detail = {
            "iteration": iteration,
            "model": agent.model,
            "elapsed_seconds": round(elapsed, 2),
            "output_tokens": output_tokens,
            "has_tool_calls": response.has_tool_calls,
            "tool_calls": [
                {"name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ] if response.has_tool_calls else [],
            "response_content": response.content or ""
        }
        await websocket.send_json({
            "type": "debug_step",
            "step": "call_llm",
            "status": "complete",
            "details": f"LLM responded in {elapsed:.1f}s, ~{output_tokens} tokens",
            "step_detail": step_detail
        })
        if record:
            record.add_step("call_llm", "complete", f"Iteration {iteration}: {elapsed:.1f}s, {output_tokens} tokens", **step_detail)
        
        # Update metrics with output tokens
        await websocket.send_json({
            "type": "debug_metrics",
            "content": {
                "model": agent.model,
                "contextTokens": context_tokens + total_output_tokens,
                "contextLimit": 128000,
                "inputTokens": len(message) // 4,
                "outputTokens": total_output_tokens
            }
        })
        
        if response.has_tool_calls:
            # ========================================
            # Step 4: Execute Tools
            # ========================================
            await websocket.send_json({
                "type": "debug_step",
                "step": "execute_tools",
                "status": "start",
                "details": f"Executing {len(response.tool_calls)} tool(s)..."
            })
            
            tool_names = [tc.name for tc in response.tool_calls]
            await websocket.send_json({
                "type": "tools",
                "content": f"Executing: {', '.join(tool_names)}"
            })
            
            # Add assistant message with tool calls
            tool_call_dicts = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments)
                    }
                }
                for tc in response.tool_calls
            ]
            messages = agent.context.add_assistant_message(
                messages, response.content, tool_call_dicts
            )
            
            # Execute each tool and send detailed results
            tool_execution_details = []
            for tool_call in response.tool_calls:
                tool_start = time.time()
                result = await agent.tools.execute(tool_call.name, tool_call.arguments)
                tool_elapsed = time.time() - tool_start
                
                messages = agent.context.add_tool_result(
                    messages, tool_call.id, tool_call.name, result
                )
                
                # Collect for step_detail
                tool_execution_details.append({
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "result": result,
                    "elapsed_seconds": round(tool_elapsed, 2)
                })
                
                # Send detailed tool result via WebSocket
                await websocket.send_json({
                    "type": "tool_result",
                    "tool": tool_call.name,
                    "arguments": tool_call.arguments,
                    "content": result,
                    "elapsed": f"{tool_elapsed:.2f}s"
                })
                if record:
                    record.add_tool_execution(tool_call.name, tool_call.arguments, result, tool_elapsed)
            
            step_detail = {
                "tool_count": len(response.tool_calls),
                "tools": tool_execution_details
            }
            await websocket.send_json({
                "type": "debug_step",
                "step": "execute_tools",
                "status": "complete",
                "details": f"Completed {len(response.tool_calls)} tool(s)",
                "step_detail": step_detail
            })
            if record:
                record.add_step("execute_tools", "complete", f"{len(response.tool_calls)} tools", **step_detail)
        else:
            # No tool calls - we have the final response
            final_content = response.content
            break
    
    # ========================================
    # Step 5: Generate Response
    # ========================================
    await websocket.send_json({
        "type": "debug_step",
        "step": "generate_response",
        "status": "start",
        "details": "Preparing final response..."
    })
    
    if final_content is None:
        final_content = "Processing complete (max iterations reached)."
    
    # Save to session
    session.add_message("user", message)
    session.add_message("assistant", final_content)
    agent.sessions.save(session)
    
    step_detail = {
        "response_length": len(final_content),
        "total_iterations": iteration,
        "session_id": session_id,
        "full_response": final_content or ""
    }
    await websocket.send_json({
        "type": "debug_step",
        "step": "generate_response",
        "status": "complete",
        "details": f"Response generated, session saved ({len(final_content)} chars)",
        "step_detail": step_detail
    })
    
    # Save execution log for persistence
    if record:
        record.assistant_response = final_content
        record.iterations = iteration
        record.metrics = {
            "context_tokens": context_tokens,
            "output_tokens": total_output_tokens,
            "model": agent.model
        }
        record.add_step("generate_response", "complete", f"{len(final_content)} chars", **step_detail)
        log_id = execution_log.save(record)
        # Send log_id to client for reference
        await websocket.send_json({
            "type": "execution_log_saved",
            "log_id": log_id,
            "session_id": session_id
        })
    
    return final_content

