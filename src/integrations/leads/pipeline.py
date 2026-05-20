"""Post-call leads pipeline.

Called from main.py after the ElevenLabs sync writes the session JSON.
Reads the session, classifies spam/lead from ElevenLabs analysis data_collection,
appends a row to the tenant XLSX (only for real leads), and fires off an async
audio fetch.
"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
    _EASTERN = ZoneInfo("America/New_York")
except Exception:
    _EASTERN = None  # type: ignore

from src.integrations.leads.audio_fetcher import download_recording
from src.integrations.leads.phone_utils import normalize_phone
from src.integrations.leads.transcript_html import render_transcript_html
from src.integrations.leads.xlsx_repository import append_lead

logger = logging.getLogger(__name__)

_TRUTHY = {"true", "yes", "spam", "1"}


def _coerce_bool(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if s in _TRUTHY:
            return True
        if s in {"false", "no", "lead", "0", "real", "real_lead"}:
            return False
    return None


def _from_data_collection(dc: Dict[str, Any], key: str) -> Optional[str]:
    """ElevenLabs data_collection_results structure: { key: { value: ..., rationale: ... } }."""
    entry = dc.get(key)
    if entry is None:
        return None
    if isinstance(entry, dict):
        v = entry.get("value")
        return v if v is not None else None
    return entry


def _format_times(meta: Dict[str, Any]) -> Dict[str, str]:
    """Derive call_date / call_time / call_started_at / call_ended_at from meta."""
    start_unix = meta.get("start_time")
    duration = meta.get("duration_seconds") or 0
    out = {"call_date": "", "call_time": "", "call_started_at": "", "call_ended_at": ""}

    try:
        if isinstance(start_unix, (int, float)) and start_unix > 0:
            start_utc = datetime.fromtimestamp(float(start_unix), tz=timezone.utc)
        elif isinstance(start_unix, str) and start_unix:
            # ISO string from local recorder
            s = start_unix.replace("Z", "+00:00")
            start_utc = datetime.fromisoformat(s)
            if start_utc.tzinfo is None:
                start_utc = start_utc.replace(tzinfo=timezone.utc)
        else:
            return out

        end_utc = start_utc.fromtimestamp(start_utc.timestamp() + float(duration or 0), tz=timezone.utc)
        local = start_utc.astimezone(_EASTERN) if _EASTERN else start_utc
        out["call_date"] = local.strftime("%Y-%m-%d")
        out["call_time"] = local.strftime("%H:%M")
        out["call_started_at"] = start_utc.isoformat(timespec="seconds")
        out["call_ended_at"] = end_utc.isoformat(timespec="seconds")
    except Exception:
        pass
    return out


async def run_leads_pipeline(
    session_data: Dict[str, Any],
    session_json_path: Path,
    leads_root: Path = Path("data/leads"),
) -> None:
    """
    Classify, save transcript HTML, fire async audio fetch, append XLSX row.

    Wrapped in try/except by the caller — should never raise.
    """
    meta = session_data.get("meta", {})
    summary = session_data.get("summary", {})
    tenant_id = meta.get("tenant_id", "unknown")
    session_id = session_data.get("session_id", "unknown")
    conversation_id = meta.get("elevenlabs_conversation_id", "") or ""

    # ── 1. Read data_collection / analysis ───────────────────────────
    dc = summary.get("data_collection", {}) or {}

    is_spam_raw = _from_data_collection(dc, "is_spam")
    is_spam = _coerce_bool(is_spam_raw)

    # ── 2. Extract lead fields (also needed for spam rows so we keep what we got) ─
    name = _from_data_collection(dc, "customer_name")
    raw_phone = _from_data_collection(dc, "customer_phone") or meta.get("customer_phone")
    address = _from_data_collection(dc, "address")
    service = _from_data_collection(dc, "service_requested")
    intent = _from_data_collection(dc, "core_intent") or summary.get("call_successful") or ""
    summary_text = summary.get("transcript_summary", "") or ""

    cost = session_data.get("cost", {}).get("total_credits")
    duration = meta.get("duration_seconds")
    phone_e164 = normalize_phone(raw_phone)

    # ── 3. Classify ──────────────────────────────────────────────────
    # spam: explicitly marked spam by the agent's analysis
    # needs_review: analysis didn't produce a verdict (LLM parse failure / missing)
    # lead: real customer with all required fields
    # irrelevant: real customer but missing required fields (e.g. just called to chat)
    required = {"customer_name": name, "phone": phone_e164, "address": address, "service_requested": service}
    missing = [k for k, v in required.items() if not v or not str(v).strip()]
    missing_fields_str = ",".join(missing) if missing else ""

    if is_spam is True:
        classification = "spam"
    elif is_spam is None:
        classification = "needs_review"
    elif missing:
        classification = "irrelevant"
    else:
        classification = "lead"

    # ── 4. Render HTML transcript (for every call, lead or not) ─────
    tenant_leads_dir = leads_root / tenant_id
    transcripts_dir = tenant_leads_dir / "transcripts"
    audio_dir = tenant_leads_dir / "audio"
    html_path = transcripts_dir / f"{session_id}.html"
    mp3_path = audio_dir / f"{session_id}.mp3"

    try:
        render_transcript_html(session_data, html_path)
    except Exception as e:
        logger.warning(f"[leads] {tenant_id}:{session_id} transcript html failed: {e}")
        html_path = None  # type: ignore

    # ── 5. Fire-and-forget MP3 download (every call — spam too) ─────
    if conversation_id:
        asyncio.create_task(
            _safe_download(conversation_id, mp3_path),
            name=f"audio_dl_{session_id}",
        )

    # ── 6. Append XLSX row (every call, classification marks the kind) ─
    times = _format_times(meta)
    termination = meta.get("termination_reason", "") or ""
    voice_provider = meta.get("voice_provider", "") or ""
    agent_id = (meta.get("elevenlabs_agent_id") or
                __import__("os").environ.get("ELEVENLABS_AGENT_ID", ""))

    row = {
        # When
        **times,
        "duration_s": duration,
        # Who / why
        "classification": classification,
        "customer_name": name,
        "phone_e164": phone_e164,
        "address": address,
        "service_requested": service,
        "intent": intent,
        "summary": summary_text,
        # Status
        "missing_fields": missing_fields_str,
        "termination_reason": termination,
        # Cost
        "cost_credits": cost,
        # Cross-refs
        "session_id": session_id,
        "conversation_id": conversation_id,
        "agent_id": agent_id,
        "voice_provider": voice_provider,
        "tenant_id": tenant_id,
        # Artifacts
        "transcript_html": html_path.resolve() if html_path else None,
        "audio_mp3": mp3_path.resolve(),
        "session_json": session_json_path.resolve() if session_json_path else None,
        # Bookkeeping
        "row_written_at": datetime.now().isoformat(timespec="seconds"),
    }
    await append_lead(tenant_id, row, leads_root=leads_root)
    logger.info(f"[leads] {tenant_id}:{session_id} classified={classification} missing={missing_fields_str or 'none'}")


async def _safe_download(conversation_id: str, dest_path: Path) -> None:
    try:
        await download_recording(conversation_id, dest_path)
    except Exception as e:
        logger.warning(f"[leads] background audio download failed for {conversation_id}: {e}")
