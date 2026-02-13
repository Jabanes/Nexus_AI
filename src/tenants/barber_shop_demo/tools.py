from typing import Dict, Any
from src.interfaces.base_tool import BaseTool

class CheckAvailabilityTool(BaseTool):
    @property
    def name(self) -> str:
        return "check_availability"

    @property
    def description(self) -> str:
        return "Checks if there are available slots on a specific date and time"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD"},
                "time": {"type": "string", "description": "HH:MM"}
            },
            "required": ["date"]
        }

    async def execute(self, **kwargs) -> Any:
        # DB or Google Calendar call would go here
        # Returning mock response
        date = kwargs.get("date")
        time = kwargs.get("time")
        
        if time == "16:00":
            return "The time 16:00 is taken. 16:30 is available."
        return f"There is an available slot on {date} at {time}."

class BookAppointmentTool(BaseTool):
    @property
    def name(self) -> str:
        return "book_appointment"

    @property
    def description(self) -> str:
        return "Books a new appointment in the calendar"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "time": {"type": "string"}
            },
            "required": ["name", "time"]
        }

    async def execute(self, **kwargs) -> Any:
        return "Appointment booked successfully!"