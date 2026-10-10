"""
Free VIN decoder.

1. Checks and tidies the VIN (length, letters, check digit).
2. Asks the free US government decoder (NHTSA vPIC) for full details.
   No key, no cost. Results are cached so each VIN is only looked up once a month.
3. If NHTSA is down or doesn't know the car (common for European / Japanese-market
   VINs), falls back to an offline decode: manufacturer, country and model year
   from the VIN's structure.

Japanese domestic imports often have a chassis/frame number (e.g. NZE121-1234567)
instead of a 17-character VIN. Those can't be decoded by any VIN decoder.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional

import httpx

NHTSA_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json"
NHTSA_TIMEOUT_SECONDS = 10
CACHE_TTL_SECONDS = 30 * 24 * 3600
CACHE_MAX_ENTRIES = 5000

VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
CHASSIS_RE = re.compile(r"^[A-Z]{2,6}\d{0,3}[A-Z]?-?\d{5,7}$")

# ---------------------------------------------------------------------------
# Manufacturer codes (first 3 characters of the VIN). Focused on brands common
# in Sierra Leone, including cars built in Thailand, South Africa, Europe and the US.
# ---------------------------------------------------------------------------
WMI = {
    # Toyota / Lexus
    "JTD": "Toyota", "JTE": "Toyota", "JTM": "Toyota", "JTN": "Toyota", "JTK": "Toyota",
    "JTL": "Toyota", "JTF": "Toyota", "JTG": "Toyota", "JT2": "Toyota", "JT3": "Toyota",
    "JT4": "Toyota", "JT5": "Toyota", "JT6": "Lexus", "JT8": "Lexus", "JTH": "Lexus",
    "JTJ": "Lexus", "2T2": "Lexus", "58A": "Lexus",
    "4T1": "Toyota", "4T3": "Toyota", "4T4": "Toyota", "5TD": "Toyota", "5TF": "Toyota",
    "5TB": "Toyota", "5TE": "Toyota", "5YF": "Toyota", "2T1": "Toyota", "2T3": "Toyota",
    "1NX": "Toyota", "SB1": "Toyota", "VNK": "Toyota", "NMT": "Toyota", "AHT": "Toyota",
    "MR0": "Toyota", "MR1": "Toyota", "MR2": "Toyota", "MHF": "Toyota", "8AJ": "Toyota",
    "9BR": "Toyota", "6T1": "Toyota", "JTB": "Toyota",
    # Nissan / Infiniti
    "JN1": "Nissan", "JN6": "Nissan", "JN8": "Nissan", "JNK": "Infiniti", "JNR": "Infiniti",
    "JNA": "Nissan", "1N4": "Nissan", "1N6": "Nissan", "3N1": "Nissan", "3N6": "Nissan",
    "5N1": "Nissan", "SJN": "Nissan", "VSK": "Nissan", "MNT": "Nissan", "ADN": "Nissan",
    "VWA": "Nissan",
    # Honda / Acura
    "JHM": "Honda", "JHL": "Honda", "JHG": "Honda", "1HG": "Honda", "2HG": "Honda",
    "2HK": "Honda", "5FN": "Honda", "5J6": "Honda", "SHH": "Honda", "SHS": "Honda",
    "MRH": "Honda", "19X": "Honda", "19U": "Acura", "JH4": "Acura", "2HN": "Acura",
    # Hyundai / Kia
    "KMH": "Hyundai", "KM8": "Hyundai", "KMF": "Hyundai", "5NP": "Hyundai", "5NM": "Hyundai",
    "TMA": "Hyundai", "NLH": "Hyundai", "MAL": "Hyundai",
    "KNA": "Kia", "KND": "Kia", "KNE": "Kia", "KNM": "Renault Samsung", "5XY": "Kia",
    "U5Y": "Kia", "3KP": "Kia",
    # Mitsubishi / Mazda / Suzuki / Subaru / Isuzu / Daihatsu
    "JA3": "Mitsubishi", "JA4": "Mitsubishi", "JA7": "Mitsubishi", "JMB": "Mitsubishi",
    "JMY": "Mitsubishi", "MMB": "Mitsubishi", "MMC": "Mitsubishi", "MMT": "Mitsubishi",
    "4A3": "Mitsubishi", "4A4": "Mitsubishi",
    "JM1": "Mazda", "JM3": "Mazda", "JMZ": "Mazda", "1YV": "Mazda", "3MZ": "Mazda",
    "MM7": "Mazda", "MM8": "Mazda",
    "JS2": "Suzuki", "JS3": "Suzuki", "JSA": "Suzuki", "TSM": "Suzuki", "MA3": "Suzuki",
    "MBH": "Suzuki", "VSE": "Suzuki",
    "JF1": "Subaru", "JF2": "Subaru", "4S3": "Subaru", "4S4": "Subaru",
    "JAA": "Isuzu", "JAL": "Isuzu", "MPA": "Isuzu", "JDA": "Daihatsu",
    # Ford
    "1FA": "Ford", "1FB": "Ford", "1FC": "Ford", "1FD": "Ford", "1FM": "Ford",
    "1FT": "Ford", "1FU": "Freightliner", "2FA": "Ford", "2FM": "Ford", "2FT": "Ford",
    "3FA": "Ford", "3FE": "Ford", "WF0": "Ford", "MNB": "Ford", "AFA": "Ford",
    "MAJ": "Ford", "NM0": "Ford", "6FP": "Ford", "9BF": "Ford", "1LN": "Lincoln",
    "5LM": "Lincoln",
    # GM
    "1G1": "Chevrolet", "1GC": "Chevrolet", "1GN": "Chevrolet", "1GB": "Chevrolet",
    "2G1": "Chevrolet", "2GN": "Chevrolet", "3G1": "Chevrolet", "3GN": "Chevrolet",
    "3GC": "Chevrolet", "KL1": "Chevrolet", "KL7": "Chevrolet", "KL8": "Chevrolet",
    "1GT": "GMC", "1GK": "GMC", "2GT": "GMC", "3GT": "GMC", "1G6": "Cadillac",
    "1GY": "Cadillac", "1G4": "Buick", "W0L": "Opel", "W0V": "Opel",
    # Stellantis (Chrysler, Dodge, Jeep, Ram, Fiat, Peugeot, Citroen, Opel)
    "1C3": "Chrysler", "2C3": "Chrysler", "2C4": "Chrysler", "1C4": "Jeep",
    "1J4": "Jeep", "1J8": "Jeep", "1B3": "Dodge", "1D3": "Dodge", "1D7": "Dodge",
    "2B3": "Dodge", "2D3": "Dodge", "3D7": "Dodge", "3C6": "Ram", "1C6": "Ram",
    "ZFA": "Fiat", "ZFB": "Fiat", "ZAR": "Alfa Romeo", "ZAM": "Maserati",
    "VF3": "Peugeot", "VR3": "Peugeot", "VF7": "Citroen", "VR7": "Citroen",
    # Renault / Dacia
    "VF1": "Renault", "VF6": "Renault Trucks", "UU1": "Dacia", "VF8": "Renault",
    # Volkswagen group
    "WVW": "Volkswagen", "WV1": "Volkswagen", "WV2": "Volkswagen", "WV3": "Volkswagen",
    "3VW": "Volkswagen", "1VW": "Volkswagen", "9BW": "Volkswagen", "AAV": "Volkswagen",
    "WAU": "Audi", "WA1": "Audi", "TRU": "Audi", "WUA": "Audi",
    "WP0": "Porsche", "WP1": "Porsche", "TMB": "Skoda", "VSS": "SEAT",
    # BMW / Mini / Mercedes
    "WBA": "BMW", "WBS": "BMW M", "WBX": "BMW", "WBY": "BMW", "5UX": "BMW",
    "4US": "BMW", "5YM": "BMW M", "WMW": "MINI",
    "WDB": "Mercedes-Benz", "WDC": "Mercedes-Benz", "WDD": "Mercedes-Benz",
    "WDF": "Mercedes-Benz", "WD3": "Mercedes-Benz", "WD4": "Mercedes-Benz",
    "W1K": "Mercedes-Benz", "W1N": "Mercedes-Benz", "W1V": "Mercedes-Benz",
    "W1W": "Mercedes-Benz", "4JG": "Mercedes-Benz", "55S": "Mercedes-Benz",
    "ADB": "Mercedes-Benz",
    # Others
    "YV1": "Volvo", "YV4": "Volvo", "YV2": "Volvo Trucks", "LYV": "Volvo",
    "SAL": "Land Rover", "SAJ": "Jaguar", "SAD": "Jaguar", "SCC": "Lotus",
    "5YJ": "Tesla", "7SA": "Tesla", "LRW": "Tesla",
    "L6T": "Geely", "LVV": "Chery", "LGW": "Great Wall", "LGX": "BYD", "LC0": "BYD",
    "LSG": "SAIC", "LZW": "SAIC-GM-Wuling", "LFV": "FAW-Volkswagen", "LDC": "Dongfeng",
    "MAT": "Tata", "MA1": "Mahindra", "MBJ": "Toyota", "XTA": "Lada",
}

# Region by first character (ISO 3780).
REGIONS = [
    ("ABCDEFGH", "Africa"), ("JKLMNPR", "Asia"), ("STUVWXYZ", "Europe"),
    ("12345", "North America"), ("67", "Oceania"), ("89", "South America"),
]

# Country by the first two characters. (start, end, country) inclusive, in VIN alphabet order.
_ALPHABET = "ABCDEFGHJKLMNPRSTUVWXYZ1234567890"
COUNTRY_RANGES = [
    ("AA", "AH", "South Africa"), ("AJ", "AN", "Côte d'Ivoire"), ("BA", "BE", "Angola"),
    ("BF", "BK", "Kenya"), ("BL", "BR", "Tanzania"), ("CA", "CE", "Benin"),
    ("DA", "DE", "Egypt"), ("DF", "DK", "Morocco"), ("EA", "EE", "Ethiopia"),
    ("FA", "FE", "Tunisia"), ("GA", "GE", "Ghana"), ("HA", "HE", "Nigeria"),
    ("J", "J", "Japan"), ("KA", "KE", "Sri Lanka"), ("KF", "KK", "Israel"),
    ("KL", "KR", "South Korea"), ("KS", "K0", "Kazakhstan"), ("L", "L", "China"),
    ("MA", "ME", "India"), ("MF", "MK", "Indonesia"), ("ML", "MR", "Thailand"),
    ("MS", "M0", "Myanmar"), ("NA", "NE", "Iran"), ("NF", "NK", "Pakistan"),
    ("NL", "NR", "Turkey"), ("PA", "PE", "Philippines"), ("PF", "PK", "Singapore"),
    ("PL", "PR", "Malaysia"), ("RA", "RE", "United Arab Emirates"), ("RF", "RK", "Taiwan"),
    ("RL", "RR", "Vietnam"), ("RS", "R0", "Saudi Arabia"),
    ("SA", "SM", "United Kingdom"), ("SN", "ST", "Germany"), ("SU", "SZ", "Poland"),
    ("S1", "S4", "Latvia"), ("TA", "TH", "Switzerland"), ("TJ", "TP", "Czech Republic"),
    ("TR", "TV", "Hungary"), ("TW", "T1", "Portugal"), ("UH", "UM", "Denmark"),
    ("UN", "UR", "Ireland"), ("U5", "U7", "Slovakia"), ("VA", "VE", "Austria"),
    ("VF", "VR", "France"), ("VS", "VW", "Spain"), ("VX", "V2", "Serbia"),
    ("V3", "V5", "Croatia"), ("W", "W", "Germany"), ("XA", "XE", "Bulgaria"),
    ("XF", "XK", "Greece"), ("XL", "XR", "Netherlands"), ("XS", "XW", "Russia"),
    ("X3", "X0", "Russia"), ("YA", "YE", "Belgium"), ("YF", "YK", "Finland"),
    ("YS", "YW", "Sweden"), ("ZA", "ZR", "Italy"),
    ("1", "1", "United States"), ("2", "2", "Canada"), ("3A", "3W", "Mexico"),
    ("4", "5", "United States"), ("6A", "6W", "Australia"), ("7A", "7E", "New Zealand"),
    ("8A", "8E", "Argentina"), ("8F", "8K", "Chile"), ("8X", "82", "Venezuela"),
    ("9A", "9E", "Brazil"), ("9F", "9K", "Colombia"), ("93", "99", "Brazil"),
]

# Model year codes (position 10). The same letters repeat every 30 years.
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 1980..2009, then repeats from 2010

BODY_TO_SILHOUETTE = [
    ("pickup", "pickup.svg"), ("truck", "pickup.svg"), ("sport utility", "suv.svg"),
    ("suv", "suv.svg"), ("crossover", "suv.svg"), ("minivan", "minivan.svg"),
    ("multipurpose", "minivan.svg"), ("mpv", "minivan.svg"), ("van", "van.svg"),
    ("bus", "van.svg"), ("hatchback", "hatchback.svg"), ("liftback", "hatchback.svg"),
    ("wagon", "hatchback.svg"), ("coupe", "coupe.svg"), ("convertible", "coupe.svg"),
    ("roadster", "coupe.svg"), ("sedan", "sedan.svg"), ("saloon", "sedan.svg"),
]

_cache: dict[str, tuple[float, dict]] = {}

# ---------------------------------------------------------------------------
# Free genuine-parts catalogues. Neither site has an API, so we link to the
# right brand catalogue and the buyer pastes their VIN / frame number there.
# ---------------------------------------------------------------------------
AMAYAMA_BRANDS = {
    "Toyota": "toyota", "Lexus": "lexus", "Nissan": "nissan", "Infiniti": "infiniti",
    "Honda": "honda", "Mitsubishi": "mitsubishi", "Mazda": "mazda", "Subaru": "subaru",
    "Suzuki": "suzuki", "Daihatsu": "daihatsu", "Hyundai": "hyundai", "Kia": "kia",
    "BMW": "bmw", "BMW M": "bmw", "Mercedes-Benz": "mercedes-benz", "Volkswagen": "volkswagen",
    "Audi": "audi", "Porsche": "porsche", "SEAT": "seat", "Skoda": "skoda",
}
MEGAZIP_BRANDS = {
    "Toyota": "toyota", "Nissan": "nissan", "Honda": "honda", "Hyundai": "hyundai", "Kia": "kia",
}
AMAYAMA_INDEX = "https://www.amayama.com/en/genuine-catalogs"
MEGAZIP_INDEX = "https://www.megazip.net/zapchasti-dlya-avtomobilej"
MEGAZIP_COVERS = {"Toyota", "Lexus", "Nissan", "Honda", "Mazda", "Mitsubishi", "Subaru", "Suzuki", "Hyundai", "Kia"}


def parts_catalog_links(make: Optional[str]) -> list[dict]:
    """Links to free genuine-parts catalogues for this make (or the catalogue index)."""
    links = []
    canonical = None
    for name in AMAYAMA_BRANDS:
        if make and name.lower() == make.strip().lower():
            canonical = name
            break
    if make is None or canonical in AMAYAMA_BRANDS:
        slug = AMAYAMA_BRANDS.get(canonical)
        links.append({
            "name": "Amayama",
            "url": f"{AMAYAMA_INDEX}/{slug}" if slug else AMAYAMA_INDEX,
            "accepts": "VIN or Japanese frame number",
        })
    if make is None or canonical in MEGAZIP_COVERS:
        slug = MEGAZIP_BRANDS.get(canonical)
        links.append({
            "name": "Megazip",
            "url": f"https://www.megazip.net/parts/{slug}" if slug else MEGAZIP_INDEX,
            "accepts": "VIN or frame number",
        })
    return links


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_vin(raw: str) -> tuple[str, list[str]]:
    """Upper-case, strip spaces/dashes, and fix the letters VINs never use (I→1, O/Q→0)."""
    notes = []
    vin = re.sub(r"[\s\-\.]", "", (raw or "").upper())
    fixed = vin.translate(str.maketrans({"I": "1", "O": "0", "Q": "0"}))
    if fixed != vin:
        notes.append("VINs never use the letters I, O or Q, so we read them as 1 and 0.")
    return fixed, notes


def looks_like_chassis_number(raw: str) -> bool:
    value = re.sub(r"\s", "", (raw or "").upper())
    return len(value) != 17 and bool(CHASSIS_RE.match(value))


_TRANSLIT = {
    **{str(d): d for d in range(10)},
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def check_digit(vin: str) -> str:
    total = sum(_TRANSLIT[c] * w for c, w in zip(vin, _WEIGHTS))
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def region_for(vin: str) -> Optional[str]:
    for chars, region in REGIONS:
        if vin[0] in chars:
            return region
    return None


def _alpha_index(ch: str) -> int:
    return _ALPHABET.index(ch) if ch in _ALPHABET else -1


def country_for(vin: str) -> Optional[str]:
    first, second = vin[0], vin[1]
    for start, end, country in COUNTRY_RANGES:
        if len(start) == 1:  # whole first character, e.g. "J" or "4".."5"
            if _alpha_index(start) <= _alpha_index(first) <= _alpha_index(end):
                return country
            continue
        if first != start[0] or first != end[0]:
            continue
        if _alpha_index(start[1]) <= _alpha_index(second) <= _alpha_index(end[1]):
            return country
    return None


def is_north_american_spec(vin: str) -> bool:
    """North American (and Chinese) VINs follow the strict rules: check digit and year code."""
    return vin[0] in "12345L"


def model_year_candidates(vin: str, current_year: Optional[int] = None) -> list[int]:
    """Possible model years, most likely first. Empty if position 10 isn't a year code."""
    current_year = current_year or datetime.utcnow().year
    code = vin[9]
    if code not in _YEAR_CODES:
        return []
    older = 1980 + _YEAR_CODES.index(code)
    newer = older + 30
    if is_north_american_spec(vin):
        # Position 7: a digit means 1980-2009, a letter means 2010-2039.
        return [newer] if vin[6].isalpha() else [older]
    candidates = [y for y in (newer, older) if y <= current_year + 1]
    # Cars from before 1988 are very rare on the road today; don't offer them as an option.
    recent = [y for y in candidates if y >= 1988]
    return recent or candidates


