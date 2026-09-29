"""A presigned URL carries its object key in the path.

Migration 20260929120000 added jobs.cover_letter_s3_key and recorded:

    Backfill is not possible for existing rows — the key was never recorded
    anywhere — so their links stay dead until the job is regenerated.

That was wrong, and the evidence was in the row. Only the SIGNATURE of a
presigned URL expires; the path still names the object. 672 of 673 rows were
recovered from their own stored URL on 2026-09-29, every one confirmed with
head_object first.

Worth keeping as a test because the parse has to handle both S3 URL styles and
must never hand back a key with the bucket name still glued to the front —
that would produce users/... prefixed with utkarsh-job-hunt/, head_object would
404, and the row would be skipped for a reason that looks like "object gone".
"""
import sys

sys.path.insert(0, "scripts")
from backfill_cover_letter_keys import key_from_presigned  # noqa: E402

KEY = "users/7b28f6d3/cover_letters/cc39103faca8_cover.pdf"
SIG = "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=604800&X-Amz-Signature=deadbeef"


def test_virtual_hosted_style():
    assert key_from_presigned(f"https://utkarsh-job-hunt.s3.eu-west-1.amazonaws.com/{KEY}{SIG}") == KEY


def test_path_style_strips_the_bucket():
    assert key_from_presigned(f"https://s3.eu-west-1.amazonaws.com/utkarsh-job-hunt/{KEY}{SIG}") == KEY


def test_an_expired_signature_still_yields_the_key():
    """The whole premise. Expiry is in the query string, not the path."""
    assert key_from_presigned(f"https://utkarsh-job-hunt.s3.amazonaws.com/{KEY}"
                              "?X-Amz-Date=20200101T000000Z&X-Amz-Expires=1") == KEY


def test_percent_encoded_paths_are_decoded():
    """The legacy convention has spaces and punctuation in the filename."""
    url = ("https://utkarsh-job-hunt.s3.amazonaws.com/users/u1/2026-03-26/"
           "cover_letters/Utkarsh%20Singh_Cloud%20Engineer.pdf" + SIG)
    assert key_from_presigned(url) == "users/u1/2026-03-26/cover_letters/Utkarsh Singh_Cloud Engineer.pdf"


def test_empty_and_missing_inputs():
    assert key_from_presigned("") is None
    assert key_from_presigned(None) is None
    assert key_from_presigned("https://utkarsh-job-hunt.s3.amazonaws.com/") is None


def test_a_bucket_named_key_is_not_double_stripped():
    """A key that legitimately starts with the bucket name as a DIRECTORY must
    survive. Only a leading `{bucket}/` from path-style addressing is removed,
    and that case is covered above; this pins that the check is anchored."""
    k = "users/u1/utkarsh-job-hunt-archive/x.pdf"
    assert key_from_presigned(f"https://utkarsh-job-hunt.s3.amazonaws.com/{k}") == k
