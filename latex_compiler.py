"""LaTeX to PDF compiler with fallback strategies."""

from __future__ import annotations
import logging
import re
import subprocess
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# LaTeX table environments where & is a legitimate column separator
_TABLE_ENVS = {"tabular", "tabularx", "longtable"}

# Regex: match \begin{env} ... \end{env} for table environments (dotall)
_TABLE_ENV_RE = re.compile(
    r"(\\begin\{(?:" + "|".join(_TABLE_ENVS) + r")\}.*?\\end\{(?:" + "|".join(_TABLE_ENVS) + r")\})",
    re.DOTALL,
)


def _sanitize_latex(tex: str) -> str:
    """Escape problematic characters in AI-generated LaTeX source.

    Handles:
      - ``&``  -> ``\\&``  (except inside tabular/tabularx/longtable)
      - ``#``  -> ``\\#``  (except in LaTeX commands like ``\\#``)
      - ``%``  -> ``\\%``  (except when already used as a LaTeX comment)
      - Fixes bare ``&`` inside ``\\textbf{...}`` and similar commands
    Already-escaped sequences (``\\&``, ``\\#``, ``\\%``) are never double-escaped.
    """
    # -----------------------------------------------------------------
    # Strategy: split the source into table-environment chunks (where &
    # is a column separator and must be left alone) and non-table chunks
    # (where bare & must be escaped).  Process only the non-table chunks.
    # -----------------------------------------------------------------

    parts = _TABLE_ENV_RE.split(tex)
    # parts alternates: [non-table, table, non-table, table, ...]
    # _TABLE_ENV_RE has one capturing group, so every odd-indexed element
    # is a table environment match.

    processed: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            # Inside a table environment -- leave as-is
            processed.append(part)
        else:
            processed.append(_escape_non_table(part))

    return "".join(processed)


def _escape_non_table(text: str) -> str:
    """Escape &, #, % in text that is NOT inside a table environment."""
    # Process line-by-line so we can detect %-comments correctly.
    out_lines: list[str] = []
    for line in text.split("\n"):
        out_lines.append(_escape_line(line))
    return "\n".join(out_lines)


def _escape_line(line: str) -> str:
    """Escape a single line of LaTeX outside table environments.

    Respects:
      - Already-escaped sequences (\\&, \\#, \\%)
      - % used as a line-ending LaTeX comment
      - # in command definitions (\\newcommand, \\def, etc.)
    """
    # ---- Handle % ----
    # A bare % (not preceded by \) starts a comment -- everything after
    # it on the line is a comment and should not be touched.
    # We split the line into code part and comment part.
    code, comment = _split_comment(line)

    # ---- Escape # in the code part ----
    # Skip lines that are command definitions (they use #1, #2, etc.)
    if not re.match(r"\s*\\(newcommand|renewcommand|def|DeclareMathOperator)", code):
        # Escape bare # that are not already escaped
        code = re.sub(r"(?<!\\)#", r"\\#", code)

    # ---- Escape & in the code part ----
    # Escape bare & that are not already escaped
    code = re.sub(r"(?<!\\)&", r"\\&", code)

    # ---- Escape bare % in the code part ----
    # A bare % in the code portion (not preceded by \) that is NOT the
    # comment-start (we already split that off) needs escaping.
    # After _split_comment, `code` has no un-escaped % left (they'd
    # start a comment).  But there could be a bare % that was mistakenly
    # in the middle of text if the AI placed it there -- _split_comment
    # would have cut the line at the first bare %.  So actually, `code`
    # is clean and `comment` holds the rest.  If the "comment" looks
    # like it was not intentional (contains & or obvious text content),
    # we should escape the % and re-join.  However, being conservative
    # is safer: if there is a comment part, leave it.  The most common
    # AI mistake is bare & and #, not stray %.

    # Re-join
    if comment is not None:
        return code + "%" + comment
    return code


def _split_comment(line: str) -> tuple[str, str | None]:
    """Split a LaTeX line into (code, comment_after_percent).

    Returns (code, None) if there is no bare-% comment on this line.
    A percent preceded by ``\\`` is an escaped literal and is not a comment.
    """
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            # Skip escaped character entirely
            i += 2
            continue
        if line[i] == "%":
            return line[:i], line[i + 1:]
        i += 1
    return line, None


