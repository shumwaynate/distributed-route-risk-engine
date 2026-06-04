"""
route_risk/testing/manual_test.py

Manual test runner for the Route Risk Engine.

Purpose:
- Run route-risk scoring against predictable sample data.
- Print clean, readable terminal output.
- Keep testing code separate from the core scoring logic.

This file is part of the route-risk testing utilities.

It does not replace the original orchestrator.
It gives us a safe way to test route-risk behavior before connecting it
to FastAPI, Redis, Celery, Docker, and external APIs.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict


# ============================================================
# IMPORT PATH SETUP FOR LOCAL MANUAL TESTING
# ============================================================
#
# Why this exists:
# When running this file directly like:
#
#     python .\route_risk\testing\manual_test.py
#
# Python may only look inside the route_risk/testing folder instead of the full
# project root. This section makes sure the project root is available so
# imports like "from route_risk.core.scoring import score_route" work.
#
# This is only for local manual testing convenience.
# It does not change the original orchestrator architecture.

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from route_risk.core.scoring import score_route
from route_risk.testing.sample_data import get_sample_route


# ============================================================
# ROUTE RISK ENGINE MANUAL TEST RUNNER
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


def run_route_risk_manual_tests() -> None:
    """
    Run low, moderate, and high route-risk examples.
    """

    print_section_title("ROUTE RISK ENGINE MANUAL TESTS")

    for route_type in ["low", "moderate", "high"]:
        print_section_title(f"{route_type.upper()} RISK ROUTE TEST")

        sample_route = get_sample_route(route_type)
        result = score_route(sample_route)

        print_json_result(result)

    print_section_title("END ROUTE RISK ENGINE MANUAL TESTS")


# ============================================================
# LOCAL MANUAL TESTING ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_route_risk_manual_tests()