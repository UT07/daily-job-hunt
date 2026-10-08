"""Refuse LaTeX that reads or writes files when it is compiled.

Why this exists (audit, 2026-10-08): TeX can open any file the process can
read. Measured with tectonic 0.15: `X\\input{/tmp/x/secret.txt}Y` compiled
with exit 0 and the PDF read "XTOPSECRETVALUE Y". `tectonic --untrusted`
made no difference -- it disables shell escape, not file reads -- so the
engine cannot be relied on and the source has to be checked. LaTeX reaches a
compile from two places a user influences: a `.tex` résumé upload (its
preamble survives tailoring) and model output.

One function, used everywhere: at upload (`app.py /api/resumes/upload`
answers 400) and immediately before every engine run (`latex_compiler` and
`lambdas/pipeline/compile_latex.py` refuse to compile).

How it decides:

- Control-sequence names are read under BOTH tokenisations TeX may apply:
  letters only (normal catcodes, where `\\write@x` is `\\write` then `@x`)
  and letters plus `@` (after `\\makeatletter`, where `\\@@input` is one
  name). A name blocked under either reading is rejected.
- Anything that builds a control sequence from characters -- `\\csname`,
  `\\UseName`, `\\@nameuse`, `\\scantokens`, `^^` hex notation, `\\catcode`,
  `\\ExplSyntaxOn` -- is rejected outright, because it can spell any blocked
  name without the name appearing in the source.
- Commands that legitimately load files by NAME (`\\usepackage`,
  `\\documentclass`, `\\includegraphics`, ...) are allowed only with plain
  relative names: no leading `/` or `~`, no `..`, no `|`.
- Comments are NOT stripped first. `\\verb|%|\\input{...}` would hide a live
  command from a comment stripper; scanning everything over-rejects a
  commented-out `\\input`, which no real document in the repo has (see
  tests/unit/test_latex_safety.py, which measures the false-positive rate on
  every .tex in the repo and requires 0).

This is a blocklist, so it is a floor, not a proof. Measured against the
1,135 .tex files in the working tree and its worktrees (every generated
résumé and cover letter there), none uses any blocked command; the only
file-loading commands in real use are `\\documentclass{article}` and
`\\usepackage` of bundle packages.
"""
from __future__ import annotations

import re

# Read, write or open files, run code, or embed a file's bytes.
_IO_COMMANDS = {
    "input", "include", "InputIfFileExists", "IfFileExists",
    "@input", "@@input", "@iinput", "@input@", "input@path",
    "openin", "read", "readline", "closein",
    "openout", "write", "closeout", "immediate", "write18", "ShellEscape",
    "directlua", "latelua", "luaexec", "luadirect", "luacode",
    "special",
    "XeTeXpdffile", "XeTeXpicfile",
    "pdffiledump", "filedump", "pdfmdfivesum", "mdfivesum",
    "pdffilesize", "filesize", "pdffilemoddate", "filemoddate",
    "pdfobj", "pdfximage",
    "lstinputlisting", "verbatiminput", "VerbatimInput", "BVerbatimInput",
    "LVerbatimInput", "inputminted", "includepdf", "includepdfmerge",
    "import", "subimport", "inputfrom", "includefrom", "subinputfrom", "subincludefrom",
}

# Build a control sequence from characters, or change how characters are
# read: any of these can spell a blocked name without writing it.
_INDIRECTION = {
    "csname", "begincsname", "UseName", "@nameuse", "scantokens",
    "catcode", "ExplSyntaxOn",
}

_BLOCKED = _IO_COMMANDS | _INDIRECTION

_ENV_RE = re.compile(
    r"\\begin\s*\{\s*(filecontents\*?|VerbatimOut|verbatimwrite|luacode\*?)\s*\}"
)

# Commands whose argument names a file to load.
_PATH_ARG_RE = re.compile(
    r"\\(usepackage|RequirePackage|documentclass|LoadClass|includegraphics|"
    r"includesvg|includestandalone|bibliography|addbibresource|graphicspath)\*?"
    r"\s*(?:\[[^\]]*\]\s*)?\{((?:[^{}]|\{[^{}]*\})*)\}"
)
_BAD_PATH_RE = re.compile(r"(?:^|[\s,{])[/~]|\.\.|\|")
_FONT_PATH_RE = re.compile(r"\bPath\s*=\s*\{?\s*(?:[/~]|\.\.)")


class UnsafeLatex(ValueError):
    """The source uses a file or IO primitive and must not be compiled."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("; ".join(violations))


def find_unsafe_latex(tex: str) -> list[str]:
    """Every reason `tex` must not be compiled. Empty means none was found."""
    if not tex:
        return []
    found: list[str] = []

    names = set(re.findall(r"\\([A-Za-z]+)", tex)) | set(re.findall(r"\\([A-Za-z@]+)", tex))
    for name in sorted(names & _BLOCKED):
        kind = "builds commands from text" if name in _INDIRECTION else "reads or writes files"
        found.append(f"\\{name} is not allowed ({kind} at compile time)")

    if "^^" in tex:
        found.append("^^ character notation is not allowed (it can spell any command)")

    for env in sorted(set(_ENV_RE.findall(tex))):
        found.append(f"the {env} environment is not allowed (it writes files)")

    for cmd, arg in _PATH_ARG_RE.findall(tex):
        if _BAD_PATH_RE.search(arg):
            found.append(f"\\{cmd}{{{arg[:80]}}} may only name a file by a plain relative name")

    if _FONT_PATH_RE.search(tex):
        found.append("font Path= may not be absolute or contain ..")

    return found


def assert_safe_latex(tex: str) -> None:
    violations = find_unsafe_latex(tex)
    if violations:
        raise UnsafeLatex(violations)
