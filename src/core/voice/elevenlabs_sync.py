"""
ElevenLabs Conversation Sync — Pulls full conversation data from ElevenLabs API
and saves it to our local session history.

This is provider-specific: only runs when the voice provider is ElevenLabs.
Other providers use the built-in SessionRecorder for history.
"""

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

    async def pull_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """
        Pull full conversation data from ElevenLabs.

        Args:
            conversation_id: The ElevenLabs conversation ID

        Returns:
            The raw ElevenLabs conversation data, or None on failure
        """
        if not self.api_key or not conversation_id:
            return None

        url = f"https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}"
        try:
            resp = requests.get(url, headers={"xi-api-key": self.api_key}, timeout=10)
            if resp.status_code == 200:
                logger.info(f"[ElevenLabsSync] Pulled conversation {conversation_id}")
                return resp.json()
            else:
                logger.warning(f"[ElevenLabsSync] Failed to pull {conversation_id}: {resp.status_code}")
                return None
        except Exception as e:
            logger.error(f"[ElevenLabsSync] Error pulling conversation: {e}")
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
        """
        metadata = el_data.get("metadata", {})
        analysis = el_data.get("analysis", {})
        el_transcript = el_data.get("transcript", [])

        # Convert transcript — include EVERYTHING
        transcript = []
        for entry in el_transcript:
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
                "llm_usage": metadata.get("charging", {}).get("llm_usage", {}),
            },
            "summary": {
                "call_successful": analysis.get("call_successful", "unknown"),
                "transcript_summary": analysis.get("transcript_summary", ""),
                "data_collection": analysis.get("data_collection_results", {}),
            },
            "transcript": transcript,
            # Preserve raw ElevenLabs data for anything we might need later
            "elevenlabs_raw": {
                "analysis": analysis,
                "metadata": metadata,
            },
        }
