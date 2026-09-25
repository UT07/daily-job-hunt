"""Resume bullet extraction, indexing, and JD-driven retrieval.

Retrieved bullets are facts already present in the user's own resume, so
grounding tailoring in them reduces fabrication. This is retrieval used as a
hallucination-mitigation control, to be measured by the existing
_check_fabrication guard (resume_scorer.py) in a follow-up task.

Import resolution for `ai_helper` mirrors embeddings.py, store.py, and
agents/_ai_helper.py: this package lives inside the pipeline Lambdas' own
CodeUri (template.yaml: CodeUri: lambdas/pipeline/), which SAM/CFN flattens
into /var/task at deploy time, making `ai_helper` a flat sibling module
there -- identical to the shape tests/conftest.py creates by putting
lambdas/pipeline on sys.path. The container-image Lambda (Dockerfile.lambda)
instead ships the whole lambdas/ package tree, where only the qualified
`lambdas.pipeline` import resolves. Try the flat import first since it
covers both tests and the deployed zip Lambda; fall back to the qualified
import only for the container-image shape. Never spell this as
`from lambdas.pipeline.ai_helper import ...` -- that form cannot resolve
once CodeUri flattens this directory in the zip-based pipeline Lambdas (see
tests/unit/test_deploy_path_parity.py, which scans every .py file under
lambdas/pipeline/ for exactly this mistake).

--------------------------------------------------------------------------
Macro assumptions verified against real data before implementing (Task 16's
own instructions required this, given the risk of a silent no-op).

The task-16 design brief for this module assumed plain `\\section{...}`
headings and a dedicated `\\resumeItem{...}` bullet macro. Neither exists
anywhere in this repo: `grep -rl resumeItem` across the whole tree --
including old output/ dirs and worktrees -- returns zero hits. Inspecting
both real base resumes (resumes/fullstack.tex, resumes/sre_devops.tex) and
the two live rows in Supabase's user_resumes.tex_content (verbatim copies of
those same files, confirmed 2026-09-25 via a read-only query) shows they all
use:

  - `\\section*{Name}` -- WITH the star, styled by \\titleformat{\\section}
    in the resume preamble.
  - plain `\\begin{itemize}...\\end{itemize}` blocks of `\\item ...` text --
    the exact convention tailor_resume.py._check_required_sections and
    tailorer.py.extract_base_sections's own `_extract_itemize_bullets`
    already parse elsewhere in this codebase. There is no per-bullet macro
    at all; job/project headers use `\\jobentry{...}`/`\\projectentry{,url}`
    macros, but the bullets themselves are just `\\item` lines under those
    headers.

extract_bullets() below matches that real shape instead of the brief's
invented one. Had the brief's regexes shipped unmodified, extract_bullets()
would have silently returned [] against both real resumes and both live
user_resumes rows, index_bullets() would have indexed 0 rows, and
retrieve_evidence() would always come back empty -- a no-op safety control
that would still have passed every one of the brief's own tests, because
those tests were written against the brief's own invented macro rather than
real data. tests/unit/test_retrieval_bullets.py now fixtures a verbatim
excerpt of resumes/fullstack.tex to guard against exactly that failure mode.

A second real defect turned up while verifying against that same resume:
its Certifications section nests two levels of braces inside \\href's
second argument (`\\href{url}{\\textbf{\\textit{Name}}}`). A flat `[^}]*`
regex-based stripper stops at the FIRST closing brace, which mis-parses
that nesting and silently drops the certification's name, keeping only the
trailing "Issued <date>" text -- data loss in the exact feature meant to
ground tailoring in real facts. _strip_href/_unwrap_command below do a
small brace-depth scan instead of a flat regex specifically to survive
this.
"""
import logging
import re

from retrieval.embeddings import embed, embed_batch
from retrieval.store import similar_bullets

try:
    import ai_helper  # flat import -- resolves under pytest and in the zip Lambda
except ImportError:
    from lambdas.pipeline import ai_helper  # container-image shape only

logger = logging.getLogger()

# Real section headings are \section*{Name} (see module docstring), not the
# design brief's plain \section{Name}. The optional `\*?` accepts either
# spelling so a future starred/unstarred change either way still matches.
_SECTION = re.compile(r"\\section\*?\{([^}]*)\}")

# Real bullets are `\item ...` inside \begin{itemize}...\end{itemize} blocks
# -- there is no `\resumeItem{...}` macro anywhere in this repo (see module
# docstring). An item runs until the next \item, the end of the itemize
# block, the next section heading, or end of string; \jobentry/\projectentry
# header lines between a section heading and its itemize block are simply
# not matched by either alternative below, so they're skipped rather than
# mistaken for bullets.
_TOKEN = re.compile(
    r"\\section\*?\{[^}]*\}|\\item\s+.*?(?=\\item\b|\\end\{itemize\}|\\section\*?\{|\Z)",
    re.DOTALL,
)
_ITEM_TEXT = re.compile(r"\\item\s+(.*)", re.DOTALL)

_UNESCAPE = {r"\%": "%", r"\&": "&", r"\_": "_", r"\#": "#", r"\$": "$"}

# Inline formatting commands to unwrap to their argument's plain text.
# Mirrors tailorer.py's own private `_strip_latex` helper (used for the same
# "make an itemize bullet readable" job in the Google-Docs plain-text
# bridge), reimplemented standalone here so this module keeps its only two
# intra-repo dependencies (retrieval.embeddings, retrieval.store).
_TEXT_COMMANDS = ("textbf", "textit", "texttt", "textrm", "textsc", "textsf", "emph")


