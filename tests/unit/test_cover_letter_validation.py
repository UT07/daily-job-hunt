"""Unit tests for cover letter validation."""
from cover_letter import validate_cover_letter, BANNED_PHRASES


def test_valid_cover_letter():
    result = validate_cover_letter(" ".join(["word"] * 300))
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["word_count"] == 300


def test_too_short():
    result = validate_cover_letter(" ".join(["word"] * 100))
    assert not result["valid"]
    assert any("word_count" in e for e in result["errors"])
    assert result["word_count"] == 100


def test_too_long():
    result = validate_cover_letter(" ".join(["word"] * 400))
    assert not result["valid"]
    assert any("word_count" in e for e in result["errors"])
    assert result["word_count"] == 400


def test_exact_lower_bound():
    result = validate_cover_letter(" ".join(["word"] * 280))
    assert result["valid"] is True


def test_exact_upper_bound():
    result = validate_cover_letter(" ".join(["word"] * 380))
    assert result["valid"] is True


def test_below_lower_bound():
    result = validate_cover_letter(" ".join(["word"] * 279))
    assert not result["valid"]


def test_above_upper_bound():
    result = validate_cover_letter(" ".join(["word"] * 381))
    assert not result["valid"]


def test_banned_phrases():
    text = "I am excited to apply. " + " ".join(["word"] * 280)
    result = validate_cover_letter(text)
    assert not result["valid"]
    assert any("banned_phrase" in e and "i am excited" in e for e in result["errors"])


def test_multiple_banned_phrases():
    text = "I am excited and passionate about synergy. " + " ".join(["word"] * 280)
    result = validate_cover_letter(text)
    assert not result["valid"]
    banned_errors = [e for e in result["errors"] if "banned_phrase" in e]
    assert len(banned_errors) >= 3  # "i am excited", "passionate", "synergy"


def test_banned_phrase_case_insensitive():
    text = "I AM EXCITED to join. " + " ".join(["word"] * 280)
    result = validate_cover_letter(text)
    assert not result["valid"]
    assert any("i am excited" in e for e in result["errors"])


def test_dashes_em_dash():
    text = "Great opportunity \u2014 really great. " + " ".join(["word"] * 275)
    result = validate_cover_letter(text)
    assert not result["valid"]
    assert any("dashes" in e for e in result["errors"])


def test_dashes_en_dash():
    text = "Great opportunity \u2013 really great. " + " ".join(["word"] * 275)
    result = validate_cover_letter(text)
    assert not result["valid"]
    assert any("dashes" in e for e in result["errors"])


def test_dashes_double_hyphen():
    text = "Great opportunity -- really great. " + " ".join(["word"] * 275)
    result = validate_cover_letter(text)
    assert not result["valid"]
    assert any("dashes" in e for e in result["errors"])


def test_single_hyphen_ok():
    """Single hyphens (e.g. in hyphenated words) should NOT trigger the dash error."""
    text = "Well-known full-stack developer. " + " ".join(["word"] * 278)
    result = validate_cover_letter(text)
    # Only check that no dash error is present (word count may vary)
    assert not any("dashes" in e for e in result["errors"])


def test_no_errors_all_clean():
    text = "This is a perfectly clean cover letter body. " + " ".join(["word"] * 275)
    result = validate_cover_letter(text)
    assert result["valid"] is True
    assert result["errors"] == []


def test_all_banned_phrases_are_lowercase():
    """Ensure BANNED_PHRASES list is all lowercase for correct matching."""
    for phrase in BANNED_PHRASES:
        assert phrase == phrase.lower(), f"Banned phrase not lowercase: {phrase}"
