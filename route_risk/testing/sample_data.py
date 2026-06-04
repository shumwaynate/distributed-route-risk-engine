"""
route_risk/sample_data.py

Sample route-risk data for local testing.

Purpose:
- Provide predictable test data for the Route Risk Engine.
- Keep sample route/weather data separate from scoring logic.
- Avoid using live APIs until the local route-risk pipeline works.

This file is part of the route-risk pivot and does not replace the original
distributed orchestrator infrastructure.
"""

from typing import Any, Dict, List


# ============================================================
# ROUTE RISK ENGINE SAMPLE DATA
# ============================================================

SAMPLE_ROUTE_SEGMENTS: List[Dict[str, Any]] = [
    {
        "label": "Rexburg to Rigby",
        "weather": {
            "temperature_f": 28,
            "wind_mph": 18,
            "condition": "snow",
            "visibility_miles": 3,
        },
        "road_condition": "normal",
        "is_night": True,
    },
    {
        "label": "Rigby to Idaho Falls",
        "weather": {
            "temperature_f": 34,
            "wind_mph": 30,
            "condition": "cloudy",
            "visibility_miles": 5,
        },
        "road_condition": "construction",
        "is_night": True,
    },
]


LOW_RISK_ROUTE_SEGMENTS: List[Dict[str, Any]] = [
    {
        "label": "Rexburg to Rigby",
        "weather": {
            "temperature_f": 72,
            "wind_mph": 8,
            "condition": "clear",
            "visibility_miles": 10,
        },
        "road_condition": "normal",
        "is_night": False,
    },
    {
        "label": "Rigby to Idaho Falls",
        "weather": {
            "temperature_f": 75,
            "wind_mph": 6,
            "condition": "clear",
            "visibility_miles": 10,
        },
        "road_condition": "normal",
        "is_night": False,
    },
]


HIGH_RISK_ROUTE_SEGMENTS: List[Dict[str, Any]] = [
    {
        "label": "Rexburg to Rigby",
        "weather": {
            "temperature_f": 20,
            "wind_mph": 35,
            "condition": "snow and ice",
            "visibility_miles": 1,
        },
        "road_condition": "icy",
        "is_night": True,
    },
    {
        "label": "Rigby to Idaho Falls",
        "weather": {
            "temperature_f": 24,
            "wind_mph": 28,
            "condition": "fog and snow",
            "visibility_miles": 1.5,
        },
        "road_condition": "construction",
        "is_night": True,
    },
]


def get_sample_route(route_type: str = "moderate") -> List[Dict[str, Any]]:
    """
    Return sample route data by route type.

    Supported route types:
    - low
    - moderate
    - high
    """

    normalized_route_type = route_type.lower().strip()

    if normalized_route_type == "low":
        return LOW_RISK_ROUTE_SEGMENTS

    if normalized_route_type == "high":
        return HIGH_RISK_ROUTE_SEGMENTS

    return SAMPLE_ROUTE_SEGMENTS


# ============================================================
# LOCAL MANUAL TESTING
# ============================================================

if __name__ == "__main__":
    import json

    print("\n============================================================")
    print("ROUTE RISK SAMPLE DATA MANUAL TEST")
    print("============================================================\n")

    print("Low risk sample:")
    print(json.dumps(get_sample_route("low"), indent=2))

    print("\nModerate risk sample:")
    print(json.dumps(get_sample_route("moderate"), indent=2))

    print("\nHigh risk sample:")
    print(json.dumps(get_sample_route("high"), indent=2))

    print("\n============================================================")
    print("END TEST")
    print("============================================================\n")