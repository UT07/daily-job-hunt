"""Tier 4c: Writing quality tests. Structural checks = MUST PASS, AI checks = REPORT ONLY."""
import pytest
from cover_letter import validate_cover_letter
from latex_compiler import check_brace_balance, check_section_completeness


class TestCoverLetterValidation:
    """MUST PASS: Cover letter structural checks."""
    def test_word_count_in_range(self):
        valid = " ".join(["word"] * 300)
        result = validate_cover_letter(valid)
        assert result["valid"] is True

    def test_word_count_too_short(self):
        short = " ".join(["word"] * 100)
        result = validate_cover_letter(short)
        assert not result["valid"]

    def test_word_count_too_long(self):
        long = " ".join(["word"] * 400)
        result = validate_cover_letter(long)
        assert not result["valid"]

    def test_banned_phrases_detected(self):
        text = "I am excited to apply for this role. " + " ".join(["word"] * 280)
        result = validate_cover_letter(text)
        assert not result["valid"]
        assert any("banned_phrase" in e for e in result["errors"])

    def test_dashes_detected(self):
        text = "Great opportunity \u2014 really great. " + " ".join(["word"] * 275)
        result = validate_cover_letter(text)
        assert not result["valid"]
        assert any("dashes" in e for e in result["errors"])


class TestBraceBalance:
    """MUST PASS: LaTeX brace balance before compilation."""
    def test_balanced(self):
        assert check_brace_balance("\\textbf{hello} \\textit{world}") is True

    def test_unbalanced_open(self):
        assert check_brace_balance("\\textbf{hello \\textit{world}") is False

    def test_unbalanced_close(self):
        assert check_brace_balance("hello} world") is False

    def test_escaped_braces_ignored(self):
        assert check_brace_balance("\\{escaped\\}") is True

    def test_nested(self):
        assert check_brace_balance("\\textbf{\\textit{nested}}") is True


class TestSectionCompleteness:
    """MUST PASS: All required resume sections present."""
    def test_all_sections_present(self):
        content = "\\section{Summary}\\section{Skills}\\section{Experience}\\section{Projects}\\section{Education}"
        assert check_section_completeness(content) is True

    def test_missing_section(self):
        content = "\\section{Summary}\\section{Skills}"
        assert check_section_completeness(content) is False


class TestPDFValidation:
    """MUST PASS: PDF output checks."""
    def test_file_size_bounds(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(5000) == "too_small"
        assert check_file_size(50000) is None
        assert check_file_size(600000) == "too_large"


class TestKeywordCoverage:
    """REPORT ONLY: Keyword extraction works."""
    def test_keyword_extraction_works(self):
        from utils.keyword_extractor import extract_keywords
        keywords = extract_keywords("We need Python and Kubernetes experience")
        assert "python" in keywords
        assert "kubernetes" in keywords
