"""
Gemini LLM Client - Handles all interactions with Google Gemini API.

This module is responsible for:
- Initializing Gemini models
- Managing conversation context
- Converting tools to Gemini function declarations
- Handling function calls from the LLM
"""
import logging
import os
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from src.interfaces.base_tool import BaseTool

logger = logging.getLogger(__name__)


class GeminiClient:
    """
    Wrapper for Google Gemini API with tool calling support.
    
    This client is tenant-agnostic - it receives tools and prompts
    from the tenant configuration and executes them generically.
    """
    
    def __init__(self, model_name: str = "gemini-1.5-pro"):
        """
        Initialize the Gemini client.
        
        Args:
            model_name: The Gemini model to use (default: gemini-1.5-pro)
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        
        genai.configure(api_key=api_key)
        self.model_name = model_name
        logger.info(f"Gemini client initialized with model: {model_name}")
    
    def create_chat_session(
        self, 
        system_prompt: str, 
        tools: List[BaseTool]
    ) -> genai.GenerativeModel:
        """
        Creates a new chat session with the given system prompt and tools.
        
        Args:
            system_prompt: The system instruction for the LLM
            tools: List of BaseTool instances available for this session
            
        Returns:
            A configured GenerativeModel instance
        """
        logger.debug(f"Creating chat session with {len(tools)} tools")
        
        # Convert BaseTool instances to Gemini function declarations
        function_declarations = self._convert_tools_to_declarations(tools)
        
        # Create the model with tools (if any)
        if function_declarations:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
                tools=function_declarations
            )
        else:
            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt
            )
        
        logger.info("Chat session created successfully")
        return model
    
    def _convert_tools_to_declarations(self, tools: List[BaseTool]) -> Optional[List[Any]]:
        """
        Converts BaseTool instances to Gemini function declaration format.
        
        Args:
            tools: List of BaseTool instances
            
        Returns:
            List of function declaration dictionaries for Gemini
        """
        if not tools:
            return None
        
        declarations = []
        for tool in tools:
            # Convert JSON Schema parameters to Gemini format
            params_schema = tool.parameters
            
            # Build properties dict with proper Gemini types
            properties = {}
            for prop_name, prop_def in params_schema.get("properties", {}).items():
                properties[prop_name] = {
                    "type": self._json_type_to_gemini_type_string(prop_def.get("type", "STRING")),
                    "description": prop_def.get("description", "")
                }
            
            # Create function declaration as dict
            func_decl = {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "OBJECT",
                    "properties": properties,
                    "required": params_schema.get("required", [])
                }
            }
            
            declarations.append(func_decl)
            logger.debug(f"Registered tool: {tool.name}")
        
        return declarations
    
    def _json_type_to_gemini_type_string(self, json_type: str) -> str:
        """
        Convert JSON Schema type to Gemini Type string.
        
        Args:
            json_type: JSON Schema type string
            
        Returns:
            Gemini Type string (uppercase)
        """
        type_mapping = {
            "string": "STRING",
            "integer": "INTEGER",
            "number": "NUMBER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT"
        }
        return type_mapping.get(json_type.lower(), "STRING")
    
    async def send_message(
        self, 
        chat: Any,  # genai.ChatSession
        message: str
    ) -> Dict[str, Any]:
        """
        Sends a message to the chat session and handles the response.
        
        Args:
            chat: The active chat session
            message: User's message
            
        Returns:
            Dictionary containing:
                - text: The LLM's text response
                - function_calls: List of function calls (if any)
        """
        try:
            logger.debug(f"Sending message to Gemini: {message[:100]}...")
            response = chat.send_message(message)
            
            # Parse response
            result = {
                "text": "",
                "function_calls": []
            }
            
            # Check if response contains function calls
            for part in response.parts:
                if hasattr(part, 'text') and part.text:
                    result["text"] += part.text
                elif hasattr(part, 'function_call') and part.function_call:
                    # Extract function call details
                    func_call = part.function_call
                    result["function_calls"].append({
                        "name": func_call.name,
                        "args": dict(func_call.args)
                    })
                    logger.info(f"Function call requested: {func_call.name}")
            
            return result
            
        except Exception as e:
            logger.exception(f"Error sending message to Gemini: {e}")
            raise
    
    async def send_function_response(
        self, 
        chat: Any,
        function_name: str,
        function_response: Any
    ) -> str:
        """
        Sends a function execution result back to Gemini.
        
        Args:
            chat: The active chat session
            function_name: Name of the executed function
            function_response: The result from the function
            
        Returns:
            The LLM's follow-up text response
        """
        try:
            logger.debug(f"Sending function response for: {function_name}")
            
            # Send the function result back to Gemini
            response = chat.send_message(
                genai.protos.Content(
                    parts=[
                        genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=function_name,
                                response={"result": str(function_response)}
                            )
                        )
                    ]
                )
            )
            
            # Extract text response
            text_response = ""
            for part in response.parts:
                if hasattr(part, 'text') and part.text:
                    text_response += part.text
            
            return text_response
            
        except Exception as e:
            logger.exception(f"Error sending function response: {e}")
            raise
