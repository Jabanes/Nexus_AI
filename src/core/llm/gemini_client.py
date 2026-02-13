"""
Gemini LLM Client - Handles all interactions with Google Gemini API using the new google-genai SDK.

This module is responsible for:
- Initializing Gemini models using genai.Client (Instance-based/Thread-safe)
- Managing conversation context
- Handling function calls from the LLM
- Supporting REST transport for stability on Windows
"""
import logging
import os
import asyncio
from typing import List, Dict, Any, Optional, AsyncGenerator
from google import genai
from google.genai import types

from src.interfaces.base_tool import BaseTool

logger = logging.getLogger(__name__)


class GeminiClient:
    """
    Wrapper for Google Gemini API (google-genai) with tool calling support.
    
    This client is tenant-agnostic - it receives tools and prompts
    from the tenant configuration and executes them generically.
    Uses instance-based genai.Client for multi-tenant safety.
    """
    
    def __init__(self, model_name: str = None):
        """
        Initialize the Gemini client.
        
        Args:
            model_name: The Gemini model to use (default: reads from GEMINI_MODEL env var)
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        
        # Read model from environment if not explicitly provided
        if model_name is None:
            model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        
        self.model_name = model_name
        
        # MIGRATION CHANGE: Instance-based Client
        # The new SDK handles transport automatically, but we can configure it if needed
        # Default is usually okay. For Windows, it uses httpx internally.
        self.client = genai.Client(api_key=api_key)
        
        logger.info(f"Gemini client initialized with model: {self.model_name}")
    
    def create_chat_session(
        self, 
        system_prompt: str, 
        tools: List[BaseTool]
    ) -> Any:
        """
        Creates a new chat session with the given system prompt and tools.
        
        Args:
            system_prompt: The system instruction for the LLM
            tools: List of BaseTool instances available for this session
            
        Returns:
            A Chat object from the new SDK
        """
        logger.debug(f"Creating chat session with {len(tools)} tools")
        
        # Convert BaseTool instances to declarations
        function_declarations = self._convert_tools_to_declarations(tools)
        
        # Create config
        config_kwargs = {
            "system_instruction": system_prompt,
        }
        
        if function_declarations:
            # In the new SDK, tools are passed as a list of types.Tool
            config_kwargs["tools"] = [types.Tool(function_declarations=function_declarations)]
        
        # FIX: Use 'client.aio' for Async Chat creation
        chat = self.client.aio.chats.create(
            model=self.model_name,
            config=types.GenerateContentConfig(**config_kwargs)
        )
        
        logger.info("Chat session created successfully")
        return chat
    
    def _convert_tools_to_declarations(self, tools: List[BaseTool]) -> Optional[List[types.FunctionDeclaration]]:
        """
        Converts BaseTool instances to Gemini function declaration format.
        """
        if not tools:
            return None
        
        declarations = []
        for tool in tools:
            # Convert JSON Schema parameters to types.Schema format
            params_schema = tool.parameters
            
            # Map JSON types to SDK types
            properties = {}
            for prop_name, prop_def in params_schema.get("properties", {}).items():
                properties[prop_name] = types.Schema(
                    type=self._json_type_to_gemini_type(prop_def.get("type", "string")),
                    description=prop_def.get("description", "")
                )
            
            # Create function declaration
            func_decl = types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=types.Schema(
                    type="OBJECT",
                    properties=properties,
                    required=params_schema.get("required", [])
                )
            )
            
            declarations.append(func_decl)
            logger.debug(f"Registered tool: {tool.name}")
        
        return declarations
    
    def _json_type_to_gemini_type(self, json_type: str) -> str:
        """Map JSON Schema type to SDK type string."""
        type_mapping = {
            "string": "STRING",
            "integer": "INTEGER",
            "number": "NUMBER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT"
        }
        return type_mapping.get(json_type.lower(), "STRING")

    async def send_message_stream(
        self, 
        chat: Any, 
        message: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streams the response from Gemini.
        
        Yields chunks of the response as they arrive.
        """
        try:
            logger.debug(f"Stream-sending message to Gemini: {message[:100]}...")
            
            # In google-genai, chat.send_message(..., stream=True) returns a wrapper
            # that we can iterate asynchronously.
            response_stream = await chat.send_message_stream(message)
            
            async for chunk in response_stream:
                # Check for text content
                if chunk.text:
                    yield {"type": "text", "content": chunk.text}
                
                # Check for function calls
                # In the new SDK, calls are under chunk.candidates[0].content.parts
                for part in chunk.candidates[0].content.parts:
                    if part.function_call:
                        fn_call = part.function_call
                        yield {
                            "type": "function_call", 
                            "call": {
                                "name": fn_call.name,
                                "args": fn_call.args
                            }
                        }
                        logger.info(f"Function call received in stream: {fn_call.name}")

        except Exception as e:
            logger.error(f"Error in Gemini stream: {e}")
            yield {"type": "error", "error": str(e)}

    async def send_function_response_stream(
        self, 
        chat: Any,
        function_name: str,
        function_response: Any
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Stream the LLM's follow-up response after a function execution.
        """
        try:
            logger.debug(f"Stream-sending function response for: {function_name}")
            
            # The new SDK uses types.Part.from_function_response
            part = types.Part.from_function_response(
                name=function_name,
                response={"result": str(function_response)}
            )
            
            response_stream = await chat.send_message_stream(message=[part])
            
            async for chunk in response_stream:
                if chunk.text:
                    yield {"type": "text", "content": chunk.text}
                
                for part in chunk.candidates[0].content.parts:
                    if part.function_call:
                        fn_call = part.function_call
                        yield {
                            "type": "function_call", 
                            "call": {
                                "name": fn_call.name,
                                "args": fn_call.args
                            }
                        }
                        logger.info(f"Function call received in stream: {fn_call.name}")

        except Exception as e:
            logger.error(f"Error in Gemini function response stream: {e}")
            yield {"type": "error", "error": str(e)}

    # Standard (non-streaming) versions if needed
    async def send_message(self, chat: Any, message: str) -> Dict[str, Any]:
        """Non-streaming version of send_message."""
        try:
            response = await chat.send_message(message)
            result = {"text": response.text or "", "function_calls": []}
            
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    result["function_calls"].append({
                        "name": part.function_call.name,
                        "args": part.function_call.args
                    })
            return result
        except Exception as e:
            logger.exception(f"Error sending message to Gemini: {e}")
            raise

    async def send_function_response(self, chat: Any, function_name: str, function_response: Any) -> str:
        """Non-streaming version of send_function_response."""
        try:
            part = types.Part.from_function_response(
                name=function_name,
                response={"result": str(function_response)}
            )
            response = await chat.send_message(message=[part])
            return response.text or ""
        except Exception as e:
            logger.exception(f"Error sending function response: {e}")
            raise
