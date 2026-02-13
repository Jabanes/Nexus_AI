"""
Post-Call Intelligence Engine - Migrated to google-genai SDK

Analyzes completed session transcripts using an LLM to extract
structured lead data. This module turns raw conversation logs
into actionable revenue intelligence.
"""

import json
import logging
import os
import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


# ─── Data Model ───────────────────────────────────────────────

class LeadObject(BaseModel):
    """
    Structured lead extracted from a call transcript.
    """
    customer_name: Optional[str] = Field(None, description="Customer name if mentioned")
    customer_phone: Optional[str] = Field(None, description="Phone number")
    core_intent: str = Field("unknown", description="Primary intent")
    sentiment: str = Field("neutral", description="Overall sentiment")
    call_outcome: str = Field("unknown", description="Result of the call")
    key_topics: List[str] = Field(default_factory=list, description="Main topics")
    follow_up_required: bool = Field(False, description="Follow-up needed")
    summary: str = Field("", description="One-line summary")


# ─── Engine ───────────────────────────────────────────────────

class PostCallIntelligenceEngine:
    """
    Analyzes completed call sessions using google-genai SDK (Async).
    """

    def __init__(self, model_name: str = None):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.warning("GEMINI_API_KEY not set. Intelligence engine disabled.")
            self.client = None
        else:
            # Instance-based Client (Thread-Safe)
            self.client = genai.Client(api_key=api_key)

        if model_name is None:
            self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        else:
            self.model_name = model_name
            
        logger.info(f"PostCallIntelligenceEngine initialized (model={self.model_name})")

    def _extract_text_transcript(self, session_data: Dict[str, Any]) -> str:
        """Extract readable transcript from session events."""
        lines = []
        # Robust retrieval of events list
        events = session_data.get("transcript") or session_data.get("events") or []

        for entry in events:
            role = entry.get("role") or entry.get("type", "").split("/")[0]
            content = entry.get("content") or entry.get("data", {}).get("text", "")
            
            # Normalize roles for readability
            if "user" in role or role == "customer":
                lines.append(f"Customer: {content}")
            elif "ai" in role or "model" in role:
                lines.append(f"Agent: {content}")
            elif "tool" in role:
                lines.append(f"[Tool: {entry.get('name', 'unknown')}] -> {entry.get('output', '')}")

        return "\n".join(lines) if lines else ""

    async def analyze_session(
        self,
        session_data: Dict[str, Any],
        customer_phone: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze a completed session and extract structured data.
        Returns a Dictionary for compatibility with the rest of the app.
        """
        if not self.client:
            return LeadObject(customer_phone=customer_phone, summary="No Config").model_dump()

        session_id = session_data.get("session_id", "unknown")
        tenant_id = session_data.get("meta", {}).get("tenant_id", "unknown")

        # 1. Extract transcript
        transcript = self._extract_text_transcript(session_data)
        if not transcript:
            logger.warning(f"[{tenant_id}:{session_id}] No text content — skipping LLM analysis")
            return LeadObject(
                customer_phone=customer_phone, 
                summary="No text content"
            ).model_dump()

        # 2. Build Prompt
        prompt = f"""
        Analyze the following transcript and return a valid JSON object.
        
        TRANSCRIPT:
        {transcript}
        
        JSON SCHEMA:
        {{
            "customer_name": "string or null",
            "core_intent": "string",
            "sentiment": "string",
            "call_outcome": "string",
            "key_topics": ["string"],
            "follow_up_required": boolean,
            "summary": "string"
        }}
        """

        try:
            # 3. Async Call (.aio) - Non-Blocking
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )

            # 4. Parse & Validate
            parsed = json.loads(response.text)
            
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

            logger.info(f"[{tenant_id}:{session_id}] ✅ Intelligence: intent={lead.core_intent}, outcome={lead.call_outcome}")
            
            # COMPATIBILITY FIX: Return dict so main.py doesn't crash
            return lead.model_dump()

        except Exception as e:
            logger.error(f"[{tenant_id}:{session_id}] Intelligence analysis failed: {e}")
            return LeadObject(
                customer_phone=customer_phone, 
                summary=f"Error: {str(e)}"
            ).model_dump()
