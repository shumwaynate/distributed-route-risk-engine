"""Small browser dashboard and API proxy for the Route Risk Engine.

Run from the project root with:
    python -m uvicorn app.dashboard.server:app --host 127.0.0.1 --port 8080

The browser talks only to this dashboard server. The dashboard server proxies
requests to the existing FastAPI backend on port 8000, avoiding browser CORS
configuration changes in the main API.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse


BACKEND_BASE_URL = os.getenv(
    "ROUTE_RISK_BACKEND_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

DASHBOARD_DIRECTORY = Path(__file__).resolve().parent
INDEX_FILE = DASHBOARD_DIRECTORY / "index.html"

app = FastAPI(
    title="Distributed Route Risk Engine Dashboard",
    version="1.0.0",
)


def _proxy_response(response: requests.Response) -> Any:
    """Return JSON from the backend or raise a useful HTTP error."""

    try:
        payload = response.json()
    except ValueError:
        payload = {
            "detail": response.text or "The backend returned a non-JSON response."
        }

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=payload,
        )

    return payload


@app.get("/")
def dashboard() -> FileResponse:
    """Serve the dashboard page."""

    if not INDEX_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Dashboard file was not found: {INDEX_FILE}",
        )

    return FileResponse(INDEX_FILE)


@app.get("/health")
def health() -> dict[str, Any]:
    """Report whether the dashboard and backend are reachable."""

    backend_reachable = False
    backend_detail: Any = None

    try:
        response = requests.get(
            f"{BACKEND_BASE_URL}/",
            timeout=5,
        )
        backend_reachable = response.ok
        try:
            backend_detail = response.json()
        except ValueError:
            backend_detail = response.text
    except requests.RequestException as exc:
        backend_detail = str(exc)

    return {
        "dashboard_status": "ready",
        "backend_url": BACKEND_BASE_URL,
        "backend_reachable": backend_reachable,
        "backend_detail": backend_detail,
    }


@app.post("/api/submit_route_comparison_job")
async def submit_route_comparison_job(request: Request) -> Any:
    """Proxy a route-comparison request to the existing backend."""

    payload = await request.json()

    try:
        response = requests.post(
            f"{BACKEND_BASE_URL}/submit_route_comparison_job",
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the Route Risk API: {exc}",
        ) from exc

    return _proxy_response(response)


@app.get("/api/job_status/{job_id}")
def job_status(job_id: str) -> Any:
    """Proxy route job status requests."""

    try:
        response = requests.get(
            f"{BACKEND_BASE_URL}/job_status/{job_id}",
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the Route Risk API: {exc}",
        ) from exc

    return _proxy_response(response)


@app.get("/api/route_comparison_summary/{job_id}")
def route_comparison_summary(job_id: str) -> Any:
    """Proxy completed route-comparison summaries."""

    try:
        response = requests.get(
            f"{BACKEND_BASE_URL}/route_comparison_summary/{job_id}",
            timeout=60,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the Route Risk API: {exc}",
        ) from exc

    return _proxy_response(response)

# N9_N10_ROUTE_HISTORY
@app.get("/api/route_history")
def route_history(limit: int = 20) -> Any:
    safe_limit = max(1, min(int(limit), 200))
    try:
        response = requests.get(
            f"{BACKEND_BASE_URL}/route_history",
            params={"limit": safe_limit},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the Route Risk API: {exc}",
        ) from exc
    return _proxy_response(response)


@app.get("/api/route_history/{job_id}")
def route_history_detail(job_id: str) -> Any:
    try:
        response = requests.get(
            f"{BACKEND_BASE_URL}/route_history/{job_id}",
            timeout=60,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the Route Risk API: {exc}",
        ) from exc
    return _proxy_response(response)

# N9_N10_HISTORY_RETENTION_DELETE
@app.delete("/api/route_history/{job_id}")
def delete_route_history(job_id: str) -> Any:
    try:
        response = requests.delete(
            f"{BACKEND_BASE_URL}/route_history/{job_id}",
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach the Route Risk API: {exc}",
        ) from exc
    return _proxy_response(response)
