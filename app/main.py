from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import (
    auth,
    parts,
    vehicles,
    vehicle_listings,
    orders,
    vin_lookup,
    upload,
    ai_assistant,
    search,
    import_requests,
    supplier_catalog,
)

app = FastAPI(
    title="Salon Car Parts API",
    description="Backend API for Salon Car Parts marketplace",
    version="1.0.0",
)

# CORS Configuration (allows frontend connections)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with specific frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Root & Health Endpoints ----------

@app.get("/", tags=["Health"])
async def root():
    return {
        "status": "online",
        "service": "Salon Car Parts API",
        "interactive_docs": "/docs",
        "redoc_docs": "/redoc",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}


# ---------- Include Routers ----------

app.include_router(auth.router)
app.include_router(parts.router)
app.include_router(vehicles.router)
app.include_router(vehicle_listings.router)
app.include_router(orders.router)
app.include_router(vin_lookup.router)
app.include_router(upload.router)
app.include_router(ai_assistant.router)
app.include_router(search.router)
app.include_router(import_requests.router)
app.include_router(supplier_catalog.router)
