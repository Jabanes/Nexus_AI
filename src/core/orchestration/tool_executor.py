"""
Tool Executor - Dynamically executes tenant tools.

This module provides a registry and execution engine for tools.
It maintains a mapping of tool names to tool instances and
safely executes them with error handling and logging.
"""
import logging
from typing import List, Dict, Any, Optional
from src.interfaces.base_tool import BaseTool

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Manages and executes tools for a specific session.
    
    This class maintains a registry of available tools and
    provides safe execution with comprehensive error handling.
    """
    
    def __init__(self, tools: List[BaseTool]):
        """
        Initialize the tool executor with a list of tools.
        
        Args:
            tools: List of BaseTool instances available for execution
        """
        self.tools_registry: Dict[str, BaseTool] = {}
        
        # Build the registry
        for tool in tools:
            self.tools_registry[tool.name] = tool
            logger.debug(f"Registered tool in executor: {tool.name}")
        
        logger.info(f"ToolExecutor initialized with {len(tools)} tools")
    
    def get_tool(self, tool_name: str) -> Optional[BaseTool]:
        """
        Retrieve a tool by name.
        
        Args:
            tool_name: The name of the tool to retrieve
            
        Returns:
            The BaseTool instance or None if not found
        """
        return self.tools_registry.get(tool_name)
    
    def list_tools(self) -> List[str]:
        """
        Get a list of all available tool names.
        
        Returns:
            List of tool names
        """
        return list(self.tools_registry.keys())
    
    async def execute_tool(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Execute a tool with the given arguments.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Dictionary of arguments to pass to the tool
            
        Returns:
            Dictionary containing:
                - success: Boolean indicating if execution succeeded
                - result: The tool's output (if successful)
                - error: Error message (if failed)
        """
        logger.info(f"Executing tool: {tool_name} with args: {arguments}")
        
        # Check if tool exists
        tool = self.get_tool(tool_name)
        if not tool:
            error_msg = f"Tool '{tool_name}' not found in registry"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "result": None
            }
        
        # Execute the tool
        try:
            result = await tool.execute(**arguments)
            logger.info(f"Tool '{tool_name}' executed successfully")
            
            return {
                "success": True,
                "result": result,
                "error": None
            }
            
        except TypeError as e:
            # Parameter mismatch error
            error_msg = f"Invalid arguments for tool '{tool_name}': {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "result": None
            }
            
        except Exception as e:
            # Generic execution error
            error_msg = f"Tool '{tool_name}' execution failed: {str(e)}"
            logger.exception(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "result": None
            }
    
    async def execute_multiple_tools(
        self, 
        function_calls: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Execute multiple tools in sequence.
        
        Args:
            function_calls: List of function call dictionaries with 'name' and 'args'
            
        Returns:
            List of execution results
        """
        logger.info(f"Executing {len(function_calls)} tool calls")
        
        results = []
        for call in function_calls:
            tool_name = call.get("name")
            arguments = call.get("args", {})
            
            result = await self.execute_tool(tool_name, arguments)
            results.append({
                "tool_name": tool_name,
                **result
            })
        
        return results
    
    def validate_tool_call(
        self, 
        tool_name: str, 
        arguments: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        """
        Validate a tool call before execution.
        
        Args:
            tool_name: Name of the tool
            arguments: Arguments to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if tool exists
        tool = self.get_tool(tool_name)
        if not tool:
            return False, f"Tool '{tool_name}' not found"
        
        # Validate required parameters
        params_schema = tool.parameters
        required_params = params_schema.get("required", [])
        
        for param in required_params:
            if param not in arguments:
                return False, f"Missing required parameter: {param}"
        
        return True, None
