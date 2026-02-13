"""
Post-Call Intelligence Engine

Analyzes completed session transcripts using an LLM to extract
structured lead data. This module turns raw conversation logs
into actionable revenue intelligence.

Architecture:
- Non-blocking: failures are logged but never crash the call flow
- Uses Gemini for structured extraction
- Output is a LeadObject stored alongside the session JSON
"""

import json
import logging
import os
import re
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field
import google.generativeai as genai

logger = logging.getLogger(__name__)


# ─── Data Model ───────────────────────────────────────────────

class LeadObject(BaseModel):
    """
    Structured lead extracted from a call transcript.
    
    This is the core output of the intelligence engine —
    every completed call produces one LeadObject.
    """
    customer_name: Optional[str] = Field(
        default=None,
        description="Customer name if mentioned during the call"
    )
    customer_phone: Optional[str] = Field(
        default=None,
        description="Phone number from session metadata"
    )
    core_intent: str = Field(
        default="unknown",
        description="Primary intent: booking, inquiry, complaint, support, other"
    )
    sentiment: str = Field(
        default="neutral",
        description="Overall sentiment: positive, neutral, negative"
    )
    call_outcome: str = Field(
        default="unknown",
        description="Result: booking_made, info_provided, issue_resolved, abandoned, escalated"
    )
    key_topics: List[str] = Field(
        default_factory=list,
        description="Main topics discussed during the call"
    )
    follow_up_required: bool = Field(
        default=False,
        description="Whether a follow-up action is needed"
    )
    summary: str = Field(
        default="",
        description="One-line natural language summary of the call"
    )


# ─── Extraction Prompt ───────────────────────────────────────

EXTRACTION_PROMPT = """You are a call analysis AI. Analyze the following conversation transcript and extract structured data.

TRANSCRIPT:
{transcript}

Extract the following fields and return ONLY a valid JSON object (no markdown, no explanation):

{{
  "customer_name": "string or null — the customer's name if mentioned",
  "core_intent": "one of: booking, inquiry, complaint, support, other",
  "sentiment": "one of: positive, neutral, negative",
  "call_outcome": "one of: booking_made, info_provided, issue_resolved, abandoned, escalated, unknown",
  "key_topics": ["list", "of", "main", "topics"],
  "follow_up_required": true/false,
  "summary": "One concise sentence summarizing the call"
}}

Return ONLY the JSON object. No other text."""


# ─── Engine ───────────────────────────────────────────────────

class PostCallIntelligenceEngine:
    """
    Analyzes completed call sessions and extracts structured lead data.
    
    Usage:
        engine = PostCallIntelligenceEngine()
        lead = await engine.analyze_session(session_data)
    """

    def __init__(self):
        """Initialize with Gemini configuration from environment."""
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")

        genai.configure(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-pro")
        self.model = genai.GenerativeModel(model_name=model_name)
        logger.info(f"PostCallIntelligenceEngine initialized (model={model_name})")

    def _extract_text_transcript(self, session_data: Dict[str, Any]) -> str:
        """
        Extract human-readable text lines from the session transcript.
        
        Filters to only user/ai text events and tool executions,
        skipping raw audio events and system metadata.
        
        Args:
            session_data: Full session JSON from SessionRecorder.export()
            
        Returns:
            Formatted transcript string
        """
        lines = []
        transcript = session_data.get("transcript", [])

        for entry in transcript:
            role = entry.get("role", "")
            event_type = entry.get("type", "")

            if role == "user" and event_type == "text":
                content = entry.get("content", "")
                lines.append(f"Customer: {content}")

            elif role == "ai" and event_type == "text":
                content = entry.get("content", "")
                lines.append(f"Agent: {content}")

            elif role == "tool" and event_type == "execution":
                name = entry.get("name", "unknown_tool")
                output = entry.get("output", "")
                lines.append(f"[Tool: {name}] → {output}")

        if not lines:
            return "(No text content in transcript)"

        return "\n".join(lines)

    def _parse_llm_json(self, raw_response: str) -> Dict[str, Any]:
        """
        Robustly parse JSON from LLM response.
        
        Handles common LLM quirks:
        - Markdown code fences (```json ... ```)
        - Leading/trailing whitespace
        - Partial markdown wrapping
        
        Args:
            raw_response: Raw text from the LLM
            
        Returns:
            Parsed dictionary
            
        Raises:
            ValueError: If JSON cannot be parsed after cleanup
        """
        text = raw_response.strip()

        # Strip markdown code fences: ```json ... ``` or ``` ... ```
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}\nRaw: {text[:500]}")
            raise ValueError(f"LLM returned invalid JSON: {e}")

    async def analyze_session(
        self,
        session_data: Dict[str, Any],
        customer_phone: Optional[str] = None
    ) -> LeadObject:
        """
        Analyze a completed session and extract a structured LeadObject.
        
        Args:
            session_data: Full session JSON from SessionRecorder.export()
            customer_phone: Optional phone number from the call endpoint
            
        Returns:
            LeadObject with extracted intelligence
        """
        session_id = session_data.get("session_id", "unknown")
        tenant_id = session_data.get("meta", {}).get("tenant_id", "unknown")

        logger.info(f"[{tenant_id}:{session_id}] Starting post-call analysis...")

        # 1. Extract text transcript
        text_transcript = self._extract_text_transcript(session_data)

        if text_transcript == "(No text content in transcript)":
            logger.warning(f"[{tenant_id}:{session_id}] No text content — skipping LLM analysis")
            return LeadObject(
                customer_phone=customer_phone,
                summary="Call had no text content (audio-only or empty session)"
            )

        # 2. Build prompt and call LLM
        prompt = EXTRACTION_PROMPT.format(transcript=text_transcript)

        try:
            response = self.model.generate_content(prompt)
            raw_text = response.text
            logger.debug(f"[{tenant_id}:{session_id}] LLM raw response: {raw_text[:300]}")
        except Exception as e:
            logger.error(f"[{tenant_id}:{session_id}] LLM call failed: {e}")
            return LeadObject(
                customer_phone=customer_phone,
                summary=f"Analysis failed: LLM error ({type(e).__name__})"
            )

        # 3. Parse JSON response
        try:
            parsed = self._parse_llm_json(raw_text)
        except ValueError:
            return LeadObject(
                customer_phone=customer_phone,
                summary="Analysis failed: could not parse LLM response"
            )

        # 4. Build LeadObject (with safe defaults for missing fields)
        try:
            lead = LeadObject(
                customer_name=parsed.get("customer_name"),
                customer_phone=customer_phone,
                core_intent=parsed.get("core_intent", "unknown"),
                sentiment=parsed.get("sentiment", "neutral"),
                call_outcome=parsed.get("call_outcome", "unknown"),
                key_topics=parsed.get("key_topics", []),
                follow_up_required=parsed.get("follow_up_required", False),
                summary=parsed.get("summary", "")
            )
        except Exception as e:
            logger.error(f"[{tenant_id}:{session_id}] LeadObject construction failed: {e}")
            return LeadObject(
                customer_phone=customer_phone,
                summary=f"Analysis failed: data validation error ({e})"
            )

        logger.info(
            f"[{tenant_id}:{session_id}] ✅ Intelligence extracted: "
            f"intent={lead.core_intent}, sentiment={lead.sentiment}, "
            f"outcome={lead.call_outcome}"
        )
        return lead
