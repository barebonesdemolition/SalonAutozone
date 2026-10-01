from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/vin", tags=["VIN Lookup"])

class VinRequest(BaseModel):
    vin: str

# --- VIN Decoder Database ---
WMI_MAP = {
    "JTD": {"make": "Toyota", "country": "Japan", "type": "Passenger Car"},
    "JTH": {"make": "Lexus", "country": "Japan", "type": "Passenger Car"},
    "JN1": {"make": "Nissan", "country": "Japan", "type": "Passenger Car"},
    "JN6": {"make": "Nissan", "country": "Japan", "type": "Truck"},
    "JHM": {"make": "Honda", "country": "Japan", "type": "Passenger Car"},
    "JHL": {"make": "Honda", "country": "Japan", "type": "SUV"},
    "JM1": {"make": "Mazda", "country": "Japan", "type": "Passenger Car"},
    "JS2": {"make": "Suzuki", "country": "Japan", "type": "Passenger Car"},
    "JS3": {"make": "Suzuki", "country": "Japan", "type": "SUV"},
    "JA3": {"make": "Mitsubishi", "country": "Japan", "type": "Passenger Car"},
    "JA4": {"make": "Mitsubishi", "country": "Japan", "type": "SUV"},
    "JMB": {"make": "Mitsubishi", "country": "Japan", "type": "SUV"},
    "KMH": {"make": "Hyundai", "country": "South Korea", "type": "Passenger Car"},
    "KNA": {"make": "Kia", "country": "South Korea", "type": "Passenger Car"},
    "KND": {"make": "Kia", "country": "South Korea", "type": "SUV"},
    "1HG": {"make": "Honda", "country": "USA", "type": "Passenger Car"},
    "1FT": {"make": "Ford", "country": "USA", "type": "Truck"},
    "1FA": {"make": "Ford", "country": "USA", "type": "Passenger Car"},
    "1G1": {"make": "Chevrolet", "country": "USA", "type": "Passenger Car"},
    "1C3": {"make": "Chrysler", "country": "USA", "type": "Passenger Car"},
    "WBA": {"make": "BMW", "country": "Germany", "type": "Passenger Car"},
    "WBS": {"make": "BMW M", "country": "Germany", "type": "Passenger Car"},
    "WDB": {"make": "Mercedes-Benz", "country": "Germany", "type": "Passenger Car"},
    "WDD": {"make": "Mercedes-Benz", "country": "Germany", "type": "Passenger Car"},
    "WVW": {"make": "Volkswagen", "country": "Germany", "type": "Passenger Car"},
    "WAU": {"make": "Audi", "country": "Germany", "type": "Passenger Car"},
    "VF1": {"make": "Renault", "country": "France", "type": "Passenger Car"},
    "VF3": {"make": "Peugeot", "country": "France", "type": "Passenger Car"},
    "VF7": {"make": "Citroen", "country": "France", "type": "Passenger Car"},
    "SAL": {"make": "Land Rover", "country": "UK", "type": "SUV"},
    "SAJ": {"make": "Jaguar", "country": "UK", "type": "Passenger Car"},
    "SHH": {"make": "Honda", "country": "UK", "type": "SUV"},
    "MA3": {"make": "Suzuki", "country": "India", "type": "Passenger Car"},
    "MAT": {"make": "Tata", "country": "India", "type": "Passenger Car"},
    "ME4": {"make": "Honda", "country": "India", "type": "Motorcycle"},
}

# VIN position 10 = model year
YEAR_CODES = {
    "A": 1980, "B": 1981, "C": 1982, "D": 1983, "E": 1984,
    "F": 1985, "G": 1986, "H": 1987, "J": 1988, "K": 1989,
    "L": 1990, "M": 1991, "N": 1992, "P": 1993, "R": 1994,
    "S": 1995, "T": 1996, "V": 1997, "W": 1998, "X": 1999,
    "Y": 2000, "1": 2001, "2": 2002, "3": 2003, "4": 2004,
    "5": 2005, "6": 2006, "7": 2007, "8": 2008, "9": 2009,
    "A2": 2010, "B2": 2011, "C2": 2012, "D2": 2013, "E2": 2014,
    "F2": 2015, "G2": 2016, "H2": 2017, "J2": 2018, "K2": 2019,
    "L2": 2020, "M2": 2021, "N2": 2022, "P2": 2023, "R2": 2024,
    "S2": 2025,
}

@router.post("/decode")
async def decode_vin(payload: VinRequest):
    vin = payload.vin.strip().upper()

    if len(vin) != 17:
        raise HTTPException(status_code=400, detail=f"VIN must be exactly 17 characters. You entered {len(vin)}.")

    if not vin.isalnum():
        raise HTTPException(status_code=400, detail="VIN must contain only letters and numbers (no I, O, Q).")

    wmi = vin[:3]
    year_char = vin[9]
    plant_char = vin[10]
    serial = vin[11:]

    # Decode manufacturer
    manufacturer_info = WMI_MAP.get(wmi)
    if not manufacturer_info:
        # Try country-only fallback based on first character
        first_char = vin[0]
        country_fallback = {
            "1": "USA", "2": "Canada", "3": "Mexico", "4": "USA", "5": "USA",
            "J": "Japan", "K": "South Korea", "L": "China", "M": "India",
            "S": "UK", "V": "France", "W": "Germany", "Y": "Sweden", "Z": "Italy",
        }
        manufacturer_info = {
            "make": "Unknown Manufacturer",
            "country": country_fallback.get(first_char, "Unknown"),
            "type": "Unknown",
        }

    # Decode year (handle both single-char and numeric mapping)
    year = None
    # Try single character first (most common)
    for code, y in YEAR_CODES.items():
        if len(code) == 1 and code == year_char:
            year = y
            break
    # Fallback: 2010+ codes are often letters but map to same range
    if year is None:
        # Simple lookup for modern years
        if year_char == "L": year = 2020
        elif year_char == "M": year = 2021
        elif year_char == "N": year = 2022
        elif year_char == "P": year = 2023
        elif year_char == "R": year = 2024
        elif year_char == "S": year = 2025

    # Estimate parts that commonly need replacing on this make/model
    common_parts = {
        "Toyota": ["Brake Pads", "Oil Filter", "Air Filter", "Spark Plugs", "Timing Belt"],
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
    parts = common_parts.get(manufacturer_info["make"], ["Brake Pads", "Oil Filter", "Air Filter"])

    return {
        "vin": vin,
        "valid": True,
        "manufacturer": manufacturer_info["make"],
        "country_of_origin": manufacturer_info["country"],
        "vehicle_type": manufacturer_info["type"],
        "model_year": year,
        "year_range": f"{year - 1}–{year}" if year else "Unknown",
        "assembly_plant": plant_char,
        "serial_number": serial,
        "common_parts_to_check": parts,
    }
