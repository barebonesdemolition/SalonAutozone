"""
NHTSA Vehicle Data Integration
Uses the official US Government API (free, unlimited, legal)
Docs: https://vpic.nhtsa.dot.gov/api/
"""
import httpx
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from functools import lru_cache

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
    
    if len(vin_clean) != 17:
        raise HTTPException(status_code=400, detail="VIN must be 17 characters")
    
    try:
        data = await fetch_json(
            f"{BASE_URL}/DecodeVinValues/{vin_clean}?format=json",
            f"vin_{vin_clean}",
            ttl_seconds=2592000,  # 30 days
        )
        
        results = data.get("Results", [])
        if not results:
            raise HTTPException(status_code=404, detail="VIN not found")
        
        r = results[0]
        
        def clean(v):
            """NHTSA returns empty strings or 'null' strings — clean them."""
            if not v or v == "null" or v == "":
                return None
            return v
        
        return {
            "vin": vin_clean,
            "valid": True,
            "make": clean(r.get("Make")),
            "model": clean(r.get("Model")),
            "year": clean(r.get("ModelYear")),
            "body_class": clean(r.get("BodyClass")),
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
