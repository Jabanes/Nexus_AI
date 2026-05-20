"""
ElevenLabs Conversation Sync — Pulls full conversation data from ElevenLabs API
and saves it to our local session history.

This is provider-specific: only runs when the voice provider is ElevenLabs.
Other providers use the built-in SessionRecorder for history.
"""

import asyncio
import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class ElevenLabsSync:
    """
    Pulls conversation data from the ElevenLabs API after a call ends.
    Converts it to our session format and saves to FileSessionRepository.
    """

    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY", "")

    async def pull_conversation(
        self,
        conversation_id: str,
        wait_for_analysis: bool = True,
        max_attempts: int = 8,
        initial_delay: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Pull full conversation data from ElevenLabs.

        If wait_for_analysis is True, retries with backoff until the analysis
        block is populated (evaluation_criteria + data_collection_results).
        Analysis is computed asynchronously by ElevenLabs and typically takes
        5–20 seconds after call end.

        Returns the raw conversation dict, or None on failure.
        """
        if not self.api_key or not conversation_id:
            return None

        url = f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}"
        delay = initial_delay
        last_data: Optional[Dict[str, Any]] = None

        for attempt in range(1, max_attempts + 1):
            await asyncio.sleep(delay)
            try:
                resp = requests.get(url, headers={"xi-api-key": self.api_key}, timeout=10)
                if resp.status_code != 200:
                    logger.warning(f"[ElevenLabsSync] HTTP {resp.status_code} on attempt {attempt}")
                    delay = min(delay * 1.5, 15.0)
                    continue

                data = resp.json()
                last_data = data

                if not wait_for_analysis:
                    logger.info(f"[ElevenLabsSync] Pulled {conversation_id} (no-wait)")
                    return data

                # Analysis ready check — both fields must be non-None and non-empty
                analysis = data.get("analysis") or {}
                dc_results = analysis.get("data_collection_results") or {}
                if dc_results:
                    logger.info(
                        f"[ElevenLabsSync] Pulled {conversation_id} with analysis ready (attempt {attempt})"
                    )
                    return data

                logger.debug(
                    f"[ElevenLabsSync] Analysis not ready yet for {conversation_id} (attempt {attempt})"
                )
                delay = min(delay * 1.5, 15.0)

            except Exception as e:
                logger.warning(f"[ElevenLabsSync] Pull error on attempt {attempt}: {e}")
                delay = min(delay * 1.5, 15.0)

        # Analysis never appeared — return what we have if we got anything
        if last_data is not None:
            logger.warning(
                f"[ElevenLabsSync] Analysis never populated for {conversation_id} — returning partial"
            )
            return last_data
        return None

    def convert_to_session(
        self,
        el_data: Dict[str, Any],
        tenant_id: str,
        session_id: str,
        customer_phone: str = "",
    ) -> Dict[str, Any]:
        """
        Convert ElevenLabs conversation data to our session history format.

        Preserves ALL data from ElevenLabs while fitting our schema.
        Defensive against fields being present-but-None (the API sometimes
        sets analysis/metadata to null while still in progress).
        """
        # `or {}` handles the present-but-None case that .get(key, {}) does NOT
        metadata = el_data.get("metadata") or {}
        analysis = el_data.get("analysis") or {}
        el_transcript = el_data.get("transcript") or []

        # Convert transcript — include EVERYTHING
        transcript = []
        for entry in el_transcript:
            if not entry:
                continue
            role = entry.get("role", "unknown")
            message = entry.get("message") or ""
            tool_call = entry.get("tool_call")
            tool_result = entry.get("tool_result")
            time_secs = entry.get("time_in_call_secs", 0)

            if tool_call:
                transcript.append({
                    "timestamp": time_secs,
                    "role": role,
                    "type": "tool_call",
                    "name": tool_call.get("tool_name", ""),
                    "input": tool_call.get("parameters", {}),
                })

            if tool_result:
                transcript.append({
                    "timestamp": time_secs,
                    "role": role,
                    "type": "tool_result",
                    "content": tool_result,
                })

            if message:
                transcript.append({
                    "timestamp": time_secs,
                    "role": role,
                    "type": "text",
                    "content": message,
                })

        # Count stats
        user_messages = sum(1 for t in transcript if t.get("role") == "user" and t.get("type") == "text")
        ai_messages = sum(1 for t in transcript if t.get("role") == "agent" and t.get("type") == "text")
        tool_calls = sum(1 for t in transcript if t.get("type") == "tool_call")

        # Build session
        duration = metadata.get("call_duration_secs", 0)
        cost = metadata.get("cost", 0)
        termination = metadata.get("termination_reason", "")
        charging = metadata.get("charging") or {}

        return {
            "session_id": session_id,
            "meta": {
                "tenant_id": tenant_id,
                "start_time": metadata.get("start_time_unix_secs", ""),
                "duration_seconds": duration,
                "status": "COMPLETED" if not metadata.get("error") else "ERROR",
                "customer_phone": customer_phone,
                "termination_reason": termination,
                "voice_provider": "elevenlabs",
                "elevenlabs_conversation_id": el_data.get("conversation_id", ""),
            },
            "statistics": {
                "user_messages": user_messages,
                "ai_messages": ai_messages,
                "tool_calls": tool_calls,
                "total_events": len(transcript),
            },
            "cost": {
                "total_credits": cost,
                "duration_seconds": duration,
                "llm_usage": charging.get("llm_usage") or {},
            },
            "summary": {
                "call_successful": analysis.get("call_successful") or "unknown",
                "transcript_summary": analysis.get("transcript_summary") or "",
                "data_collection": analysis.get("data_collection_results") or {},
            },
            "transcript": transcript,
            # Preserve raw ElevenLabs data for anything we might need later
            "elevenlabs_raw": {
                "analysis": analysis,
                "metadata": metadata,
            },
        }
