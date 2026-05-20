# Production Requirements — Nexus Voice Engine

**Last updated:** 2026-05-20
**Status:** Active — preparing for first paying client (Aviv / Power Roofing NYC)
**Owner:** Erez Habani

---

## 1. Executive Summary

Nexus Voice Engine is moving from local dev (Docker on `localhost:8765`) to a real production deployment for its first paying client, **Power Roofing NYC**. The owners are **Aviv** and **Moshe**, who currently use a phone-forwarding service ("Garage" — TBD exact product name) that rings both of their personal phones.

The production goal:

> The number their customers already dial should ring our AI receptionist FIRST. The agent screens spam, captures real leads (name, phone, address, what's wrong with the roof), and ends the call. Aviv and Moshe both open one shared dashboard and see every lead the agent captured, with the ability to call those leads back from the dashboard, leave notes, and track which of them handled what.

This doc captures **everything that has to be true** for that to work — and the changes to the system architecture, the deployment, and the data model needed to get there.

---

## 2. Architecture Changes Required Before Production

### 2.1 Workspace tier (NEW — required for the Aviv/Moshe model)

The current data model is too flat for multi-person teams:

**Current:**
```
users → tenants (businesses) → calls
```
One user owns one or more businesses. Other users cannot see those businesses.

**Required:**
```
users  ←─ many-to-many ─→  workspaces  ──owns→  tenants  ──has→  calls
                                                            └─→  lead_actions (CRM trail)
```

A **workspace** is a shared collaboration space. Aviv and Moshe are both members of the "Power Roofing" workspace, with their own logins, and both see the same leads.

#### Schema additions

```sql
-- New
CREATE TABLE workspaces (
    workspace_id   TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by     TEXT,                     -- user_id of creator
    FOREIGN KEY (created_by) REFERENCES users(user_id)
);

-- New: junction table for user ↔ workspace
CREATE TABLE workspace_members (
    workspace_id   TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    role           TEXT DEFAULT 'member',    -- owner | admin | member | viewer
    joined_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, user_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id),
    FOREIGN KEY (user_id)      REFERENCES users(user_id)
);

-- New: per-call CRM trail (notes, status changes, callback attempts)
CREATE TABLE lead_actions (
    action_id      TEXT PRIMARY KEY,
    call_id        TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,             -- denormalized for fast RLS
    user_id        TEXT NOT NULL,             -- who did it
    action_type    TEXT NOT NULL,             -- note | status_change | callback_attempt | callback_completed | tag
    payload_json   TEXT,                      -- {status: "won"} | {note: "..."} | {tag: "urgent"}
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES calls(call_id)
);

-- Modify: tenants now belong to a workspace, not a single user.
-- Migration: add workspace_id column, backfill from owner_user_id by creating
-- a workspace per existing owner. Drop owner_user_id once migration is verified.
ALTER TABLE tenants ADD COLUMN workspace_id TEXT;
-- (FK constraint added after backfill: REFERENCES workspaces(workspace_id))

-- Modify: calls gain a status column for the CRM workflow
ALTER TABLE calls ADD COLUMN lead_status TEXT DEFAULT 'new';
-- Values: new | contacted | callback_scheduled | won | lost | spam_confirmed
ALTER TABLE calls ADD COLUMN assigned_to TEXT;   -- user_id of the person handling it (nullable)
```

#### RLS implications

The auth check becomes:
> "Can the current user access this resource because they are a MEMBER of the workspace that owns it?"

In code: `require_workspace_member(workspace_id)` replaces `require_tenant_owner(tenant_id)`. The tenant ownership check becomes a 2-step join: tenant → workspace → workspace_members → current user.

In future Supabase: a single RLS policy on every table:
```sql
USING (workspace_id IN (SELECT workspace_id FROM workspace_members WHERE user_id = auth.uid()))
```

#### Roles within a workspace

| Role | Permissions |
|---|---|
| `owner` | Everything. Can add/remove members, edit business config, delete the workspace. |
| `admin` | Add/remove members, edit business config, edit leads. Cannot delete the workspace. |
| `member` | View leads, mark statuses, leave notes, call back leads. The default for Aviv/Moshe. |
| `viewer` | Read-only — useful for accountants, support staff. No CRM mutations. |

### 2.2 CRM features on the dashboard (NEW)

The "leads list" is no longer just a static view — it's the day-to-day CRM workspace. Every lead row needs:

#### Click-to-call
- **What:** A "Call back" button on every lead row that initiates a call to `phone_e164`.
- **How (v1):** `<a href="tel:+12925556472">` opens the phone app on mobile, or a registered SIP/dialer on desktop. Zero backend, zero cost.
- **How (v2):** Twilio Voice API — engine places the call, bridges Aviv's phone to the customer's phone. Logged as a `callback_attempted` action with duration.
- **Logged:** `lead_actions(action_type='callback_attempted', user_id=<who clicked>, created_at=NOW)`. Even if the call doesn't connect, we log the attempt.

#### Lead status workflow
Statuses (stored on `calls.lead_status`):
- `new` (default) — agent captured it, nobody touched yet
- `contacted` — a workspace member spoke to the customer
- `callback_scheduled` — manual callback scheduled for later
- `won` — turned into a real job
- `lost` — customer went elsewhere or no longer interested
- `spam_confirmed` — was actually spam that slipped through

Status changes via dropdown on the row. Each change writes a `lead_actions(action_type='status_change', payload_json={old, new})` row.

#### Notes per lead
- Free-text notes attached to a call. Multiple notes per call. Each note shows who wrote it and when.
- Surface in the table as a count badge ("3 notes") with an expandable inline editor.

#### Assignment
- "Assign to" dropdown lets Aviv assign a lead to Moshe (or himself). `calls.assigned_to = <user_id>`.
- Filter the table by assignee ("My leads" / "Moshe's leads" / "Unassigned").

#### Tags (nice-to-have, post-v1)
- `urgent`, `emergency`, `repeat_customer`, etc. Multi-valued. Surface as colored chips.

#### Activity timeline
- Per-lead detail view (modal or side panel) shows the full chronological history: agent captured at X, status changed by Aviv at Y, note added by Moshe at Z, callback attempted at W. This is the audit trail.

### 2.3 Dashboard UI changes

| Change | Why |
|---|---|
| Workspace switcher in top-left (if user has >1 workspace) | Multi-workspace users (consultants, internal staff) |
| Move "businesses sidebar" inside the active workspace context | Workspace owns businesses; sidebar shows businesses for currently-selected workspace |
| "Call back" button on every lead row | Primary CRM action |
| Lead status dropdown on every row, color-coded | Visible workflow state |
| Notes count badge → expandable note editor | Inline annotation |
| Assignee column with avatar/initial | "Who owns this" at a glance |
| Filter chips: Status, Assignee, Date range | Narrow the list fast |
| Lead detail modal with activity timeline | Forensic / audit view |
| Mobile: same CRM actions reachable via row tap → bottom-sheet | Aviv & Moshe will use phones often |

### 2.4 Settings page (NEW)

Currently no way to manage anything in the UI — everything's CLI. For production we need at minimum:

- **Members** — list workspace members, change roles, remove members, invite new members (email-based invite token)
- **Business config** — view agent settings (prompt, voice, evaluation criteria). v1: read-only. v2: edit.
- **Profile** — change own password, change name/email
- **Workspace info** — name, plan/tier, usage summary

---

## 3. Phone Integration — The Aviv/Moshe Pathway

### 3.1 Current state for the client

- Aviv & Moshe use a forwarding service called **"Garage"** (exact product name TBD — Israeli telephony service likely).
- The Garage number is the published "call us" number on the Power Roofing site.
- Currently: incoming call → Garage forwards → simultaneously rings Aviv's phone AND Moshe's phone.

### 3.2 Required state

```
Customer dials Power Roofing's published number
       │
       ▼
Garage forwarding service (existing)
       │
       ▼  (instead of forwarding to Aviv+Moshe directly)
       │
ElevenLabs Conversational AI (Power Roofing agent)
       │
       │  agent handles call: spam-screen, capture lead
       │
       ▼
Post-call: ElevenLabs analysis → Nexus engine → leads.db
       │
       ▼
Aviv & Moshe see the new lead in the shared dashboard
       │
       ▼
Either of them clicks "Call back" → talks to the actual customer
```

### 3.3 Implementation options (need to confirm what Garage supports)

| Option | How | Pro | Con |
|---|---|---|---|
| **A. Garage → SIP → ElevenLabs** | Configure Garage to forward as SIP to ElevenLabs' SIP endpoint | Cleanest, no Twilio middle layer | Depends on Garage's outbound SIP support |
| **B. Garage → Twilio → ElevenLabs** | Forward Garage to a Twilio number, Twilio handoffs to ElevenLabs ConvAI's phone integration | Twilio is a well-known intermediary; ElevenLabs has a documented Twilio integration | One more vendor in the chain ($1.15/mo Twilio number + per-minute costs) |
| **C. Replace Garage entirely** | Customer's published number = a Twilio number wired directly to ElevenLabs | Simplest architecture | Changes the customer-facing number; bigger ask of Aviv/Moshe |

**Decision needed BEFORE production:** Which path. Talk to Aviv about what Garage supports.

### 3.4 Recording and consent

- The agent already says "this call is recorded" in the first message ✓
- NY is a **one-party-consent** state — legal as long as one party (us) knows
- If the customer base ever extends outside one-party states (CA, FL, MA, etc.), need to re-validate. For Power Roofing NYC the current setup is fine.

---

## 4. Hosting & Infrastructure

### 4.1 Deployment target

| Component | Production setup |
|---|---|
| **Engine container** | Docker container running `uvicorn src.main:app --host 0.0.0.0 --port 8000` on a VPS or managed Docker host |
| **Reverse proxy + TLS** | Caddy (auto Let's Encrypt) or Nginx + certbot, terminating HTTPS on a real subdomain |
| **Domain** | e.g. `app.nexusvoice.ai` for the dashboard, same host serves `/ws/call/*` for incoming agent connections |
| **Static files** | Served by the engine itself for now (small footprint). If grows, move to Vercel/Netlify or S3+CloudFront. |
| **DB (v1)** | SQLite at `/var/lib/nexus/leads.db` on the VPS (single file, easy backup). Volume mount the same way we do locally. |
| **DB (v2)** | Migrate to Supabase Postgres when >5 concurrent workspaces or want real RLS. |
| **Audio + transcript files** | Local filesystem on VPS (v1) → Supabase Storage / S3 (v2) |

### 4.2 Recommended host (v1)

A single VPS is enough for the first dozens of workspaces:

| Provider | Tier | Approx. cost |
|---|---|---|
| **Hetzner Cloud** | CX22 (2 vCPU, 4GB RAM, 40GB) | €4.50 / mo |
| **DigitalOcean** | s-1vcpu-2gb | $12 / mo |
| **Fly.io** | shared-cpu-1x | ~$2-5 / mo depending on usage |
| **Railway** | Hobby plan, Docker | $5 / mo flat |

### 4.3 Code adjustments needed for HTTPS

In [src/main.py](../src/main.py) SessionMiddleware config:
- `https_only=False` → `https_only=True`
- `same_site="lax"` → consider `"strict"` for stronger CSRF protection (but lax is fine and works better with iframes)
- Pin a real `SESSION_SECRET` in the production env file (different from dev)

In [src/main.py](../src/main.py) CORS:
- Set `CORS_ALLOWED_ORIGINS` env var explicitly to the production frontend origins. Drop `*`.

### 4.4 Environment file (production)

```bash
# REQUIRED
SESSION_SECRET=<unique 48+ char value, never reuse dev value>
GEMINI_API_KEY=<prod key>
ELEVENLABS_API_KEY=<prod key>
ELEVENLABS_AGENT_ID=<fallback only — most tenants pin their own agent_id in config.yaml>

# Production-specific
CORS_ALLOWED_ORIGINS=https://app.nexusvoice.ai
HTTPS_ONLY_COOKIES=1   # (new env-driven toggle to add)
CREDITS_PER_USD=10000  # calibrate against actual ElevenLabs invoice

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=JSON        # structured logs for ingestion to Loki/Logtail/etc.
```

---

## 5. Database Migration Path (SQLite → Supabase)

### 5.1 When to migrate

Triggers:
- More than ~5 concurrent workspaces (SQLite write contention starts to matter)
- Need real DB-level RLS (multi-tenant security audit asks for it)
- Want analytics / BI on call data (Postgres + Metabase is much nicer than SQLite + ad-hoc scripts)

### 5.2 Migration plan

1. Provision Supabase project; get `DATABASE_URL` + anon/service keys
2. Run [src/integrations/leads/db.py](../src/integrations/leads/db.py) schema against Postgres (TEXT → TEXT, INTEGER stays, TIMESTAMP → TIMESTAMPTZ)
3. Add RLS policies (one policy per table, all on `workspace_id`)
4. Enable Supabase Auth (replaces our session-cookie layer)
5. Build `SupabaseLeadsDB` implementing the same interface as `LeadsDB`; swap via `get_db()`
6. Migrate artifact files (MP3, HTML, JSON) to Supabase Storage; update path columns to URLs
7. Swap auth: `get_current_user` now reads the Supabase JWT from the cookie or `Authorization: Bearer ...`
8. Data backfill: one-time export of the SQLite contents into Supabase (Python script using both clients)

### 5.3 What stays the same after migration

- All endpoint URLs (`/api/businesses`, `/api/leads/{t}/calls`, `/artifacts/...`)
- The dashboard JS (still calls the same APIs)
- The pipeline (`pipeline.py`) — it just calls `db.insert_call()`
- The XLSX export logic
- The agent and conversation flow

The only swapped pieces are the DB driver and the auth dependency. The interface boundaries we drew at v1 specifically protect this migration.

---

## 6. Production-readiness checklist

### 6.1 Code changes

- [ ] **Workspace tier** — schema + endpoints + auth dependency (`require_workspace_member`)
- [ ] **CRM features** — `lead_actions` table, "call back" buttons, status dropdown, notes
- [ ] **Settings page** — members, profile, business config (read-only at first)
- [ ] **HTTPS-aware cookies** — env-driven `HTTPS_ONLY_COOKIES` toggle on SessionMiddleware
- [ ] **CORS** — pin to known production origins (no `*` in prod)
- [ ] **Health endpoint** — `/health` returning 200 + JSON when DB is reachable (for uptime monitors)
- [ ] **Structured JSON logging** — `LOG_FORMAT=JSON` works correctly in production
- [ ] **Webhook URL stability** — agent tools point at the production engine URL, not ngrok
- [ ] **`CREDITS_PER_USD` calibration** — verified against an actual ElevenLabs invoice
- [ ] **Static files cache headers** — set sensible `Cache-Control` on `/static/*`

### 6.2 Operational

- [ ] **Domain + DNS** — A record pointing at the VPS, subdomain dedicated to the engine
- [ ] **TLS certificate** — Caddy or Let's Encrypt + certbot, auto-renew
- [ ] **VPS provisioning** — minimum 2 vCPU / 4GB / 40GB for the first 10 workspaces
- [ ] **Docker compose** — formalize the current `docker run` into a `docker-compose.yml` checked into the repo
- [ ] **Backup script** — `cron` daily `cp data/leads.db` to a separate dir or off-host. Retain 30 days.
- [ ] **Audio retention policy** — agreed retention window with the client (default: ElevenLabs keeps forever; we keep MP3 forever; revisit at GDPR or California-resident time)
- [ ] **Monitoring** — Uptime Robot or Better Stack ping on `/health` every minute. Slack/email alert on failure.
- [ ] **Cost guard** — daily ElevenLabs call limit set on the agent (`call_limits.daily_limit`) to cap runaway costs
- [ ] **Webhook secret rotation plan** — ngrok URL is currently public + anyone can hit `/webhook/tool/*`. Need a shared secret header check before production.

### 6.3 Compliance / legal

- [ ] **Recording disclosure** — agent says "this call is recorded" in greeting ✓
- [ ] **Privacy policy URL** — published, linked from the dashboard footer
- [ ] **Terms of service URL** — published, accepted at signup time
- [ ] **PII inventory** — name, phone, address, audio, transcript. Documented retention + deletion process.
- [ ] **Data export** — endpoint or CLI to export all data for a workspace (GDPR right to access)
- [ ] **Data deletion** — endpoint or CLI to wipe a workspace's data on request (right to be forgotten)
- [ ] **Audit log of admin actions** — who created which user, who assigned which lead

### 6.4 Client onboarding (per workspace, e.g. Aviv/Moshe)

- [ ] Confirm with Garage support: can they forward to a SIP URI or to a Twilio/ElevenLabs phone number?
- [ ] Decide path A vs B vs C (see §3.3)
- [ ] If path B: purchase Twilio number, configure Voice webhook → ElevenLabs ConvAI
- [ ] Test call from a real phone, end-to-end, with Aviv watching the dashboard live
- [ ] Create workspace + members in the system: Aviv (owner), Moshe (admin or member)
- [ ] Set up "call back" integration — at least the `tel:` link for v1
- [ ] Train Aviv & Moshe on the dashboard: how to find leads, mark status, leave notes, who-owns-what

---

## 7. Phased Roadmap

### Phase 0 (current)
- ✅ Local Docker on `localhost:8765`
- ✅ Single-user SaaS shape (Users → Tenants → Calls)
- ✅ Session-cookie auth + RLS
- ✅ Mobile-responsive dashboard

### Phase 1 — Workspace + CRM (the big build before going live)
- [ ] Workspace data model + migration
- [ ] CRM lead actions: status, notes, assignment, click-to-call
- [ ] Members settings page
- [ ] Per-lead activity timeline / detail view

### Phase 2 — First production deployment (Aviv / Power Roofing)
- [ ] VPS + Caddy + HTTPS subdomain
- [ ] Garage forwarding wired through to the AI agent
- [ ] Aviv + Moshe accounts created, trained
- [ ] Real calls flow through end-to-end
- [ ] Backup + monitoring in place

### Phase 3 — Scale prep
- [ ] Migrate to Supabase (Postgres + Auth + Storage + RLS)
- [ ] Replace session cookies with Supabase JWT
- [ ] Move MP3/HTML artifacts to object storage
- [ ] Public marketing site + self-service signup flow (or admin-only with sales workflow — decide)

### Phase 4 — SaaS features
- [ ] Billing (Stripe; per-minute or per-workspace tiers)
- [ ] Multi-language agent support
- [ ] Per-workspace branding (logo on dashboard, white-label phone-recording disclosure)
- [ ] Admin marketplace: clone-an-agent templates for common industries (roofing, plumbing, salon, clinic, etc.)

---

## 8. Open questions

1. **What is the exact name of the "Garage" forwarding service Aviv uses?** Needed to confirm SIP support and the integration path.
2. **Is the same phone number going to ring the AI agent, or does Aviv want a separate "AI line" alongside the current direct line?**
3. **Are Aviv and Moshe equal partners in the dashboard, or is one of them the admin?** (Affects role assignment.)
4. **Pricing model for the SaaS** — flat monthly per workspace? Per call? Per minute? Tiered by lead volume?
5. **Branding** — is the dashboard going to be Nexus-branded for end clients, or do we white-label it as "Power Roofing AI Receptionist" for them?
6. **Audio retention** — how long do we keep MP3 recordings? Indefinite (ElevenLabs default) feels risky for PII; 90 days is more typical.
7. **What if the same customer calls back?** Do we link calls by phone number into a single "lead record" with multiple "interactions"? Probably yes, but the data model needs a `customers` table for that.

---

## 9. Reference

- Source of truth doc: [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)
- Database schema: [src/integrations/leads/db.py](../src/integrations/leads/db.py)
- Tenant config example: [src/tenants/power_roofing/config.yaml](../src/tenants/power_roofing/config.yaml)
- Auth dependency: [src/integrations/auth/dependencies.py](../src/integrations/auth/dependencies.py)
