"""
NHTSA Vehicle Data Integration
Uses the official US Government API (free, unlimited, legal)
Docs: https://vpic.nhtsa.dot.gov/api/
"""
import asyncio
import re

import httpx
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from functools import lru_cache

from app.config import get_settings


BODY_CLASS_TO_SVG = {
    "sedan": "sedan.svg",
    "saloon": "sedan.svg",
    "suv": "suv.svg",
    "sport utility": "suv.svg",
    "mpv": "minivan.svg",
    "multipurpose": "minivan.svg",
    "pickup": "pickup.svg",
    "truck": "pickup.svg",
    "hatchback": "hatchback.svg",
    "liftback": "hatchback.svg",
    "notchback": "hatchback.svg",
    "coupe": "coupe.svg",
    "convertible": "coupe.svg",
    "minivan": "minivan.svg",
    "van": "van.svg",
    "cargo van": "van.svg",
}

def placeholder_for_body(body_class):
    if not body_class:
        return '/static/silhouettes/generic.svg'
    bc = body_class.lower()
    for key, svg in BODY_CLASS_TO_SVG.items():
        if key in bc:
            return f'/static/silhouettes/{svg}'
    return '/static/silhouettes/generic.svg'

router = APIRouter(prefix="/api/nhtsa", tags=["NHTSA Vehicle Data"])

BASE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles"

# In-memory cache to avoid hitting NHTSA too often
_cache = {}


async def fetch_json(url: str, cache_key: str, ttl_seconds: int = 86400):
    """Fetch JSON from NHTSA with simple in-memory caching."""
    import time
    now = time.time()
    
    if cache_key in _cache:
        data, timestamp = _cache[cache_key]
        if now - timestamp < ttl_seconds:
            return data
    
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()
        _cache[cache_key] = (data, now)
        return data


def _autodev_value(payload: dict, *keys):
    sources = [payload]
    for name in ("data", "vehicle", "result"):
        nested = payload.get(name)
        if isinstance(nested, dict):
            sources.append(nested)
            for child_name in ("vehicle", "result"):
                child = nested.get(child_name)
                if isinstance(child, dict):
                    sources.append(child)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, "", "null"):
                return str(value)
    return None


async def fetch_autodev_vin(vin: str, api_key: str):
    """Decode a VIN with Auto.dev when the deployment has configured its API key."""
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"https://api.auto.dev/vehicle/{vin}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, dict):
        return None
    make = _autodev_value(payload, "make", "Make", "manufacturer")
    model = _autodev_value(payload, "model", "Model", "modelName")
    if not make or not model:
        return None
    return {
        "vin": vin,
        "valid": True,
        "source": "Auto.dev",
        "make": make,
        "model": model,
        "year": _autodev_value(payload, "year", "modelYear", "model_year"),
        "body_class": _autodev_value(payload, "bodyClass", "body_class", "bodyType"),
        "vehicle_type": _autodev_value(payload, "vehicleType", "vehicle_type"),
        "doors": _autodev_value(payload, "doors"),
        "fuel_type": _autodev_value(payload, "fuelType", "fuel_type"),
        "engine_cylinders": _autodev_value(payload, "engineCylinders", "engine_cylinders"),
        "engine_displacement_l": _autodev_value(payload, "displacementL", "engineDisplacement", "engine_displacement_l"),
        "engine_hp": _autodev_value(payload, "engineHp", "engineHP", "engine_hp"),
        "transmission": _autodev_value(payload, "transmission", "transmissionStyle"),
        "drive_type": _autodev_value(payload, "driveType", "drive_type", "drivetrain"),
        "plant_country": _autodev_value(payload, "plantCountry", "plant_country"),
        "plant_city": _autodev_value(payload, "plantCity", "plant_city"),
        "manufacturer": _autodev_value(payload, "manufacturer"),
        "series": _autodev_value(payload, "series"),
        "trim": _autodev_value(payload, "trim"),
        "gvwr": _autodev_value(payload, "gvwr", "GVWR"),
        "error_code": None,
        "error_text": None,
    }


