"""Downloaded artifacts must have a name a recruiter can read.

S3 keys are job-hash based (users/<id>/resumes/823af4d638be_tailored.pdf),
which is correct for storage and wrong for a human: downloading one to attach
to an application produced "823af4d638be_tailored.pdf". Fixed via
ResponseContentDisposition on the presigned URL, so storage keys are untouched
and no migration is needed.
"""
import pytest

from app import artifact_filename


def test_includes_company_title_and_kind():
    n = artifact_filename("resume", "Notion", "Software Engineer", "Utkarsh_Singh")
    assert n == "Utkarsh_Singh_Notion_Software_Engineer_Resume.pdf"


def test_cover_letter_is_distinguishable():
    n = artifact_filename("cover_letter", "Notion", "Software Engineer", "Utkarsh_Singh")
    assert n.endswith("_CoverLetter.pdf")
    assert "Resume" not in n


@pytest.mark.parametrize("company,title", [
    ("Amazon Web Services (AWS)", "Systems Engineer, European Sovereign Cloud"),
    ("Booking.com", "SRE — Platform"),
    ("AT&T", "Engineer / Ops"),
    ("Société Générale", "Ingénieur"),
])
def test_only_safe_characters_survive(company, title):
    """Goes into an HTTP header inside quotes -- punctuation, commas, accents
    and slashes must not reach it."""
    n = artifact_filename("resume", company, title, "Utkarsh_Singh")
    assert n.endswith(".pdf")
    stem = n[:-4]
    assert stem.replace("_", "").isalnum(), n
    assert '"' not in n and "/" not in n and "," not in n


def test_no_leading_or_doubled_separators():
    n = artifact_filename("resume", "", "", "")
    assert n == "Resume.pdf"
    assert not n.startswith("_")
    assert "__" not in artifact_filename("resume", "A  B", "C,, D", "X")


def test_length_is_bounded_for_the_header():
    n = artifact_filename("resume", "C" * 200, "T" * 200, "O" * 50)
    assert len(n) <= 124, "S3 rejects an over-long ResponseContentDisposition"
    assert n.endswith(".pdf")


def test_missing_fields_still_yield_a_usable_name():
    assert artifact_filename("resume", None, None, "Utkarsh_Singh") == "Utkarsh_Singh_Resume.pdf"
    assert artifact_filename("cover_letter", "Stripe", None) == "Stripe_CoverLetter.pdf"
