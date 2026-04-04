"""
Calendar Store Factory — Resolves tenant config to the right calendar backend.

Usage in tools:
    store = create_calendar_store(self.config)
"""

import logging
from typing import Any, Dict

from src.integrations.calendar.base_store import ICalendarStore

logger = logging.getLogger(__name__)


def create_calendar_store(tool_config: Dict[str, Any]) -> ICalendarStore:
    """
    Create a calendar store from tool config.

    Reads tool_config["calendar_provider"] to determine which backend.
    Defaults to "json" if not specified.

    Config keys per provider:
        json:    tenant_id (required)
        google:  tenant_id, google_calendar_id, google_credentials_path
        cronofy: tenant_id, cronofy_api_key, cronofy_calendar_id
    """
    provider = tool_config.get("calendar_provider", "json")
    tenant_id = tool_config.get("tenant_id", "unknown")

    if provider == "json":
        from src.integrations.calendar.store import JsonCalendarStore
        return JsonCalendarStore(tenant_id)

    elif provider == "google":
        # Future: from src.integrations.calendar.google_store import GoogleCalendarStore
        # return GoogleCalendarStore(
        #     tenant_id=tenant_id,
        #     calendar_id=tool_config.get("google_calendar_id"),
        #     credentials_path=tool_config.get("google_credentials_path"),
        # )
        raise NotImplementedError(
            "Google Calendar integration not yet implemented. "
            "Use calendar_provider: 'json' for now."
        )

    elif provider == "cronofy":
        # Future: from src.integrations.calendar.cronofy_store import CronofyStore
        # return CronofyStore(
        #     tenant_id=tenant_id,
        #     api_key=tool_config.get("cronofy_api_key"),
        #     calendar_id=tool_config.get("cronofy_calendar_id"),
        # )
        raise NotImplementedError(
            "Cronofy integration not yet implemented. "
            "Use calendar_provider: 'json' for now."
        )

    else:
        raise ValueError(
            f"Unknown calendar_provider: '{provider}'. "
            f"Available: ['json', 'google', 'cronofy']"
        )