def silhouette_for(body_class: Optional[str]) -> str:
    text = (body_class or "").lower()
    for key, svg in BODY_TO_SILHOUETTE:
        if key in text:
            return f"/static/silhouettes/{svg}"
    return "/static/silhouettes/generic.svg"


def _clean(value) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    if value in ("", "null", "Not Applicable", "0"):
        return None
    return value


# ---------------------------------------------------------------------------
# NHTSA (free) lookup
# ---------------------------------------------------------------------------
async def fetch_nhtsa(vin: str) -> Optional[dict]:
    """Return NHTSA's decoded row for this VIN, or None if NHTSA can't be reached."""
    now = time.time()
    hit = _cache.get(vin)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=NHTSA_TIMEOUT_SECONDS) as client:
            response = await client.get(NHTSA_URL.format(vin=vin))
            response.raise_for_status()
            results = response.json().get("Results") or []
    except (httpx.HTTPError, ValueError):
        return None
    row = results[0] if results else None
    if row is not None:
        if len(_cache) >= CACHE_MAX_ENTRIES:
            _cache.pop(next(iter(_cache)))
        _cache[vin] = (now, row)
    return row


def _nhtsa_fields(row: dict) -> dict:
    engine_parts = []
    if _clean(row.get("DisplacementL")):
        try:
            engine_parts.append(f"{float(row['DisplacementL']):.1f}L")
        except ValueError:
            pass
    if _clean(row.get("EngineCylinders")):
        engine_parts.append(f"{row['EngineCylinders']} cyl")
    if _clean(row.get("EngineModel")):
        engine_parts.append(row["EngineModel"].strip())
    return {
        "make": _clean(row.get("Make")),
        "model": _clean(row.get("Model")),
        "year": _clean(row.get("ModelYear")),
        "series": _clean(row.get("Series")),
        "trim": _clean(row.get("Trim")),
        "body_class": _clean(row.get("BodyClass")),
        "vehicle_type": _clean(row.get("VehicleType")),
        "doors": _clean(row.get("Doors")),
        "fuel_type": _clean(row.get("FuelTypePrimary")),
        "engine_cylinders": _clean(row.get("EngineCylinders")),
        "engine_displacement_l": _clean(row.get("DisplacementL")),
        "engine_model": _clean(row.get("EngineModel")),
        "engine_hp": _clean(row.get("EngineHP")),
        "engine": " · ".join(engine_parts) or None,
        "transmission": _clean(row.get("TransmissionStyle")),
        "drive_type": _clean(row.get("DriveType")),
        "manufacturer": _clean(row.get("Manufacturer")),
        "plant_country": _clean(row.get("PlantCountry")),
        "plant_city": _clean(row.get("PlantCity")),
        "gvwr": _clean(row.get("GVWR")),
        "error_code": _clean(row.get("ErrorCode")),
        "error_text": _clean(row.get("ErrorText")),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
class VinError(ValueError):
    pass


async def decode(raw_vin: str, use_online: bool = True) -> dict:
    if looks_like_chassis_number(raw_vin):
        raise VinError(
            "This looks like a Japanese chassis (frame) number, not a 17-character VIN. "
            "Please search by make, model and year instead."
        )
    vin, notes = normalize_vin(raw_vin)
    if len(vin) != 17:
        raise VinError(f"A VIN has exactly 17 characters. You entered {len(vin)}.")
    if not VIN_RE.match(vin):
        raise VinError("A VIN can only contain letters and numbers.")

    warnings = list(notes)
    expected_check = check_digit(vin)
    check_ok = expected_check == vin[8]
    if not check_ok and is_north_american_spec(vin):
        warnings.append(
            "The VIN's check digit doesn't add up, so one character is probably mistyped. "
            "Please compare it with the car's windscreen or door sticker."
        )

    years = model_year_candidates(vin)
    if years and not is_north_american_spec(vin) and len(years) > 1:
        warnings.append(f"The model year could be {years[0]} or {years[1]}; {years[0]} is more likely.")

    result = {
        "vin": vin,
        "valid": True,
        "source": "offline",
        "wmi": vin[:3],
        "region": region_for(vin),
        "country_of_origin": country_for(vin),
        "make": WMI.get(vin[:3]),
        "model": None,
        "year": str(years[0]) if years else None,
        "model_year_candidates": years,
        "check_digit_valid": check_ok,
        "plant_code": vin[10],
        "serial_number": vin[11:],
        "series": None, "trim": None, "body_class": None, "vehicle_type": None,
        "doors": None, "fuel_type": None, "engine": None, "engine_cylinders": None,
        "engine_displacement_l": None, "engine_model": None, "engine_hp": None,
        "transmission": None, "drive_type": None, "manufacturer": None,
        "plant_country": None, "plant_city": None, "gvwr": None,
        "error_code": None, "error_text": None,
        "warnings": warnings,
        "ai_insights": None,
    }

    if use_online:
        row = await fetch_nhtsa(vin)
        if row is None:
            warnings.append("The online decoder is unavailable right now, so only basic details are shown.")
        else:
            online = _nhtsa_fields(row)
            if online["make"] or online["model"]:
                result["source"] = "NHTSA"
                for key, value in online.items():
                    if value is not None:
                        result[key] = value
            else:
                warnings.append(
                    "The online decoder has no details for this VIN (common for cars built for "
                    "Europe, Japan or Africa), so only basic details are shown."
                )

    if not result["make"]:
        warnings.append("We don't recognise this manufacturer code yet.")
    result["parts_catalogs"] = parts_catalog_links(result["make"]) if result["make"] else []
    result["placeholder"] = silhouette_for(result["body_class"])
    result["title"] = " ".join(str(p) for p in (result["year"], result["make"], result["model"]) if p) or "Unknown vehicle"
    return result


# ---------------------------------------------------------------------------
# Free US safety recalls (NHTSA). Only cars sold in the US have recall data.
# ---------------------------------------------------------------------------
RECALLS_URL = "https://api.nhtsa.gov/recalls/recallsByVehicle"
_recall_cache: dict[str, tuple[float, list]] = {}


async def fetch_recalls(make: str, model: str, year) -> Optional[list[dict]]:
    """Recalls for a make/model/year, or None if NHTSA can't be reached."""
    key = f"{make}|{model}|{year}".lower()
    now = time.time()
    hit = _recall_cache.get(key)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=NHTSA_TIMEOUT_SECONDS) as client:
            response = await client.get(RECALLS_URL, params={"make": make, "model": model, "modelYear": year})
            if response.status_code == 400:  # NHTSA answers 400 for makes/models it doesn't know
                rows = []
            else:
                response.raise_for_status()
                rows = response.json().get("results") or []
    except (httpx.HTTPError, ValueError):
        return None
    recalls = [{
        "campaign": r.get("NHTSACampaignNumber"),
        "date": r.get("ReportReceivedDate"),
        "component": r.get("Component"),
        "summary": r.get("Summary"),
        "consequence": r.get("Consequence"),
        "remedy": r.get("Remedy"),
    } for r in rows]
    if len(_recall_cache) >= CACHE_MAX_ENTRIES:
        _recall_cache.pop(next(iter(_recall_cache)))
    _recall_cache[key] = (now, recalls)
    return recalls
