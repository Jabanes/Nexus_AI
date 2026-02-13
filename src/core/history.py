"""
Session History & Persistence

This module implements the session recording and persistence layer
using the Repository Pattern for future-proof architecture.

Components:
- SessionRecorder: In-memory buffer that captures real-time events
- FileSessionRepository: File-based implementation of ISessionRepository
"""

import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from src.interfaces.session_repository import ISessionRepository

logger = logging.getLogger(__name__)


class SessionRecorder:
    """
    In-memory session recorder that captures real-time conversation events.
    
    This class acts as a buffer during an active call, recording:
    - User audio/text input
    - AI responses
    - Tool executions
    - System events
    
    The recorded data can be exported and persisted via ISessionRepository.
    """
    
    def __init__(self, tenant_id: str, session_id: str):
        """
        Initialize a new session recorder.
        
        Args:
            tenant_id: The tenant identifier for isolation
            session_id: Unique session UUID
        """
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.start_time = datetime.now(timezone.utc)
        self.end_time: Optional[datetime] = None
        self.status = "IN_PROGRESS"
        self.transcript: List[Dict[str, Any]] = []
        self.session_data: Dict[str, Any] = {}
        self.filepath: Optional[str] = None
        
        # Record connection establishment
        self._log_event("system", "connection_established", {})
        
        logger.info(f"[{tenant_id}:{session_id}] SessionRecorder initialized")
    
    def _log_event(self, role: str, event_type: str, data: Dict[str, Any]) -> None:
        """
        Internal method to log a structured event.
        
        Args:
            role: The role (user, ai, system, tool)
            event_type: Type of event (text, audio, tool_call, etc.)
            data: Event-specific data
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "type": event_type,
            **data
        }
        self.transcript.append(entry)
        logger.debug(f"[{self.tenant_id}:{self.session_id}] Event logged: {role}/{event_type}")
    
    def log_user_audio(self, duration_ms: int, bytes_sent: int) -> None:
        """
        Log user audio chunk sent to PersonaPlex.
        
        Args:
            duration_ms: Duration of audio chunk in milliseconds
            bytes_sent: Number of bytes transmitted
        """
        self._log_event("user", "audio", {
            "duration_ms": duration_ms,
            "bytes": bytes_sent
        })
    
    def log_user_text(self, text: str, confidence: Optional[float] = None) -> None:
        """
        Log user's transcribed speech.
        
        Args:
            text: The transcribed text from STT
            confidence: Optional confidence score from STT engine
        """
        data = {"content": text}
        if confidence is not None:
            data["confidence"] = confidence
        
        self._log_event("user", "text", data)
        logger.info(f"[{self.tenant_id}:{self.session_id}] User: {text[:50]}...")
    
    def log_ai_text(self, text: str, voice_id: Optional[str] = None) -> None:
        """
        Log AI's response text.
        
        Args:
            text: The AI's response text
            voice_id: Optional voice ID used for TTS
        """
        data = {"content": text}
        if voice_id:
            data["voice_id"] = voice_id
        
        self._log_event("ai", "text", data)
        logger.info(f"[{self.tenant_id}:{self.session_id}] AI: {text[:50]}...")
    
    def log_ai_audio(self, duration_ms: int, bytes_sent: int) -> None:
        """
        Log AI audio chunk sent to client.
        
        Args:
            duration_ms: Duration of audio chunk in milliseconds
            bytes_sent: Number of bytes transmitted
        """
        self._log_event("ai", "audio", {
            "duration_ms": duration_ms,
            "bytes": bytes_sent
        })
    
    def log_tool_usage(
        self, 
        tool_name: str, 
        tool_input: Dict[str, Any], 
        tool_output: Any,
        execution_time_ms: Optional[int] = None
    ) -> None:
        """
        Log tool execution.
        
        Args:
            tool_name: Name of the tool executed
            tool_input: Input parameters passed to the tool
            tool_output: Output/result from the tool
            execution_time_ms: Optional execution time in milliseconds
        """
        data = {
            "name": tool_name,
            "input": tool_input,
            "output": str(tool_output)  # Convert to string for JSON serialization
        }
        if execution_time_ms is not None:
            data["execution_time_ms"] = execution_time_ms
        
        self._log_event("tool", "execution", data)
        logger.info(f"[{self.tenant_id}:{self.session_id}] Tool executed: {tool_name}")
    
    def log_error(self, error_type: str, error_message: str, details: Optional[Dict] = None) -> None:
        """
        Log an error event.
        
        Args:
            error_type: Type of error (connection_lost, transcoding_failed, etc.)
            error_message: Human-readable error message
            details: Optional additional error details
        """
        data = {
            "error_type": error_type,
            "message": error_message
        }
        if details:
            data["details"] = details
        
        self._log_event("system", "error", data)
        logger.error(f"[{self.tenant_id}:{self.session_id}] Error: {error_type} - {error_message}")
    
    def log_barge_in(self) -> None:
        """Log a barge-in event (user interrupted AI)."""
        self._log_event("system", "barge_in", {
            "message": "User interrupted AI response"
        })
    
    def finalize(self, status: str = "COMPLETED") -> None:
        """
        Finalize the session recording.
        
        Args:
            status: Final status (COMPLETED, DISCONNECTED, ERROR)
        """
        self.end_time = datetime.now(timezone.utc)
        self.status = status
        
        self._log_event("system", "session_ended", {
            "status": status,
            "duration_seconds": (self.end_time - self.start_time).total_seconds()
        })
        
        # Populate session_data
        self.session_data = self.export()
        
        logger.info(f"[{self.tenant_id}:{self.session_id}] Session finalized: {status}")

    async def save_session(self) -> str:
        """
        Persist the current session_data to disk.
        
        Returns:
            str: The full path to the saved file.
        """
        from src.core.history import FileSessionRepository
        repository = FileSessionRepository()
        self.filepath = await repository.save_session(self.tenant_id, self.session_data)
        return self.filepath
    
    def export(self) -> Dict[str, Any]:
        """
        Export complete session data, preserving AI intelligence if it exists.
        """
        end = self.end_time or datetime.now(timezone.utc)
        duration_seconds = (end - self.start_time).total_seconds()
        
        user_messages = len([t for t in self.transcript if t["role"] == "user" and t["type"] == "text"])
        ai_messages = len([t for t in self.transcript if t["role"] == "ai" and t["type"] == "text"])
        tool_calls = len([t for t in self.transcript if t["role"] == "tool"])
        errors = len([t for t in self.transcript if t.get("type") == "error"])
        
        # --- FIX: Intelligence Preservation Logic ---
        # Check if session_data already contains a real intent (not the placeholder)
        existing_summary = self.session_data.get("summary", {})
        intent = existing_summary.get("intent", "")
        
        if intent and "Unknown" not in intent:
            # We already have AI data! Use the existing summary.
            summary = existing_summary
        else:
            # No AI data yet, use the default placeholder
            summary = {
                "intent": "Unknown (To be implemented by LLM)",
                "outcome": "Success" if self.status == "COMPLETED" else "Incomplete"
            }
        # ---------------------------------------------

        return {
            "session_id": self.session_id,
            "meta": {
                "tenant_id": self.tenant_id,
                "start_time": self.start_time.isoformat(),
                "end_time": end.isoformat(),
                "duration_seconds": round(duration_seconds, 2),
                "status": self.status
            },
            "statistics": {
                "user_messages": user_messages,
                "ai_messages": ai_messages,
                "tool_calls": tool_calls,
                "errors": errors,
                "total_events": len(self.transcript)
            },
            "summary": summary,  # Now preserves your "Book haircut" intent
            "transcript": self.transcript
        }


class FileSessionRepository(ISessionRepository):
    """
    File-based implementation of ISessionRepository.
    
    Stores session data as JSON files in:
    data/history/{tenant_id}/{session_id}.json
    
    This implementation provides:
    - Tenant isolation via directory structure
    - Atomic writes (write to temp, then rename)
    - Automatic directory creation
    - UTF-8 encoding with pretty-printing
    """
    
    def __init__(self, base_path: str = "data/history"):
        """
        Initialize the file repository.
        
        Args:
            base_path: Base directory for session storage
        """
        self.base_path = Path(base_path)
        logger.info(f"FileSessionRepository initialized: {self.base_path}")
    
    def _get_tenant_dir(self, tenant_id: str) -> Path:
        """Get the directory path for a specific tenant."""
        return self.base_path / tenant_id
    
    def _ensure_directory(self, path: Path) -> None:
        """Ensure directory exists, create if necessary."""
        path.mkdir(parents=True, exist_ok=True)
    
    async def save_session(self, tenant_id: str, session_data: Dict[str, Any]) -> str:
        """
        Save session data to a JSON file.
        
        Args:
            tenant_id: The tenant identifier
            session_data: Complete session data from SessionRecorder.export()
            
        Returns:
            str: Full path to the saved file
            
        Raises:
            ValueError: If session_data is missing required fields
            IOError: If file write fails
        """
        # Validate required fields
        if "session_id" not in session_data:
            raise ValueError("session_data must contain 'session_id'")
        
        session_id = session_data["session_id"]
        
        # Get tenant directory and ensure it exists
        tenant_dir = self._get_tenant_dir(tenant_id)
        self._ensure_directory(tenant_dir)
        
        # Construct file path
        file_path = tenant_dir / f"{session_id}.json"
        
        try:
            # Write to temporary file first (atomic write)
            temp_path = file_path.with_suffix('.tmp')
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
            
            # Replace final path (atomic and overwrites on Windows if using .replace)
            temp_path.replace(file_path)
            
            logger.info(f"[{tenant_id}:{session_id}] Session saved to: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"[{tenant_id}:{session_id}] Failed to save session: {e}")
            raise IOError(f"Failed to save session: {e}")
    
    async def get_session(self, tenant_id: str, session_id: str) -> Dict[str, Any]:
        """
        Retrieve a specific session by ID.
        
        Args:
            tenant_id: The tenant identifier
            session_id: The session UUID
            
        Returns:
            Dict containing the complete session data
            
        Raises:
            FileNotFoundError: If session doesn't exist
        """
        file_path = self._get_tenant_dir(tenant_id) / f"{session_id}.json"
        
        if not file_path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            
            logger.debug(f"[{tenant_id}:{session_id}] Session retrieved from: {file_path}")
            return session_data
            
        except Exception as e:
            logger.error(f"[{tenant_id}:{session_id}] Failed to read session: {e}")
            raise IOError(f"Failed to read session: {e}")
    
    async def list_sessions(self, tenant_id: str, limit: int = 100) -> list:
        """
        List recent sessions for a tenant.
        
        Args:
            tenant_id: The tenant identifier
            limit: Maximum number of sessions to return
            
        Returns:
            List of session metadata dictionaries (sorted by most recent first)
        """
        tenant_dir = self._get_tenant_dir(tenant_id)
        
        if not tenant_dir.exists():
            logger.debug(f"[{tenant_id}] No sessions found (directory doesn't exist)")
            return []
        
        try:
            # Get all JSON files
            session_files = list(tenant_dir.glob("*.json"))
            
            # Sort by modification time (most recent first)
            session_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            
            # Limit results
            session_files = session_files[:limit]
            
            # Load metadata from each file
            sessions = []
            for file_path in session_files:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    # Extract metadata only
                    sessions.append({
                        "session_id": data.get("session_id"),
                        "meta": data.get("meta", {}),
                        "statistics": data.get("statistics", {}),
                        "summary": data.get("summary", {})
                    })
                except Exception as e:
                    logger.warning(f"[{tenant_id}] Failed to read session file {file_path}: {e}")
                    continue
            
            logger.info(f"[{tenant_id}] Listed {len(sessions)} sessions")
            return sessions
            
        except Exception as e:
            logger.error(f"[{tenant_id}] Failed to list sessions: {e}")
            return []
