from dotenv import load_dotenv
load_dotenv()  # MUST BE AT THE ABSOLUTE TOP

import logging
import os
import json

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

# --- Architecture Imports ---
from config.logging_config import setup_logging
from src.core.context import set_request_context, reset_context
from src.tenants.loader import TenantLoader
from src.core.orchestration.conversation_manager import ConversationManager
from src.core.voice import VoiceBridge, create_voice_provider
from src.core.orchestration.tool_executor import ToolExecutor
from src.core.history import SessionRecorder, FileSessionRepository
from src.core.intelligence import PostCallIntelligenceEngine

# 1. Bootstrapping (Logs)
setup_logging()

# Get the configured logger for this module
logger = logging.getLogger("nexus.api")

app = FastAPI(title="Nexus Voice Engine", version="1.0.0")

# --- CORS Middleware ---
from fastapi.middleware.cors import CORSMiddleware
cors_origins = [o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "*").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Session Middleware (signed cookie, no server-side state) ---
from starlette.middleware.sessions import SessionMiddleware
_session_secret = os.getenv("SESSION_SECRET")
if not _session_secret:
    import secrets as _secrets
    _session_secret = _secrets.token_urlsafe(32)
    logger.warning(
        "SESSION_SECRET not set — generated a random one for this process. "
        "Sessions will be invalidated on every restart. Set SESSION_SECRET in .env "
        "to persist sessions across restarts."
    )
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    session_cookie="nexus_session",
    https_only=False,    # set True behind TLS in prod
    same_site="lax",
    max_age=60 * 60 * 24 * 14,  # 14 days
)

# --- Static Files (test UI + audio worklet) ---
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/test")
async def test_ui():
    return FileResponse("test_call.html")


# ── Auth endpoints ──────────────────────────────────────────────────
from src.integrations.auth.dependencies import get_current_user, require_tenant_owner
from src.integrations.auth.passwords import verify_password


