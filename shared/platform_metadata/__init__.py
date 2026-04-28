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
