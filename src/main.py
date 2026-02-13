from dotenv import load_dotenv
load_dotenv()  # MUST BE AT THE ABSOLUTE TOP

import logging
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

# --- Architecture Imports ---
from config.logging_config import setup_logging
from src.core.context import set_request_context, reset_context
from src.tenants.loader import TenantLoader
from src.core.orchestration.conversation_manager import ConversationManager
from src.core.audio.streamer import AudioBridge
from src.core.history import SessionRecorder, FileSessionRepository
from src.core.intelligence import PostCallIntelligenceEngine

# 1. Bootstrapping (Logs)
setup_logging()

# Get the configured logger for this module
logger = logging.getLogger("nexus.api")

app = FastAPI(title="Nexus Voice Engine", version="1.0.0")

# --- CORS Middleware (Enable for Test UI) ---
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Initialize the conversation manager (singleton)
conversation_manager = ConversationManager()

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
    success = conversation_manager.close_session(session_id)
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
    
    This endpoint implements the Sidecar Pattern:
    [User Phone/Browser] <-(WS)-> [Nexus Engine] <-(WS)-> [NVIDIA Docker Container]
    
    The flow:
    1. Client connects with audio stream
    2. Load tenant configuration
    3. Create AudioBridge to connect to PersonaPlex sidecar
    4. Start bidirectional audio streaming with transcoding
    5. Handle barge-in and interruptions
    6. Cleanup on disconnect
    
    Args:
        websocket: Client WebSocket connection
        tenant_id: Tenant identifier
        customer_phone: Optional customer phone number
    """
    await websocket.accept()
    session_id = None
    audio_bridge = None
    session_recorder = None
    session_repository = None
    conversation_session = None
    
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
        
        # 3. Load Tenant Config for Voice Settings
        try:
            tenant_config = TenantLoader.load_tenant(tenant_id)
            voice_settings = tenant_config.get("voice_settings", {})
        except Exception as e:
            logger.error(f"Failed to load tenant config: {e}")
            voice_settings = {}

        try:
            # 4. Start Audio Bridge
            audio_bridge = AudioBridge(
                client_ws=websocket,
                tenant_id=tenant_id,
                session_id=session_id,
                conversation_session_id=conversation_session.session_id,
                system_prompt=conversation_session.system_prompt,
                voice_settings=voice_settings,
                session_recorder=session_recorder
            )
            # Store for cleanup
            conversation_session.audio_bridge = audio_bridge
            
            logger.info("🎙️ AudioBridge created, connecting to PersonaPlex...")
            
            # Send ready status
            await websocket.send_json({
                "type": "ready",
                "session_id": session_id,
                "message": "Audio bridge ready. Start speaking!"
            })
            
            # Start the streaming loop (this blocks until disconnected)
            await audio_bridge.process_stream()
            
            logger.info(f"✅ Call completed normally: session={session_id}")
            
        except ConnectionError as e:
            # PersonaPlex connection failed
            logger.error(f"❌ PersonaPlex connection failed: {e}")
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
        if audio_bridge:
            try: await audio_bridge.stop()
            except: pass
        
        # 2. CAPTURE the recorder and CLOSE the session
        # We close the session FIRST to let the manager do its basic cleanup
        effective_recorder = session_recorder
        if conversation_session:
            conv_sess = conversation_manager.get_session(conversation_session.session_id)
            if conv_sess and conv_sess.session_recorder:
                effective_recorder = conv_sess.session_recorder
            
            # Close session (triggers manager's internal finalize)
            conversation_manager.close_session(conversation_session.session_id)

        # 3. ENRICH WITH INTELLIGENCE (The absolute last word)
        if effective_recorder:
            try:
                # Ensure the recorder is in a finalized state so it has a transcript
                if effective_recorder.status == "IN_PROGRESS":
                    effective_recorder.finalize(status="COMPLETED")

                # Run AI Analysis on the finalized data
                intelligence_engine = PostCallIntelligenceEngine()
                analysis = await intelligence_engine.analyze_session(
                    session_data=effective_recorder.session_data,
                    customer_phone=customer_phone
                )
                
                # Merge analysis back into memory
                effective_recorder.session_data["summary"] = {
                    "intent": analysis.get("core_intent", "Inquiry"),
                    "outcome": analysis.get("call_outcome", "Resolved"),
                    "sentiment": analysis.get("sentiment", "Neutral"),
                    "summary": analysis.get("summary", ""),
                    "follow_up_required": analysis.get("follow_up_required", False)
                }
                
                # UPDATE METADATA
                if analysis.get("customer_name"):
                    effective_recorder.session_data["meta"]["customer_name"] = analysis["customer_name"]

                # 4. FINAL OVERWRITE: This save wins the race
                file_path = await effective_recorder.save_session()
                logger.info(f"💾 FINAL enriched session saved: {file_path}")

            except Exception as e:
                logger.error(f"❌ Final enrichment failed: {e}")

        logger.info(f"🔚 Call endpoint cleanup complete: session={session_id}")
        reset_context()


if __name__ == "__main__":
    import uvicorn
    # log_config=None ensures uvicorn uses OUR logging config
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)