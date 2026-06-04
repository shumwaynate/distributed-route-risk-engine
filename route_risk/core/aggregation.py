"""
route_risk/core/aggregation.py

Aggregation helpers for the Route Risk Engine.

Purpose:
- Combine multiple route segment task results into one route-level result.
- Preserve the distributed design where each segment can be scored separately.
- Prepare for future route comparison, such as safest, fastest, and balanced routes.

This file belongs to the Route Risk Engine core logic.

It does not replace the original orchestrator.
It helps turn distributed segment results into a user-facing route summary.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List


# ============================================================
# IMPORT PATH SETUP FOR LOCAL MANUAL TESTING
# ============================================================
#
# Why this exists:
# When running this file directly like:
#
#     python .\route_risk\core\aggregation.py
#
# Python may only look inside the route_risk/core folder instead of the full
# project root. This section makes sure the project root is available so
# imports like "from route_risk.core.scoring import classify_risk" work.
#
# This is only for local manual testing convenience.
# It does not change the original orchestrator architecture.

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from route_risk.core.scoring import classify_risk


# ============================================================
# ROUTE RISK ENGINE AGGREGATION LOGIC
# ============================================================

def aggregate_segment_results(
    segment_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Combine multiple route segment risk results into one route-level summary.

    Expected input:
        [
            {
                "task_id": 1,
                "workload": "route_segment_risk",
                "segment_label": "Rexburg to Rigby",
                "risk_score": 70,
                "risk_level": "High",
                "factors": [
                    "freezing temperature",
                    "snow",
                    "nighttime travel"
                ]
            },
            ...
        ]

    Returns:
        {
            "route_risk_score": 55,
            "route_risk_level": "Moderate",
            "highest_risk_segment": {...},
            "segment_results": [...],
            "summary": "..."
        }
    """

    if not segment_results:
        return {
            "route_risk_score": 0,
            "route_risk_level": "Low",
            "highest_risk_segment": None,
            "segment_results": [],
            "summary": "No completed route segment results were available.",
        }

    total_score = sum(
        int(segment.get("risk_score", 0))
        for segment in segment_results
    )

    average_score = round(total_score / len(segment_results))

    highest_risk_segment = max(
        segment_results,
        key=lambda segment: int(segment.get("risk_score", 0)),
    )

    summary = build_aggregate_summary(
        segment_results=segment_results,
        route_score=average_score,
        highest_risk_segment=highest_risk_segment,
    )

    return {
        "route_risk_score": average_score,
        "route_risk_level": classify_risk(average_score),
        "highest_risk_segment": highest_risk_segment,
        "segment_results": segment_results,
        "summary": summary,
    }


def build_aggregate_summary(
    segment_results: List[Dict[str, Any]],
    route_score: int,
    highest_risk_segment: Dict[str, Any],
) -> str:
    """
    Build a human-readable route summary from distributed segment results.
    """

    all_factors: List[str] = []

    for segment in segment_results:
        all_factors.extend(segment.get("factors", []))

    unique_factors = sorted(set(all_factors))

    highest_label = highest_risk_segment.get("segment_label", "Unknown segment")
    highest_score = highest_risk_segment.get("risk_score", 0)
    highest_level = highest_risk_segment.get("risk_level", "Unknown")

    if not unique_factors:
        return (
            f"This route has a risk score of {route_score}. "
            "No major risk factors were detected in the completed segment results."
        )

    factor_text = ", ".join(unique_factors)

    return (
        f"This route has an overall risk score of {route_score}. "
        f"The highest-risk segment is {highest_label} with a score of "
        f"{highest_score} ({highest_level}). "
        f"The main risk factors are: {factor_text}."
    )


def extract_successful_segment_results(
    job_results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Extract successful route segment results from the existing /results format.

    This helper filters out:
    - failed tasks
    - pending tasks
    - non-route-segment tasks
    """

    successful_segments = []

    for task_result in job_results:
        if task_result.get("status") != "SUCCESS":
            continue

        result = task_result.get("result")

        if not isinstance(result, dict):
            continue

        if result.get("workload") != "route_segment_risk":
            continue

        successful_segments.append(result)

    return successful_segments


def aggregate_job_results(
    job_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Aggregate route risk segment results from the existing /results task format.
    """

    segment_results = extract_successful_segment_results(job_results)

    return aggregate_segment_results(segment_results)


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
    sample_job_results = [
        {
            "task_id": "fake-celery-task-1",
            "status": "SUCCESS",
            "result": {
                "task_id": 1,
                "workload": "route_segment_risk",
                "segment_label": "Rexburg to Rigby",
                "risk_score": 70,
                "risk_level": "High",
                "factors": [
                    "freezing temperature",
                    "snow",
                    "nighttime travel",
                ],
            },
            "error": None,
        },
        {
            "task_id": "fake-celery-task-2",
            "status": "SUCCESS",
            "result": {
                "task_id": 2,
                "workload": "route_segment_risk",
                "segment_label": "Rigby to Idaho Falls",
                "risk_score": 40,
                "risk_level": "Moderate",
                "factors": [
                    "high wind",
                    "nighttime travel",
                    "construction",
                ],
            },
            "error": None,
        },
    ]

    print_section_title("ROUTE RISK AGGREGATION MANUAL TEST")

    aggregate_result = aggregate_job_results(sample_job_results)

    print(json.dumps(aggregate_result, indent=2))

    print_section_title("END ROUTE RISK AGGREGATION MANUAL TEST")