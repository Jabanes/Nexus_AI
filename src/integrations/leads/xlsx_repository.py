"""Per-tenant XLSX leads ledger.

Single workbook per tenant at data/leads/{tenant}/leads.xlsx.
Two sheets:
  • "Calls"       — end-client view. Business-relevant fields only.
  • "Diagnostics" — internal/dev view. Identifiers, cost, raw paths.

Both sheets gain one row per call, in lockstep, keyed on session_id.

Concurrent calls are serialized by an asyncio.Lock per tenant.

When we migrate to a real DB this module is the single replacement point —
main.py only calls append_lead().
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

logger = logging.getLogger(__name__)


# ── Sheet 1: Calls (what the end client sees) ──────────────────────────
# (header label, row dict key, column width)
_CALLS_COLUMNS: List[Tuple[str, str, int]] = [
    ("Date",            "call_date",         12),
    ("Time",            "call_time",          8),
    ("Status",          "classification",    14),
    ("Customer Name",   "customer_name",     22),
    ("Phone",           "phone_e164",        16),
    ("Address",         "address",           38),
    ("Service Needed",  "service_requested", 30),
    ("Intent",          "intent",            18),
    ("Summary",         "summary",           60),
    ("Duration (s)",    "duration_s",        12),
    ("Missing Fields",  "missing_fields",    22),
    ("Transcript",      "transcript_html",   40),
    ("Recording",       "audio_mp3",         40),
]
_CALLS_CLASSIFICATION_COL = 3   # 1-indexed column number for classification
_CALLS_HYPERLINK_COLS = (12, 13)  # transcript_html, audio_mp3


# ── Sheet 2: Diagnostics (internal / dev) ──────────────────────────────
_DIAG_COLUMNS: List[Tuple[str, str, int]] = [
    ("Session ID",       "session_id",          38),
    ("Conversation ID",  "conversation_id",     38),
    ("Agent ID",         "agent_id",            38),
    ("Tenant ID",        "tenant_id",           18),
    ("Voice Provider",   "voice_provider",      14),
    ("Started (UTC)",    "call_started_at",     22),
    ("Ended (UTC)",      "call_ended_at",       22),
    ("Termination",      "termination_reason",  28),
    ("Cost (credits)",   "cost_credits",        14),
    ("Session JSON",     "session_json",        40),
    ("Row Written",      "row_written_at",      22),
]
_DIAG_HYPERLINK_COLS = (10,)  # session_json


_locks: Dict[str, asyncio.Lock] = {}


def _lock_for(tenant_id: str) -> asyncio.Lock:
    if tenant_id not in _locks:
        _locks[tenant_id] = asyncio.Lock()
    return _locks[tenant_id]


def _style_header_row(ws: Worksheet, columns: List[Tuple[str, str, int]]) -> None:
    """Write header row, style it, set column widths."""
    ws.append([label for (label, _key, _w) in columns])
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2C3E50")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
    for col_idx, (_label, _key, w) in enumerate(columns, start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = w
    ws.freeze_panes = "A2"


def _ensure_workbook(path: Path) -> Workbook:
    if path.exists():
        return load_workbook(path)
    wb = Workbook()
    # Default sheet → "Calls"
    ws_calls = wb.active
    ws_calls.title = "Calls"
    _style_header_row(ws_calls, _CALLS_COLUMNS)
    # Second sheet → "Diagnostics"
    ws_diag = wb.create_sheet("Diagnostics")
    _style_header_row(ws_diag, _DIAG_COLUMNS)
    return wb


async def append_lead(
    tenant_id: str,
    row: Dict[str, Any],
    leads_root: Path = Path("data/leads"),
) -> Path:
    """
    Append one call to BOTH sheets of the tenant's workbook.
    Creates the workbook on first use.
    """
    tenant_dir = leads_root / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = tenant_dir / "leads.xlsx"

    async with _lock_for(tenant_id):
        # openpyxl is sync — run in default executor to keep the event loop free
        await asyncio.to_thread(_write_row, xlsx_path, row)

    logger.info(f"[leads] {tenant_id}: appended {row.get('session_id', '?')} → {xlsx_path}")
    return xlsx_path


def _coerce(value: Any) -> Any:
    """Normalize None / Path → string; numeric pass-through."""
    if value is None:
        return ""
    if isinstance(value, Path):
        return str(value)
    return value


def _append_row_to_sheet(
    ws: Worksheet,
    columns: List[Tuple[str, str, int]],
    row: Dict[str, Any],
    hyperlink_cols: Tuple[int, ...],
    classification_col: Optional[int] = None,
) -> None:
    """Generic row writer for either sheet."""
    values = [_coerce(row.get(key)) for (_label, key, _w) in columns]
    ws.append(values)
    row_idx = ws.max_row

    # Color-code classification (Calls sheet only)
    if classification_col is not None:
        cls = row.get("classification", "")
        cell = ws.cell(row=row_idx, column=classification_col)
        if cls == "lead":
            cell.fill = PatternFill("solid", fgColor="C8E6C9")  # green
        elif cls in ("spam", "irrelevant"):
            cell.fill = PatternFill("solid", fgColor="FFCDD2")  # red
        elif cls == "needs_review":
            cell.fill = PatternFill("solid", fgColor="FFE0B2")  # amber

    # Hyperlink file-path columns
    for col_idx in hyperlink_cols:
        cell = ws.cell(row=row_idx, column=col_idx)
        if cell.value:
            cell.hyperlink = str(cell.value)
            cell.font = Font(color="0563C1", underline="single")


def _write_row(xlsx_path: Path, row: Dict[str, Any]) -> None:
    wb = _ensure_workbook(xlsx_path)

    if "row_written_at" not in row or not row["row_written_at"]:
        row["row_written_at"] = datetime.now().isoformat(timespec="seconds")

    # Sheet 1: Calls
    ws_calls = wb["Calls"]
    _append_row_to_sheet(
        ws_calls,
        _CALLS_COLUMNS,
        row,
        hyperlink_cols=_CALLS_HYPERLINK_COLS,
        classification_col=_CALLS_CLASSIFICATION_COL,
    )

    # Sheet 2: Diagnostics
    ws_diag = wb["Diagnostics"]
    _append_row_to_sheet(
        ws_diag,
        _DIAG_COLUMNS,
        row,
        hyperlink_cols=_DIAG_HYPERLINK_COLS,
    )

    # Atomic-ish save
    tmp = xlsx_path.with_suffix(".xlsx.tmp")
    wb.save(tmp)
    tmp.replace(xlsx_path)
