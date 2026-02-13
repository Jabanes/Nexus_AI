"""
Conversation Manager - Orchestrates the full conversation flow.

This module is the heart of the voice engine. It:
1. Manages conversation sessions
2. Coordinates between LLM and tool execution
3. Maintains conversation state
4. Handles the request/response cycle
"""
import logging
import uuid
from typing import Dict, Any, List, Optional
from src.core.llm.gemini_client import GeminiClient
from src.core.orchestration.tool_executor import ToolExecutor
from src.interfaces.base_tool import BaseTool
from src.core.history import SessionRecorder, FileSessionRepository

logger = logging.getLogger(__name__)


class ConversationSession:
    """
    Represents a single conversation session.
    
    Contains all the state and context needed for a conversation:
    - Session ID for tracking
    - Tenant configuration
    - Active chat instance
    - Tool executor
    - Conversation history
    """
    
    def __init__(
        self,
        session_id: str,
        tenant_id: str,
        customer_phone: str,
        chat_session: Any,
        tool_executor: ToolExecutor,
        system_prompt: str,
        session_recorder: SessionRecorder
    ):
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.customer_phone = customer_phone
        self.chat_session = chat_session
        self.tool_executor = tool_executor
        self.system_prompt = system_prompt
        self.session_recorder = session_recorder
        self.created_at = None  # Would be datetime in production
        
        logger.info(f"Conversation session created: {session_id}")


