"""LaTeX to PDF compiler with fallback strategies."""

from __future__ import annotations
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  LaTeX command whitelist — known-good commands for resume/cover-letter PDFs
# ---------------------------------------------------------------------------

KNOWN_COMMANDS = {
    "documentclass", "begin", "end", "usepackage", "newcommand", "renewcommand",
    "textbf", "textit", "emph", "underline", "textsc", "textrm", "textsf", "texttt",
    "section", "subsection", "subsubsection", "paragraph",
    "item", "hfill", "vspace", "hspace", "noindent", "centering",
    "small", "footnotesize", "large", "Large", "huge", "Huge",
    "href", "url", "color", "textcolor",
    "includegraphics", "input", "include",
    "setlength", "addtolength", "emergencystretch",
    "pagestyle", "thispagestyle", "fancyhf", "fancyhead", "fancyfoot",
    "geometry", "setmainfont", "setsansfont", "setmonofont",
    "faIcon", "faLinkedin", "faGithub", "faEnvelope", "faPhone", "faMapMarker",
    "raisebox", "makebox", "mbox", "parbox", "minipage",
    "tabularx", "multicolumn", "cline", "hline", "toprule", "midrule", "bottomrule",
    "newpage", "clearpage", "pagebreak",
}


def validate_latex_commands(content: str) -> list[str]:
    """Check for LaTeX commands not in the whitelist. Returns list of warnings."""
    commands = re.findall(r"\\([a-zA-Z]+)", content)
    unknown = set()
    for cmd in commands:
        if cmd not in KNOWN_COMMANDS:
            unknown.add(cmd)
    return [f"Unknown LaTeX command: \\{cmd}" for cmd in sorted(unknown)]


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
    """Escape &, #, % in text that is NOT inside a table environment.

    Preserves #1, #2 etc. inside \\newcommand definitions (which can span
    multiple lines enclosed in braces).
    """
    # Split into command-definition blocks (where # is a parameter) and regular text.
    # Strategy: track brace depth after \newcommand to find the definition body.
    lines = text.split("\n")
    out_lines: list[str] = []
    in_command_def = False
    brace_depth = 0

    for line in lines:
        stripped = line.strip()
        # Detect start of command definition
        if re.match(r"\s*\\(newcommand|renewcommand|def|DeclareMathOperator)", stripped):
            in_command_def = True
            brace_depth = 0

        if in_command_def:
            # Count braces to know when the definition ends
            for ch in line:
                if ch == '{':
                    brace_depth += 1
                elif ch == '}':
                    brace_depth -= 1
            # Don't escape # inside command definitions
            out_lines.append(line)
            if brace_depth <= 0:
                in_command_def = False
        else:
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
        # Fix any double-escapes (\\# -> \#)
        code = code.replace("\\\\#", "\\#")

    # ---- Escape & in the code part ----
    # Escape bare & that are not already escaped (avoid double-escaping \\&)
    code = re.sub(r"(?<!\\)&", r"\\&", code)
    # Fix any double-escapes that crept in (\\& -> \&)
    code = code.replace("\\\\&", "\\&")

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


def check_brace_balance(content: str) -> bool:
    """Hard gate: return False if braces are unbalanced.

    Skips escaped braces (\\{ and \\}).
    """
    depth = 0
    i = 0
    while i < len(content):
        if content[i] == '\\' and i + 1 < len(content) and content[i + 1] in '{}':
            i += 2  # Skip escaped braces
            continue
        if content[i] == '{':
            depth += 1
        elif content[i] == '}':
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


# Required resume sections for the completeness gate
REQUIRED_SECTIONS = ["summary", "skills", "experience", "projects", "education"]


def check_section_completeness(content: str) -> bool:
    """Check all required resume sections are present.

    Accepts both \\section{Name} and \\section*{Name} forms.
    """
    content_lower = content.lower()
    for section in REQUIRED_SECTIONS:
        if (f"\\section{{{section}}}" not in content_lower
                and f"\\section*{{{section}}}" not in content_lower):
            return False
    return True


def check_size_bounds(
    input_len: int,
    output_len: int,
    min_ratio: float = 0.6,
    max_ratio: float = 1.5,
) -> bool:
    """Output must be 60-150% of input size.

    Returns True if within bounds, False otherwise. Used as a WARNING gate only.
    """
    if input_len == 0:
        return output_len == 0
    ratio = output_len / input_len
    return min_ratio <= ratio <= max_ratio