class LoginPayload(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
async def auth_login(request: Request, payload: LoginPayload):
    from src.integrations.leads.db import get_db
    db = get_db()
    user = await db.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    request.session["user_id"] = user["user_id"]
    return {"ok": True, "user": {"user_id": user["user_id"], "email": user["email"], "name": user.get("name"), "role": user.get("role")}}


@app.post("/api/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me")
async def auth_me(user=Depends(get_current_user)):
    # Strip password_hash before returning
    return {k: v for k, v in user.items() if k != "password_hash"}


@app.get("/login")
async def login_page():
    return FileResponse("static/login.html")


# ── Per-tenant data endpoints (gated by RLS) ────────────────────────

@app.get("/leads/{tenant_id}/xlsx")
async def download_leads_xlsx(tenant_id: str, _ctx=Depends(require_tenant_owner)):
    """Generate and download the per-tenant leads ledger as XLSX (export view from DB)."""
    from pathlib import Path as _Path
    from src.integrations.leads.xlsx_repository import build_xlsx_from_db

    export_path = _Path(f"data/leads/{tenant_id}/leads.xlsx")
    await build_xlsx_from_db(tenant_id, export_path)
    return FileResponse(
        export_path,
        filename=f"{tenant_id}_leads.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/leads/{tenant_id}/calls")
async def api_list_calls(
    tenant_id: str,
    classification: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    _ctx=Depends(require_tenant_owner),
):
    """JSON list of calls for a tenant (dashboard data source). RLS-gated."""
    from src.integrations.leads.db import get_db
    from src.integrations.leads.cost import credits_to_usd, credits_per_usd
    db = get_db()
    calls = await db.list_calls(tenant_id, classification=classification, since=since, limit=limit, offset=offset)
    for c in calls:
        c["cost_usd"] = credits_to_usd(c.get("cost_credits"))
    stats = await db.stats(tenant_id)
    stats["total_cost_usd"] = credits_to_usd(stats.get("total_cost"))
    return {
        "tenant_id": tenant_id,
        "stats": stats,
        "count": len(calls),
        "calls": calls,
        "credits_per_usd": credits_per_usd(),
    }


@app.get("/api/elevenlabs/credits")
async def api_elevenlabs_credits(user=Depends(get_current_user)):
    """
    Surface ElevenLabs subscription state so the dashboard can warn before a
    quota exhaust hard-cuts a call. Cached briefly to avoid hammering the API.
    """
    import time
    import urllib.request
    cache = getattr(api_elevenlabs_credits, "_cache", None)
    if cache and (time.time() - cache["at"] < 60):
        return cache["data"]

    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        return {"available": False, "reason": "ELEVENLABS_API_KEY not set"}

    try:
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": api_key},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
    except Exception as e:
        return {"available": False, "reason": str(e)[:200]}

    used = d.get("character_count", 0)
    limit = d.get("character_limit", 0)
    out = {
        "available": True,
        "tier": d.get("tier"),
        "used_credits": used,
        "limit_credits": limit,
        "remaining_credits": max(0, limit - used),
        "percent_used": round((used / limit) * 100, 1) if limit else 0,
        "next_reset_unix": d.get("next_character_count_reset_unix"),
    }
    api_elevenlabs_credits._cache = {"at": time.time(), "data": out}  # type: ignore[attr-defined]
    return out


@app.get("/api/businesses")
async def api_list_businesses(user=Depends(get_current_user)):
    """List businesses owned by the current user (RLS-filtered)."""
    from src.integrations.leads.db import get_db
    from src.integrations.leads.cost import credits_to_usd
    db = get_db()
    # Admins see everything; everyone else sees only what they own.
    if user.get("role") == "admin":
        rows = await db.list_tenants_with_stats()
    else:
        rows = await db.list_tenants_with_stats(owner_user_id=user["user_id"])
    for r in rows:
        r["total_cost_usd"] = credits_to_usd(r.get("total_cost"))
    return {"businesses": rows}


@app.get("/dashboard")
@app.get("/dashboard/{tenant_id}")
async def dashboard(tenant_id: Optional[str] = None):
    """
    Unified SPA shell. Sidebar lists businesses the current user owns;
    main area shows the selected business's calls. The tenant_id in the
    URL (when present) preselects which business to load.

    Auth + RLS enforced by /api/auth/me + /api/leads/{t}/calls — the page
    redirects to /login on 401, and shows "access denied" on 403.
    """
    if tenant_id:
        logger.debug(f"Dashboard request for tenant={tenant_id}")
    return FileResponse("static/dashboard.html")


# ── Artifact serving (transcript HTML, MP3 audio, session JSON) ──────
# Look up the call by ID, return the file referenced in the DB row.
# All three live under /artifacts/{tenant_id}/{kind}/{call_id}.

@app.get("/artifacts/{tenant_id}/{kind}/{call_id}")
async def serve_artifact(tenant_id: str, kind: str, call_id: str, _ctx=Depends(require_tenant_owner)):
    """
    Serve a per-call artifact (transcript HTML, MP3 audio, or session JSON).

    The DB stores absolute paths, but those can be host-side (Windows) while
    we run in a Linux container. So we resolve by convention against the
    CWD-relative data tree and fall back to the DB-stored absolute path.
    """
    from fastapi import HTTPException
    from pathlib import Path as _Path
    from src.integrations.leads.db import get_db

    if kind not in ("transcript", "audio", "json"):
        raise HTTPException(status_code=404, detail="unknown artifact kind")

    db = get_db()
    call = await db.get_call(call_id)
    if not call or call.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="call not found for this tenant")

    media_type = {
        "transcript": "text/html",
        "audio":      "audio/mpeg",
        "json":       "application/json",
    }[kind]

    # 1. Convention path — works regardless of host/container path differences
    conventional = {
        "transcript": _Path(f"data/leads/{tenant_id}/transcripts/{call_id}.html"),
        "audio":      _Path(f"data/leads/{tenant_id}/audio/{call_id}.mp3"),
        "json":       _Path(f"data/history/{tenant_id}/{call_id}.json"),
    }[kind]

    if conventional.exists():
        return FileResponse(conventional, media_type=media_type)

    # 2. Fallback — try the absolute path stored in the DB (legacy / host-paths)
    stored = call.get({
        "transcript": "transcript_html_path",
        "audio":      "audio_mp3_path",
        "json":       "session_json_path",
    }[kind])
    if stored:
        p = _Path(stored)
        if p.exists():
            return FileResponse(p, media_type=media_type)

    raise HTTPException(status_code=404, detail=f"{kind} file not found for this call")


# Initialize the conversation manager (singleton)
conversation_manager = ConversationManager()