class ConversationManager:
    """
    Manages conversation sessions and orchestrates the conversation flow.
    
    This is the main orchestrator that brings together:
    - Tenant configuration
    - LLM communication
    - Tool execution
    - Session state management
    """
    
    def __init__(self):
        """Initialize the conversation manager."""
        self.gemini_client = GeminiClient()
        self.active_sessions: Dict[str, ConversationSession] = {}
        self.session_repository = FileSessionRepository()
        logger.info("ConversationManager initialized")
    
    def create_session(
        self,
        tenant_id: str,
        customer_phone: str,
        system_prompt: str,
        tools: List[BaseTool]
    ) -> ConversationSession:
        """
        Create a new conversation session.
        
        Args:
            tenant_id: The tenant this session belongs to
            customer_phone: Customer's phone number
            system_prompt: System instruction for the LLM
            tools: List of available tools for this session
            
        Returns:
            ConversationSession instance
        """
        session_id = str(uuid.uuid4())
        logger.info(f"Creating new session for tenant: {tenant_id}")
        
        # Initialize Gemini chat session
        model = self.gemini_client.create_chat_session(system_prompt, tools)
        chat_session = model.start_chat()
        
        # Initialize tool executor
        tool_executor = ToolExecutor(tools)
        
        # Initialize session recorder
        session_recorder = SessionRecorder(tenant_id, session_id)
        
        # Create session object
        session = ConversationSession(
            session_id=session_id,
            tenant_id=tenant_id,
            customer_phone=customer_phone,
            chat_session=chat_session,
            tool_executor=tool_executor,
            system_prompt=system_prompt,
            session_recorder=session_recorder
        )
        
        # Store in active sessions
        self.active_sessions[session_id] = session
        
        logger.info(f"Session {session_id} created successfully with {len(tools)} tools")
        return session
    
    def get_session(self, session_id: str) -> Optional[ConversationSession]:
        """
        Retrieve an active session by ID.
        
        Args:
            session_id: The session ID to retrieve
            
        Returns:
            ConversationSession or None if not found
        """
        return self.active_sessions.get(session_id)
    
    def close_session(self, session_id: str) -> bool:
        """
        Close and remove a session.
        
        Args:
            session_id: The session ID to close
            
        Returns:
            True if session was closed, False if not found
        """
        if session_id in self.active_sessions:
            session = self.active_sessions[session_id]
            
            # Finalize recording
            try:
                session.session_recorder.finalize()
                asyncio.create_task(self.session_repository.save_session(
                    session.tenant_id, 
                    session.session_recorder.export()
                ))
            except Exception as e:
                logger.error(f"Error saving session {session_id}: {e}")
                
            del self.active_sessions[session_id]
            logger.info(f"Session {session_id} closed")
            return True
        return False
    
    async def process_message(
        self,
        session_id: str,
        user_message: str
    ) -> Dict[str, Any]:
        """
        Process a user message and return the assistant's response.
        
        This is the main conversation loop:
        1. Send user message to LLM
        2. If LLM requests function calls, execute them
        3. Send results back to LLM
        4. Return final response
        
        Args:
            session_id: The active session ID
            user_message: The user's message/speech transcript
            
        Returns:
            Dictionary containing:
                - text: The assistant's response
                - tools_used: List of tools that were called
                - success: Boolean indicating if processing succeeded
        """
        logger.info(f"Processing message for session: {session_id}")
        
        # Retrieve session
        session = self.get_session(session_id)
        if not session:
            logger.error(f"Session {session_id} not found")
            return {
                "success": False,
                "error": "Session not found",
                "text": "I'm sorry, your session has expired. Please start a new conversation.",
                "tools_used": []
            }
        
        try:
            # Send message to Gemini
            llm_response = await self.gemini_client.send_message(
                session.chat_session,
                user_message
            )
            
            tools_used = []
            final_text = llm_response.get("text", "")
            
            # Handle function calls if any
            function_calls = llm_response.get("function_calls", [])
            
            if function_calls:
                logger.info(f"Processing {len(function_calls)} function calls")
                
                # Execute each function call
                for func_call in function_calls:
                    tool_name = func_call["name"]
                    tool_args = func_call["args"]
                    
                    # Execute the tool
                    execution_result = await session.tool_executor.execute_tool(
                        tool_name,
                        tool_args
                    )
                    
                    tools_used.append({
                        "name": tool_name,
                        "success": execution_result["success"]
                    })
                    
                    # Send result back to Gemini
                    if execution_result["success"]:
                        final_text = await self.gemini_client.send_function_response(
                            session.chat_session,
                            tool_name,
                            execution_result["result"]
                        )
                    else:
                        # Tool failed - inform the LLM
                        error_msg = f"Tool execution failed: {execution_result['error']}"
                        final_text = await self.gemini_client.send_function_response(
                            session.chat_session,
                            tool_name,
                            error_msg
                        )
            
            logger.info(f"Message processed successfully. Tools used: {len(tools_used)}")
            
            # Log user message
            session.session_recorder.log_user_text(user_message)
            
            # Log AI response
            session.session_recorder.log_ai_text(final_text)
            
            # Log tools used
            for tool in tools_used:
                session.session_recorder.log_tool_usage(
                    tool["name"],
                    {},  # We don't have args easily accessible here without parsing again, or capture earlier
                    "Success" if tool["success"] else "Failed"
                )
            
            # Save intermediate state
            # Note: In a real app we might not save every turn to disk to avoid I/O, 
            # but for this verified prototype it's safer.
            try:
                await self.session_repository.save_session(
                    session.tenant_id, 
                    session.session_recorder.export()
                )
            except Exception as e:
                logger.error(f"Failed to save session state: {e}")

            return {
                "success": True,
                "text": final_text,
                "tools_used": tools_used,
                "error": None
            }
            
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            return {
                "success": False,
                "error": str(e),
                "text": "I encountered an error processing your request. Please try again.",
                "tools_used": []
            }
    
    def get_active_session_count(self) -> int:
        """
        Get the number of active sessions.
        
        Returns:
            Count of active sessions
        """
        return len(self.active_sessions)
    
    def get_sessions_by_tenant(self, tenant_id: str) -> List[ConversationSession]:
        """
        Get all active sessions for a specific tenant.
        
        Args:
            tenant_id: The tenant ID
            
        Returns:
            List of ConversationSession instances
        """
        return [
            session for session in self.active_sessions.values()
            if session.tenant_id == tenant_id
        ]
