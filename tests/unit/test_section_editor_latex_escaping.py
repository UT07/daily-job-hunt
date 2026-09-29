r"""Typing an ordinary character into the section editor broke the compile.

LaTeX has ten special characters — # $ % & ~ _ ^ \ { } — and `_escape_tex`
handled three. Section-editor content reaches the compiler through
`rebuild_tex_from_sections` with no other guard, so the rest went straight into
the document.

Measured 2026-09-29 by compiling through that exact path with tectonic. Things
a person plausibly types into a resume:

    "Cut cloud spend by $2,400"      FAILS  \end{itemize} invalid — the $
                                            opened math mode and swallowed the
                                            block
    "the user_profile service"       FAILS  Missing $ inserted
    "reduced O(n^2) to O(n)"         FAILS  Missing $ inserted
    "across CI\CD pipelines"         FAILS  Undefined control sequence
    "handled ~500 requests"          compiles, renders a non-breaking space —
                                     silently wrong output, which is worse than
                                     a failure
    "AT&T", "35%"                    already handled

Four of those took the whole document down, from one keystroke in a plain-text
field. #143 hardened AI-generated suggestions against the same characters;
typing was left open, and this closes it.

The escaping is idempotent because parsing does NOT unescape: `\&` survives out
of `parse_resume_sections` into what the editor receives, verified against the
real resume. A plain escape-everything pass would turn a round-tripped "R\&D"
into "R\textbackslash{}\&D", so an existing escape is left alone.

The header was worse than the bullets: `name` had no escaping at all, and the
contact line was split and re-joined without any either — so an ordinary email
like first_last@example.com broke the build.
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "lambdas/pipeline")

from lambdas.pipeline.parse_sections import (  # noqa: E402
    _escape_tex,
    _rebuild_header,
    rebuild_tex_from_sections,
)

# Every LaTeX special character, with what a user typing it means.
SPECIALS = {
    "$": "Cut cloud spend by $2,400 per month.",
    "_": "Maintained the user_profile service.",
    "^": "Reduced complexity from O(n^2) to O(n).",
    "\\": "Worked across CI\\CD pipelines.",
    "{": "Templated config via {env} placeholders.",
    "~": "Handled ~500 requests per second.",
    "&": "Worked at AT&T on R&D.",
    "%": "Improved latency by 35%.",
    "#": "Wrote C# and F# services.",
}


def _sections(bullet="A bullet.", name="Test User", contact="t@example.com"):
    return {
        "header": {"name": name, "title": "Engineer", "contact": contact},
        "summary": "An engineer.",
        "skills": [{"category": "Languages", "items": "Python"}],
        "experience": [{"company": "Acme", "title": "Eng", "dates": "2024",
                        "bullets": [bullet]}],
        "projects": [], "education": [], "certifications": [],
    }


# --- the escaper ------------------------------------------------------------

def test_every_special_character_is_escaped():
    for char, sentence in SPECIALS.items():
        out = _escape_tex(sentence)
        assert char not in out.replace("\\" + char, "").replace(
            "\\textbackslash{}", "").replace("\\textasciitilde{}", "").replace(
            "\\textasciicircum{}", ""), f"{char!r} survived unescaped in {out!r}"


def test_the_four_that_broke_the_compile():
    """Named individually because each produced a different LaTeX error."""
    assert _escape_tex("$2,400") == r"\$2,400"
    assert _escape_tex("user_profile") == r"user\_profile"
    assert _escape_tex("O(n^2)") == r"O(n\textasciicircum{}2)"
    assert _escape_tex("CI\\CD") == r"CI\textbackslash{}CD"


def test_tilde_is_escaped_even_though_it_compiled():
    """It rendered a non-breaking space instead of a tilde. Output that is
    quietly wrong is worse than output that fails loudly."""
    assert _escape_tex("~500") == r"\textasciitilde{}500"


def test_an_existing_escape_is_not_escaped_again():
    """Parsing does not unescape, so \\& arrives here from a round trip."""
    assert _escape_tex(r"R\&D") == r"R\&D"
    assert _escape_tex(r"35\%") == r"35\%"
    assert _escape_tex(r"C\#") == r"C\#"


def test_escaping_is_idempotent():
    """parse -> edit -> rebuild can run any number of times."""
    for sentence in SPECIALS.values():
        once = _escape_tex(sentence)
        assert _escape_tex(once) == once, f"second pass changed {once!r}"


def test_a_replacements_own_braces_are_not_re_escaped():
    r"""\textbackslash{} contains { and }. A naive sequence of .replace() calls
    escapes the backslash first and then mangles the braces it just inserted."""
    assert _escape_tex("\\") == r"\textbackslash{}"
    assert r"\textbackslash\{\}" not in _escape_tex("\\")


def test_empty_and_none_are_safe():
    assert _escape_tex("") == ""
    assert _escape_tex(None) is None


# --- the header, which had no escaping at all -------------------------------

def test_an_ordinary_email_no_longer_breaks_the_build():
    """first_last@example.com — an unescaped underscore in the contact line."""
    out = _rebuild_header({"name": "A B", "title": "Eng",
                           "contact": "first_last@example.com"})
    assert r"first\_last@example.com" in out


def test_a_name_with_an_ampersand_is_escaped():
    """`name` was interpolated raw — the one field on this path with no guard."""
    out = _rebuild_header({"name": "Smith & Jones", "title": "Eng", "contact": "a@b.com"})
    assert r"Smith \& Jones" in out


def test_the_contact_separator_survives():
    """Parts are escaped, the LaTeX separator joining them is not."""
    out = _rebuild_header({"name": "A", "title": "T",
                           "contact": "a_b@c.com | +353 1 234"})
    assert r"\textbar" in out, "the separator was escaped along with the parts"
    assert r"a\_b@c.com" in out


# --- end to end through the real rebuild ------------------------------------

def test_no_special_character_reaches_the_document_unescaped():
    """The property that matters: whatever is typed, the body is compilable.

    Compilation itself is verified separately with tectonic — 12/12 cases
    including every special character at once — but that needs a LaTeX
    toolchain, so the unit test asserts the structural precondition.
    """
    import re
    # \ { } are excluded: backslash IS the command character, and the escape
    # sequences themselves contain braces (\textbackslash{}), so "appears
    # unescaped" is meaningless for them on a rendered line. They are covered
    # directly by test_the_four_that_broke_the_compile and
    # test_a_replacements_own_braces_are_not_re_escaped, and by the tectonic
    # run that compiles all twelve cases.
    checkable = {c: t for c, t in SPECIALS.items() if c not in "\\{}"}
    assert len(checkable) == 7, checkable.keys()
    for char, sentence in checkable.items():
        tex = rebuild_tex_from_sections(_sections(bullet=sentence), _MINIMAL_BASE)
        # Scope to the USER'S line. The document legitimately contains
        # unescaped specials that the rebuild itself emits — %==== SECTION ====
        # banners are LaTeX comments, macros use braces, every command starts
        # with a backslash. Asserting over the whole body measures the
        # generator, not the input, which is the mistake this test made first.
        items = [ln for ln in tex.splitlines() if ln.strip().startswith(r"\item")]
        assert items, "no \\item line — the bullet never reached the document"
        line = items[-1]
        bare = re.findall(r"(?<!\\)" + re.escape(char), line)
        assert not bare, (
            f"{char!r} reached the document unescaped in {line.strip()!r}"
        )


def test_everything_at_once():
    """The case that would have caught all four failures in one go."""
    tex = rebuild_tex_from_sections(
        _sections(bullet=r"$1 & 2% #3 _4 ^5 ~6 \7 {8}",
                  name="Smith & Jones", contact="a_b@c.com"),
        _MINIMAL_BASE,
    )
    for fragment in (r"\$1", r"\&", r"2\%", r"\#3", r"\_4",
                     r"\textasciicircum{}5", r"\textasciitilde{}6",
                     r"\textbackslash{}7", r"\{8\}"):
        assert fragment in tex, f"missing {fragment!r}"


_MINIMAL_BASE = r"""\documentclass{article}
\newcommand{\jobentry}[4]{#1 #2 #3 #4}
\newcommand{\projectentry}[3]{#1 #2 #3}
\begin{document}
\end{document}
"""
