import pytest
from utils.canonical_hash import canonical_hash, normalize_company, normalize_whitespace


class TestNormalizeCompany:
    def test_strips_legal_suffixes(self):
        assert normalize_company("Acme Inc") == "acme"
        assert normalize_company("Acme Inc.") == "acme"
        assert normalize_company("Acme Ltd") == "acme"
        assert normalize_company("Acme Ltd.") == "acme"
        assert normalize_company("Acme GmbH") == "acme"
        assert normalize_company("Acme LLC") == "acme"

    def test_lowercase_and_strip(self):
        assert normalize_company("  ACME  ") == "acme"

    def test_preserves_meaningful_names(self):
        assert normalize_company("Google") == "google"
        assert normalize_company("Meta Platforms") == "meta platforms"


class TestNormalizeWhitespace:
    def test_collapses_runs(self):
        assert normalize_whitespace("hello   world") == "hello world"

    def test_strips_leading_trailing(self):
        assert normalize_whitespace("  hello  ") == "hello"

    def test_handles_newlines_and_tabs(self):
        assert normalize_whitespace("hello\n\n\tworld") == "hello world"


class TestCanonicalHash:
    def test_same_job_different_sources(self):
        h1 = canonical_hash("Acme Inc", "Backend Engineer", "Build APIs using Python and FastAPI")
        h2 = canonical_hash("Acme Inc.", "Backend Engineer", "Build APIs using Python and FastAPI")
        assert h1 == h2

    def test_different_descriptions_different_hash(self):
        h1 = canonical_hash("Acme", "Backend Engineer", "Build APIs using Python")
        h2 = canonical_hash("Acme", "Backend Engineer", "Build frontends using React")
        assert h1 != h2

    def test_whitespace_normalization(self):
        h1 = canonical_hash("Acme", "Backend Engineer", "Build APIs\n\nusing Python")
        h2 = canonical_hash("Acme", "Backend Engineer", "Build APIs using Python")
        assert h1 == h2

    def test_case_insensitive(self):
        h1 = canonical_hash("ACME", "BACKEND ENGINEER", "BUILD APIS")
        h2 = canonical_hash("acme", "backend engineer", "build apis")
        assert h1 == h2

    def test_returns_12_char_hex(self):
        h = canonical_hash("Acme", "Engineer", "Description")
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_full_description_not_truncated(self):
        base = "x" * 500
        h1 = canonical_hash("Acme", "Engineer", base + "AAAA")
        h2 = canonical_hash("Acme", "Engineer", base + "BBBB")
        assert h1 != h2

    def test_empty_description(self):
        h = canonical_hash("Acme", "Engineer", "")
        assert len(h) == 12

    def test_location_excluded(self):
        h1 = canonical_hash("Acme", "Engineer", "Build APIs")
        h2 = canonical_hash("Acme", "Engineer", "Build APIs")
        assert h1 == h2

    def test_handles_none_inputs(self):
        h = canonical_hash(None, None, None)
        assert len(h) == 12

    def test_title_whitespace_normalization(self):
        h1 = canonical_hash("Acme", "Backend  Engineer", "desc")
        h2 = canonical_hash("Acme", "Backend Engineer", "desc")
        assert h1 == h2
