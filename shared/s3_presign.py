"""The one place that turns an S3 key into a link a browser can open.

Every presigned URL in the product is minted here, and nothing else calls
`generate_presigned_url` (tests/unit/test_presign_single_path.py enforces it).

Why one place: on 2026-10-09 the Studio's compile returned links that S3
answered with 403 InvalidAccessKeyId, while the links the dashboard minted for
the same object worked. The dashboard re-signs from the stored key every time
it is asked; the Studio minted a URL once, inside the SQS task, and the task
result it was stored in passed through a credential scrubber that replaced the
URL's `AWSAccessKeyId` and `x-amz-security-token` with `<REDACTED_...>`. A
presigned URL is a key plus a signature (CLAUDE.md #9), and it carries the
signer's session token by design -- so it must be minted at the moment it is
handed out, from the key, and never persisted as the thing to hand out.

The client uses the default credential chain and nothing else. On Lambda that
is the execution role's temporary credentials, which only work WITH their
session token; a client built from an explicit access key and secret signs
URLs without it, and S3 answers those with the same InvalidAccessKeyId.
"""
from __future__ import annotations

import os

import boto3

# SigV4 rejects anything longer, and with role credentials the URL dies with
# the session token anyway. Callers that need a lasting link store the KEY.
MAX_EXPIRES_SECONDS = 7 * 24 * 3600

_client = None


def default_bucket() -> str:
    return os.environ.get("S3_BUCKET", os.environ.get("S3_BUCKET_NAME", "utkarsh-job-hunt"))


def s3_client():
    """The process-wide S3 client: default credential chain, region only."""
    global _client
    if _client is None:
        _client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    return _client


def reset_client() -> None:
    """Forget the cached client (tests, and anything that swaps credentials)."""
    global _client
    _client = None


def presign_get(
    key: str,
    *,
    bucket: str | None = None,
    expires: int = MAX_EXPIRES_SECONDS,
    disposition: str | None = None,
    client=None,
) -> str:
    """A GET URL for `key`, valid for at most seven days.

    `client` exists so a caller that already holds the process's S3 client (or
    a test double of it) can pass it; it is never a reason to build a client
    with explicit credentials.
    """
    params = {"Bucket": bucket or default_bucket(), "Key": key}
    if disposition:
        params["ResponseContentDisposition"] = disposition
    return (client or s3_client()).generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=min(int(expires), MAX_EXPIRES_SECONDS),
    )
