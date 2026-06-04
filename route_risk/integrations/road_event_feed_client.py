"""
route_risk/integrations/road_event_feed_client.py

Road event feed client for the Route Risk Engine.

Purpose:
- Prepare the project for real construction/work-zone feeds.
- Fetch WZDx-style GeoJSON feeds when a public feed URL is available.
- Normalize feed features into the road-event shape already used by
  road_conditions_client.py.

Current stage:
- Supports WZDx-style GeoJSON FeatureCollection data.
- Includes a local manual test using sample WZDx-like data.
- Does not require a live feed URL yet.

Future stage:
- Use real WZDx feed URLs from the USDOT Work Zone Data Feed Registry.
- Add state/provider configuration.
- Pass normalized road events into the routed FastAPI endpoint automatically.

Normalized event shape:
    {
        "event_id": "event-1",
        "event_type": "construction",
        "description": "Lane closure due to construction",
        "latitude": 43.59723,
        "longitude": -111.965417,
        "source": "wzdx"
    }
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


# ============================================================
# WZDX / GEOJSON FEED FETCHING
# ============================================================

def fetch_json_from_url(
    url: str,
    timeout_seconds: int = 20,
) -> Dict[str, Any]:
    """
    Fetch JSON from a URL.

    This is intentionally generic so it can be used for WZDx feeds or other
    GeoJSON road-event feeds later.
    """

    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body)

    except HTTPError as error:
        raise RuntimeError(
            f"Road-event feed request failed with HTTP status {error.code}"
        ) from error

    except URLError as error:
        raise RuntimeError(
            f"Road-event feed request failed because the URL could not be reached: {error}"
        ) from error

    except json.JSONDecodeError as error:
        raise RuntimeError("Road-event feed returned invalid JSON.") from error


def fetch_wzdx_events_from_url(
    url: str,
    timeout_seconds: int = 20,
) -> List[Dict[str, Any]]:
    """
    Fetch a WZDx-style GeoJSON feed and normalize it into road events.
    """

    feed_json = fetch_json_from_url(
        url=url,
        timeout_seconds=timeout_seconds,
    )

    return normalize_wzdx_feature_collection(feed_json)


# ============================================================
# WZDX / GEOJSON NORMALIZATION
# ============================================================

def normalize_wzdx_feature_collection(
    feed_json: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Normalize a WZDx-style GeoJSON FeatureCollection into road events.

    Expected high-level shape:
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {...},
                    "properties": {...}
                }
            ]
        }

    This function is intentionally defensive because WZDx versions and provider
    feed details may vary slightly.
    """

    features = feed_json.get("features")

    if not isinstance(features, list):
        raise RuntimeError(
            "Road-event feed did not include a usable GeoJSON features list."
        )

    normalized_events = []

    for index, feature in enumerate(features, start=1):
        normalized_event = normalize_wzdx_feature(
            feature=feature,
            fallback_index=index,
        )

        if normalized_event is not None:
            normalized_events.append(normalized_event)

    return normalized_events


def normalize_wzdx_feature(
    feature: Dict[str, Any],
    fallback_index: int,
) -> Optional[Dict[str, Any]]:
    """
    Normalize one WZDx-style GeoJSON Feature into one road event.

    Returns None if the feature does not have usable geometry.
    """

    if not isinstance(feature, dict):
        return None

    properties = feature.get("properties", {})
    geometry = feature.get("geometry", {})

    if not isinstance(properties, dict):
        properties = {}

    latitude_longitude = extract_representative_latitude_longitude(geometry)

    if latitude_longitude is None:
        return None

    latitude, longitude = latitude_longitude

    event_id = extract_first_available_value(
        properties,
        [
            "id",
            "event_id",
            "road_event_id",
            "identifier",
            "uuid",
        ],
        default_value=f"wzdx-event-{fallback_index}",
    )

    event_type = extract_event_type(properties)

    description = extract_description(properties)

    return {
        "event_id": str(event_id),
        "event_type": event_type,
        "description": description,
        "latitude": latitude,
        "longitude": longitude,
        "source": "wzdx-feed",
        "raw_properties": properties,
    }


def extract_event_type(properties: Dict[str, Any]) -> str:
    """
    Extract a useful event type from WZDx-style properties.

    Many WZDx work-zone events use event_type values like:
    - work-zone
    - detour

    Some feeds may also include extra description fields that mention closure,
    lane closure, maintenance, or construction.
    """

    event_type = extract_first_available_value(
        properties,
        [
            "event_type",
            "type",
            "activity_type",
            "road_event_type",
        ],
        default_value="work zone",
    )

    event_type_text = str(event_type).lower()

    description_text = extract_description(properties).lower()

    combined_text = f"{event_type_text} {description_text}"

    if "closure" in combined_text or "closed" in combined_text:
        return "road closure"

    if "construction" in combined_text:
        return "construction"

    if "maintenance" in combined_text:
        return "maintenance"

    if "lane" in combined_text and "closed" in combined_text:
        return "road closure"

    if "work zone" in combined_text or "work-zone" in combined_text:
        return "construction"

    return str(event_type)


