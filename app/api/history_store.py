"""Persistent JSON snapshots for completed route comparisons."""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from app.api.job_store import load_job


# N9_N10_ROUTE_HISTORY

PROJECT_ROOT = Path(__file__).resolve().parents[2]
HISTORY_DIRECTORY = Path(
    os.getenv(
        "ROUTE_HISTORY_DIRECTORY",
        str(PROJECT_ROOT / "data" / "route_history"),
    )
).expanduser()
HISTORY_INDEX_PATH = HISTORY_DIRECTORY / "index.json"
MAX_HISTORY_RECORDS = 10

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_HISTORY_LOCK = threading.RLock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validated_job_id(job_id: str) -> str:
    value = str(job_id).strip()
    if not value or not _JOB_ID_PATTERN.fullmatch(value):
        raise ValueError("Invalid route-history job ID.")
    return value


def _record_path(job_id: str) -> Path:
    return HISTORY_DIRECTORY / f"{_validated_job_id(job_id)}.json"


def _atomic_write_json(path: Path, payload: Any) -> None:
    HISTORY_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback


def _load_index() -> List[Dict[str, Any]]:
    payload = _load_json(HISTORY_INDEX_PATH, [])
    if not isinstance(payload, list):
        return []
    return [
        item
        for item in payload
        if isinstance(item, dict) and item.get("job_id")
    ]


