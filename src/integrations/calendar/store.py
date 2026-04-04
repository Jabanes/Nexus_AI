"""
JSON Calendar Store — File-based appointment persistence.

Stores appointments per tenant in data/appointments/{tenant_id}.json.
Implements ICalendarStore for the tool layer.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.integrations.calendar.base_store import ICalendarStore

logger = logging.getLogger(__name__)

APPOINTMENTS_DIR = Path("data/appointments")


class JsonCalendarStore(ICalendarStore):
    """
    JSON-backed appointment storage.
    Each tenant gets its own JSON file. Thread-safe via atomic writes.
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        APPOINTMENTS_DIR.mkdir(parents=True, exist_ok=True)
        self.file_path = APPOINTMENTS_DIR / f"{tenant_id}.json"

    def _load(self) -> List[Dict[str, Any]]:
        if not self.file_path.exists():
            return []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            logger.warning(f"[{self.tenant_id}] Corrupt appointments file, starting fresh")
            return []

    def _save(self, appointments: List[Dict[str, Any]]):
        temp = self.file_path.with_suffix(".tmp")
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(appointments, f, indent=2, ensure_ascii=False)
        temp.replace(self.file_path)

    def get_appointments_for_date(self, target_date: str) -> List[Dict[str, Any]]:
        return [a for a in self._load() if a.get("date") == target_date]

    def is_slot_taken(self, target_date: str, target_time: str, duration_min: int = 30) -> bool:
        appts = self.get_appointments_for_date(target_date)
        req_start = self._parse_time(target_time)
        req_end = req_start + timedelta(minutes=duration_min)

        for appt in appts:
            appt_start = self._parse_time(appt["time"])
            appt_end = appt_start + timedelta(minutes=appt.get("duration_min", 30))
            if req_start < appt_end and req_end > appt_start:
                return True
        return False

    def find_available_slots(
        self,
        target_date: str,
        business_hours: Dict[str, str],
        duration_min: int = 30,
        interval_min: int = 30,
    ) -> List[str]:
        dt = datetime.strptime(target_date, "%Y-%m-%d")
        day_name = dt.strftime("%A").lower()

        hours_str = self._get_hours_for_day(day_name, business_hours)
        if not hours_str or hours_str.lower() == "closed":
            return []

        try:
            open_str, close_str = hours_str.split("-")
            open_time = self._parse_time(open_str.strip())
            close_time = self._parse_time(close_str.strip())
        except (ValueError, AttributeError):
            logger.error(f"Invalid business hours format: {hours_str}")
            return []

        available = []
        current = open_time
        while current + timedelta(minutes=duration_min) <= close_time:
            time_str = current.strftime("%H:%M")
            if not self.is_slot_taken(target_date, time_str, duration_min):
                available.append(time_str)
            current += timedelta(minutes=interval_min)
        return available

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
        if self.is_slot_taken(target_date, target_time, duration_min):
            raise ValueError(f"Time slot {target_date} {target_time} is already taken")

        appointment = {
            "id": f"{target_date}_{target_time}_{customer_name}".replace(" ", "_"),
            "date": target_date,
            "time": target_time,
            "duration_min": duration_min,
            "customer_name": customer_name,
            "customer_phone": customer_phone,
            "address": address,
            "service": service,
            "notes": notes,
            "booked_at": datetime.utcnow().isoformat(),
        }

        all_appts = self._load()
        all_appts.append(appointment)
        self._save(all_appts)

        logger.info(f"[{self.tenant_id}] Booked: {customer_name} on {target_date} at {target_time}")
        return appointment

    def update_appointment(
        self,
        customer_name: str,
        target_date: str,
        target_time: str,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        all_appts = self._load()
        found = None

        for appt in all_appts:
            if (
                appt["customer_name"].lower() == customer_name.lower()
                and appt["date"] == target_date
                and appt["time"] == target_time
            ):
                appt.update(updates)
                found = appt
                break

        if found:
            self._save(all_appts)
            logger.info(f"[{self.tenant_id}] Updated: {customer_name} -> {updates}")
        return found

    def _parse_time(self, time_str: str) -> datetime:
        h, m = time_str.strip().split(":")
        return datetime(2000, 1, 1, int(h), int(m))

    def _get_hours_for_day(self, day_name: str, business_hours: Dict[str, str]) -> Optional[str]:
        day_name = day_name.lower()
        if day_name in business_hours:
            return business_hours[day_name]

        day_order = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        try:
            day_idx = day_order.index(day_name)
        except ValueError:
            return None

        for key, value in business_hours.items():
            parts = key.lower().split("_")
            if len(parts) == 2:
                try:
                    start_idx = day_order.index(parts[0])
                    end_idx = day_order.index(parts[1])
                    if start_idx <= day_idx <= end_idx:
                        return value
                except ValueError:
                    continue
        return None
