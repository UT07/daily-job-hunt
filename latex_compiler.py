"""LaTeX to PDF compiler with fallback strategies."""

from __future__ import annotations
import subprocess
import shutil
from pathlib import Path


def compile_tex_to_pdf(tex_path: str, output_dir: str = None) -> str:
    """Compile a .tex file to PDF using pdflatex.

    Returns the path to the generated PDF, or empty string on failure.
    """
    tex_path = Path(tex_path)
    if not tex_path.exists():
        print(f"  [ERROR] TeX file not found: {tex_path}")
        return ""

    if output_dir:
        out_dir = Path(output_dir)
    else:
        out_dir = tex_path.parent

    # Check for pdflatex
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        print("  [WARN] pdflatex not found. Trying tectonic...")
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
            print(f"  [PDF] Compiled -> {pdf_path.name}")
            return str(pdf_path)
        else:
            print(f"  [ERROR] pdflatex failed for {tex_path.name}")
            # Print last 20 lines of log for debugging
            log_lines = result.stdout.split("\n")[-20:]
            for line in log_lines:
                if line.strip():
                    print(f"    {line}")
            return ""

    except subprocess.TimeoutExpired:
        print(f"  [ERROR] pdflatex timed out for {tex_path.name}")
        return ""
    except FileNotFoundError:
        print("  [ERROR] pdflatex not found")
        return ""


def _compile_with_tectonic(tex_path: Path, out_dir: Path) -> str:
    """Fallback: compile with tectonic if pdflatex isn't available."""
    tectonic = shutil.which("tectonic")
    if not tectonic:
        print("  [ERROR] Neither pdflatex nor tectonic found. Install texlive or tectonic.")
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
            print(f"  [PDF] Compiled (tectonic) -> {pdf_path.name}")
            return str(pdf_path)
        else:
            print(f"  [ERROR] tectonic failed: {result.stderr[-500:]}")
            return ""

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  [ERROR] tectonic error: {e}")
        return ""


def batch_compile(tex_files: list[str], output_dir: str = None) -> dict[str, str]:
    """Compile multiple .tex files. Returns {tex_path: pdf_path} mapping."""
    results = {}
    for tex in tex_files:
        pdf = compile_tex_to_pdf(tex, output_dir)
        results[tex] = pdf
    return results