def compile_tex_to_pdf(tex_path: str, output_dir: str = None) -> str:
    """Compile a .tex file to PDF with quality gates and rollback.

    Works on a copy of the .tex file to preserve the original.
    Applies hard gates (brace balance, section completeness) that block
    compilation on failure, and soft gates (size bounds) that warn only.

    Compiler preference: tectonic (fast, self-contained) -> pdflatex (fallback).
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

    # Work on a copy to preserve the original file
    work_copy = tex_path.with_suffix(".work.tex")
    shutil.copy2(tex_path, work_copy)
    try:
        raw_tex = work_copy.read_text(encoding="utf-8")
        sanitized = _sanitize_latex(raw_tex)

        # --- Hard gate: brace balance ---
        if not check_brace_balance(sanitized):
            logger.error(f"[HARD GATE] Brace imbalance in {tex_path.name} — compilation blocked")
            return ""

        # --- Hard gate: section completeness ---
        if not check_section_completeness(sanitized):
            missing = [
                s for s in REQUIRED_SECTIONS
                if f"\\section{{{s}}}" not in sanitized.lower()
                and f"\\section*{{{s}}}" not in sanitized.lower()
            ]
            logger.error(
                f"[HARD GATE] Missing required sections in {tex_path.name}: "
                f"{', '.join(missing)} — compilation blocked"
            )
            return ""

        # --- Soft gate: size bounds (warning only) ---
        if not check_size_bounds(len(raw_tex), len(sanitized)):
            ratio = len(sanitized) / len(raw_tex) if len(raw_tex) > 0 else 0
            logger.warning(
                f"[SIZE CHECK] Output/input ratio {ratio:.2f} out of bounds "
                f"(0.60-1.50) in {tex_path.name}"
            )

        # --- Soft gate: command whitelist (warning only) ---
        cmd_warnings = validate_latex_commands(sanitized)
        for w in cmd_warnings:
            logger.warning(f"[COMMAND CHECK] {w} in {tex_path.name}")

        # Write sanitized content to the work copy
        if sanitized != raw_tex:
            work_copy.write_text(sanitized, encoding="utf-8")
            logger.info(f"[SANITIZE] Escaped problematic chars in {tex_path.name}")

        # Compile the work copy
        pdf_path = _compile_work_copy(work_copy, tex_path, out_dir)
        return pdf_path

    except Exception as e:
        logger.error(f"Compilation failed for {tex_path.name}: {e}")
        return ""
    finally:
        # Always clean up the work copy
        if work_copy.exists():
            work_copy.unlink()


def _compile_work_copy(work_copy: Path, original_path: Path, out_dir: Path) -> str:
    """Try tectonic then pdflatex on the work copy. Returns PDF path or empty string."""
    # Try tectonic first (fast, single binary, no texlive needed)
    tectonic = shutil.which("tectonic")
    if tectonic:
        result = _compile_with_tectonic(work_copy, out_dir)
        if result:
            # Rename the PDF from work copy stem to original stem
            return _rename_pdf(result, original_path.stem, out_dir)
        logger.warning(f"tectonic failed for {original_path.name}, trying pdflatex...")

    # Fallback to pdflatex
    pdflatex = shutil.which("pdflatex")
    if pdflatex:
        result = _compile_with_pdflatex(work_copy, out_dir)
        if result:
            return _rename_pdf(result, original_path.stem, out_dir)
        return ""

    logger.error("Neither tectonic nor pdflatex found. Install tectonic (recommended) or texlive.")
    return ""


def _rename_pdf(pdf_path: str, target_stem: str, out_dir: Path) -> str:
    """Rename a compiled PDF from work copy name to the original file's name."""
    pdf = Path(pdf_path)
    expected_name = target_stem + ".pdf"
    if pdf.name != expected_name:
        final = out_dir / expected_name
        shutil.move(str(pdf), str(final))
        return str(final)
    return pdf_path


def _compile_with_tectonic(tex_path: Path, out_dir: Path) -> str:
    """Compile with tectonic — fast, self-contained LaTeX engine.

    Tectonic automatically downloads only the packages your .tex file needs
    and caches them (~50MB for a typical resume vs ~500MB for full texlive).
    """
    try:
        # Tectonic needs a writable cache dir. On Lambda, only /tmp is writable.
        env = os.environ.copy()
        env.setdefault("XDG_CACHE_HOME", "/tmp")

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
            env=env,
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
    """Compile with pdflatex — traditional fallback.

    Runs pdflatex from the directory containing the .tex file to avoid
    issues with spaces in paths when using -output-directory.
    """
    try:
        # Run from the tex file's directory with just the filename
        # This avoids path-with-spaces issues in -output-directory
        tex_dir = tex_path.parent.resolve()
        tex_name = tex_path.name
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                tex_name,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(tex_dir),
        )

        pdf_name = tex_path.stem + ".pdf"
        # PDF is generated in the same dir as the tex file (cwd)
        pdf_path = tex_dir / pdf_name

        if pdf_path.exists():
            # Move to out_dir if different from tex_dir
            if out_dir.resolve() != tex_dir:
                final_path = out_dir / pdf_name
                import shutil as _shutil
                _shutil.move(str(pdf_path), str(final_path))
                pdf_path = final_path
            # Clean up auxiliary files
            for ext in [".aux", ".log", ".out", ".toc", ".nav", ".snm"]:
                aux = tex_dir / (tex_path.stem + ext)
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
