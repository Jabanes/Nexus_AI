"""Per-tenant XLSX leads ledger.

Single workbook per tenant at data/leads/{tenant}/leads.xlsx.
Concurrent calls are serialized by an asyncio.Lock keyed on tenant_id.

When we migrate to a real DB this module is the single replacement point —
main.py only calls append_lead().
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

logger = logging.getLogger(__name__)

_HEADERS = [
    # When
    "call_date",            # YYYY-MM-DD (call start, US/Eastern)
    "call_time",            # HH:MM      (call start, US/Eastern)
    "call_started_at",      # ISO 8601   (UTC)
    "call_ended_at",        # ISO 8601   (UTC)
    "duration_s",
    # Who / why
    "classification",       # lead | spam | irrelevant | needs_review
    "customer_name",
    "phone_e164",
    "address",
    "service_requested",
    "intent",
    "summary",
    # Quality / status
    "missing_fields",
    "termination_reason",
    # Cost
    "cost_credits",
    # Cross-references
    "session_id",
    "conversation_id",      # ElevenLabs conversation_id
    "agent_id",             # ElevenLabs agent_id
    "voice_provider",
    "tenant_id",
    # Artifacts (clickable)
    "transcript_html",
    "audio_mp3",
    "session_json",
    # Bookkeeping
    "row_written_at",       # ISO 8601 — when this row was appended
]

_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(tenant_id: str) -> asyncio.Lock:
    if tenant_id not in _locks:
        _locks[tenant_id] = asyncio.Lock()
    return _locks[tenant_id]


def _ensure_workbook(path: Path) -> Workbook:
    if path.exists():
        return load_workbook(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "leads"
    ws.append(_HEADERS)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2C3E50")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
    widths = [
        12, 8, 22, 22, 10,         # when block
        14, 22, 16, 38, 30, 18, 50,  # who/why block
        22, 28,                     # status
        12,                         # cost
        38, 38, 38, 14, 18,         # cross-refs
        40, 40, 40,                 # artifacts
        22,                         # row_written_at
    ]
    for col_idx, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = w
    return wb


async def append_lead(
    tenant_id: str,
    row: Dict[str, Any],
    leads_root: Path = Path("data/leads"),
) -> Path:
    """
    Append one lead row to the tenant's XLSX. Creates the workbook on first use.

    row keys (all optional except session_id): customer_name, phone_e164, address,
    service_requested, intent, summary, cost_credits, duration_s, needs_review,
    transcript_html, audio_mp3, session_json.
    """
    tenant_dir = leads_root / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = tenant_dir / "leads.xlsx"

    async with _lock_for(tenant_id):
        # openpyxl is sync — run in default executor to keep the event loop free
        await asyncio.to_thread(_write_row, xlsx_path, row)

    logger.info(f"[leads] {tenant_id}: appended {row.get('session_id', '?')} → {xlsx_path}")
    return xlsx_path


def _write_row(xlsx_path: Path, row: Dict[str, Any]) -> None:
    wb = _ensure_workbook(xlsx_path)
    ws = wb["leads"] if "leads" in wb.sheetnames else wb.active

    values = [
        row.get("call_date", "") or "",
        row.get("call_time", "") or "",
        row.get("call_started_at", "") or "",
        row.get("call_ended_at", "") or "",
        row.get("duration_s", "") if row.get("duration_s") is not None else "",
        row.get("classification", "") or "",
        row.get("customer_name", "") or "",
        row.get("phone_e164", "") or "",
        row.get("address", "") or "",
        row.get("service_requested", "") or "",
        row.get("intent", "") or "",
        row.get("summary", "") or "",
        row.get("missing_fields", "") or "",
        row.get("termination_reason", "") or "",
        row.get("cost_credits", "") if row.get("cost_credits") is not None else "",
        row.get("session_id", "") or "",
        row.get("conversation_id", "") or "",
        row.get("agent_id", "") or "",
        row.get("voice_provider", "") or "",
        row.get("tenant_id", "") or "",
        _path_or_blank(row.get("transcript_html")),
        _path_or_blank(row.get("audio_mp3")),
        _path_or_blank(row.get("session_json")),
        row.get("row_written_at") or datetime.now().isoformat(timespec="seconds"),
    ]
    ws.append(values)

    row_idx = ws.max_row
    # Color-code the classification cell (column 6 = classification)
    cls = row.get("classification", "")
    cls_cell = ws.cell(row=row_idx, column=6)
    if cls == "lead":
        cls_cell.fill = PatternFill("solid", fgColor="C8E6C9")  # green
    elif cls in ("spam", "irrelevant"):
        cls_cell.fill = PatternFill("solid", fgColor="FFCDD2")  # red
    elif cls == "needs_review":
        cls_cell.fill = PatternFill("solid", fgColor="FFE0B2")  # amber

    # Hyperlink the artifact path columns (transcript_html, audio_mp3, session_json — cols 21, 22, 23)
    for col_idx in (21, 22, 23):
        cell = ws.cell(row=row_idx, column=col_idx)
        if cell.value:
            cell.hyperlink = cell.value
            cell.font = Font(color="0563C1", underline="single")

    # Atomic-ish save: write to temp, replace
    tmp = xlsx_path.with_suffix(".xlsx.tmp")
    wb.save(tmp)
    tmp.replace(xlsx_path)


def _path_or_blank(p: Optional[Path | str]) -> str:
    if not p:
        return ""
    return str(p)
