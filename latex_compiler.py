"""LaTeX to PDF compiler with fallback strategies."""

from __future__ import annotations
import logging
import subprocess
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def compile_tex_to_pdf(tex_path: str, output_dir: str = None) -> str:
    """Compile a .tex file to PDF using pdflatex.

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

    # Check for pdflatex
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        logger.warning("pdflatex not found. Trying tectonic...")
        return _compile_with_tectonic(tex_path, out_dir)

    try:
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                f"-output-directory={out_dir}",
                str(tex_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(tex_path.parent),
        )

        pdf_name = tex_path.stem + ".pdf"
        pdf_path = out_dir / pdf_name

        if pdf_path.exists():
            # Clean up auxiliary files
            for ext in [".aux", ".log", ".out", ".toc", ".nav", ".snm"]:
                aux = out_dir / (tex_path.stem + ext)
                if aux.exists():
                    aux.unlink()
            logger.info(f"[PDF] Compiled -> {pdf_path.name}")
            return str(pdf_path)
        else:
            logger.error(f"pdflatex failed for {tex_path.name} (exit code {result.returncode})")
            # Log the actual error lines from stdout and stderr
            all_output = (result.stdout or "") + "\n" + (result.stderr or "")
            error_lines = [l for l in all_output.split("\n") if l.strip() and ("!" in l or "Error" in l or "Fatal" in l or "Missing" in l)]
            if error_lines:
                for line in error_lines[:5]:
                    logger.error(f"  LaTeX: {line.strip()}")
            else:
                # Fallback: last 10 lines
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


def _compile_with_tectonic(tex_path: Path, out_dir: Path) -> str:
    """Fallback: compile with tectonic if pdflatex isn't available."""
    tectonic = shutil.which("tectonic")
    if not tectonic:
        logger.error("Neither pdflatex nor tectonic found. Install texlive or tectonic.")
        return ""

    try:
        result = subprocess.run(
            ["tectonic", "-o", str(out_dir), str(tex_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        pdf_path = out_dir / (tex_path.stem + ".pdf")
        if pdf_path.exists():
            logger.info(f"[PDF] Compiled (tectonic) -> {pdf_path.name}")
            return str(pdf_path)
        else:
            logger.error(f"tectonic failed: {result.stderr[-500:]}")
            return ""

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error(f"tectonic error: {e}")
        return ""


def batch_compile(tex_files: list[str], output_dir: str = None) -> dict[str, str]:
    """Compile multiple .tex files. Returns {tex_path: pdf_path} mapping."""
    results = {}
    for tex in tex_files:
        pdf = compile_tex_to_pdf(tex, output_dir)
        results[tex] = pdf
    return results
