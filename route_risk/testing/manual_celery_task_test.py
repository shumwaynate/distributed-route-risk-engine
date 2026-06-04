"""
route_risk/testing/manual_celery_task_test.py

Manual test runner for Route Risk Celery task functions.

Purpose:
- Confirm the Route Risk Engine Celery task functions can be imported.
- Confirm the route-risk workload works from app/worker/tasks.py.
- Print readable terminal output.
- Test the task logic before requiring Redis, Docker, or a running Celery worker.

Important:
This file calls the task function logic directly using .run(...).

That means:
- It does not queue work in Redis yet.
- It does not prove distributed execution yet.
- It only proves the task code itself works.

The next stage will connect this through the real worker/orchestrator path.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict


# ============================================================
# IMPORT PATH SETUP FOR LOCAL MANUAL TESTING
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from app.worker.tasks import route_risk_summary_task, route_segment_risk_task
from route_risk.testing.sample_data import get_sample_route


# ============================================================
# ROUTE RISK CELERY TASK MANUAL TEST RUNNER
# ============================================================

def print_section_title(title: str) -> None:
    """
    Print a clear section title for readable terminal output.
    """

    print("\n============================================================")
    print(title)
    print("============================================================\n")


def print_json_result(result: Dict[str, Any]) -> None:
    """
    Print a dictionary in readable JSON format.
    """

    print(json.dumps(result, indent=2))


def run_route_segment_task_test() -> None:
    """
    Run the route_segment_risk_task logic directly.
    """

    print_section_title("ROUTE SEGMENT RISK TASK DIRECT TEST")

    sample_route = get_sample_route("moderate")
    first_segment = sample_route[0]

    result = route_segment_risk_task.run(
        task_id=1,
        segment=first_segment,
    )

    print_json_result(result)


def run_route_summary_task_test() -> None:
    """
    Run the route_risk_summary_task logic directly.
    """

    print_section_title("ROUTE RISK SUMMARY TASK DIRECT TEST")

    sample_route = get_sample_route("moderate")

    result = route_risk_summary_task.run(
        task_id=100,
        segments=sample_route,
    )

    print_json_result(result)


def run_manual_celery_task_tests() -> None:
    """
    Run all direct Route Risk Celery task tests.
    """

    print_section_title("ROUTE RISK CELERY TASK DIRECT TESTS")

    run_route_segment_task_test()
    run_route_summary_task_test()

    print_section_title("END ROUTE RISK CELERY TASK DIRECT TESTS")


# ============================================================
# LOCAL MANUAL TESTING ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_manual_celery_task_tests()