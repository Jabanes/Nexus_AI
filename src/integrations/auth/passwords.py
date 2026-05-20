"""Password hashing with bcrypt (direct, no passlib).

passlib's bcrypt backend broke with bcrypt >= 4.1 (the `__about__` attribute
was removed). Using bcrypt directly avoids that compatibility tangle.

bcrypt has a hard 72-byte limit on the input; we truncate at that boundary
(documented behavior — collisions for >72-byte passwords are accepted as the
price of using bcrypt). 6-char minimum is enforced at the API layer.
"""

import bcrypt


def hash_password(plain: str) -> str:
    """Return a bcrypt hash safe to store in the DB."""
    pw = plain.encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True iff plain matches the stored hash. Never raises on bad hash."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False
