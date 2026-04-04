from unittest.mock import MagicMock, patch


class TestCheckFileSize:
    def test_too_small(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(5000) == "too_small"

    def test_at_lower_boundary(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(10_000) is None

    def test_normal_size(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(50000) is None

    def test_at_upper_boundary(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(500_000) is None

    def test_too_large(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(600000) == "too_large"

    def test_zero_bytes(self):
        from utils.pdf_validator import check_file_size
        assert check_file_size(0) == "too_small"


def _mock_path(size_bytes=50000):
    """Create a mock Path that reports the given file size."""
    mock_path_cls = MagicMock()
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.stat.return_value.st_size = size_bytes
    mock_path_cls.return_value = mock_path
    return mock_path_cls


class TestValidatePdf:
    def test_page_count_validation(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz") as mock_fitz, \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda self: 2
            mock_doc.load_page.return_value.get_text.return_value = (
                "Name\nSkills\nExperience " * 20
            )
            mock_fitz.open.return_value = mock_doc
            result = validate_pdf("/fake/path.pdf", expected_pages=2)
            assert result["valid"] is True

    def test_page_count_wrong(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz") as mock_fitz, \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda self: 3
            mock_doc.load_page.return_value.get_text.return_value = (
                "skills experience text " * 20
            )
            mock_fitz.open.return_value = mock_doc
            result = validate_pdf("/fake/path.pdf", expected_pages=2)
            assert result["valid"] is False
            assert any("page_count" in e for e in result["errors"])

    def test_file_not_found(self):
        from utils.pdf_validator import validate_pdf
        result = validate_pdf("/nonexistent/path.pdf")
        assert result["valid"] is False
        assert "file_not_found" in result["errors"]

    def test_fitz_not_installed_skips_content_validation(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz", None), \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            result = validate_pdf("/fake/path.pdf")
            assert result["valid"] is True
            assert any("pymupdf not installed" in w for w in result["warnings"])

    def test_too_little_text_extracted(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz") as mock_fitz, \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda self: 2
            mock_doc.load_page.return_value.get_text.return_value = "short"
            mock_fitz.open.return_value = mock_doc
            result = validate_pdf("/fake/path.pdf", expected_pages=2)
            assert result["valid"] is False
            assert any("text_extraction" in e for e in result["errors"])

    def test_missing_sections_are_warnings(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz") as mock_fitz, \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda self: 2
            # Long text but missing required section keywords
            mock_doc.load_page.return_value.get_text.return_value = "a" * 200
            mock_fitz.open.return_value = mock_doc
            result = validate_pdf("/fake/path.pdf", expected_pages=2)
            assert any("missing_section" in w for w in result["warnings"])

    def test_check_sections_disabled(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz") as mock_fitz, \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda self: 2
            mock_doc.load_page.return_value.get_text.return_value = "a" * 200
            mock_fitz.open.return_value = mock_doc
            result = validate_pdf("/fake/path.pdf", expected_pages=2, check_sections=False)
            assert not any("missing_section" in w for w in result["warnings"])

    def test_content_overflow_warning(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz") as mock_fitz, \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda self: 2

            def fake_load_page(n):
                p = MagicMock()
                if n == 1:
                    p.get_text.return_value = "This sentence ends abruptly with no punct"
                else:
                    p.get_text.return_value = (
                        "Name\nskills section\nexperience section with enough text " * 5
                    )
                return p

            mock_doc.load_page.side_effect = fake_load_page
            mock_fitz.open.return_value = mock_doc
            result = validate_pdf("/fake/path.pdf", expected_pages=2)
            assert any("content_overflow" in w for w in result["warnings"])

    def test_valid_pdf_no_warnings(self):
        from utils.pdf_validator import validate_pdf
        with patch("utils.pdf_validator.fitz") as mock_fitz, \
             patch("utils.pdf_validator.Path", _mock_path(50000)):
            mock_doc = MagicMock()
            mock_doc.__len__ = lambda self: 2

            def fake_load_page(n):
                p = MagicMock()
                if n == 1:
                    p.get_text.return_value = "More experience details and achievements."
                else:
                    p.get_text.return_value = (
                        "Name\nskills section\nexperience section with enough text " * 5
                    )
                return p

            mock_doc.load_page.side_effect = fake_load_page
            mock_fitz.open.return_value = mock_doc
            result = validate_pdf("/fake/path.pdf", expected_pages=2)
            assert result["valid"] is True
            assert result["errors"] == []
