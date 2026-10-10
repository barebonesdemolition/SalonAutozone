import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import vin_decoder as vd


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def offline(monkeypatch):
    """Simulate NHTSA being unreachable."""
    async def none(_vin):
        return None
    monkeypatch.setattr(vd, "fetch_nhtsa", none)


# ---------- building blocks ----------

def test_check_digit_known_vins():
    assert vd.check_digit("1HGCM82633A004352") == "3"
    assert vd.check_digit("5YJSA1DG9DFP14705") == "9"


def test_normalize_fixes_forbidden_letters_and_spaces():
    vin, notes = vd.normalize_vin(" 1hgcm8263 3a0O4352 ")
    assert vin == "1HGCM82633A004352"
    assert notes


@pytest.mark.parametrize("vin,expected", [
    ("1HGCM82633A004352", [2003]),   # North American, position 7 digit -> 1980-2009 cycle
    ("5YJSA1DG9DFP14705", [2013]),   # position 7 letter -> 2010+ (old decoder said 1983)
    ("JTDBR32E1A0000000", [2010]),   # Japanese: 'A' is 2010, not 1980
    ("WVWZZZ1KZ6W000000", [2006]),   # German: '6' -> 2006 (2036 is in the future)
    ("SALLAAA14XA000000", [1999]),   # 'X' -> 1999 (2029 is in the future)
])
def test_model_year(vin, expected):
    assert vd.model_year_candidates(vin, current_year=2026)[: len(expected)] == expected


def test_model_year_ambiguous_for_non_us_cars():
    # 'T' could be 1996 or 2026 for a non-North-American VIN; newer is listed first.
    assert vd.model_year_candidates("JN1AAAAAAT0000000", current_year=2026) == [2026, 1996]
    # 'B' could be 1981 or 2011, but 1981 is too old to be worth offering.
    assert vd.model_year_candidates("JN1AAAAAAB0000000", current_year=2026) == [2011]


def test_no_year_when_position_10_is_not_a_year_code():
    assert vd.model_year_candidates("WDB123456Z0000000", current_year=2026) == []


@pytest.mark.parametrize("vin,country,region", [
    ("JTDBR32E173000001", "Japan", "Asia"),
    ("MR0FX22G801234567", "Thailand", "Asia"),
    ("AHTFX22G801234567", "South Africa", "Africa"),
    ("WVWZZZ1KZ6W000000", "Germany", "Europe"),
    ("VF1AAAAAAAA000000", "France", "Europe"),
    ("SALLAAA14XA000000", "United Kingdom", "Europe"),
    ("1HGCM82633A004352", "United States", "North America"),
    ("3N1AB7AP5KY000000", "Mexico", "North America"),
    ("KMHCT4AE5DU000000", "South Korea", "Asia"),
])
def test_country_and_region(vin, country, region):
    assert vd.country_for(vin) == country
    assert vd.region_for(vin) == region


# ---------- full decode ----------

def test_offline_decode_when_nhtsa_unreachable(offline):
    result = run(vd.decode("JTDBR32E1A0000000"))
    assert result["source"] == "offline"
    assert result["make"] == "Toyota"
    assert result["year"] == "2010"
    assert result["country_of_origin"] == "Japan"
    assert any("unavailable" in w for w in result["warnings"])


def test_nhtsa_details_are_used_when_available(monkeypatch):
    async def fake(_vin):
        return {
            "Make": "HONDA", "Model": "Accord", "ModelYear": "2003", "BodyClass": "Coupe",
            "DisplacementL": "3.0", "EngineCylinders": "6", "TransmissionStyle": "Automatic",
            "PlantCountry": "UNITED STATES (USA)", "ErrorCode": "0", "ErrorText": "0 - VIN decoded clean.",
        }
    monkeypatch.setattr(vd, "fetch_nhtsa", fake)
    result = run(vd.decode("1HGCM82633A004352"))
    assert result["source"] == "NHTSA"
    assert result["model"] == "Accord"
    assert result["engine"] == "3.0L · 6 cyl"
    assert result["placeholder"].endswith("coupe.svg")
    assert result["check_digit_valid"] is True
    assert result["title"] == "2003 HONDA Accord"


def test_nhtsa_without_data_falls_back(monkeypatch):
    async def empty(_vin):
        return {"Make": "", "Model": "", "ErrorCode": "8"}
    monkeypatch.setattr(vd, "fetch_nhtsa", empty)
    result = run(vd.decode("WVWZZZ1KZ6W000000"))
    assert result["source"] == "offline"
    assert result["make"] == "Volkswagen"
    assert any("no details" in w for w in result["warnings"])


def test_bad_check_digit_warns_for_us_vin(offline):
    result = run(vd.decode("1HGCM82634A004352"))
    assert result["check_digit_valid"] is False
    assert any("check digit" in w for w in result["warnings"])


@pytest.mark.parametrize("raw,message", [
    ("1HGCM8263", "exactly 17"),
    ("NZE121-1234567", "chassis"),
    ("1HGCM82633A00435*", "letters and numbers"),
])
def test_invalid_input(raw, message, offline):
    with pytest.raises(vd.VinError, match=message):
        run(vd.decode(raw))


# ---------- API ----------

def test_api_endpoints(offline):
    client = TestClient(app)
    for url in ("/api/nhtsa/decode-vin/JTDBR32E1A0000000", "/api/vin/decode/JTDBR32E1A0000000"):
        body = client.get(url).json()
        assert body["make"] == "Toyota" and body["year"] == "2010"
    legacy = client.post("/api/vin/decode", json={"vin": "JTDBR32E1A0000000"}).json()
    assert legacy["model_year"] == 2010
    assert legacy["common_parts_to_check"]
    bad = client.get("/api/nhtsa/decode-vin/NZE121-1234567")
    assert bad.status_code == 400 and "chassis" in bad.json()["detail"]


# ---------- genuine parts catalogue links ----------

def test_catalog_links_for_toyota():
    links = {l["name"]: l["url"] for l in vd.parts_catalog_links("TOYOTA")}
    assert links == {
        "Amayama": "https://www.amayama.com/en/genuine-catalogs/toyota",
        "Megazip": "https://www.megazip.net/parts/toyota",
    }


def test_catalog_links_for_mazda_uses_megazip_index():
    links = {l["name"]: l["url"] for l in vd.parts_catalog_links("Mazda")}
    assert links["Amayama"].endswith("/mazda")
    assert links["Megazip"] == vd.MEGAZIP_INDEX


def test_no_catalog_links_for_unsupported_make():
    assert vd.parts_catalog_links("Ford") == []


def test_catalog_index_links_when_make_unknown():
    names = [l["name"] for l in vd.parts_catalog_links(None)]
    assert names == ["Amayama", "Megazip"]


def test_decode_includes_catalog_links(offline):
    result = run(vd.decode("JTDBR32E1A0000000"))
    assert [l["name"] for l in result["parts_catalogs"]] == ["Amayama", "Megazip"]
