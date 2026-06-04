"""
route_risk/core/aggregation.py

Aggregation logic for Route Risk Engine task results.

Purpose:
- Combine completed route segment results into one route-level result.
- Preserve the raw segment results for debugging and explanation.
- Identify the highest-risk segment.
- Escalate the route when a blocking condition, such as a road closure, is found.

Important design decision:
A route should not be judged only by average score.

Example:
- 7 checkpoints are safe.
- 1 checkpoint is a road closure.

A simple average may look low, but the route is not actually usable.
For that reason, blocking events override the average route score.
"""

import json
from typing import Any, Dict, List

from route_risk.core.scoring import classify_risk


# ============================================================
# ROUTE RISK ENGINE AGGREGATION LOGIC
# ============================================================

def aggregate_job_results(task_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate Celery task result wrappers into one route-level risk result.

    Expected task result shape:
        {
            "task_id": "...",
            "status": "SUCCESS",
            "result": {
                "segment_label": "...",
                "risk_score": 70,
                "risk_level": "High",
                ...
            },
            "error": None
        }

    This function only aggregates successful task results.
    Pending, failed, and incomplete task results are ignored for scoring.
    """

    successful_segment_results = []

    incomplete_or_failed_results = []

    for task_result in task_results:
        if task_result.get("status") == "SUCCESS" and task_result.get("result"):
            successful_segment_results.append(task_result["result"])
        else:
            incomplete_or_failed_results.append(task_result)

    aggregated_result = aggregate_segment_results(successful_segment_results)

    if incomplete_or_failed_results:
        aggregated_result["incomplete_task_count"] = len(incomplete_or_failed_results)
        aggregated_result["summary"] = (
            aggregated_result["summary"]
            + f" Note: {len(incomplete_or_failed_results)} task(s) were not complete "
            "when this summary was generated."
        )
    else:
        aggregated_result["incomplete_task_count"] = 0

    return aggregated_result


def aggregate_segment_results(segment_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate already-completed segment results into a route-level result.

    Route-level logic:
    - Average score is still calculated.
    - Highest-risk segment is still identified.
    - If any segment has a road closure, the route is marked as blocked.
    - A blocked route gets route_risk_score = 100 and route_risk_level = "Blocked".

    This makes the app behave more like a real route recommendation system.
    One closed segment should stop the route, not disappear into an average.
    """

    if not segment_results:
        return {
            "route_risk_score": 0,
            "route_risk_level": "Low",
            "route_blocked": False,
            "route_warning": None,
            "highest_risk_segment": None,
            "blocking_segments": [],
            "segment_results": [],
            "summary": "No completed segment results were available to aggregate.",
        }

    highest_risk_segment = max(
        segment_results,
        key=lambda segment: int(segment.get("risk_score", 0)),
    )

    total_score = sum(int(segment.get("risk_score", 0)) for segment in segment_results)
    average_score = round(total_score / len(segment_results))

    blocking_segments = find_blocking_segments(segment_results)

    if blocking_segments:
        route_risk_score = 100
        route_risk_level = "Blocked"
        route_blocked = True
        route_warning = (
            "This route has at least one blocking road event and should not be "
            "recommended without rerouting."
        )
    else:
        route_risk_score = average_score
        route_risk_level = classify_risk(route_risk_score)
        route_blocked = False
        route_warning = None

    summary = build_aggregation_summary(
        segment_results=segment_results,
        route_risk_score=route_risk_score,
        route_risk_level=route_risk_level,
        route_blocked=route_blocked,
        highest_risk_segment=highest_risk_segment,
        blocking_segments=blocking_segments,
        average_score=average_score,
    )

    return {
        "route_risk_score": route_risk_score,
        "route_risk_level": route_risk_level,
        "route_blocked": route_blocked,
        "route_warning": route_warning,
        "average_segment_score": average_score,
        "highest_risk_segment": highest_risk_segment,
        "blocking_segments": blocking_segments,
        "segment_results": segment_results,
        "summary": summary,
    }


def find_blocking_segments(segment_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Find segments that should block the route.

    Current blocking logic:
    - road_condition == "closed"
    - factors include "road closure"

    This keeps the logic flexible because some providers may express closure
    through normalized road_condition, while others may only show it in factors.
    """

    blocking_segments = []

    for segment in segment_results:
        road_condition = str(segment.get("road_condition", "")).lower()
        factors = [
            str(factor).lower()
            for factor in segment.get("factors", [])
        ]

        has_closed_condition = road_condition == "closed"
        has_road_closure_factor = "road closure" in factors

        if has_closed_condition or has_road_closure_factor:
            blocking_segments.append(segment)

    return blocking_segments


def build_aggregation_summary(
    segment_results: List[Dict[str, Any]],
    route_risk_score: int,
    route_risk_level: str,
    route_blocked: bool,
    highest_risk_segment: Dict[str, Any],
    blocking_segments: List[Dict[str, Any]],
    average_score: int,
) -> str:
    """
    Build a human-readable route risk summary.
    """

    all_factors: List[str] = []

    for segment in segment_results:
        all_factors.extend(segment.get("factors", []))

    unique_factors = sorted(set(all_factors))

    if route_blocked:
        blocking_labels = [
            str(segment.get("segment_label", "Unnamed segment"))
            for segment in blocking_segments
        ]

        blocking_text = ", ".join(blocking_labels)

        if unique_factors:
            factor_text = ", ".join(unique_factors)
        else:
            factor_text = "road closure"

        return (
            "This route is blocked and should not be recommended without rerouting. "
            f"Blocking segment(s): {blocking_text}. "
            f"The highest-risk segment is "
            f"{highest_risk_segment.get('segment_label', 'Unnamed segment')} "
            f"with a score of {highest_risk_segment.get('risk_score')} "
            f"({highest_risk_segment.get('risk_level')}). "
            f"The route-level score was escalated to {route_risk_score} "
            f"from an average segment score of {average_score}. "
            f"The main risk factors are: {factor_text}."
        )

    if not unique_factors:
        return "This route has a risk score of 0. No major risk factors were detected in the completed segment results."

    factor_text = ", ".join(unique_factors)

    return (
        f"This route has an overall risk score of {route_risk_score}. "
        f"The highest-risk segment is "
        f"{highest_risk_segment.get('segment_label', 'Unnamed segment')} "
        f"with a score of {highest_risk_segment.get('risk_score')} "
        f"({highest_risk_segment.get('risk_level')}). "
        f"The main risk factors are: {factor_text}."
    )


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
    print_section_title("ROUTE RISK AGGREGATION MANUAL TEST")

    sample_segment_results = [
        {
            "task_id": 1,
            "workload": "route_segment_risk",
            "weather_mode": "live",
            "segment_label": "Route checkpoint 1",
            "latitude": 43.8231,
            "longitude": -111.792468,
            "road_condition": "normal",
            "risk_score": 0,
            "risk_level": "Low",
            "factors": [],
        },
        {
            "task_id": 2,
            "workload": "route_segment_risk",
            "weather_mode": "live",
            "segment_label": "Route checkpoint 2",
            "latitude": 43.59723,
            "longitude": -111.965417,
            "road_condition": "construction",
            "risk_score": 15,
            "risk_level": "Low",
            "factors": [
                "construction",
            ],
        },
        {
            "task_id": 3,
            "workload": "route_segment_risk",
            "weather_mode": "live",
            "segment_label": "Route checkpoint 3",
            "latitude": 43.540506,
            "longitude": -112.007668,
            "road_condition": "closed",
            "risk_score": 100,
            "risk_level": "High",
            "factors": [
                "road closure",
            ],
        },
    ]

    result = aggregate_segment_results(sample_segment_results)

    print(json.dumps(result, indent=2))

    print_section_title("END ROUTE RISK AGGREGATION MANUAL TEST")