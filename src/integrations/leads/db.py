"""Local leads database — SQLite with a Supabase-shaped schema.

Single file at data/leads.db. Two tables:

  • tenants — one row per tenant (name, agent_id, voice_id, created_at).
  • calls   — one row per completed call. tenant_id is the natural partition
    column; indexed for fast per-tenant queries and future RLS in Supabase.

The schema is intentionally identical in shape to what Supabase will hold —
TEXT, INTEGER, JSON-as-TEXT — so the migration becomes:
  1. Run CREATE TABLE statements against postgres.
  2. Add RLS policies: USING (tenant_id = (auth.jwt() ->> 'tenant_id'))
  3. Optionally migrate artifact files to Supabase Storage.

This module is the SOLE write path for structured call data. The legacy
xlsx_repository.append_lead is no longer called from the pipeline — XLSX is
now an export view, generated on demand by querying this DB.
"""

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
-- ────────────────────────────────────────────────────────────────────
-- Future: SaaS users (humans who log into the dashboard).
-- Empty today; populated when the auth layer is built. Schema is in place
-- now so the FK from tenants.owner_user_id is real from day one.
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    email         TEXT UNIQUE,
    password_hash TEXT,                   -- bcrypt / argon2 once auth lands
    name          TEXT,
    role          TEXT DEFAULT 'owner',   -- owner | admin (future)
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ────────────────────────────────────────────────────────────────────
-- A "tenant" here = one business / configured agent unit. Owned by a user.
-- owner_user_id is nullable today so the system runs without auth; will be
-- backfilled when users are created.
-- ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id      TEXT PRIMARY KEY,
    owner_user_id  TEXT,                   -- FK → users.user_id (NULL today)
    name           TEXT,                   -- display name e.g. "Power Roofing"
    agent_id       TEXT,                   -- ElevenLabs agent_id
    voice_id       TEXT,
    voice_provider TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (owner_user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenants_owner ON tenants(owner_user_id);

CREATE TABLE IF NOT EXISTS calls (
    call_id              TEXT PRIMARY KEY,             -- our session_id
    tenant_id            TEXT NOT NULL,                -- future RLS column
    conversation_id      TEXT,                          -- ElevenLabs ID
    agent_id             TEXT,
    voice_provider       TEXT,

    -- timing (UTC)
    call_started_at      TIMESTAMP,
    call_ended_at        TIMESTAMP,
    duration_s           INTEGER,

    -- classification
    classification       TEXT NOT NULL,                 -- lead | spam | irrelevant | needs_review
    is_spam              INTEGER,                       -- 0/1, nullable

    -- extracted lead fields
    customer_name        TEXT,
    customer_phone       TEXT,                          -- raw, as said
    phone_e164           TEXT,                          -- normalized
    address              TEXT,
    service_requested    TEXT,
    intent               TEXT,
    summary              TEXT,

    -- quality / status
    missing_fields       TEXT,                          -- comma list
    termination_reason   TEXT,

    -- cost
    cost_credits         INTEGER,

    -- artifact references (absolute paths today; URLs after Supabase Storage)
    transcript_html_path TEXT,
    audio_mp3_path       TEXT,
    session_json_path    TEXT,

    -- structured ElevenLabs analysis (lean blob — value + rationale only)
    data_collection_json TEXT,

    -- bookkeeping
    row_written_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_calls_tenant         ON calls(tenant_id);
CREATE INDEX IF NOT EXISTS idx_calls_classification ON calls(classification);
CREATE INDEX IF NOT EXISTS idx_calls_started        ON calls(call_started_at);
CREATE INDEX IF NOT EXISTS idx_calls_phone          ON calls(phone_e164);
CREATE INDEX IF NOT EXISTS idx_calls_tenant_started ON calls(tenant_id, call_started_at);
"""


@dataclass
class CallRecord:
    """A single completed call. One row in the `calls` table."""
    call_id: str
    tenant_id: str
    classification: str            # lead | spam | irrelevant | needs_review

    conversation_id: Optional[str] = None
    agent_id: Optional[str] = None
    voice_provider: Optional[str] = None

    call_started_at: Optional[str] = None   # ISO UTC
    call_ended_at: Optional[str] = None
    duration_s: Optional[int] = None

    is_spam: Optional[bool] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    phone_e164: Optional[str] = None
    address: Optional[str] = None
    service_requested: Optional[str] = None
    intent: Optional[str] = None
    summary: Optional[str] = None

    missing_fields: Optional[str] = None
    termination_reason: Optional[str] = None
    cost_credits: Optional[int] = None

    transcript_html_path: Optional[str] = None
    audio_mp3_path: Optional[str] = None
    session_json_path: Optional[str] = None

    data_collection: Optional[Dict[str, Any]] = field(default=None)


class LeadsDB:
    """Thin async wrapper around sqlite3. One DB file for the whole engine."""

    def __init__(self, path: Path = Path("data/leads.db")):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    # ── Schema ─────────────────────────────────────────────────────
    def init_schema_sync(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        logger.info(f"[LeadsDB] schema ready at {self.path}")

    async def init_schema(self) -> None:
        await asyncio.to_thread(self.init_schema_sync)

    # ── Tenants ────────────────────────────────────────────────────
    async def upsert_tenant(
        self,
        tenant_id: str,
        name: Optional[str] = None,
        agent_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        voice_provider: Optional[str] = None,
    ) -> None:
        def _do():
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """
                    INSERT INTO tenants (tenant_id, name, agent_id, voice_id, voice_provider)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id) DO UPDATE SET
                        name           = COALESCE(excluded.name, tenants.name),
                        agent_id       = COALESCE(excluded.agent_id, tenants.agent_id),
                        voice_id       = COALESCE(excluded.voice_id, tenants.voice_id),
                        voice_provider = COALESCE(excluded.voice_provider, tenants.voice_provider),
                        updated_at     = CURRENT_TIMESTAMP
                    """,
                    (tenant_id, name, agent_id, voice_id, voice_provider),
                )
                conn.commit()
        async with self._lock:
            await asyncio.to_thread(_do)

    async def list_tenants(self) -> List[Dict[str, Any]]:
        def _do():
            with sqlite3.connect(self.path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM tenants ORDER BY tenant_id").fetchall()
                return [dict(r) for r in rows]
        return await asyncio.to_thread(_do)

    async def list_tenants_with_stats(self) -> List[Dict[str, Any]]:
        """
        One row per business, joined with aggregate call stats.
        Used by the businesses list view ("home" dashboard).
        """
        def _do():
            with sqlite3.connect(self.path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT
                        t.*,
                        COALESCE(c.total, 0)            AS total_calls,
                        COALESCE(c.leads, 0)            AS leads,
                        COALESCE(c.spam, 0)             AS spam,
                        COALESCE(c.irrelevant, 0)       AS irrelevant,
                        COALESCE(c.needs_review, 0)     AS needs_review,
                        COALESCE(c.total_cost, 0)       AS total_cost,
                        COALESCE(c.total_duration_s, 0) AS total_duration_s,
                        c.last_call_at                  AS last_call_at
                    FROM tenants t
                    LEFT JOIN (
                        SELECT
                            tenant_id,
                            COUNT(*)                                                         AS total,
                            SUM(CASE WHEN classification = 'lead'         THEN 1 ELSE 0 END) AS leads,
                            SUM(CASE WHEN classification = 'spam'         THEN 1 ELSE 0 END) AS spam,
                            SUM(CASE WHEN classification = 'irrelevant'   THEN 1 ELSE 0 END) AS irrelevant,
                            SUM(CASE WHEN classification = 'needs_review' THEN 1 ELSE 0 END) AS needs_review,
                            COALESCE(SUM(cost_credits), 0)                                   AS total_cost,
                            COALESCE(SUM(duration_s),   0)                                   AS total_duration_s,
                            MAX(call_started_at)                                             AS last_call_at
                        FROM calls
                        GROUP BY tenant_id
                    ) c ON c.tenant_id = t.tenant_id
                    ORDER BY t.tenant_id
                    """
                ).fetchall()
                return [dict(r) for r in rows]
        return await asyncio.to_thread(_do)

    # ── Calls ──────────────────────────────────────────────────────
    async def insert_call(self, record: CallRecord) -> None:
        """Insert one call row. Idempotent on call_id (replaces existing)."""
        def _do():
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO calls (
                        call_id, tenant_id, conversation_id, agent_id, voice_provider,
                        call_started_at, call_ended_at, duration_s,
                        classification, is_spam,
                        customer_name, customer_phone, phone_e164,
                        address, service_requested, intent, summary,
                        missing_fields, termination_reason, cost_credits,
                        transcript_html_path, audio_mp3_path, session_json_path,
                        data_collection_json
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?
                    )
                    """,
                    (
                        record.call_id, record.tenant_id, record.conversation_id,
                        record.agent_id, record.voice_provider,
                        record.call_started_at, record.call_ended_at, record.duration_s,
                        record.classification,
                        None if record.is_spam is None else int(bool(record.is_spam)),
                        record.customer_name, record.customer_phone, record.phone_e164,
                        record.address, record.service_requested, record.intent, record.summary,
                        record.missing_fields, record.termination_reason, record.cost_credits,
                        record.transcript_html_path, record.audio_mp3_path, record.session_json_path,
                        json.dumps(record.data_collection, ensure_ascii=False) if record.data_collection else None,
                    ),
                )
                conn.commit()
        async with self._lock:
            await asyncio.to_thread(_do)

    async def list_calls(
        self,
        tenant_id: str,
        classification: Optional[str] = None,
        since: Optional[str] = None,             # ISO datetime, filter call_started_at >=
        limit: int = 200,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        def _do():
            with sqlite3.connect(self.path) as conn:
                conn.row_factory = sqlite3.Row
                clauses = ["tenant_id = ?"]
                params: List[Any] = [tenant_id]
                if classification:
                    clauses.append("classification = ?")
                    params.append(classification)
                if since:
                    clauses.append("call_started_at >= ?")
                    params.append(since)
                where = " AND ".join(clauses)
                sql = (
                    f"SELECT * FROM calls WHERE {where} "
                    f"ORDER BY call_started_at DESC NULLS LAST, row_written_at DESC "
                    f"LIMIT ? OFFSET ?"
                )
                params.extend([limit, offset])
                # SQLite doesn't support NULLS LAST until 3.30; fallback for older
                try:
                    rows = conn.execute(sql, params).fetchall()
                except sqlite3.OperationalError:
                    sql = sql.replace(" NULLS LAST", "")
                    rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
        return await asyncio.to_thread(_do)

    async def stats(self, tenant_id: str) -> Dict[str, Any]:
        def _do():
            with sqlite3.connect(self.path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN classification = 'lead'           THEN 1 ELSE 0 END) AS leads,
                        SUM(CASE WHEN classification = 'spam'           THEN 1 ELSE 0 END) AS spam,
                        SUM(CASE WHEN classification = 'irrelevant'     THEN 1 ELSE 0 END) AS irrelevant,
                        SUM(CASE WHEN classification = 'needs_review'   THEN 1 ELSE 0 END) AS needs_review,
                        COALESCE(SUM(cost_credits), 0) AS total_cost,
                        COALESCE(SUM(duration_s), 0)   AS total_duration_s
                    FROM calls
                    WHERE tenant_id = ?
                    """,
                    (tenant_id,),
                ).fetchone()
                return dict(row) if row else {}
        return await asyncio.to_thread(_do)

    async def get_call(self, call_id: str) -> Optional[Dict[str, Any]]:
        def _do():
            with sqlite3.connect(self.path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM calls WHERE call_id = ?", (call_id,)).fetchone()
                return dict(row) if row else None
        return await asyncio.to_thread(_do)


# ── Module-level singleton ────────────────────────────────────────────
_singleton: Optional[LeadsDB] = None


def get_db() -> LeadsDB:
    global _singleton
    if _singleton is None:
        _singleton = LeadsDB()
    return _singleton
