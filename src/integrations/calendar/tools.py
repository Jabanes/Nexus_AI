"""
Calendar Integration Tools — Provider-agnostic appointment booking.

Tools use ICalendarStore via the factory. The actual backend (JSON, Google
Calendar, Cronofy) is resolved from tenant config's calendar_provider field.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from src.interfaces.base_tool import BaseTool
from src.integrations.calendar.store_factory import create_calendar_store

logger = logging.getLogger(__name__)


class CheckAvailabilityTool(BaseTool):
    """Check available appointment slots for a given date."""

    @property
    def name(self) -> str:
        return "check_availability"

    @property
    def description(self) -> str:
        return (
            "Check available appointment slots for a specific date. "
            "Returns a list of open time slots. If no date is provided, "
            "checks today. Always use this before booking to verify availability."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date to check in YYYY-MM-DD format. If the customer says 'tomorrow' or 'next Tuesday', convert it to YYYY-MM-DD.",
                },
            },
            "required": ["date"],
        }

    async def execute(self, **kwargs) -> Any:
        target_date = kwargs.get("date", "")

        business_hours = self.config.get("business_hours", {})
        duration = self.config.get("default_duration_min", 30)

        store = create_calendar_store(self.config)

        # Validate date format
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            return f"Invalid date format: '{target_date}'. Please use YYYY-MM-DD format."

        # Check if date is in the past
        if dt.date() < datetime.now().date():
            return f"Cannot check availability for past date {target_date}."

        # Find available slots
        available = store.find_available_slots(
            target_date=target_date,
            business_hours=business_hours,
            duration_min=duration,
        )

        day_name = dt.strftime("%A")

        if not available:
            # Check if it's a closed day
            existing = store.get_appointments_for_date(target_date)
            if not existing:
                return f"We are closed on {day_name}s. Please choose a different day."
            return f"Sorry, there are no available slots on {day_name} {target_date}. Please try another date."

        # Format nicely
        slots_display = ", ".join(available[:10])
        remaining = len(available) - 10 if len(available) > 10 else 0
        result = f"Available slots on {day_name} {target_date}: {slots_display}"
        if remaining:
            result += f" (and {remaining} more)"

        return result


class BookAppointmentTool(BaseTool):
    """Book an appointment at a specific date and time."""

    @property
    def name(self) -> str:
        return "book_appointment"

    @property
    def description(self) -> str:
        return (
            "Book an appointment for a customer. Requires the customer's name, "
            "the date (YYYY-MM-DD), and time (HH:MM). Optionally accepts the service type "
            "and phone number. Always check availability first before booking."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Full name of the customer",
                },
                "date": {
                    "type": "string",
                    "description": "Appointment date in YYYY-MM-DD format",
                },
                "time": {
                    "type": "string",
                    "description": "Appointment time in HH:MM format (24-hour)",
                },
                "service": {
                    "type": "string",
                    "description": "Service requested (e.g., 'Men Haircut', 'Beard Trim')",
                },
                "phone": {
                    "type": "string",
                    "description": "Customer phone number",
                },
                "address": {
                    "type": "string",
                    "description": "Customer address or project location",
                },
            },
            "required": ["customer_name", "date", "time", "phone"],
        }

    async def execute(self, **kwargs) -> Any:
        customer_name = kwargs.get("customer_name", "")
        target_date = kwargs.get("date", "")
        target_time = kwargs.get("time", "")
        service = kwargs.get("service", "")
        phone = kwargs.get("phone", "")
        address = kwargs.get("address", "")

        # Server-side validation: reject if required fields missing
        if not customer_name:
            return "Cannot book without a customer name. Please ask for their name."
        if not phone:
            return "Cannot book without a phone number. Please ask the customer for their phone number before booking."


        business_hours = self.config.get("business_hours", {})
        duration = self.config.get("default_duration_min", 30)

        store = create_calendar_store(self.config)

        # Validate date
        try:
            dt = datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            return f"Invalid date format: '{target_date}'. Use YYYY-MM-DD."

        if dt.date() < datetime.now().date():
            return f"Cannot book for past date {target_date}."

        # Validate time format
        try:
            datetime.strptime(target_time, "%H:%M")
        except ValueError:
            return f"Invalid time format: '{target_time}'. Use HH:MM (24-hour)."

        # Check business hours
        day_name = dt.strftime("%A")
        available = store.find_available_slots(target_date, business_hours, duration)
        if not available:
            return f"We are closed on {day_name}s. Please choose a different day."

        if target_time not in available:
            # Suggest nearby slots
            nearby = [s for s in available if abs(
                int(s.split(":")[0]) * 60 + int(s.split(":")[1]) -
                int(target_time.split(":")[0]) * 60 - int(target_time.split(":")[1])
            ) <= 60]
            if nearby:
                return f"The slot at {target_time} is not available. Nearby open slots: {', '.join(nearby[:5])}"
            return f"The slot at {target_time} on {target_date} is not available. Please check availability for open slots."

        # Book it
        try:
            appointment = store.book_appointment(
                target_date=target_date,
                target_time=target_time,
                customer_name=customer_name,
                customer_phone=phone,
                service=service,
                duration_min=duration,
                address=address,
            )
            result = (
                f"Appointment booked successfully! "
                f"{customer_name} on {day_name} {target_date} at {target_time}"
            )
            if service:
                result += f" for {service}"
            result += f" ({duration} minutes)."
            return result
        except ValueError as e:
            return str(e)


class UpdateAppointmentTool(BaseTool):
    """Update an existing appointment — add notes, change service, etc."""

    @property
    def name(self) -> str:
        return "update_appointment"

    @property
    def description(self) -> str:
        return (
            "Update an existing appointment. Use this to add notes or special requests "
            "after booking. Requires customer name, date, and time to find the appointment."
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Customer name (must match the booking)",
                },
                "date": {
                    "type": "string",
                    "description": "Appointment date YYYY-MM-DD",
                },
                "time": {
                    "type": "string",
                    "description": "Appointment time HH:MM",
                },
                "notes": {
                    "type": "string",
                    "description": "Notes or special requests from the customer",
                },
            },
            "required": ["customer_name", "date", "time", "notes"],
        }

    async def execute(self, **kwargs) -> Any:
        customer_name = kwargs.get("customer_name", "")
        target_date = kwargs.get("date", "")
        target_time = kwargs.get("time", "")
        notes = kwargs.get("notes", "")


        store = create_calendar_store(self.config)

        result = store.update_appointment(
            customer_name=customer_name,
            target_date=target_date,
            target_time=target_time,
            updates={"notes": notes},
        )

        if result:
            return f"Notes saved for {customer_name}'s appointment on {target_date} at {target_time}: \"{notes}\""
        return f"Could not find appointment for {customer_name} on {target_date} at {target_time}."
