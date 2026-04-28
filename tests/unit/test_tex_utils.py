from shared.tex_utils import tex_to_plaintext


class TestTexToPlaintext:
    def test_strips_simple_commands(self):
        tex = r"\textbf{Hello} \textit{world}"
        assert tex_to_plaintext(tex) == "Hello world"

    def test_strips_section_commands(self):
        tex = r"\section{Experience}\subsection{Acme Corp}"
        assert tex_to_plaintext(tex) == "Experience Acme Corp"

    def test_strips_comments(self):
        tex = "Visible text\n% This is a comment\nMore text"
        assert tex_to_plaintext(tex) == "Visible text\nMore text"

    def test_strips_environments_keeps_content(self):
        tex = r"\begin{itemize}\item First\item Second\end{itemize}"
        result = tex_to_plaintext(tex)
        assert "First" in result
        assert "Second" in result
        assert "itemize" not in result

    def test_collapses_whitespace(self):
        tex = "Line 1\n\n\n\nLine 2"
        result = tex_to_plaintext(tex)
        assert result == "Line 1\n\nLine 2"

    def test_handles_empty_input(self):
        assert tex_to_plaintext("") == ""
        assert tex_to_plaintext(None) == ""

    def test_real_cover_letter_excerpt(self):
        tex = r"""\documentclass{letter}
\begin{document}
\section*{Cover Letter}
Dear \textbf{Hiring Manager},

I am writing to apply for the \textit{Senior Engineer} position at Airbnb.
% Personal note: tweak per role

\section*{Experience}
\begin{itemize}
\item Led migration of 50M-user database
\item Shipped 3 major features
\end{itemize}

Sincerely, \\
John Doe
\end{document}"""
        result = tex_to_plaintext(tex)
        assert "Hiring Manager" in result
        assert "Senior Engineer" in result
        assert "Personal note" not in result  # comment stripped
        assert "documentclass" not in result
        assert "begin{itemize}" not in result
        assert "Led migration" in result
