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
            from nanobot.config.loader import convert_keys
            validated = Config.model_validate(convert_keys(update.config))
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
                        agent, message, session_id, websocket
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
            
            # Get provider
            api_key = config.get_api_key()
            api_base = config.get_api_base()
            model = config.agents.defaults.model
            
            provider = LiteLLMProvider(
                api_key=api_key,
                api_base=api_base,
                default_model=model
            )
            
            # Create agent loop
            app.state.agent = AgentLoop(
                bus=app.state.bus,
                provider=provider,
                workspace=config.workspace_path,
                model=model,
                max_iterations=config.agents.defaults.max_tool_iterations,
                brave_api_key=config.tools.web.search.api_key or None,
                exec_config=config.tools.exec,
                restrict_to_workspace=config.tools.restrict_to_workspace,
            )
        
        return app.state.agent


async def _process_with_updates(
    agent: AgentLoop,
    message: str,
    session_id: str,
    websocket: WebSocket
) -> str:
    """Process a message and send tool execution updates via WebSocket."""
    from nanobot.bus.events import InboundMessage
    import json
    
    # Get session
    session = agent.sessions.get_or_create(session_id)
    
    # Build context
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
    
    # Build messages
    messages = agent.context.build_messages(
        history=session.get_history(),
        current_message=message,
        channel="ui",
        chat_id=msg.chat_id,
    )
    
    # Agent loop with updates
    iteration = 0
    final_content = None
    
    while iteration < agent.max_iterations:
        iteration += 1
        
        # Call LLM
        response = await agent.provider.chat(
            messages=messages,
            tools=agent.tools.get_definitions(),
            model=agent.model
        )
        
        if response.has_tool_calls:
            # Send tool call info
            tool_names = [tc.name for tc in response.tool_calls]
            await websocket.send_json({
                "type": "tools",
                "content": f"Executing: {', '.join(tool_names)}"
            })
            
            # Add assistant message
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
            
            # Execute tools
            for tool_call in response.tool_calls:
                result = await agent.tools.execute(tool_call.name, tool_call.arguments)
                messages = agent.context.add_tool_result(
                    messages, tool_call.id, tool_call.name, result
                )
                
                # Send brief result update
                result_preview = result[:200] + "..." if len(result) > 200 else result
                await websocket.send_json({
                    "type": "tool_result",
                    "tool": tool_call.name,
                    "content": result_preview
                })
        else:
            final_content = response.content
            break
    
    if final_content is None:
        final_content = "Processing complete."
    
    # Save to session
    session.add_message("user", message)
    session.add_message("assistant", final_content)
    agent.sessions.save(session)
    
    return final_content