def _prune_history_items(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Keep newest records and remove older snapshot files."""
    sorted_items = sorted(
        items,
        key=lambda item: item.get("saved_at_utc", ""),
        reverse=True,
    )
    kept_items = sorted_items[:MAX_HISTORY_RECORDS]
    removed_items = sorted_items[MAX_HISTORY_RECORDS:]

    for item in removed_items:
        job_id = item.get("job_id")
        if not job_id:
            continue
        try:
            _record_path(str(job_id)).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    _atomic_write_json(HISTORY_INDEX_PATH, kept_items)
    return kept_items


def _request_snapshot(metadata: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "route_name": metadata.get("route_name"),
        "origin_label": metadata.get("origin"),
        "origin_latitude": metadata.get("origin_latitude"),
        "origin_longitude": metadata.get("origin_longitude"),
        "destination_label": metadata.get("destination"),
        "destination_latitude": metadata.get(
            "destination_latitude"
        ),
        "destination_longitude": metadata.get(
            "destination_longitude"
        ),
        "checkpoint_count": metadata.get(
            "checkpoint_count_per_route"
        ),
        "target_route_count": metadata.get(
            "route_candidate_count"
        ),
        "share_factor": metadata.get("share_factor"),
        "weight_factor": metadata.get("weight_factor"),
        "use_live_state_events": metadata.get(
            "use_live_state_events",
            False,
        ),
        "state_codes": metadata.get("state_codes", []),
        "road_condition": metadata.get(
            "fallback_road_condition",
            "normal",
        ),
        "road_event_radius_miles": metadata.get(
            "road_event_radius_miles"
        ),
        "is_night": metadata.get("is_night", False),
    }


def _endpoint_snapshot(
    request: Dict[str, Any],
    endpoint_name: str,
) -> Dict[str, Any]:
    return {
        "label": request.get(f"{endpoint_name}_label"),
        "latitude": request.get(
            f"{endpoint_name}_latitude"
        ),
        "longitude": request.get(
            f"{endpoint_name}_longitude"
        ),
    }


def _index_item(record: Dict[str, Any]) -> Dict[str, Any]:
    comparison = record.get("comparison", {})
    recommended = comparison.get("recommended_route") or {}
    return {
        "job_id": record.get("job_id"),
        "saved_at_utc": record.get("saved_at_utc"),
        "comparison_status": comparison.get(
            "comparison_status"
        ),
        "route_name": comparison.get("route_name"),
        "origin": record.get("origin"),
        "destination": record.get("destination"),
        "driving_period": (
            "Night"
            if record.get("request", {}).get("is_night")
            else "Day"
        ),
        "route_count": comparison.get(
            "route_candidate_count",
            len(comparison.get("routes", [])),
        ),
        "checkpoint_count_per_route": comparison.get(
            "checkpoint_count_per_route"
        ),
        "recommended_route": {
            "route_id": recommended.get("route_id"),
            "route_label": recommended.get("route_label"),
            "risk_score": recommended.get(
                "route_risk_score"
            ),
            "risk_level": recommended.get(
                "route_risk_level"
            ),
            "route_blocked": recommended.get(
                "route_blocked",
                False,
            ),
        },
    }


def save_route_history_snapshot(
    job_id: str,
    comparison: Dict[str, Any],
) -> Dict[str, Any]:
    """Save a completed comparison exactly as it was returned."""
    normalized_job_id = _validated_job_id(job_id)

    if comparison.get("comparison_status") != "READY":
        raise ValueError(
            "Only READY route comparisons can be archived."
        )
    if not isinstance(comparison.get("routes"), list):
        raise ValueError(
            "The comparison does not contain route data."
        )

    job_data = load_job(normalized_job_id)
    try:
        metadata = json.loads(job_data.get("metadata", "{}"))
    except json.JSONDecodeError as error:
        raise ValueError(
            "Stored route metadata is not valid JSON."
        ) from error

    request = _request_snapshot(metadata)
    path = _record_path(normalized_job_id)

    with _HISTORY_LOCK:
        existing = _load_json(path, {})
        saved_at_utc = (
            existing.get("saved_at_utc")
            if isinstance(existing, dict)
            else None
        ) or _utc_now()

        total_tasks = int(
            job_data.get(
                "total_tasks",
                comparison.get(
                    "total_checkpoint_task_count",
                    0,
                ),
            )
        )

        record = {
            "schema_version": 1,
            "job_id": normalized_job_id,
            "saved_at_utc": saved_at_utc,
            "last_refreshed_at_utc": _utc_now(),
            "request": request,
            "origin": _endpoint_snapshot(
                request,
                "origin",
            ),
            "destination": _endpoint_snapshot(
                request,
                "destination",
            ),
            "job_status": {
                "status": "SUCCESS",
                "total_tasks": total_tasks,
                "completed_tasks": total_tasks,
                "failed_tasks": 0,
            },
            "comparison": comparison,
        }

        _atomic_write_json(path, record)

        items = [
            item
            for item in _load_index()
            if item.get("job_id") != normalized_job_id
        ]
        compact = _index_item(record)
        items.append(compact)
        items.sort(
            key=lambda item: item.get("saved_at_utc", ""),
            reverse=True,
        )
        _prune_history_items(items)

    return compact


def list_route_history(limit: int = 10) -> List[Dict[str, Any]]:
    safe_limit = max(1, min(int(limit), MAX_HISTORY_RECORDS))
    with _HISTORY_LOCK:
        items = _prune_history_items(_load_index())
        return items[:safe_limit]


def delete_route_history_record(
    job_id: str,
) -> Dict[str, Any]:
    """Delete one saved snapshot and remove it from the index."""
    normalized_job_id = _validated_job_id(job_id)
    path = _record_path(normalized_job_id)

    with _HISTORY_LOCK:
        items = _load_index()
        indexed = any(
            item.get("job_id") == normalized_job_id
            for item in items
        )

        if not indexed and not path.is_file():
            raise FileNotFoundError(
                f"Saved route analysis not found: {job_id}"
            )

        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise OSError(
                "Could not delete the saved route-analysis file."
            ) from error

        remaining_items = [
            item
            for item in items
            if item.get("job_id") != normalized_job_id
        ]
        remaining_items = _prune_history_items(remaining_items)

    return {
        "deleted": True,
        "job_id": normalized_job_id,
        "remaining_count": len(remaining_items),
    }


def load_route_history_record(job_id: str) -> Dict[str, Any]:
    path = _record_path(job_id)
    with _HISTORY_LOCK:
        payload = _load_json(path, None)

    if not isinstance(payload, dict):
        raise FileNotFoundError(
            f"Saved route analysis not found: {job_id}"
        )
    return payload
