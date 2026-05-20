"""Auth layer — session-cookie based.

Session storage: Starlette SessionMiddleware (signed cookie, no server-side state).
Passwords: bcrypt via passlib.
RLS: enforced at the API/dependency layer, filtering by owner_user_id. When we
migrate to Supabase the same shape becomes real Postgres RLS via auth.uid().
"""