def extract_description(properties: Dict[str, Any]) -> str:
    """
    Extract a readable event description from common property names.
    """

    description = extract_first_available_value(
        properties,
        [
            "description",
            "short_description",
            "name",
            "headline",
            "comment",
            "comments",
            "details",
        ],
        default_value="Work zone or road event from WZDx feed.",
    )

    return str(description)


def extract_first_available_value(
    properties: Dict[str, Any],
    possible_keys: List[str],
    default_value: Any,
) -> Any:
    """
    Return the first non-empty value found for any of the possible keys.
    """

    for key in possible_keys:
        value = properties.get(key)

        if value is not None and value != "":
            return value

    return default_value


# ============================================================
# GEOJSON COORDINATE HELPERS
# ============================================================

def extract_representative_latitude_longitude(
    geometry: Dict[str, Any],
) -> Optional[Tuple[float, float]]:
    """
    Extract one representative latitude/longitude pair from GeoJSON geometry.

    GeoJSON coordinate order is longitude, latitude.

    Supported geometry types:
    - Point
    - LineString
    - MultiLineString
    - Polygon

    For lines, this uses the middle coordinate. That is good enough for matching
    a road event to nearby route checkpoints in the current prototype.
    """

    if not isinstance(geometry, dict):
        return None

    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Point":
        return convert_coordinate_pair_to_lat_lon(coordinates)

    if geometry_type == "LineString":
        return extract_middle_coordinate_from_line(coordinates)

    if geometry_type == "MultiLineString":
        if not isinstance(coordinates, list) or not coordinates:
            return None

        longest_line = max(
            coordinates,
            key=lambda line: len(line) if isinstance(line, list) else 0,
        )

        return extract_middle_coordinate_from_line(longest_line)

    if geometry_type == "Polygon":
        if not isinstance(coordinates, list) or not coordinates:
            return None

        outer_ring = coordinates[0]

        return extract_middle_coordinate_from_line(outer_ring)

    return None


def extract_middle_coordinate_from_line(
    coordinates: Any,
) -> Optional[Tuple[float, float]]:
    """
    Extract the middle coordinate from a GeoJSON LineString-style coordinate list.
    """

    if not isinstance(coordinates, list) or not coordinates:
        return None

    middle_index = len(coordinates) // 2

    return convert_coordinate_pair_to_lat_lon(coordinates[middle_index])


def convert_coordinate_pair_to_lat_lon(
    coordinate_pair: Any,
) -> Optional[Tuple[float, float]]:
    """
    Convert a GeoJSON coordinate pair into latitude/longitude.

    GeoJSON order:
        [longitude, latitude]

    Internal project order:
        latitude, longitude
    """

    if not isinstance(coordinate_pair, list) or len(coordinate_pair) < 2:
        return None

    longitude = coordinate_pair[0]
    latitude = coordinate_pair[1]

    if latitude is None or longitude is None:
        return None

    return float(latitude), float(longitude)


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


def build_sample_wzdx_like_feed() -> Dict[str, Any]:
    """
    Build a small WZDx-like GeoJSON feed for local testing.

    The coordinates are intentionally near the Rexburg-to-Idaho-Falls route
    checkpoints used in earlier tests.
    """

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        -111.965417,
                        43.59723,
                    ],
                },
                "properties": {
                    "id": "sample-wzdx-work-zone-1",
                    "event_type": "work-zone",
                    "description": "Sample WZDx work zone near Rigby.",
                },
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [
                            -112.0069,
                            43.5401,
                        ],
                        [
                            -112.007668,
                            43.540506,
                        ],
                        [
                            -112.0082,
                            43.5410,
                        ],
                    ],
                },
                "properties": {
                    "id": "sample-wzdx-closure-1",
                    "event_type": "work-zone",
                    "description": "Sample WZDx lane closure near north Idaho Falls.",
                },
            },
        ],
    }


if __name__ == "__main__":
    print_section_title("ROAD EVENT FEED CLIENT MANUAL TEST")

    sample_feed = build_sample_wzdx_like_feed()

    normalized_events = normalize_wzdx_feature_collection(sample_feed)

    print(json.dumps(normalized_events, indent=2))

    print_section_title("END ROAD EVENT FEED CLIENT MANUAL TEST")