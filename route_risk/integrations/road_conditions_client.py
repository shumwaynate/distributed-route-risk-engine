"""
route_risk/integrations/road_conditions_client.py

Road condition and work-zone matching utilities for the Route Risk Engine.

Purpose:
- Match road events such as construction, closures, restrictions, or advisories
  to route checkpoints.
- Normalize external road-event data into simple road_condition values that the
  core scoring engine already understands.
- Prepare the project for future WZDx / 511 / DOT feed integrations.

Current stage:
- Uses provided event dictionaries.
- Does not call a live API yet.
- Proves that route checkpoints can be compared against road events.

Future stage:
- Fetch real work-zone data from WZDx or 511 feeds.
- Normalize those feed events into the event format used here.
"""

import json
import math
from typing import Any, Dict, List, Optional


# ============================================================
# DISTANCE HELPERS
# ============================================================

def haversine_distance_miles(
    latitude_1: float,
    longitude_1: float,
    latitude_2: float,
    longitude_2: float,
) -> float:
    """
    Calculate the approximate distance in miles between two latitude/longitude points.

    This is good enough for matching route checkpoints to nearby road events.
    """

    earth_radius_miles = 3958.8

    lat_1_rad = math.radians(latitude_1)
    lon_1_rad = math.radians(longitude_1)
    lat_2_rad = math.radians(latitude_2)
    lon_2_rad = math.radians(longitude_2)

    delta_lat = lat_2_rad - lat_1_rad
    delta_lon = lon_2_rad - lon_1_rad

    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat_1_rad)
        * math.cos(lat_2_rad)
        * math.sin(delta_lon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius_miles * c


# ============================================================
# ROAD EVENT NORMALIZATION
# ============================================================

def normalize_event_type_to_road_condition(event_type: str) -> str:
    """
    Convert event type text into a scoring-friendly road_condition value.

    Scoring currently understands:
    - normal
    - construction
    - wet
    - snowy
    - icy
    - closed
    """

    normalized = str(event_type or "").lower()

    if "closed" in normalized or "closure" in normalized:
        return "closed"

    if "work zone" in normalized:
        return "construction"

    if "construction" in normalized:
        return "construction"

    if "maintenance" in normalized:
        return "construction"

    if "restriction" in normalized:
        return "construction"

    if "icy" in normalized or "ice" in normalized:
        return "icy"

    if "snow" in normalized:
        return "snowy"

    if "wet" in normalized or "water" in normalized:
        return "wet"

    return "normal"


def road_condition_priority(road_condition: str) -> int:
    """
    Rank road conditions so the most serious nearby condition wins.
    """

    priorities = {
        "normal": 0,
        "wet": 1,
        "construction": 2,
        "snowy": 3,
        "icy": 4,
        "closed": 5,
    }

    return priorities.get(road_condition, 0)


# ============================================================
# ROAD EVENT MATCHING
# ============================================================

def find_nearby_road_events(
    checkpoint: Dict[str, Any],
    road_events: List[Dict[str, Any]],
    radius_miles: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Find road events within radius_miles of one route checkpoint.

    Expected checkpoint shape:
        {
            "label": "Route checkpoint 1",
            "latitude": 43.8231,
            "longitude": -111.7924
        }

    Expected road event shape:
        {
            "event_id": "event-1",
            "event_type": "construction",
            "description": "Lane closure due to construction",
            "latitude": 43.80,
            "longitude": -111.81
        }
    """

    checkpoint_latitude = float(checkpoint["latitude"])
    checkpoint_longitude = float(checkpoint["longitude"])

    nearby_events = []

    for event in road_events:
        event_latitude = event.get("latitude")
        event_longitude = event.get("longitude")

        if event_latitude is None or event_longitude is None:
            continue

        distance_miles = haversine_distance_miles(
            latitude_1=checkpoint_latitude,
            longitude_1=checkpoint_longitude,
            latitude_2=float(event_latitude),
            longitude_2=float(event_longitude),
        )

        if distance_miles <= radius_miles:
            normalized_condition = normalize_event_type_to_road_condition(
                str(event.get("event_type", "normal"))
            )

            nearby_events.append(
                {
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "description": event.get("description"),
                    "latitude": event_latitude,
                    "longitude": event_longitude,
                    "distance_miles": round(distance_miles, 3),
                    "road_condition": normalized_condition,
                    "source": event.get("source", "provided-road-events"),
                }
            )

    nearby_events.sort(
        key=lambda event: (
            -road_condition_priority(event["road_condition"]),
            event["distance_miles"],
        )
    )

    return nearby_events


def determine_checkpoint_road_condition(
    checkpoint: Dict[str, Any],
    road_events: List[Dict[str, Any]],
    radius_miles: float = 2.0,
    fallback_road_condition: str = "normal",
) -> Dict[str, Any]:
    """
    Determine the road condition for one checkpoint based on nearby road events.
    """

    nearby_events = find_nearby_road_events(
        checkpoint=checkpoint,
        road_events=road_events,
        radius_miles=radius_miles,
    )

    if not nearby_events:
        return {
            "checkpoint_label": checkpoint.get("label"),
            "latitude": checkpoint.get("latitude"),
            "longitude": checkpoint.get("longitude"),
            "road_condition": fallback_road_condition,
            "matched_event": None,
            "nearby_event_count": 0,
            "source": "fallback",
        }

    highest_priority_event = nearby_events[0]

    return {
        "checkpoint_label": checkpoint.get("label"),
        "latitude": checkpoint.get("latitude"),
        "longitude": checkpoint.get("longitude"),
        "road_condition": highest_priority_event["road_condition"],
        "matched_event": highest_priority_event,
        "nearby_event_count": len(nearby_events),
        "source": highest_priority_event.get("source"),
    }


def determine_route_road_conditions(
    checkpoints: List[Dict[str, Any]],
    road_events: List[Dict[str, Any]],
    radius_miles: float = 2.0,
    fallback_road_condition: str = "normal",
) -> List[Dict[str, Any]]:
    """
    Determine road condition for each route checkpoint.
    """

    results = []

    for checkpoint in checkpoints:
        result = determine_checkpoint_road_condition(
            checkpoint=checkpoint,
            road_events=road_events,
            radius_miles=radius_miles,
            fallback_road_condition=fallback_road_condition,
        )

        results.append(result)

    return results


def apply_road_conditions_to_checkpoints(
    checkpoints: List[Dict[str, Any]],
    road_events: List[Dict[str, Any]],
    radius_miles: float = 2.0,
    fallback_road_condition: str = "normal",
) -> List[Dict[str, Any]]:
    """
    Return checkpoint dictionaries with road_condition and road_event details added.

    This prepares route checkpoints to become route-risk segments.
    """

    road_condition_results = determine_route_road_conditions(
        checkpoints=checkpoints,
        road_events=road_events,
        radius_miles=radius_miles,
        fallback_road_condition=fallback_road_condition,
    )

    enriched_checkpoints = []

    for checkpoint, road_condition_result in zip(checkpoints, road_condition_results):
        enriched_checkpoint = {
            **checkpoint,
            "road_condition": road_condition_result["road_condition"],
            "road_condition_source": road_condition_result["source"],
            "matched_road_event": road_condition_result["matched_event"],
            "nearby_road_event_count": road_condition_result["nearby_event_count"],
        }

        enriched_checkpoints.append(enriched_checkpoint)

    return enriched_checkpoints


# ============================================================
# LOCAL MANUAL TESTING
# ============================================================

def print_section_title(title: str) -> None:
    """
    Print a clear section title for readable terminal output.
    """

    print("\n============================================================")
    print(title)
    print("============================================================\n")


if __name__ == "__main__":
    print_section_title("ROAD CONDITIONS CLIENT MANUAL TEST")

    sample_checkpoints = [
        {
            "label": "Route checkpoint 1",
            "latitude": 43.8231,
            "longitude": -111.792468,
        },
        {
            "label": "Route checkpoint 2",
            "latitude": 43.804545,
            "longitude": -111.811928,
        },
        {
            "label": "Route checkpoint 3",
            "latitude": 43.753344,
            "longitude": -111.850925,
        },
    ]

    sample_road_events = [
        {
            "event_id": "demo-construction-1",
            "event_type": "construction",
            "description": "Demo work zone near checkpoint 2.",
            "latitude": 43.8047,
            "longitude": -111.812,
            "source": "manual-demo-event",
        },
        {
            "event_id": "demo-closure-1",
            "event_type": "road closure",
            "description": "Demo closure near checkpoint 3.",
            "latitude": 43.7534,
            "longitude": -111.851,
            "source": "manual-demo-event",
        },
    ]

    enriched = apply_road_conditions_to_checkpoints(
        checkpoints=sample_checkpoints,
        road_events=sample_road_events,
        radius_miles=1.0,
        fallback_road_condition="normal",
    )

    print(json.dumps(enriched, indent=2))

    print_section_title("END ROAD CONDITIONS CLIENT MANUAL TEST")