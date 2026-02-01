"""
Session Repository Interface

Defines the abstract contract for session persistence.
This allows the application logic to remain decoupled from
the actual storage implementation (File, PostgreSQL, MongoDB, etc.).

Following the Repository Pattern for future-proof architecture.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class ISessionRepository(ABC):
    """
    Abstract interface for session data persistence.
    
    This interface must be implemented by any storage backend
    (filesystem, database, cloud storage, etc.).
    """
    
    @abstractmethod
    async def save_session(self, tenant_id: str, session_data: Dict[str, Any]) -> str:
        """
        Saves session data to the configured storage backend.
        
        Args:
            tenant_id: The tenant identifier for data isolation
            session_data: Complete session data including transcript, metadata, etc.
            
        Returns:
            str: Storage location/ID (e.g., file path, database record ID, S3 key)
            
        Raises:
            PermissionError: If write access is denied
            ValueError: If session_data is invalid
            IOError: If storage backend is unavailable
        """
        pass
    
    @abstractmethod
    async def get_session(self, tenant_id: str, session_id: str) -> Dict[str, Any]:
        """
        Retrieves a specific session by ID.
        
        Args:
            tenant_id: The tenant identifier
            session_id: The session UUID
            
        Returns:
            Dict containing the complete session data
            
        Raises:
            FileNotFoundError: If session doesn't exist
        """
        pass
    
    @abstractmethod
    async def list_sessions(self, tenant_id: str, limit: int = 100) -> list:
        """
        Lists recent sessions for a tenant.
        
        Args:
            tenant_id: The tenant identifier
            limit: Maximum number of sessions to return
            
        Returns:
            List of session metadata dictionaries
        """
        pass