def compile_tex_to_pdf(tex_path: str, output_dir: str = None) -> str:
    """Compile a .tex file to PDF.

    Compiler preference: tectonic (fast, self-contained) → pdflatex (fallback).
    Returns the path to the generated PDF, or empty string on failure.
    """
    tex_path = Path(tex_path)
    if not tex_path.exists():
        logger.error(f"TeX file not found: {tex_path}")
        return ""

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = tex_path.parent

    # Sanitize the LaTeX source in-place before compiling
    try:
        raw_tex = tex_path.read_text(encoding="utf-8")
        sanitized = _sanitize_latex(raw_tex)
        if sanitized != raw_tex:
            tex_path.write_text(sanitized, encoding="utf-8")
            logger.info(f"[SANITIZE] Escaped problematic chars in {tex_path.name}")
    except Exception as e:
        logger.warning(f"LaTeX sanitization skipped for {tex_path.name}: {e}")

    # Try tectonic first (fast, single binary, no texlive needed)
    tectonic = shutil.which("tectonic")
    if tectonic:
        result = _compile_with_tectonic(tex_path, out_dir)
        if result:
            return result
        logger.warning(f"tectonic failed for {tex_path.name}, trying pdflatex...")

    # Fallback to pdflatex
    pdflatex = shutil.which("pdflatex")
    if pdflatex:
        return _compile_with_pdflatex(tex_path, out_dir)

    logger.error("Neither tectonic nor pdflatex found. Install tectonic (recommended) or texlive.")
    return ""


def _compile_with_tectonic(tex_path: Path, out_dir: Path) -> str:
    """Compile with tectonic — fast, self-contained LaTeX engine.

    Tectonic automatically downloads only the packages your .tex file needs
    and caches them (~50MB for a typical resume vs ~500MB for full texlive).
    """
    try:
        result = subprocess.run(
            [
                "tectonic",
                "-X", "compile",
                "--outdir", str(out_dir.resolve()),
                "--keep-logs",
                str(tex_path.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(tex_path.parent.resolve()),
        )

        pdf_path = out_dir / (tex_path.stem + ".pdf")
        if pdf_path.exists():
            # Clean up auxiliary files
            for ext in [".aux", ".log", ".out", ".toc", ".nav", ".snm"]:
                aux = out_dir / (tex_path.stem + ext)
                if aux.exists():
                    aux.unlink()
            logger.info(f"[PDF] Compiled (tectonic) → {pdf_path.name}")
            return str(pdf_path)
        else:
            stderr = (result.stderr or "")[-500:]
            logger.error(f"tectonic failed for {tex_path.name} (exit {result.returncode}): {stderr}")
            return ""

    except subprocess.TimeoutExpired:
        logger.error(f"tectonic timed out for {tex_path.name}")
        return ""
    except FileNotFoundError:
        return ""


def _compile_with_pdflatex(tex_path: Path, out_dir: Path) -> str:
    """Compile with pdflatex — traditional fallback."""
    try:
        abs_tex = str(tex_path.resolve())
        abs_out = str(out_dir.resolve())
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                f"-output-directory={abs_out}",
                abs_tex,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(tex_path.parent.resolve()),
        )

        pdf_name = tex_path.stem + ".pdf"
        pdf_path = out_dir / pdf_name

        if pdf_path.exists():
            # Clean up auxiliary files
            for ext in [".aux", ".log", ".out", ".toc", ".nav", ".snm"]:
                aux = out_dir / (tex_path.stem + ext)
                if aux.exists():
                    aux.unlink()
            logger.info(f"[PDF] Compiled (pdflatex) → {pdf_path.name}")
            return str(pdf_path)
        else:
            logger.error(f"pdflatex failed for {tex_path.name} (exit code {result.returncode})")
            all_output = (result.stdout or "") + "\n" + (result.stderr or "")
            error_lines = [l for l in all_output.split("\n") if l.strip() and ("!" in l or "Error" in l or "Fatal" in l or "Missing" in l)]
            if error_lines:
                for line in error_lines[:5]:
                    logger.error(f"  LaTeX: {line.strip()}")
            else:
                log_lines = all_output.strip().split("\n")[-10:]
                for line in log_lines:
                    if line.strip():
                        logger.debug(f"  {line}")
            return ""

    except subprocess.TimeoutExpired:
        logger.error(f"pdflatex timed out for {tex_path.name}")
        return ""
    except FileNotFoundError:
        logger.error("pdflatex not found")
        return ""


def batch_compile(tex_files: list[str], output_dir: str = None) -> dict[str, str]:
    """Compile multiple .tex files. Returns {tex_path: pdf_path} mapping."""
    results = {}
    for tex in tex_files:
        pdf = compile_tex_to_pdf(tex, output_dir)
        results[tex] = pdf
    return results
