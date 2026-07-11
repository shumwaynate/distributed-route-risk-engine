"""Health and capability reporting for the Route Risk Engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List

import redis

from app.core.config import REDIS_URL
from app.worker.celery_app import celery_app
from route_risk.config import (
    get_arizona_511_api_key,
    get_nevada_511_api_key,
    get_ors_api_key,
    get_utah_udot_api_key,
)
from route_risk.integrations.state_511_clients.state_event_loader import (
    STATE_EVENT_GROUP_FETCHERS,
)


# N18_HEALTH_CAPABILITY_ENDPOINT

REDIS_TIMEOUT_SECONDS = 1.0
WORKER_INSPECTION_TIMEOUT_SECONDS = 1.5

ACTIVE_WORKLOAD_TYPES = [
    "route_segment_risk",
    "live_weather_route_segment_risk",
    "route_risk_summary",
]

ROAD_EVENT_PROVIDER_CONFIG = {
    "AZ": {
        "name": "Arizona 511",
        "source": "arizona-511-events",
        "key_loader": get_arizona_511_api_key,
    },
    "NV": {
        "name": "Nevada 511",
        "source": "nevada-511-events",
        "key_loader": get_nevada_511_api_key,
    },
    "UT": {
        "name": "Utah UDOT",
        "source": "utah-udot-events",
        "key_loader": get_utah_udot_api_key,
    },
}


def _safe_error_name(error: Exception) -> str:
    """Return a useful error type without leaking connection strings or keys."""
    return type(error).__name__


def _key_is_configured(key_loader: Callable[[], str]) -> bool:
    """Check whether a provider key can be loaded without returning the key."""
    try:
        return bool(key_loader().strip())
    except Exception:
        return False


def _check_redis() -> Dict[str, Any]:
    """Ping Redis with short timeouts."""
    client = redis.Redis.from_url(
        REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        socket_timeout=REDIS_TIMEOUT_SECONDS,
        decode_responses=True,
    )

    try:
        connected = bool(client.ping())
        return {
            "status": "available" if connected else "unavailable",
            "connected": connected,
        }
    except Exception as error:
        return {
            "status": "unavailable",
            "connected": False,
            "error_type": _safe_error_name(error),
        }
    finally:
        try:
            client.close()
        except Exception:
            pass


def _check_workers() -> Dict[str, Any]:
    """Ask Celery workers to identify themselves."""
    try:
        inspector = celery_app.control.inspect(
            timeout=WORKER_INSPECTION_TIMEOUT_SECONDS
        )
        ping_results = inspector.ping() or {}
        worker_names = sorted(str(name) for name in ping_results)
        worker_count = len(worker_names)

        return {
            "status": "ready" if worker_count > 0 else "not_ready",
            "ready": worker_count > 0,
            "worker_count": worker_count,
            "worker_names": worker_names,
        }
    except Exception as error:
        return {
            "status": "not_ready",
            "ready": False,
            "worker_count": 0,
            "worker_names": [],
            "error_type": _safe_error_name(error),
        }


def _routing_providers() -> List[Dict[str, Any]]:
    """Describe implemented routing providers without making billable API calls."""
    ors_configured = _key_is_configured(get_ors_api_key)

    return [
        {
            "id": "openrouteservice",
            "name": "HeiGIT OpenRouteService",
            "implemented": True,
            "configured": ors_configured,
            "status": "available" if ors_configured else "not_configured",
            "supports_alternative_routes": True,
        },
        {
            "id": "osrm",
            "name": "Open Source Routing Machine",
            "implemented": True,
            "configured": True,
            "status": "available",
            "supports_alternative_routes": False,
        },
    ]


def _road_event_providers() -> List[Dict[str, Any]]:
    """Describe implemented state roadway-event providers."""
    providers: List[Dict[str, Any]] = []

    for state_code in sorted(STATE_EVENT_GROUP_FETCHERS):
        provider = ROAD_EVENT_PROVIDER_CONFIG.get(state_code)
        if provider is None:
            providers.append(
                {
                    "state_code": state_code,
                    "name": f"{state_code} roadway-event provider",
                    "implemented": True,
                    "configured": False,
                    "status": "configuration_unknown",
                }
            )
            continue

        configured = _key_is_configured(provider["key_loader"])
        providers.append(
            {
                "state_code": state_code,
                "name": provider["name"],
                "source": provider["source"],
                "implemented": True,
                "configured": configured,
                "status": "available" if configured else "not_configured",
            }
        )

    return providers


def build_health_report(api_version: str) -> Dict[str, Any]:
    """Build one consolidated N18 health and capability response."""
    redis_status = _check_redis()
    worker_status = _check_workers()
    routing_providers = _routing_providers()
    road_event_providers = _road_event_providers()

    ors_ready = any(
        provider["id"] == "openrouteservice"
        and provider["status"] == "available"
        for provider in routing_providers
    )

    infrastructure_ready = (
        redis_status["connected"] and worker_status["ready"]
    )
    route_comparison_ready = infrastructure_ready and ors_ready

    if route_comparison_ready:
        overall_status = "healthy"
    elif infrastructure_ready:
        overall_status = "degraded"
    else:
        overall_status = "unavailable"

    return {
        "service": "Distributed Route Risk Engine",
        "api_version": api_version,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_status": overall_status,
        "ready_for_route_jobs": infrastructure_ready,
        "ready_for_route_comparison": route_comparison_ready,
        "components": {
            "api": {
                "status": "available",
                "available": True,
            },
            "redis": redis_status,
            "celery_workers": worker_status,
        },
        "capabilities": {
            "supported_workload_types": ACTIVE_WORKLOAD_TYPES,
            "route_analysis": {
                "single_route": True,
                "multiple_route_comparison": True,
                "distributed_checkpoint_processing": True,
                "live_weather": True,
                "manual_road_events": True,
                "state_road_events": True,
                "day_night_scoring": True,
                "blocked_route_detection": True,
                "html_report_export": True,
            },
            "weather_providers": [
                {
                    "id": "open-meteo",
                    "name": "Open-Meteo",
                    "implemented": True,
                    "requires_api_key": False,
                    "status": "available",
                }
            ],
            "routing_providers": routing_providers,
            "road_event_providers": road_event_providers,
            "supported_state_codes": sorted(
                STATE_EVENT_GROUP_FETCHERS
            ),
        },
    }
