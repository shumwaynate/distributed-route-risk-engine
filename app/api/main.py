from typing import Any, Dict

from fastapi import FastAPI

from app.api.routers.jobs import router as jobs_router
from app.api.routers.routes import router as routes_router
from app.api.health import build_health_report


app = FastAPI(
    title="Distributed Route Risk Engine",
    description=(
        "Distributed driving-route analysis using FastAPI, Redis, Celery, "
        "live weather, routing providers, and state 511 roadway events."
    ),
    version="1.6.0",
)

app.include_router(routes_router)
app.include_router(jobs_router)





# N18_HEALTH_CAPABILITY_ENDPOINT
@app.get("/health", tags=["Health"])
def health() -> Dict[str, Any]:
    """Report infrastructure health and available route-risk capabilities."""
    return build_health_report(api_version=app.version)

@app.get("/", tags=["Health"])
def root() -> Dict[str, str]:
    return {
        "message": "Distributed Route Risk Engine API is running",
        "route_risk_status": (
            "Single-route and multi-route comparison endpoints are available"
        ),
    }