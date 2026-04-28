"""Cover letter loader: reads user's tailored .tex from S3 and returns plaintext.

S3 path matches lambdas/pipeline/generate_cover_letter.py:335:
    users/{user_id}/cover_letters/{job_hash}_cover.tex
"""
from __future__ import annotations

import logging
from typing import Optional

from botocore.exceptions import ClientError

from shared.tex_utils import tex_to_plaintext

logger = logging.getLogger(__name__)


def load_cover_letter(
    user_id: str,
    job_hash: str,
    s3_client,
    bucket: str,
) -> Optional[dict]:
    """Load and convert the user's cover letter for a specific job.

    Returns {"text": str, "source": "tailored"} if found in S3, None otherwise.
    Never raises — logs and returns None on any S3 error.

    The "source" field maps to spec §7.1 cover_letter.source enum:
    - "tailored" when loaded from users/{uid}/cover_letters/{job_hash}_cover.tex
    - "not_generated" handled by caller when this returns None

    (A future "default" source could be added if/when we ship a fallback CL.)
    """
    key = f"users/{user_id}/cover_letters/{job_hash}_cover.tex"
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        tex = response["Body"].read().decode("utf-8", errors="replace")
        return {"text": tex_to_plaintext(tex), "source": "tailored"}
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "NoSuchKey":
            logger.info(f"[cover_letter_loader] No CL at {key}")
        else:
            logger.warning(f"[cover_letter_loader] S3 error for {key}: {code}")
        return None
    except Exception as e:
        logger.warning(f"[cover_letter_loader] Unexpected error for {key}: {e}")
        return None
