from typing import Dict, Any
from src.interfaces.base_tool import BaseTool

class CheckAvailabilityTool(BaseTool):
    @property
    def name(self) -> str:
        return "check_availability"

    @property
    def description(self) -> str:
        return "בודק אם יש תורים פנויים ביום ובשעה מסוימים"

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
        # כאן תהיה קריאה ל-DB או ליומן גוגל
        # כרגע נחזיר תשובה מדומה (Mock)
        date = kwargs.get("date")
        time = kwargs.get("time")
        
        if time == "16:00":
            return "השעה 16:00 תפוסה. 16:30 פנוי."
        return f"יש מקום פנוי ב-{date} בשעה {time}."

class BookAppointmentTool(BaseTool):
    @property
    def name(self) -> str:
        return "book_appointment"

    @property
    def description(self) -> str:
        return "קובע תור חדש ביומן"

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
        return "התור נקבע בהצלחה!"