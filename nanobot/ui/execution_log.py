"""Execution logging for debug persistence.

This module provides persistent storage for execution details,
allowing users to review past requests even after closing the chat.
"""

import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import Any
from dataclasses import dataclass, field, asdict

from nanobot.utils.helpers import ensure_dir


@dataclass
class StepDetail:
    """Details for a single processing step."""
    step: str  # receive_input, build_context, call_llm, execute_tools, generate_response
    status: str  # start, complete, error
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    summary: str = ""  # Short summary for display
    details: dict[str, Any] = field(default_factory=dict)  # Full details


@dataclass
class ToolExecution:
    """Details for a single tool execution."""
    tool_name: str
    arguments: dict[str, Any]
    result: str
    elapsed_seconds: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ExecutionRecord:
    """Complete record of a single request execution."""
    log_id: str
    session_id: str
    user_message: str
    assistant_response: str = ""
    model: str = ""
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: str = ""
    iterations: int = 0
    steps: list[StepDetail] = field(default_factory=list)
    tool_executions: list[ToolExecution] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    system_prompt: str = ""
    error: str = ""
    
    def add_step(self, step: str, status: str, summary: str = "", **details) -> None:
        """Add a step record."""
        self.steps.append(StepDetail(
            step=step,
            status=status,
            summary=summary,
            details=details
        ))
    
    def add_tool_execution(self, tool_name: str, arguments: dict, result: str, elapsed: float) -> None:
        """Add a tool execution record."""
        self.tool_executions.append(ToolExecution(
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            elapsed_seconds=elapsed
        ))
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        return data


class ExecutionLog:
    """Persists debug information for each chat request.
    
    Logs are stored in ~/.nanobot/logs/{session_id}/{timestamp}_{log_id}.json
    """
    
    def __init__(self, log_dir: Path | None = None):
        """Initialize the execution log.
        
        Args:
            log_dir: Directory for logs. Defaults to ~/.nanobot/logs/
        """
        if log_dir is None:
            log_dir = Path.home() / ".nanobot" / "logs"
        self.log_dir = ensure_dir(log_dir)
    
    def _get_session_dir(self, session_id: str) -> Path:
        """Get the directory for a session's logs."""
        # Sanitize session_id for filesystem
        safe_id = session_id.replace(":", "_").replace("/", "_")
        return ensure_dir(self.log_dir / safe_id)
    
    def create_record(self, session_id: str, user_message: str, model: str = "") -> ExecutionRecord:
        """Create a new execution record.
        
        Args:
            session_id: The session identifier.
            user_message: The user's message.
            model: The model being used.
        
        Returns:
            A new ExecutionRecord instance.
        """
        log_id = str(uuid.uuid4())[:8]
        return ExecutionRecord(
            log_id=log_id,
            session_id=session_id,
            user_message=user_message,
            model=model
        )
    
    def save(self, record: ExecutionRecord) -> str:
        """Save an execution record to disk.
        
        Args:
            record: The execution record to save.
        
        Returns:
            The log_id of the saved record.
        """
        record.completed_at = datetime.now().isoformat()
        
        session_dir = self._get_session_dir(record.session_id)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{record.log_id}.json"
        filepath = session_dir / filename
        
        with open(filepath, "w") as f:
            json.dump(record.to_dict(), f, indent=2, default=str)
        
        return record.log_id
    
    def get_log(self, session_id: str, log_id: str) -> dict[str, Any] | None:
        """Retrieve a specific execution log.
        
        Args:
            session_id: The session identifier.
            log_id: The log identifier.
        
        Returns:
            The log data or None if not found.
        """
        session_dir = self._get_session_dir(session_id)
        
        # Find the log file with this log_id
        for filepath in session_dir.glob(f"*_{log_id}.json"):
            try:
                with open(filepath) as f:
                    return json.load(f)
            except Exception:
                continue
        
        return None
    
    def list_logs(self, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List execution logs.
        
        Args:
            session_id: Optional session to filter by.
            limit: Maximum number of logs to return.
        
        Returns:
            List of log summaries (id, session, timestamp, message preview).
        """
        logs = []
        
        if session_id:
            dirs = [self._get_session_dir(session_id)]
        else:
            dirs = [d for d in self.log_dir.iterdir() if d.is_dir()]
        
        for session_dir in dirs:
            for filepath in session_dir.glob("*.json"):
                try:
                    with open(filepath) as f:
                        data = json.load(f)
                    
                    logs.append({
                        "log_id": data.get("log_id"),
                        "session_id": data.get("session_id"),
                        "started_at": data.get("started_at"),
                        "model": data.get("model", ""),
                        "message_preview": data.get("user_message", "")[:100],
                        "iterations": data.get("iterations", 0),
                        "tool_count": len(data.get("tool_executions", [])),
                        "has_error": bool(data.get("error")),
                    })
                except Exception:
                    continue
        
        # Sort by timestamp descending
        logs.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        
        return logs[:limit]
    
    def delete_log(self, session_id: str, log_id: str) -> bool:
        """Delete a specific log.
        
        Args:
            session_id: The session identifier.
            log_id: The log identifier.
        
        Returns:
            True if deleted, False if not found.
        """
        session_dir = self._get_session_dir(session_id)
        
        for filepath in session_dir.glob(f"*_{log_id}.json"):
            filepath.unlink()
            return True
        
        return False
    
    def delete_session_logs(self, session_id: str) -> int:
        """Delete all logs for a session.
        
        Args:
            session_id: The session identifier.
        
        Returns:
            Number of logs deleted.
        """
        session_dir = self._get_session_dir(session_id)
        count = 0
        
        for filepath in session_dir.glob("*.json"):
            filepath.unlink()
            count += 1
        
        # Remove the directory if empty
        try:
            session_dir.rmdir()
        except OSError:
            pass
        
        return count
