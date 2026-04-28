"""Platform metadata fetchers for ATS application questions.

Each module exposes a `fetch_<platform>(*ids) -> dict` function that returns
a normalized payload:

    {
      "platform": str,
      "job_title": str,
      "questions": [
        {
          "label": str,           # human-readable question text
          "description": str|None,  # optional context (often used for EEO disclosures)
          "required": bool,
          "type": str,            # one of text|textarea|select|multi_select|checkbox|yes_no|file
          "field_name": str,      # platform's field id (used at submit time)
          "options": list[str],   # for select fields, the option labels
          "category": str,        # OPTIONAL — pre-tagged 'eeo' for compliance/demographic
        },
        ...
      ],
      "cover_letter_field_present": bool,
      "cover_letter_required": bool,
      "cover_letter_max_length": int,  # platform default if unspecified
    }

Fetchers raise the platform-specific Error class on failure with a `.reason`
attribute that maps to the preview endpoint's `reason` field.
"""
from typing import Tuple, Type
from .greenhouse import fetch_greenhouse, GreenhouseFetchError
from .ashby import fetch_ashby, AshbyFetchError

# Tuple of exception classes for `except PlatformFetchError as e:` syntax
PlatformFetchError: Tuple[Type[Exception], ...] = (GreenhouseFetchError, AshbyFetchError)


def fetch_metadata(platform: str, board_token: str, posting_id: str) -> dict:
    """Dispatch to the right platform fetcher.

    Raises ValueError for unsupported platforms — the caller should treat
    this as 'no platform metadata available, degrade gracefully' rather than
    a fatal error (cloud browser handles unknown forms via AI vision).
    """
    if platform == "greenhouse":
        return fetch_greenhouse(board_token, posting_id)
    if platform == "ashby":
        return fetch_ashby(board_token, posting_id)
    raise ValueError(f"Unsupported platform: {platform}")
