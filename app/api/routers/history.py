"""API endpoints for persistent route-comparison history."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from app.api.history_store import (
    delete_route_history_record,
    list_route_history,
    load_route_history_record,
)


# N9_N10_ROUTE_HISTORY

router = APIRouter(tags=["Route History"])


@router.get("/route_history")
def route_history(
    limit: int = Query(default=10, ge=1, le=10),
) -> Dict[str, Any]:
    items = list_route_history(limit=limit)
    return {
        "history_count": len(items),
        "items": items,
    }


@router.get("/route_history/{job_id}")
def route_history_detail(
    job_id: str,
) -> Dict[str, Any]:
    try:
        return load_route_history_record(job_id)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error


@router.delete("/route_history/{job_id}")
def delete_route_history(
    job_id: str,
) -> Dict[str, Any]:
    try:
        return delete_route_history_record(job_id)
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
        ) from error
    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error
