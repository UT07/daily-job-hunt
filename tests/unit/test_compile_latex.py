"""Unit tests for LaTeX quality gates and compilation rollback."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure the project root is importable
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from latex_compiler import (
    check_brace_balance,
    check_section_completeness,
    check_size_bounds,
    compile_tex_to_pdf,
    validate_latex_commands,
    KNOWN_COMMANDS,
    REQUIRED_SECTIONS,
)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _make_complete_tex(extra_content: str = "") -> str:
    """Build a minimal LaTeX resume with all required sections."""
    return rf"""\documentclass[11pt]{{article}}
\begin{{document}}

\section*{{Summary}}
Experienced software engineer with 5+ years.

\section*{{Skills}}
Python, AWS, React, Docker, Kubernetes

\section*{{Experience}}
\textbf{{Senior Engineer}} --- Acme Corp (2022--Present)
\begin{{itemize}}
\item Built scalable microservices
\end{{itemize}}

\section*{{Projects}}
\textbf{{NaukriBaba}} --- Automated job search pipeline

\section*{{Education}}
B.Tech Computer Science, 2018

{extra_content}
\end{{document}}"""


# ---------------------------------------------------------------------------
#  test_brace_balance_hard_gate
# ---------------------------------------------------------------------------

class TestBraceBalance:
    """Tests for check_brace_balance — hard gate."""

    def test_balanced_braces(self):
        assert check_brace_balance(r"\section{Hello} \textbf{world}") is True

    def test_empty_string(self):
        assert check_brace_balance("") is True

    def test_no_braces(self):
        assert check_brace_balance("plain text with no braces") is True

    def test_unbalanced_open(self):
        """Extra opening brace should fail."""
        assert check_brace_balance(r"\section{Hello") is False

    def test_unbalanced_close(self):
        """Extra closing brace should fail."""
        assert check_brace_balance(r"Hello}") is False

    def test_escaped_braces_ignored(self):
        r"""Escaped braces \{ and \} should not count."""
        assert check_brace_balance(r"\{escaped\}") is True

    def test_escaped_braces_with_real_braces(self):
        r"""Mix of escaped and real braces."""
        assert check_brace_balance(r"\section{text with \{ escaped \} inside}") is True

    def test_nested_braces(self):
        assert check_brace_balance(r"\textbf{\textit{nested}}") is True

    def test_deeply_nested(self):
        assert check_brace_balance("{{{{{deep}}}}}") is True

    def test_deeply_nested_unbalanced(self):
        assert check_brace_balance("{{{{{deep}}}}") is False

    def test_multiple_close_before_open(self):
        """Closing brace before any opening should fail immediately."""
        assert check_brace_balance(r"} {") is False

    def test_realistic_latex(self):
        tex = _make_complete_tex()
        assert check_brace_balance(tex) is True

    def test_realistic_latex_with_missing_close(self):
        tex = r"""\documentclass{article}
