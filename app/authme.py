"""authme.py - exposes GET /api/auth/me. Save next to your other router files."""
from fastapi import APIRouter, Depends

from app import models

# Import get_current_user from wherever your auth code lives.
# The first one that works is used; if none do, fix the list below.
try:
    from app.auth import get_current_user
except ImportError:
    try:
        from app.security import get_current_user
    except ImportError:
        try:
            from app.core.security import get_current_user
        except ImportError:
            from app.dependencies import get_current_user

router = APIRouter()


def compute_role(user: models.User) -> str:
    """Pick one role for frontend routing. Highest privilege wins."""
    roles = {r.strip().lower() for r in (getattr(user, "roles", "") or "").split(",") if r.strip()}
    if roles & {"admin", "superadmin"}:
        return "admin"
    if "business" in roles:
        return "business"
    if roles & {"seller", "vendor"} or getattr(user, "is_vendor", False):
        return "seller"
    return "buyer"


@router.get("/me")
async def me(user: models.User = Depends(get_current_user)):
    role = compute_role(user)
    return {
        "id": user.id,
        "full_name": getattr(user, "full_name", None) or getattr(user, "name", None),
        "email": getattr(user, "email", None),
        "phone": getattr(user, "phone", None),
        "roles": getattr(user, "roles", "") or "",
        "is_vendor": bool(getattr(user, "is_vendor", False)),
        "is_admin": role == "admin",
        "account_type": role,
        "role": role,
    }
