from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import vin_decoder

router = APIRouter(prefix="/api/vin", tags=["VIN Lookup"])

class VinRequest(BaseModel):
    vin: str

# Rough list of parts worth checking first, by make. Not a fitment guarantee.
COMMON_PARTS = {
    "Toyota": ["Brake Pads", "Oil Filter", "Air Filter", "Spark Plugs", "Timing Belt"],
    "Lexus": ["Brake Pads", "Oil Filter", "Air Filter", "Spark Plugs", "Water Pump"],
    "Nissan": ["Brake Pads", "Oil Filter", "CV Joint", "Alternator", "Radiator"],
    "Honda": ["Brake Pads", "Oil Filter", "Timing Belt", "Spark Plugs", "Water Pump"],
    "Hyundai": ["Brake Pads", "Oil Filter", "Timing Belt", "Spark Plugs", "Clutch Kit"],
    "Kia": ["Brake Pads", "Oil Filter", "Timing Belt", "Spark Plugs", "Radiator"],
    "BMW": ["Brake Pads", "Oil Filter", "Water Pump", "Control Arms", "Battery"],
    "Mercedes-Benz": ["Brake Pads", "Oil Filter", "Air Suspension", "Alternator", "Battery"],
    "Ford": ["Brake Pads", "Oil Filter", "Spark Plugs", "Alternator", "Radiator"],
    "Mazda": ["Brake Pads", "Oil Filter", "Spark Plugs", "Timing Belt", "Radiator"],
    "Mitsubishi": ["Brake Pads", "Oil Filter", "Timing Belt", "Alternator", "CV Joint"],
}
DEFAULT_PARTS = ["Brake Pads", "Oil Filter", "Air Filter", "Spark Plugs", "Battery"]


def _with_legacy_fields(decoded: dict) -> dict:
    """Keep the field names older pages expect."""
    year = int(decoded["year"]) if decoded.get("year") and str(decoded["year"]).isdigit() else None
    return {
        **decoded,
        "manufacturer": decoded.get("manufacturer") or decoded.get("make") or "Unknown Manufacturer",
        "model_year": year,
        "year_range": str(year) if year else "Unknown",
        "assembly_plant": decoded.get("plant_code"),
        "common_parts_to_check": COMMON_PARTS.get(decoded.get("make") or "", DEFAULT_PARTS),
    }


async def _decode_or_400(vin: str) -> dict:
    try:
        return _with_legacy_fields(await vin_decoder.decode(vin))
    except vin_decoder.VinError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/decode")
async def decode_vin(payload: VinRequest):
    return await _decode_or_400(payload.vin)


@router.get("/decode/{vin}")
async def decode_vin_get(vin: str):
    return await _decode_or_400(vin)


@router.get("/recalls")
async def vehicle_recalls(
    make: str = Query(..., min_length=1, max_length=40),
    model: str = Query(..., min_length=1, max_length=60),
    year: int = Query(..., ge=1950, le=2100),
):
    """Free US safety recalls for a make/model/year (NHTSA). Cars never sold in the US have none listed."""
    recalls = await vin_decoder.fetch_recalls(make, model, year)
    if recalls is None:
        raise HTTPException(status_code=503, detail="The recall service is unavailable right now. Try again later.")
    return {"make": make, "model": model, "year": year, "count": len(recalls), "recalls": recalls}