# ── DB startup hook ─────────────────────────────────────────────────
@app.on_event("startup")
async def _init_leads_db():
    from src.integrations.leads.db import get_db
    await get_db().init_schema()
    logger.info("LeadsDB schema initialized")

# --- Middleware ---
@app.middleware("http")
async def context_middleware(request: Request, call_next):
    """
    Ensures every request has a context. 
    Resets context after request to ensure no leakage between requests.
    """
    # Initialize with default/system context
    set_request_context(tenant_id="system")
    
    try:
        response = await call_next(request)
        return response
    finally:
        # Crucial: Prevent context leakage in async workers
        reset_context()

# --- Models ---
class InitCallRequest(BaseModel):
    tenant_id: str
    customer_phone: str


class StartConversationRequest(BaseModel):
    tenant_id: str
    customer_phone: str


class SendMessageRequest(BaseModel):
    session_id: str
    message: str

# --- Routes ---
@app.get("/")
async def health_check():
    logger.debug("Health check probe received")
    return {"status": "active", "engine": "Nexus v1.0"}

@app.get("/tenants")
async def get_tenants():
    """Returns a list of available tenants based on directory structure."""
    import os
    tenants_dir = "src/tenants"
    try:
        tenants = [
            d for d in os.listdir(tenants_dir)
            if os.path.isdir(os.path.join(tenants_dir, d))
            and not d.startswith("_")
        ]
        return {"tenants": tenants}
    except Exception as e:
        logger.error(f"Error listing tenants: {e}")
        return {"tenants": []}

@app.post("/init-session")
async def init_session(request: InitCallRequest):
    """
    Starts a voice session.
    Updates the logging context to reflect the specific tenant.
    """
    try:
        # 1. Context Upgrade: Now we know the tenant
        req_id = set_request_context(tenant_id=request.tenant_id)
        logger.info(f"Initializing session for {request.customer_phone} (ReqID: {req_id})")

        # 2. Load Tenant Logic
        context = TenantLoader.load_tenant(request.tenant_id)
        logger.debug(f"Configuration loaded for {request.tenant_id}")
        
        # 3. Verify Tools
        tool_names = [t.name for t in context['tools']]
        logger.info(f"Tools active for this session: {tool_names}")
        
        return {
            "status": "session_initialized",
            "request_id": req_id,
            "tenant": context['tenant_id'],
            "active_tools": tool_names
        }

    except FileNotFoundError:
        logger.warning(f"Tenant not found: {request.tenant_id}")
        raise HTTPException(status_code=404, detail="Tenant not found")
    except Exception as e:
        logger.exception("Critical failure in init_session")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@app.post("/conversation/start")
async def start_conversation(request: StartConversationRequest):
    """
    Start a new conversation session.
    
    This creates a session with:
    - Loaded tenant configuration
    - Active Gemini chat instance
    - Tool executor ready
    
    Returns the session_id for subsequent interactions.
    """
    try:
        # Set context for logging
        req_id = set_request_context(tenant_id=request.tenant_id)
        logger.info(f"Starting conversation for {request.customer_phone}")
        
        # Load tenant configuration
        tenant_context = TenantLoader.load_tenant(request.tenant_id)
        
        # Create conversation session
        session = conversation_manager.create_session(
            tenant_id=request.tenant_id,
            customer_phone=request.customer_phone,
            system_prompt=tenant_context['system_prompt'],
            tools=tenant_context['tools']
        )
        
        logger.info(f"Conversation session created: {session.session_id}")
        
        return {
            "status": "conversation_started",
            "session_id": session.session_id,
            "tenant_id": request.tenant_id,
            "available_tools": [tool.name for tool in tenant_context['tools']],
            "request_id": req_id
        }
        
    except FileNotFoundError:
        logger.warning(f"Tenant not found: {request.tenant_id}")
        raise HTTPException(status_code=404, detail="Tenant not found")
    except Exception as e:
        logger.exception("Error starting conversation")
        raise HTTPException(status_code=500, detail=f"Error starting conversation: {str(e)}")


