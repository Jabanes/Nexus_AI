import logging
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from dotenv import load_dotenv

# --- Architecture Imports ---
from config.logging_config import setup_logging
from src.core.context import set_request_context, reset_context
from src.tenants.loader import TenantLoader

# 1. Bootstrapping (Env + Logs)
load_dotenv()
setup_logging()

# Get the configured logger for this module
logger = logging.getLogger("nexus.api")

app = FastAPI(title="Nexus Voice Engine")

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

# --- Routes ---
@app.get("/")
async def health_check():
    logger.debug("Health check probe received")
    return {"status": "active", "engine": "Nexus v1.0"}

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

if __name__ == "__main__":
    import uvicorn
    # log_config=None ensures uvicorn uses OUR logging config
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, log_config=None)