@router.get("/makes")
async def get_all_makes():
    """Get every car make that has ever been registered in the US."""
    try:
        data = await fetch_json(
            f"{BASE_URL}/GetAllMakes?format=json",
            "all_makes",
            ttl_seconds=604800,  # 1 week
        )
        makes = sorted([item["Make_Name"] for item in data.get("Results", [])])
        return {"count": len(makes), "makes": makes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NHTSA error: {str(e)}")


@router.get("/models")
async def get_models_for_make_year(
    make: str = Query(..., description="e.g. toyota"),
    year: Optional[int] = Query(None, description="e.g. 2015"),
):
    """Get all models for a given make (optionally filtered by year)."""
    make_clean = make.strip().lower()
    
    try:
        if year:
            url = f"{BASE_URL}/GetModelsForMakeYear/make/{make_clean}/modelyear/{year}?format=json"
            cache_key = f"models_{make_clean}_{year}"
        else:
            url = f"{BASE_URL}/GetModelsForMake/{make_clean}?format=json"
            cache_key = f"models_{make_clean}"
        
        data = await fetch_json(url, cache_key)
        models = sorted(list(set(item["Model_Name"] for item in data.get("Results", []))))
        
        return {
            "make": make,
            "year": year,
            "count": len(models),
            "models": models,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NHTSA error: {str(e)}")


@router.get("/decode-vin/{vin}")
async def decode_vin_official(vin: str):
    """
    Official NHTSA VIN decoder.
    Returns full details: make, model, year, engine, body, etc.
    """
    vin_clean = vin.strip().upper()
    
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin_clean):
        raise HTTPException(status_code=400, detail="VIN must be 17 valid characters; I, O, and Q are not allowed.")
    
    try:
        def clean(v):
            """NHTSA returns empty strings or 'null' strings — clean them."""
            if not v or v == "null" or v == "":
                return None
            return v
        settings = get_settings()
        decoded = None
        if settings.AUTODEV_API_KEY:
            try:
                decoded = await fetch_autodev_vin(vin_clean, settings.AUTODEV_API_KEY)
            except Exception:
                decoded = None

        if decoded is None:
            data = await fetch_json(
                f"{BASE_URL}/DecodeVinValues/{vin_clean}?format=json",
                f"vin_{vin_clean}",
                ttl_seconds=2592000,  # 30 days
            )
            results = data.get("Results", [])
            if not results:
                raise HTTPException(status_code=404, detail="VIN not found")
            r = results[0]
            decoded = {
                "vin": vin_clean,
                "valid": True,
                "source": "NHTSA",
                "make": clean(r.get("Make")),
                "model": clean(r.get("Model")),
                "year": clean(r.get("ModelYear")),
                "body_class": clean(r.get("BodyClass")),
        "placeholder": placeholder_for_body(clean(r.get("BodyClass"))),
                "vehicle_type": clean(r.get("VehicleType")),
                "doors": clean(r.get("Doors")),
                "fuel_type": clean(r.get("FuelTypePrimary")),
                "engine_cylinders": clean(r.get("EngineCylinders")),
                "engine_displacement_l": clean(r.get("DisplacementL")),
                "engine_hp": clean(r.get("EngineHP")),
                "transmission": clean(r.get("TransmissionStyle")),
                "drive_type": clean(r.get("DriveType")),
                "plant_country": clean(r.get("PlantCountry")),
                "plant_city": clean(r.get("PlantCity")),
                "manufacturer": clean(r.get("Manufacturer")),
                "series": clean(r.get("Series")),
                "trim": clean(r.get("Trim")),
                "gvwr": clean(r.get("GVWR")),
                "error_code": clean(r.get("ErrorCode")),
                "error_text": clean(r.get("ErrorText")),
            }

        if settings.GEMINI_API_KEY and decoded["make"] and decoded["model"]:
            try:
                from google import genai

                client = genai.Client(api_key=settings.GEMINI_API_KEY)
                prompt = (
                    "Given this verified vehicle decode, provide a concise, cautious parts-fitment and "
                    "maintenance note for a buyer. Do not invent exact part numbers or claim compatibility "
                    "without checking the full VIN. Mention what details a parts seller should confirm. "
                    "Vehicle data: "
                    f"{decoded}"
                )
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=settings.GEMINI_MODEL,
                    contents=prompt,
                )
                decoded["ai_insights"] = (response.text or "").strip() or None
            except Exception:
                decoded["ai_insights"] = None
        else:
            decoded["ai_insights"] = None

        return decoded
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NHTSA error: {str(e)}")


@router.get("/search")
async def search_vehicles(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(20, le=100),
):
    """
    Search vehicle makes/models by name (fuzzy search).
    """
    q_clean = q.strip().lower()
    results = []
    
    try:
        # Get all makes
        data = await fetch_json(
            f"{BASE_URL}/GetAllMakes?format=json",
            "all_makes",
            ttl_seconds=604800,
        )
        
        # Find makes that match the query
        for item in data.get("Results", []):
            make = item["Make_Name"]
            if q_clean in make.lower():
                results.append({
                    "type": "make",
                    "name": make,
                    "label": make,
                })
                if len(results) >= limit:
                    break
        
        return {"query": q, "count": len(results), "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"NHTSA error: {str(e)}")


@router.get("/health")
async def nhtsa_health():
    """Test NHTSA connection."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{BASE_URL}/GetAllMakes?format=json")
            data = r.json()
            return {
                "status": "ok",
                "makes_count": len(data.get("Results", [])),
                "source": "NHTSA vPIC API",
            }
    except Exception as e:
        return {"status": "error", "detail": str(e)}
