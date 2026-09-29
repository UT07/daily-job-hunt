r"""An unescaped % in AI-generated resume text silently destroys the document.

Production, 2026-09-29: 2 of 10 resumes failed to compile. Both with

    ! File ended while scanning use of \textbf .
    Runaway argument?
    {MTTR by 35\section *{Technical Skills} \begin {itemize} \item \textbf \ETC.

The bullet said "reduce MTTR by 35%". The % started a LaTeX comment, which ate
the rest of the line INCLUDING the brace closing \textbf{, so the argument ran
on into the next section. Brace counting does not catch it — the } is present
in the file, LaTeX just never sees it.

The same line contained `\textbf{99.9\% uptime}`, correctly escaped, because
that text came from the base resume. Only the newly generated text was raw.

tailor_resume escaped # (after the Apr 9 macro incident) and & (R&D, AT&T) but
never %. Of the three it is the most dangerous, because # and & fail loudly at
the offending token while % fails somewhere else entirely, several lines later.
"""
import re
import sys

sys.path.insert(0, "lambdas/pipeline")


def _escape_body(tex: str) -> str:
    """Run the module's real body-escaping step over a document."""
    import tailor_resume
    return tailor_resume.escape_body_specials(tex)


PREAMBLE = "\n".join([
    r"\documentclass{article}",
    r"\newcommand{\jobentry}[4]{%",       # intentional line-continuation
    r"  \Needspace{3\baselineskip}%",      # intentional
    r"}",
])


def _doc(body: str) -> str:
    return PREAMBLE + "\n" + r"\begin{document}" + "\n" + body + "\n" + r"\end{document}"


def test_a_percent_in_body_text_is_escaped():
    out = _escape_body(_doc(r"Reduced \textbf{MTTR by 35%} across the fleet."))
    assert r"35\%" in out, "the % that destroyed two resumes in production is still raw"


def test_an_already_escaped_percent_is_not_double_escaped():
    out = _escape_body(_doc(r"Maintained \textbf{99.9\% uptime}."))
    assert r"99.9\%" in out
    assert r"\\%" not in out, "double-escaped into a literal backslash"


def test_preamble_percents_are_left_alone():
    r"""`{%` at end of line is a LaTeX line-continuation, not a typo.

    Escaping it would emit a literal % into the macro body and break every
    \jobentry — the same class of breakage the Apr 9 # incident caused.
    """
    out = _escape_body(_doc("nothing special here"))
    assert r"\newcommand{\jobentry}[4]{%" in out
    assert r"\Needspace{3\baselineskip}%" in out
    assert r"{\%" not in out


def test_hash_and_ampersand_still_escaped():
    """Do not regress what already worked."""
    out = _escape_body(_doc(r"Built in C# for R&D."))
    assert r"C\#" in out
    assert r"R\&D" in out


def test_the_exact_production_line_survives():
    line = (r"initiatives using Terraform, Helm, and CI/CD automation to maintain "
            r"\textbf{99.9\% uptime} and reduce \textbf{MTTR by 35%}.")
    out = _escape_body(_doc(line))
    body = out.split(r"\begin{document}")[1]
    # Every % in the body must now be escaped.
    assert not re.search(r"(?<!\\)%", body), f"unescaped % survives: {body!r}"
    # And the brace that % used to eat is still reachable.
    assert r"\textbf{MTTR by 35\%}" in out
