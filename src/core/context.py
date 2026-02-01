import contextvars
import uuid
from typing import Optional, Dict

# Thread-safe/Async-safe context variables
_request_id_ctx_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
_tenant_id_ctx_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("tenant_id", default=None)

def set_request_context(tenant_id: str = "system") -> str:
    """
    Initializes the context for the current request.
    Generates a unique Request ID for tracing.
    """
    req_id = str(uuid.uuid4())
    _request_id_ctx_var.set(req_id)
    _tenant_id_ctx_var.set(tenant_id)
    return req_id

def get_context() -> Dict[str, str]:
    """
    Retrieves current context for the logger.
    """
    return {
        "request_id": _request_id_ctx_var.get() or "n/a",
        "tenant_id": _tenant_id_ctx_var.get() or "n/a"
    }

def reset_context():
    """
    Cleans up context at the end of a request to prevent leakage.
    """
    _request_id_ctx_var.set(None)
    _tenant_id_ctx_var.set(None)