def _strip_comments(tex: str) -> str:
    """Drop LaTeX line comments so decorative banners can't be mistaken for
    section/item tokens.

    A `%` starts a comment unless escaped as `\\%`. The real resumes are
    full of `%==== EXPERIENCE ====`-style banners (see resumes/fullstack.tex)
    so this is a real, not theoretical, guard.
    """
    return re.sub(r"(?<!\\)%.*", "", tex)


def _unwrap_command(text: str, command: str) -> str:
    """Replace every `\\command{...}` with its brace-balanced argument.

    A plain `[^}]*` regex stops at the FIRST closing brace, which mis-parses
    a nested command like `\\textbf{\\textit{Name}}` -- confirmed against
    the real Certifications section of resumes/fullstack.tex. This scans
    forward counting brace depth instead of trusting the first `}` it sees.
    """
    marker = "\\" + command + "{"
    out = []
    i = 0
    while True:
        idx = text.find(marker, i)
        if idx == -1:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:idx])
        start = idx + len(marker)
        depth = 1
        j = start
        while j < len(text) and depth > 0:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        # depth == 0 means we found the true matching close; an unbalanced
        # tail (depth still > 0 at end of string) is passed through as-is
        # rather than swallowed.
        out.append(text[start:j - 1] if depth == 0 else text[start:j])
        i = j


def _strip_href(text: str) -> str:
    """Replace `\\href{url}{text}` with `text`, brace-balanced.

    Same nested-brace hazard as _unwrap_command: resumes/fullstack.tex's
    Certifications section nests `\\textbf{\\textit{...}}` inside \\href's
    second argument, which a flat `[^}]*` regex mis-parses and silently
    drops. Run before _unwrap_command so the (still-wrapped) inner content
    survives to be unwrapped by the later textbf/textit passes.
    """
    out = []
    i = 0
    marker = "\\href{"
    while True:
        idx = text.find(marker, i)
        if idx == -1:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:idx])
        j = idx + len(marker)
        depth = 1
        while j < len(text) and depth > 0:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
            j += 1
        if j < len(text) and text[j] == "{":
            start = j + 1
            k, depth = start, 1
            while k < len(text) and depth > 0:
                if text[k] == "{":
                    depth += 1
                elif text[k] == "}":
                    depth -= 1
                k += 1
            out.append(text[start:k - 1] if depth == 0 else text[start:k])
            i = k
        else:
            # Malformed \href with no second argument -- drop just the
            # \href{url} we already found instead of hanging on it.
            i = j


def _strip_latex(text: str) -> str:
    """Unwrap common inline LaTeX formatting into plain, readable text."""
    text = _strip_href(text)
    for command in _TEXT_COMMANDS:
        text = _unwrap_command(text, command)
    text = re.sub(r"\\textbar\\?", "|", text)
    text = re.sub(r"\\hfill\s*", " ", text)
    text = re.sub(r"\\,", "", text)
    text = re.sub(r"\\textless", "<", text)
    text = re.sub(r"\\textgreater", ">", text)
    # Any remaining (single-level, non-nesting) \command{...} or bare \command.
    text = re.sub(r"\\[a-zA-Z]+\*?\{[^}]*\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    return re.sub(r"[{}]", "", text)


def _clean(text: str) -> str:
    out = text.strip()
    for escaped, plain in _UNESCAPE.items():
        out = out.replace(escaped, plain)
    out = _strip_latex(out)
    return re.sub(r"\s+", " ", out).strip()


def extract_bullets(resume_tex: str) -> list[dict]:
    """Pull every `\\item` bullet out of the LaTeX, tagged with its `\\section*`.

    See module docstring: adapted from the design brief's
    `\\section`/`\\resumeItem` assumption to this repo's actual
    `\\section*`/itemize+`\\item` convention, verified against both
    resumes/*.tex and the live user_resumes rows.
    """
    tex = _strip_comments(resume_tex)
    results: list[dict] = []
    section = "Unknown"
    for token in _TOKEN.finditer(tex):
        chunk = token.group(0)
        heading = _SECTION.fullmatch(chunk)
        if heading:
            section = heading.group(1).strip()
            continue
        item = _ITEM_TEXT.match(chunk)
        if item:
            text = _clean(item.group(1))
            if text:
                results.append({"section": section, "text": text})
    return results


def _insert_bullets(rows: list[dict]) -> None:
    ai_helper.get_supabase().table("resume_bullets").insert(rows).execute()


def index_bullets(user_id: str, resume_tex: str, source_resume_id: str) -> int:
    """Embed and store every bullet in a resume. Returns the count indexed."""
    items = extract_bullets(resume_tex)
    if not items:
        return 0
    vectors = embed_batch([i["text"] for i in items])
    _insert_bullets([
        {
            "user_id": user_id,
            "section": item["section"],
            "text": item["text"],
            "embedding": vector,
            "source_resume_id": source_resume_id,
        }
        for item, vector in zip(items, vectors)
    ])
    return len(items)


def retrieve_evidence(user_id: str, jd_text: str, k: int = 8) -> list[dict]:
    """Top-k bullets from this user's own resume, ranked against the JD."""
    return similar_bullets(user_id, embed(jd_text), k=k)
