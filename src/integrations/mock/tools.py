"""
Mock Integration Tools — Hardcoded responses for development and testing.

These tools return static mock data. Replace with real integration tools
(google_calendar, monday, etc.) for production tenants.
"""

from typing import Any, Dict
from src.interfaces.base_tool import BaseTool


class MockCheckAvailabilityTool(BaseTool):
    """Mock availability checker — returns hardcoded slot data."""

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
                "time": {"type": "string", "description": "HH:MM"},
            },
            "required": ["date"],
        }

    async def execute(self, **kwargs) -> Any:
        date = kwargs.get("date")
        time = kwargs.get("time")
        # self.config could contain calendar_id, mock_responses, etc.
        if time == "16:00":
            return "The time 16:00 is taken. 16:30 is available."
        return f"There is an available slot on {date} at {time}."


class MockBookAppointmentTool(BaseTool):
    """Mock appointment booker — always succeeds."""

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
                "name": {"type": "string", "description": "Customer name"},
                "time": {"type": "string", "description": "Appointment time"},
            },
            "required": ["name", "time"],
        }

    async def execute(self, **kwargs) -> Any:
        name = kwargs.get("name", "Unknown")
        time = kwargs.get("time", "Unknown")
        return f"Appointment booked successfully for {name} at {time}!"
