"""Local configuration and secret-loading helpers.

This file stores the locations of secret files, but never stores the actual
secret values in the project repository.

Each key file is read only when its matching getter function is called.
This allows state API integrations to be added gradually without requiring
every API key to be available immediately.
"""

from pathlib import Path
from typing import List


KEY_DIRECTORY = Path(
    r"C:\Users\nates\OneDrive\Desktop\ORS Key"
)

ORS_KEY_FILE_PATH = KEY_DIRECTORY / "ORSKey.txt"
IDAHO_511_KEY_FILE_PATH = KEY_DIRECTORY / "Idaho511Key.txt"
NEVADA_511_KEY_FILE_PATH = KEY_DIRECTORY / "Nevada511Key.txt"
UTAH_UDOT_KEY_FILE_PATH = KEY_DIRECTORY / "UtahUDOTKey.txt"
ARIZONA_511_KEY_FILE_PATH = KEY_DIRECTORY / "Arizona511Key.txt"


def _read_api_key_from_file(
    file_path: Path,
    service_name: str,
    accepted_labels: List[str],
) -> str:
    """Read and return an API key from an external text file."""

    if not file_path.exists():
        raise RuntimeError(
            f"The {service_name} key file could not be found at: {file_path}"
        )

    if not file_path.is_file():
        raise RuntimeError(
            f"The configured {service_name} key location is not a file: "
            f"{file_path}"
        )

    file_contents = file_path.read_text(
        encoding="utf-8"
    )

    nonempty_lines = [
        line.strip()
        for line in file_contents.splitlines()
        if line.strip()
    ]

    if not nonempty_lines:
        raise RuntimeError(
            f"The {service_name} key file is empty: {file_path}"
        )

    first_line = nonempty_lines[0].lower().rstrip(":")

    normalized_labels = [
        label.lower().rstrip(":")
        for label in accepted_labels
    ]

    if first_line in normalized_labels:
        nonempty_lines = nonempty_lines[1:]

    if not nonempty_lines:
        raise RuntimeError(
            f"The {service_name} key file contains a label but does not "
            "contain an API key."
        )

    api_key = nonempty_lines[0].strip()

    if not api_key:
        raise RuntimeError(
            f"No {service_name} API key could be read from the configured file."
        )

    return api_key


def get_ors_api_key() -> str:
    """Read and return the OpenRouteService API key."""

    return _read_api_key_from_file(
        file_path=ORS_KEY_FILE_PATH,
        service_name="OpenRouteService",
        accepted_labels=[
            "ORS Key",
            "OpenRouteService Key",
            "API Key",
        ],
    )


def get_idaho_511_api_key() -> str:
    """Read and return the Idaho 511 API key."""

    return _read_api_key_from_file(
        file_path=IDAHO_511_KEY_FILE_PATH,
        service_name="Idaho 511",
        accepted_labels=[
            "Idaho 511 Key",
            "Idaho511 Key",
            "Idaho API Key",
            "API Key",
        ],
    )


def get_nevada_511_api_key() -> str:
    """Read and return the Nevada 511 API key."""

    return _read_api_key_from_file(
        file_path=NEVADA_511_KEY_FILE_PATH,
        service_name="Nevada 511",
        accepted_labels=[
            "Nevada 511 Key",
            "Nevada511 Key",
            "NV Roads Key",
            "Nevada API Key",
            "API Key",
        ],
    )


def get_utah_udot_api_key() -> str:
    """Read and return the Utah UDOT API key."""

    return _read_api_key_from_file(
        file_path=UTAH_UDOT_KEY_FILE_PATH,
        service_name="Utah UDOT",
        accepted_labels=[
            "Utah UDOT Key",
            "UtahUDOT Key",
            "Utah 511 Key",
            "Utah API Key",
            "API Key",
        ],
    )


def get_arizona_511_api_key() -> str:
    """Read and return the Arizona 511 API key."""

    return _read_api_key_from_file(
        file_path=ARIZONA_511_KEY_FILE_PATH,
        service_name="Arizona 511",
        accepted_labels=[
            "Arizona 511 Key",
            "Arizona511 Key",
            "AZ 511 Key",
            "Arizona API Key",
            "API Key",
        ],
    )