\begin{document}
\section{Unclosed section
\end{document}"""
        assert check_brace_balance(tex) is False


# ---------------------------------------------------------------------------
#  test_section_completeness
# ---------------------------------------------------------------------------

class TestSectionCompleteness:
    """Tests for check_section_completeness — hard gate."""

    def test_all_sections_present(self):
        tex = _make_complete_tex()
        assert check_section_completeness(tex) is True

    def test_missing_summary(self):
        tex = r"""\section{Skills}
\section{Experience}
\section{Projects}
\section{Education}"""
        assert check_section_completeness(tex) is False

    def test_missing_education(self):
        tex = r"""\section{Summary}
\section{Skills}
\section{Experience}
\section{Projects}"""
        assert check_section_completeness(tex) is False

    def test_starred_sections_accepted(self):
        """\\section*{Name} form should also work."""
        tex = r"""\section*{Summary}
\section*{Skills}
\section*{Experience}
\section*{Projects}
\section*{Education}"""
        assert check_section_completeness(tex) is True

    def test_mixed_starred_and_unstarred(self):
        tex = r"""\section{Summary}
\section*{Skills}
\section{Experience}
\section*{Projects}
\section{Education}"""
        assert check_section_completeness(tex) is True

    def test_case_insensitive(self):
        """Section names should match case-insensitively."""
        tex = r"""\section{SUMMARY}
\section{SKILLS}
\section{EXPERIENCE}
\section{PROJECTS}
\section{EDUCATION}"""
        assert check_section_completeness(tex) is True

    def test_empty_string(self):
        assert check_section_completeness("") is False

    def test_all_required_sections_listed(self):
        """Verify the constant has the expected sections."""
        assert set(REQUIRED_SECTIONS) == {"summary", "skills", "experience", "projects", "education"}


# ---------------------------------------------------------------------------
#  test_size_bounds
# ---------------------------------------------------------------------------

class TestSizeBounds:
    """Tests for check_size_bounds — warning gate."""

    def test_within_bounds_same_size(self):
        assert check_size_bounds(100, 100) is True

    def test_within_bounds_lower_edge(self):
        """60% of input should be exactly at the lower bound."""
        assert check_size_bounds(100, 60) is True

    def test_within_bounds_upper_edge(self):
        """150% of input should be exactly at the upper bound."""
        assert check_size_bounds(100, 150) is True

    def test_too_small(self):
        """Below 60% should fail."""
        assert check_size_bounds(100, 59) is False

    def test_too_large(self):
        """Above 150% should fail."""
        assert check_size_bounds(100, 151) is False

    def test_zero_input_zero_output(self):
        assert check_size_bounds(0, 0) is True

    def test_zero_input_nonzero_output(self):
        assert check_size_bounds(0, 10) is False

    def test_custom_bounds(self):
        assert check_size_bounds(100, 80, min_ratio=0.8, max_ratio=1.2) is True
        assert check_size_bounds(100, 70, min_ratio=0.8, max_ratio=1.2) is False
        assert check_size_bounds(100, 130, min_ratio=0.8, max_ratio=1.2) is False


# ---------------------------------------------------------------------------
#  test_compilation_preserves_original
# ---------------------------------------------------------------------------

class TestCompilationRollback:
    """Tests that compilation works on a copy and preserves the original .tex file."""

    def test_original_preserved_on_hard_gate_failure(self, tmp_path):
        """When a hard gate fails, original .tex must be untouched."""
        tex_content = r"""\documentclass{article}
\begin{document}
\section{Only one section — missing others
\end{document}"""
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_content)

        result = compile_tex_to_pdf(str(tex_file))
        assert result == ""
        # Original file must be unchanged
        assert tex_file.read_text() == tex_content
        # Work copy must be cleaned up
        assert not (tmp_path / "resume.work.tex").exists()

    def test_original_preserved_on_brace_imbalance(self, tmp_path):
        """Brace imbalance blocks compilation, original preserved."""
        tex_content = _make_complete_tex() + "\n{unclosed"
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_content)

        result = compile_tex_to_pdf(str(tex_file))
        assert result == ""
        assert tex_file.read_text() == tex_content
        assert not (tmp_path / "resume.work.tex").exists()

    def test_original_preserved_on_missing_sections(self, tmp_path):
        """Missing sections blocks compilation, original preserved."""
        # Has balanced braces but missing required sections
        tex_content = r"""\documentclass{article}
\begin{document}
\section{Summary}
Just a summary, nothing else.
\end{document}"""
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_content)

        result = compile_tex_to_pdf(str(tex_file))
        assert result == ""
        assert tex_file.read_text() == tex_content
        assert not (tmp_path / "resume.work.tex").exists()

    @patch("latex_compiler.shutil.which")
    @patch("latex_compiler.subprocess.run")
    def test_original_preserved_after_successful_compile(self, mock_run, mock_which, tmp_path):
        """After successful compilation, original .tex is unchanged."""
        tex_content = _make_complete_tex()
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_content)

        # Mock tectonic as available and producing a PDF
        mock_which.return_value = "/usr/bin/tectonic"

        def fake_run(*args, **kwargs):
            # Create a fake PDF (named after the work copy stem)
            pdf_path = tmp_path / "resume.work.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 fake")
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        mock_run.side_effect = fake_run

        result = compile_tex_to_pdf(str(tex_file), str(tmp_path))

        # Should have returned a valid PDF path
        assert result != ""
        assert Path(result).name == "resume.pdf"
        # Original .tex unchanged
        assert tex_file.read_text() == tex_content
        # Work copy cleaned up
        assert not (tmp_path / "resume.work.tex").exists()

    def test_work_copy_cleaned_up_on_exception(self, tmp_path):
        """Even if an exception occurs, the work copy should be removed."""
        tex_content = _make_complete_tex()
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_content)

        with patch("latex_compiler._sanitize_latex", side_effect=RuntimeError("boom")):
            result = compile_tex_to_pdf(str(tex_file))

        assert result == ""
        assert tex_file.read_text() == tex_content
        assert not (tmp_path / "resume.work.tex").exists()

    def test_file_not_found(self, tmp_path):
        """Non-existent file returns empty string."""
        result = compile_tex_to_pdf(str(tmp_path / "nonexistent.tex"))
        assert result == ""

    @patch("latex_compiler.shutil.which", return_value=None)
    def test_no_compiler_available(self, mock_which, tmp_path):
        """When neither tectonic nor pdflatex is found, returns empty string."""
        tex_content = _make_complete_tex()
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_content)

        result = compile_tex_to_pdf(str(tex_file))
        assert result == ""
        # Original preserved
        assert tex_file.read_text() == tex_content
        # Work copy cleaned
        assert not (tmp_path / "resume.work.tex").exists()

    @patch("latex_compiler.shutil.which")
    @patch("latex_compiler.subprocess.run")
    def test_sanitization_modifies_work_copy_not_original(self, mock_run, mock_which, tmp_path):
        """When sanitization changes content, only the work copy is modified."""
        # Include a bare & that will be escaped by sanitization
        tex_content = _make_complete_tex(extra_content="AT&T is a company.")
        tex_file = tmp_path / "resume.tex"
        tex_file.write_text(tex_content)

        mock_which.return_value = "/usr/bin/tectonic"

        def fake_run(*args, **kwargs):
            pdf_path = tmp_path / "resume.work.pdf"
            pdf_path.write_bytes(b"%PDF-1.4 fake")
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        mock_run.side_effect = fake_run

        result = compile_tex_to_pdf(str(tex_file), str(tmp_path))

        assert result != ""
        # The ORIGINAL should still have the bare &
        assert "AT&T" in tex_file.read_text()
        # Work copy should be gone
        assert not (tmp_path / "resume.work.tex").exists()


# ---------------------------------------------------------------------------
#  test_latex_command_whitelist
# ---------------------------------------------------------------------------

class TestLaTeXCommandWhitelist:
    """Tests for validate_latex_commands — warning gate."""

    def test_known_commands_pass(self):
        """All known commands should produce zero warnings."""
        content = r"\documentclass{article}\begin{document}\section{Test}\textbf{bold}\end{document}"
        assert validate_latex_commands(content) == []

    def test_unknown_command_flagged(self):
        """A typo like emphergencystretch should be flagged."""
        issues = validate_latex_commands(r"\emphergencystretch{1em}")
        assert len(issues) > 0
        assert "emphergencystretch" in issues[0]

    def test_single_known_command(self):
        assert validate_latex_commands(r"\textbf{hello}") == []

    def test_single_begin(self):
        assert validate_latex_commands(r"\begin{itemize}") == []

    def test_multiple_unknown_commands(self):
        """Multiple unknown commands each get a separate warning."""
        issues = validate_latex_commands(r"\fakecmd{x} \anotherfake{y}")
        assert len(issues) == 2
        names = {w.split("\\")[1] for w in issues}
        assert names == {"anotherfake", "fakecmd"}

    def test_empty_string(self):
        assert validate_latex_commands("") == []

    def test_no_commands(self):
        assert validate_latex_commands("plain text with no backslashes") == []

    def test_mixed_known_and_unknown(self):
        """Known commands pass; only unknown ones produce warnings."""
        content = r"\textbf{bold} \xyzzycommand{test} \section{ok}"
        issues = validate_latex_commands(content)
        assert len(issues) == 1
        assert "xyzzycommand" in issues[0]

    def test_warnings_sorted_alphabetically(self):
        """Warnings should be sorted by command name."""
        issues = validate_latex_commands(r"\zzzfake{} \aaafake{}")
        assert len(issues) == 2
        assert "aaafake" in issues[0]
        assert "zzzfake" in issues[1]

    def test_duplicate_unknown_counted_once(self):
        """Same unknown command used twice should only produce one warning."""
        issues = validate_latex_commands(r"\madeup{a} \madeup{b}")
        assert len(issues) == 1

    def test_full_resume_content(self):
        """A realistic resume with only known commands should pass clean."""
        tex = _make_complete_tex()
        issues = validate_latex_commands(tex)
        # _make_complete_tex uses documentclass, begin, end, section, textbf, item
        # All are in KNOWN_COMMANDS
        assert issues == []

    def test_known_commands_constant_has_essentials(self):
        """Verify the constant includes critical resume commands."""
        essentials = {"documentclass", "begin", "end", "section", "textbf",
                      "usepackage", "item", "href", "textit", "emph"}
        assert essentials.issubset(KNOWN_COMMANDS)
