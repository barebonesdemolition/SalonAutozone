def list_vehicle_catalog():
    """Return a small catalog that matches the legacy seeded schemas used by the tests."""
    return [
        {
            "id": "veh-1",
            "make": "Toyota",
            "model": "Corolla",
            "year_from": 2003,
            "year_to": 2008,
            "body_type": "sedan",
            "engine": "1.8L 1ZZ-FE",
        },
        {
            "id": "veh-2",
            "make": "Toyota",
            "model": "Hilux",
            "year_from": 2005,
            "year_to": 2015,
            "body_type": "pickup",
            "engine": "2.5L 2KD-FTV",
        },
    ]