@app.post("/conversation/message")
async def send_message(request: SendMessageRequest):
    """
    Send a message to an active conversation session.
    
    This endpoint:
    1. Sends the message to Gemini
    2. Executes any tool calls requested by the LLM
    3. Returns the final response
    """
    try:
        session = conversation_manager.get_session(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Set context for this session's tenant
        set_request_context(tenant_id=session.tenant_id)
        logger.info(f"Processing message for session: {request.session_id}")
        
        # Process the message through the conversation manager
        result = await conversation_manager.process_message(
            session_id=request.session_id,
            user_message=request.message
        )
        
        if not result["success"]:
            logger.error(f"Message processing failed: {result.get('error')}")
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        logger.info(f"Message processed. Tools used: {len(result['tools_used'])}")
        
        return {
            "status": "message_processed",
            "response": result["text"],
            "tools_used": result["tools_used"],
            "session_id": request.session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error processing message")
        raise HTTPException(status_code=500, detail=f"Error processing message: {str(e)}")


@app.get("/conversation/{session_id}/status")
async def get_session_status(session_id: str):
    """
    Get the status of a conversation session.
    """
    session = conversation_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "session_id": session.session_id,
        "tenant_id": session.tenant_id,
        "customer_phone": session.customer_phone,
        "available_tools": session.tool_executor.list_tools(),
        "status": "active"
    }


@app.delete("/conversation/{session_id}")
async def close_conversation(session_id: str):
    """
    Close and cleanup a conversation session.
    """
    success = await conversation_manager.close_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    logger.info(f"Session {session_id} closed")
    return {"status": "session_closed", "session_id": session_id}


@app.get("/stats")
async def get_stats():
    """
    Get system-wide statistics.
    """
    return {
        "active_sessions": conversation_manager.get_active_session_count(),
        "engine_version": "1.0.0",
        "status": "operational"
    }


# --- Tool Webhook (Provider-Agnostic) ---
# Any voice provider (ElevenLabs, PersonaPlex.io, etc.) can call this
# endpoint when its LLM decides to execute a tool.

@app.post("/webhook/tool/{tenant_id}")
async def webhook_tool_execution(tenant_id: str, request: Request):
    """
    Provider-agnostic tool execution webhook.

    Voice providers call this when their LLM wants to execute a tool.
    The request body format varies by provider, but we normalize it to:
    {tool_name, parameters} and execute against the tenant's registered tools.

    ElevenLabs format: {"tool_call_id": "...", "tool_name": "...", "parameters": {...}}
    Generic format:    {"tool_name": "...", "parameters": {...}}
    """
    set_request_context(tenant_id=tenant_id)
    logger.info(f"Tool webhook called for tenant: {tenant_id}")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    logger.debug(f"Webhook body: {body}")

    # Normalize: ElevenLabs sends flat body with tool_name + params as siblings
    # e.g. {"tool_name": "check_availability", "date": "2026-04-06"}
    # Generic format: {"tool_name": "...", "parameters": {...}}
    tool_name = body.get("tool_name") or body.get("name", "")
    tool_call_id = body.get("tool_call_id", "")

    if not tool_name:
        raise HTTPException(status_code=400, detail="Missing tool_name")

    # Extract parameters: either nested under "parameters" or flat alongside tool_name
    parameters = body.get("parameters")
    if parameters is None:
        # ElevenLabs flat format — everything except meta fields IS the parameters
        parameters = {k: v for k, v in body.items() if k not in ("tool_name", "tool_call_id", "name")}

    # Load tenant tools
    try:
        tenant_context = TenantLoader.load_tenant(tenant_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Build executor and run
    executor = ToolExecutor(tenant_context["tools"])
    result = await executor.execute_tool(tool_name, parameters)

    result_text = result.get("result", "") if result["success"] else result.get("error", "Tool execution failed")
    logger.info(f"Tool '{tool_name}' executed: success={result['success']} result={str(result_text)[:200]}")

    # Log tool call to any active session for this tenant
    for session in conversation_manager.get_sessions_by_tenant(tenant_id):
        if session.session_recorder:
            session.session_recorder.log_tool_usage(
                tool_name=tool_name,
                tool_input=parameters,
                tool_output=result_text,
            )

    # Return in a format voice providers understand
    response = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "result": result_text,
        "success": result["success"],
    }

    return response


@app.websocket("/ws/conversation/{session_id}")
async def websocket_conversation(websocket: WebSocket, session_id: str):
    """
    WebSocket endpoint for real-time conversation.
    
    This allows bidirectional streaming for voice conversations.
    Client sends text messages, server responds with LLM output.
    
    Message format (JSON):
    Client -> Server: {"type": "message", "content": "user message"}
    Server -> Client: {"type": "response", "content": "assistant response", "tools_used": [...]}
    """
    await websocket.accept()
    logger.info(f"WebSocket connection established for session: {session_id}")
    
    # Verify session exists
    session = conversation_manager.get_session(session_id)
    if not session:
        await websocket.send_json({
            "type": "error",
            "content": "Session not found"
        })
        await websocket.close()
        return
    
    # Set context for logging
    set_request_context(tenant_id=session.tenant_id)
    
    try:
        # Send welcome message
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
            "tenant_id": session.tenant_id
        })
        
        # Message loop
        while True:
            # Receive message from client
            data = await websocket.receive_json()
            
            if data.get("type") == "message":
                user_message = data.get("content", "")
                logger.debug(f"Received message via WebSocket: {user_message[:100]}...")
                
                # Process through conversation manager
                result = await conversation_manager.process_message(
                    session_id=session_id,
                    user_message=user_message
                )
                
                # Send response back
                if result["success"]:
                    await websocket.send_json({
                        "type": "response",
                        "content": result["text"],
                        "tools_used": result["tools_used"]
                    })
                else:
                    await websocket.send_json({
                        "type": "error",
                        "content": result.get("error", "Unknown error")
                    })
            
            elif data.get("type") == "ping":
                # Keep-alive ping
                await websocket.send_json({"type": "pong"})
            
            elif data.get("type") == "close":
                # Client requested close
                logger.info(f"Client requested WebSocket close for session: {session_id}")
                break
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session: {session_id}")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "content": f"Server error: {str(e)}"
            })
        except:
            pass
    finally:
        # Cleanup
        try:
            await websocket.close()
        except:
            pass
        logger.info(f"WebSocket connection closed for session: {session_id}")


