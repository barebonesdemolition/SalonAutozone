from fastapi import APIRouter, Depends
from app import models
from app.routers.auth import get_current_user

router = APIRouter()


def compute_role(user) -> str:
    roles = {r.strip().lower() for r in (getattr(user, "roles", "") or "").split(",") if r.strip()}
    if getattr(user, "is_admin", False) or roles & {"admin", "superadmin"}:
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
