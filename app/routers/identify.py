import logging
import os
import base64
import uuid
import tempfile
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Header
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.db import get_db
from app import models
from app.config import get_settings
import json
import re
from app.auth import decode_access_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/identify", tags=["AI Identification"])
settings = get_settings()

# Simple in-memory rate limit (for dev — use Redis in production)
_rate_limit = {}


class CarIdentification(BaseModel):
    make: Optional[str] = None
    model: Optional[str] = None
    year_range: Optional[str] = None
    color: Optional[str] = None
    body_type: Optional[str] = None
    confidence: Optional[str] = None
    estimated_price_sll_low: Optional[float] = None
    estimated_price_sll_high: Optional[float] = None
    common_parts: Optional[list] = None
    description: Optional[str] = None


async def get_optional_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Get user if logged in, else None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "")
    try:
        payload = decode_access_token(token)
        user_id = int(payload.get("sub"))
        result = await db.execute(select(models.User).where(models.User.id == user_id))
        return result.scalars().first()
    except (JWTError, TypeError, ValueError):
        return None


@router.post("/car")
async def identify_car(
    file: UploadFile = File(...),
    user: Optional[models.User] = Depends(get_optional_user),
):
    """
    Identify a car from a photo using Gemini Vision.
    Requires login. Rate limited to 20/day per user.
    """
    if not user:
        raise HTTPException(status_code=401, detail="Please log in to use photo identification")

    user_key = f"user_{user.id}"
    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if user_key in _rate_limit:
        count, first_time = _rate_limit[user_key]
        if first_time < day_start:
            _rate_limit[user_key] = (1, now)
        elif count >= 20:
            raise HTTPException(status_code=429, detail="Daily limit reached (20 identifications/day). Try again tomorrow.")
        else:
            _rate_limit[user_key] = (count + 1, first_time)
    else:
        _rate_limit[user_key] = (1, now)

    if file.content_type not in {"image/jpeg", "image/png", "image/webp", "image/jpg"}:
        raise HTTPException(status_code=400, detail="File type not supported. Use JPG, PNG, or WEBP.")

    contents = await file.read()
    if len(contents) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large. Max 8 MB.")

    try:
        from google import genai
        from google.genai import types

        if not settings.GEMINI_API_KEY:
            raise HTTPException(status_code=500, detail="AI not configured")

        prompt = """You are a car identification expert for the Sierra Leone automotive market.

Analyze this photo of a car and identify it. Return ONLY valid JSON with these exact fields:

{
  "make": "Toyota",
  "model": "Corolla",
  "year_range": "2015-2018",
  "color": "Silver",
  "body_type": "Sedan",
  "confidence": "high|medium|low",
  "estimated_price_sll_low": 140000000,
  "estimated_price_sll_high": 180000000,
  "common_parts": ["Brake Pads", "Oil Filter", "Air Filter", "Spark Plugs", "Battery"],
  "description": "Brief 1-2 sentence description"
}

Rules:
- If you cannot identify the car, set make/model to null and confidence to "low"
- Prices should be realistic for the Sierra Leone used car market (in Sierra Leone Leones)
- Common parts should be the top 5 maintenance parts for that model
- Return ONLY JSON, no markdown, no explanation
"""

        client = genai.Client(api_key=settings.GEMINI_API_KEY)

        response = await run_in_threadpool(
            client.models.generate_content,
            model=settings.GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(
                    data=contents,
                    mime_type=file.content_type or "image/jpeg",
                ),
                prompt,
            ],
        )

        text = (response.text or "").strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        data = json.loads(text)

        return {
            "success": True,
            "identification": data,
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="We couldn't read the result. Please try again.")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Car identification failed")
        raise HTTPException(status_code=502, detail="Photo identification isn't working right now. Please try again later.")


@router.get("/status")
async def identify_status(user: Optional[models.User] = Depends(get_optional_user)):
    """Check if user can use identification."""
    if not user:
        return {"available": False, "reason": "login_required"}

    user_key = f"user_{user.id}"
    now = datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if user_key in _rate_limit:
        count, first_time = _rate_limit[user_key]
        if first_time < day_start:
            remaining = 20
        else:
            remaining = max(0, 20 - count)
    else:
        remaining = 20

    return {
        "available": remaining > 0,
        "remaining_today": remaining,
        "daily_limit": 20,
    }