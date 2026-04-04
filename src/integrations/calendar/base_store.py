"""
Calendar Store Interface — The contract all calendar backends must implement.

Implementations:
  - JsonCalendarStore: Local JSON files (dev/testing)
  - GoogleCalendarStore: Google Calendar API (future)
  - CronofyStore: Cronofy unified calendar API (future)
  - OutlookStore: Microsoft Outlook/Graph API (future)
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ICalendarStore(ABC):
    """
    Abstract calendar store. All calendar backends implement this.
    The tool layer calls these methods — never a specific backend directly.
    """

    @abstractmethod
    def get_appointments_for_date(self, target_date: str) -> List[Dict[str, Any]]:
        """Get all appointments for a date (YYYY-MM-DD)."""
        ...

    @abstractmethod
    def is_slot_taken(self, target_date: str, target_time: str, duration_min: int = 30) -> bool:
        """Check if a time slot overlaps with existing appointments."""
        ...

    @abstractmethod
    def find_available_slots(
        self,
        target_date: str,
        business_hours: Dict[str, str],
        duration_min: int = 30,
        interval_min: int = 30,
    ) -> List[str]:
        """Find all available time slots for a date given business hours."""
        ...

    @abstractmethod
    def book_appointment(
        self,
        target_date: str,
        target_time: str,
        customer_name: str,
        customer_phone: str = "",
        service: str = "",
        duration_min: int = 30,
        notes: str = "",
        address: str = "",
    ) -> Dict[str, Any]:
        """Book an appointment. Raises ValueError if slot is taken."""
        ...

    @abstractmethod
    def update_appointment(
        self,
        customer_name: str,
        target_date: str,
        target_time: str,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Update an existing appointment. Returns updated record or None."""
        ...
