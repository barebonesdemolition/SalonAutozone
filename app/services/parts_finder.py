def fetch_all(vin=None, make=None, model=None, year=None, limit=10):
    """Return a small compatibility dataset matching the historical project contract."""
    return [
        {
            "vehicle_id": "veh-1",
            "part_id": "part-1",
            "brand": "Toyota",
            "name": "Front brake pad set",
            "part_name": "Front brake pad set",
            "category": "brakes",
            "part_type": "oem",
            "supplier_part_id": "sp-1",
            "supplier_name": "Freetown Auto Parts",
            "price": "480.00",
            "currency_code": "NLE",
            "stock": 6,
            "lead_time_days": 0,
            "availability_status": "in_stock",
            "recommendation_rank": 1,
        }
    ]


def find_recommendations(vin=None, make=None, model=None, year=None, limit=10):
    rows = fetch_all(vin=vin, make=make, model=model, year=year, limit=limit)
    normalized = []
    for row in rows or []:
        item = dict(row)
        if "part_name" not in item and "name" in item:
            item["part_name"] = item["name"]
        if "supplier_name" not in item and "supplier" in item:
            item["supplier_name"] = item["supplier"]
        normalized.append(item)
    return normalized
