"""FastAPI dependencies for auth + RLS.

Two ways to require auth on an endpoint:
  • `Depends(get_current_user)`       — raises 401 if not authenticated.
  • `Depends(require_tenant_owner)`   — also verifies the current user owns the
    tenant_id from the path. Use on all per-tenant data endpoints to enforce RLS.
"""

import logging
from typing import Any, Dict

from fastapi import Depends, HTTPException, Request

from src.integrations.leads.db import get_db

logger = logging.getLogger(__name__)


async def get_current_user(request: Request) -> Dict[str, Any]:
    """Return the logged-in user dict, or raise 401."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = get_db()
    user = await db.get_user_by_id(user_id)
    if not user:
        # Session points at a user that no longer exists — clear it.
        request.session.clear()
        raise HTTPException(status_code=401, detail="Session invalid")
    return user


async def require_tenant_owner(
    tenant_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Return (user, tenant) only if the current user owns the tenant.
    Raises 403 otherwise. Use as the auth check on all per-tenant endpoints.

    NOTE: tenant_id is taken from the path (FastAPI fills it from the route).
    """
    db = get_db()
    tenant = await db.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    # If owner_user_id is None, treat as "unassigned" — only admins can touch it
    owner = tenant.get("owner_user_id")
    if owner is None:
        if user.get("role") == "admin":
            return {"user": user, "tenant": tenant}
        raise HTTPException(
            status_code=403,
            detail="Tenant is unassigned. Ask an admin to assign it.",
        )

    if owner != user["user_id"]:
        logger.warning(
            f"[auth] user={user['user_id']} attempted to access tenant={tenant_id} "
            f"owned by {owner}"
        )
        raise HTTPException(status_code=403, detail="You do not own this tenant")

    return {"user": user, "tenant": tenant}