@app.websocket("/ws/call/{tenant_id}")
async def call_endpoint(websocket: WebSocket, tenant_id: str, customer_phone: Optional[str] = None):
    """
    WebSocket endpoint for real-time audio streaming (PRIMARY ENDPOINT).

    Uses the Voice Provider abstraction — the actual backend (ElevenLabs,
    PersonaPlex, etc.) is resolved from the tenant's voice_settings.provider.

    Flow:
    1. Client connects with audio stream
    2. Load tenant configuration
    3. Create VoiceBridge with the configured voice provider
    4. Start bidirectional audio streaming
    5. Handle barge-in and interruptions
    6. Cleanup on disconnect + post-call intelligence
    
    Args:
        websocket: Client WebSocket connection
        tenant_id: Tenant identifier
        customer_phone: Optional customer phone number
    """
    await websocket.accept()
    session_id = None
    voice_bridge = None
    session_recorder = None
    session_repository = None
    conversation_session = None
    tenant_context = None
    
    try:
        # Generate session ID
        import uuid
        session_id = str(uuid.uuid4())
        
        # Set logging context
        set_request_context(tenant_id=tenant_id)
        logger.info(
            f"📞 Call initiated: tenant={tenant_id}, session={session_id}, "
            f"phone={customer_phone or 'unknown'}"
        )
        
        # Initialize session persistence (Repository Pattern)
        session_repository = FileSessionRepository()
        session_recorder = SessionRecorder(tenant_id=tenant_id, session_id=session_id)
        logger.debug(f"Session recorder initialized for {tenant_id}:{session_id}")
        
        # Send connection acknowledgment
        await websocket.send_json({
            "type": "connected",
            "session_id": session_id,
            "tenant_id": tenant_id,
            "message": "Audio bridge initializing..."
        })
        
        # ── PRE-FLIGHT: ElevenLabs credit guard ──────────────────────
        # ElevenLabs hard-cuts the audio stream mid-frame when its quota is
        # exhausted, which produces glitchy partial audio + abrupt hangups.
        # We refuse to start the call here when remaining credits are below a
        # safety threshold so the caller gets a clear error instead of a broken
        # call experience. Bypass by setting EL_CREDIT_GUARD_DISABLE=1.
        if not os.getenv("EL_CREDIT_GUARD_DISABLE"):
            try:
                import urllib.request as _urlreq
                _req = _urlreq.Request(
                    "https://api.elevenlabs.io/v1/user/subscription",
                    headers={"xi-api-key": os.getenv("ELEVENLABS_API_KEY", "")},
                )
                with _urlreq.urlopen(_req, timeout=5) as _r:
                    _sub = json.loads(_r.read())
                _used = _sub.get("character_count", 0)
                _limit = _sub.get("character_limit", 0)
                _remaining = max(0, _limit - _used)
                _threshold = int(os.getenv("EL_CREDIT_MIN", "1000"))
                if _remaining < _threshold:
                    logger.warning(
                        f"⛔ Refusing call: only {_remaining}/{_limit} ElevenLabs credits "
                        f"remaining (threshold={_threshold})"
                    )
                    await websocket.send_json({
                        "type": "error",
                        "code": "credits_too_low",
                        "message": (
                            f"Cannot start call: only {_remaining} ElevenLabs credits remaining "
                            f"(need at least {_threshold}). The agent would be cut off mid-sentence. "
                            "Upgrade your plan or wait for the monthly reset."
                        ),
                        "remaining_credits": _remaining,
                        "limit_credits": _limit,
                    })
                    await websocket.close(code=1011, reason="Insufficient ElevenLabs credits")
                    return
            except Exception as e:
                logger.warning(f"Credit guard failed (allowing call): {e}")

        # Load tenant configuration
        try:
            tenant_context = TenantLoader.load_tenant(tenant_id)
            logger.info(f"Tenant config loaded: {len(tenant_context['tools'])} tools available")
        except FileNotFoundError:
            logger.error(f"Tenant not found: {tenant_id}")
            await websocket.send_json({
                "type": "error",
                "code": "tenant_not_found",
                "message": f"Tenant '{tenant_id}' not found"
            })
            await websocket.close(code=1008, reason="Tenant not found")
            return
        except Exception as e:
            logger.exception(f"Error loading tenant config: {e}")
            await websocket.send_json({
                "type": "error",
                "code": "config_error",
                "message": "Failed to load tenant configuration"
            })
            await websocket.close(code=1011, reason="Configuration error")
            return
        
        # Create conversation session (for LLM interaction)
        try:
            conversation_session = conversation_manager.create_session(
                tenant_id=tenant_id,
                customer_phone=customer_phone or "unknown",
                system_prompt=tenant_context['system_prompt'],
                tools=tenant_context['tools']
            )
            logger.info(f"Conversation session created: {conversation_session.session_id}")
        except Exception as e:
            logger.exception(f"Error creating conversation session: {e}")
            await websocket.send_json({
                "type": "error",
                "code": "session_error",
                "message": "Failed to initialize conversation"
            })
            await websocket.close(code=1011, reason="Session initialization error")
            return
        
        # 3. Extract Voice Settings from already-loaded tenant context
        voice_settings = tenant_context.get("voice_settings", {})

        try:
            # 4. Create Voice Provider + Bridge
            voice_provider = create_voice_provider(
                voice_settings=voice_settings,
                system_prompt=conversation_session.system_prompt,
            )
            voice_bridge = VoiceBridge(
                client_ws=websocket,
                provider=voice_provider,
                session_id=session_id,
                session_recorder=session_recorder,
            )
            # Store for cleanup
            conversation_session.voice_bridge = voice_bridge

            logger.info(f"🎙️ VoiceBridge created (provider={voice_settings.get('provider', 'default')})")

            # Send ready status
            await websocket.send_json({
                "type": "ready",
                "session_id": session_id,
                "message": "Voice bridge ready. Start speaking!"
            })

            # Start the streaming loop (this blocks until disconnected)
            await voice_bridge.process_stream()

            logger.info(f"✅ Call completed normally: session={session_id}")

        except ConnectionError as e:
            logger.error(f"❌ Voice provider connection failed: {e}")
            if session_recorder:
                session_recorder.log_error("connection_error", str(e))
                session_recorder.finalize(status="ERROR")
            await websocket.send_json({
                "type": "error",
                "code": "audio_service_unavailable",
                "message": "Audio service is currently unavailable. Please try again later."
            })
            await websocket.close(code=1011, reason="Audio service unavailable")
            return
            
        except Exception as e:
            logger.exception(f"❌ Error in audio streaming: {e}")
            if session_recorder:
                session_recorder.log_error("streaming_error", str(e))
                session_recorder.finalize(status="ERROR")
            await websocket.send_json({
                "type": "error",
                "code": "streaming_error",
                "message": "Audio streaming error occurred"
            })
            await websocket.close(code=1011, reason="Streaming error")
            return
    
    except WebSocketDisconnect:
        logger.info(f"📴 Client disconnected: session={session_id}")
        if session_recorder:
            session_recorder.finalize(status="DISCONNECTED")
    
    except Exception as e:
        logger.exception(f"❌ Unexpected error in call endpoint: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "code": "server_error",
                "message": "An unexpected error occurred"
            })
            await websocket.close(code=1011, reason="Server error")
        except:
            pass
    
    finally:
        # 1. STOP the bridge
        if voice_bridge:
            try: await voice_bridge.stop()
            except: pass

        # 2. CLOSE the conversation session
        if conversation_session:
            await conversation_manager.close_session(conversation_session.session_id)

        # 3. SYNC DATA FROM VOICE PROVIDER
        # If ElevenLabs, pull the full conversation from their API (transcripts,
        # tool calls, analysis, cost). For other providers, use our local recorder.
        try:
            el_conversation_id = None
            if voice_bridge and hasattr(voice_bridge.provider, 'conversation_id'):
                el_conversation_id = voice_bridge.provider.conversation_id

            if el_conversation_id:
                # ElevenLabs path — pull rich data from their API
                from src.core.voice.elevenlabs_sync import ElevenLabsSync

                # pull_conversation handles its own backoff until analysis is populated
                sync = ElevenLabsSync()
                el_data = await sync.pull_conversation(el_conversation_id)

                if el_data:
                    session_data = sync.convert_to_session(
                        el_data=el_data,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        customer_phone=customer_phone or "",
                    )
                    repository = FileSessionRepository()
                    file_path = await repository.save_session(tenant_id, session_data)
                    logger.info(f"💾 ElevenLabs session synced: {file_path}")

                    # Optional per-tenant leads pipeline (spam filter + xlsx + mp3)
                    if (tenant_context or {}).get("lead_capture", {}).get("enabled"):
                        try:
                            from src.integrations.leads.pipeline import run_leads_pipeline
                            from pathlib import Path as _Path
                            # Tell the pipeline what we observed locally so it can derive
                            # `ended_by=client` even before ElevenLabs's analysis populates.
                            local_status = (getattr(session_recorder, "status", None)
                                            if session_recorder else None)
                            await run_leads_pipeline(
                                session_data=session_data,
                                session_json_path=_Path(file_path),
                                local_status=local_status,
                            )
                        except Exception as e:
                            logger.error(f"❌ Leads pipeline failed: {e}")
                else:
                    logger.warning("ElevenLabs sync returned no data, falling back to local recorder")
                    await _save_local_session(session_recorder, session_id, tenant_id, customer_phone)
            else:
                # Non-ElevenLabs path — use local recorder + intelligence engine
                await _save_local_session(session_recorder, session_id, tenant_id, customer_phone)

        except Exception as e:
            logger.error(f"❌ Session save failed: {e}")

        logger.info(f"🔚 Call endpoint cleanup complete: session={session_id}")
        reset_context()


async def _save_local_session(session_recorder, session_id, tenant_id, customer_phone):
    """Fallback: save session using our local recorder + post-call intelligence."""
    if not session_recorder:
        return

    try:
        if session_recorder.status == "IN_PROGRESS":
            session_recorder.finalize(status="COMPLETED")

        intelligence_engine = PostCallIntelligenceEngine()
        analysis = await intelligence_engine.analyze_session(
            session_data=session_recorder.session_data,
            customer_phone=customer_phone,
        )

        session_recorder.session_data["summary"] = {
            "intent": analysis.get("core_intent", "Inquiry"),
            "outcome": analysis.get("call_outcome", "Resolved"),
            "sentiment": analysis.get("sentiment", "Neutral"),
            "summary": analysis.get("summary", ""),
            "follow_up_required": analysis.get("follow_up_required", False),
        }

        if analysis.get("customer_name"):
            session_recorder.session_data["meta"]["customer_name"] = analysis["customer_name"]

        file_path = await session_recorder.save_session()
        logger.info(f"💾 Local session saved: {file_path}")

    except Exception as e:
        logger.error(f"❌ Local session save failed: {e}")


if __name__ == "__main__":
    import uvicorn
    # log_config=None ensures uvicorn uses OUR logging config
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)