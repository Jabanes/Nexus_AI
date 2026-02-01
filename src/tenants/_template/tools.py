"""
Tenant Tools Template

This file contains example tool implementations for your tenant.
Each tool must inherit from BaseTool and implement all required methods.

Instructions:
1. Import BaseTool and any libraries you need
2. Create a class for each tool (must end with "Tool")
3. Implement all required properties and methods
4. Add the tool name (snake_case) to enabled_tools in config.yaml

Example Use Cases:
- CheckAvailabilityTool: Query a calendar API
- BookAppointmentTool: Create bookings in your system
- CheckInventoryTool: Query product availability
- PlaceOrderTool: Create orders in your e-commerce system
- GetPricingTool: Fetch pricing information
"""
from typing import Dict, Any
import logging
from src.interfaces.base_tool import BaseTool

# Set up logging for your tools
logger = logging.getLogger(__name__)


class ExampleTool(BaseTool):
    """
    Example tool implementation.
    
    Replace this with your actual business logic tools.
    This serves as a template showing the required structure.
    """
    
    @property
    def name(self) -> str:
        """
        The function name that the LLM will call.
        Use snake_case (e.g., 'check_availability', 'book_appointment').
        """
        return "example_tool"
    
    @property
    def description(self) -> str:
        """
        Clear description for the LLM explaining when and how to use this tool.
        
        Be specific and include:
        - What the tool does
        - When to use it
        - What information it returns
        """
        return "An example tool that demonstrates the required structure. Replace with your actual tool description."
    
    @property
    def parameters(self) -> Dict[str, Any]:
        """
        JSON Schema defining the parameters this tool accepts.
        
        Format follows OpenAPI/JSON Schema specification:
        - type: "object"
        - properties: Dictionary of parameter definitions
        - required: List of required parameter names
        """
        return {
            "type": "object",
            "properties": {
                "example_param": {
                    "type": "string",
                    "description": "Description of what this parameter is for"
                },
                "optional_param": {
                    "type": "integer",
                    "description": "An optional parameter example"
                }
            },
            "required": ["example_param"]  # Only required parameters listed here
        }
    
    async def execute(self, **kwargs) -> Any:
        """
        The actual business logic - this is where you implement your tool.
        
        Args:
            **kwargs: Parameters will be passed as keyword arguments
            
        Returns:
            A string or serializable object that the LLM can understand.
            Keep responses human-readable as the LLM will read them.
        
        Best Practices:
        - Use async/await for external API calls
        - Add error handling with try/except
        - Log important actions
        - Return human-readable strings
        - Don't return complex objects or binary data
        """
        example_param = kwargs.get("example_param")
        optional_param = kwargs.get("optional_param", 0)
        
        logger.info(f"ExampleTool executed with: {example_param}")
        
        # TODO: Replace with your actual business logic
        # Examples:
        # - result = await your_api_client.query(example_param)
        # - data = await database.fetch(example_param)
        # - response = await external_service.call(example_param)
        
        # For now, return a placeholder response
        return f"Tool executed successfully with parameter: {example_param}"


# ============================================
# EXAMPLE: Booking System Tool
# ============================================
# Uncomment and customize this for a real use case

# class CheckAvailabilityTool(BaseTool):
#     """Check if a time slot is available."""
#     
#     @property
#     def name(self) -> str:
#         return "check_availability"
#     
#     @property
#     def description(self) -> str:
#         return "Checks if a specific date and time slot is available for booking."
#     
#     @property
#     def parameters(self) -> Dict[str, Any]:
#         return {
#             "type": "object",
#             "properties": {
#                 "date": {
#                     "type": "string",
#                     "description": "Date in YYYY-MM-DD format"
#                 },
#                 "time": {
#                     "type": "string",
#                     "description": "Time in HH:MM format (24-hour)"
#                 }
#             },
#             "required": ["date", "time"]
#         }
#     
#     async def execute(self, **kwargs) -> str:
#         date = kwargs.get("date")
#         time = kwargs.get("time")
#         
#         logger.info(f"Checking availability for {date} at {time}")
#         
#         # TODO: Connect to your calendar/booking system
#         # Example:
#         # is_available = await booking_system.check_slot(date, time)
#         # if is_available:
#         #     return f"Yes, {date} at {time} is available."
#         # else:
#         #     next_slot = await booking_system.get_next_available()
#         #     return f"That time is taken. Next available: {next_slot}"
#         
#         # Mock response
#         return f"The time slot on {date} at {time} is available."


# class BookAppointmentTool(BaseTool):
#     """Book an appointment."""
#     
#     @property
#     def name(self) -> str:
#         return "book_appointment"
#     
#     @property
#     def description(self) -> str:
#         return "Books an appointment for a customer at a specific date and time."
#     
#     @property
#     def parameters(self) -> Dict[str, Any]:
#         return {
#             "type": "object",
#             "properties": {
#                 "customer_name": {"type": "string"},
#                 "phone": {"type": "string"},
#                 "date": {"type": "string", "description": "YYYY-MM-DD"},
#                 "time": {"type": "string", "description": "HH:MM"},
#                 "service": {"type": "string", "description": "Type of service"}
#             },
#             "required": ["customer_name", "phone", "date", "time"]
#         }
#     
#     async def execute(self, **kwargs) -> str:
#         customer_name = kwargs.get("customer_name")
#         phone = kwargs.get("phone")
#         date = kwargs.get("date")
#         time = kwargs.get("time")
#         service = kwargs.get("service", "Standard Service")
#         
#         logger.info(f"Booking appointment for {customer_name} on {date} at {time}")
#         
#         # TODO: Insert into your booking system
#         # Example:
#         # booking_id = await booking_system.create_appointment({
#         #     "customer_name": customer_name,
#         #     "phone": phone,
#         #     "date": date,
#         #     "time": time,
#         #     "service": service
#         # })
#         # 
#         # # Send confirmation SMS/email
#         # await notification_service.send_confirmation(phone, booking_id)
#         # 
#         # return f"Appointment confirmed! Booking ID: {booking_id}. You'll receive a confirmation shortly."
#         
#         # Mock response
#         return f"Appointment booked for {customer_name} on {date} at {time}. Confirmation sent to {phone}."


# ============================================
# Add your own tools below
# ============================================
