"""Manually verify the Arizona 511 events API connection.

Run from the project root with:

    python -m route_risk.testing.manual_arizona_511_connection_test

This test only verifies:
- The Arizona 511 key loads through the existing project config.
- The Arizona events endpoint responds successfully.
- The response contains valid JSON.
- Basic event fields can be inspected.

It does not normalize or save any Arizona events.
"""

from __future__ import annotations

import sys
from typing import Any

import requests

from route_risk.config import get_arizona_511_api_key


ARIZONA_511_EVENTS_URL = "https://az511.com/api/v2/get/event"
REQUEST_TIMEOUT_SECONDS = 30


def fetch_arizona_events() -> list[dict[str, Any]]:
    """Fetch all current events from Arizona 511."""
    api_key = get_arizona_511_api_key()

    response = requests.get(
        ARIZONA_511_EVENTS_URL,
        params={
            "key": api_key,
            "format": "json",
        },
        headers={
            "Accept": "application/json",
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    print(f"HTTP status: {response.status_code}")
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, list):
        raise TypeError(
            "Expected Arizona 511 to return a JSON list, "
            f"but received {type(payload).__name__}."
        )

    return payload


def print_sample_event(event: dict[str, Any]) -> None:
    """Print a small, safe summary of one Arizona event."""
    print("\nSample event:")
    print(f"  ID: {event.get('ID', 'Not provided')}")
    print(f"  Roadway: {event.get('RoadwayName', 'Not provided')}")
    print(f"  Direction: {event.get('DirectionOfTravel', 'Not provided')}")
    print(f"  Event type: {event.get('EventType', 'Not provided')}")
    print(f"  Event subtype: {event.get('EventSubType', 'Not provided')}")
    print(f"  Full closure: {event.get('IsFullClosure', 'Not provided')}")
    print(f"  Latitude: {event.get('Latitude', 'Not provided')}")
    print(f"  Longitude: {event.get('Longitude', 'Not provided')}")

    print("\nAvailable fields:")
    for field_name in sorted(event.keys()):
        print(f"  - {field_name}")


def main() -> int:
    """Run the Arizona 511 connection test."""
    print("Arizona 511 connection test")
    print(f"Endpoint: {ARIZONA_511_EVENTS_URL}")

    try:
        events = fetch_arizona_events()
    except FileNotFoundError as error:
        print(f"\nFAIL: Arizona key file was not found.\n{error}")
        return 1
    except ValueError as error:
        print(f"\nFAIL: Arizona key could not be loaded.\n{error}")
        return 1
    except requests.Timeout:
        print("\nFAIL: The Arizona 511 request timed out.")
        return 1
    except requests.HTTPError as error:
        print(f"\nFAIL: Arizona 511 returned an HTTP error.\n{error}")
        return 1
    except requests.RequestException as error:
        print(f"\nFAIL: Could not connect to Arizona 511.\n{error}")
        return 1
    except TypeError as error:
        print(f"\nFAIL: Unexpected Arizona response structure.\n{error}")
        return 1
    except requests.JSONDecodeError as error:
        print(f"\nFAIL: Arizona 511 did not return valid JSON.\n{error}")
        return 1

    print("\nPASS: Connected to Arizona 511.")
    print(f"Active event count: {len(events)}")

    if events:
        print_sample_event(events[0])
    else:
        print("The connection succeeded, but no active events were returned.")

    return 0


if __name__ == "__main__":
    sys.exit(main())