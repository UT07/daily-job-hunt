"""Template engine for LaTeX resume templates.

Lists available templates and renders them by replacing placeholders
with user-supplied content sections.
"""

from pathlib import Path
from typing import Dict
import logging

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def list_templates() -> list[dict]:
    """List templates with id, name, description.

    Scans the templates/ directory for .tex files and extracts the
    description from a ``% Description:`` comment in the first 5 lines.
    """
    templates = []
    for p in sorted(TEMPLATES_DIR.glob("*.tex")):
        desc = ""
        for line in p.read_text().split("\n")[:5]:
            if line.startswith("% Description:"):
                desc = line.replace("% Description:", "").strip()
                break
        templates.append({
            "id": p.stem,
            "name": p.stem.replace("_", " ").title(),
            "description": desc,
        })
    return templates


def render_template(
    template_id: str,
    sections: Dict[str, str],
    user_name: str = "",
    contact_line: str = "",
    links_line: str = "",
) -> str:
    """Render a template by replacing placeholders. Returns complete LaTeX source.

    Parameters
    ----------
    template_id : str
        Stem name of the template file (e.g. ``"professional"``).
    sections : dict
        Mapping of section name to LaTeX content. Keys should match the
        placeholder names without braces, e.g. ``{"SUMMARY": "...", "SKILLS": "..."}``.
    user_name : str
        Candidate full name inserted into ``{{NAME}}``.
    contact_line : str
        Contact info line inserted into ``{{CONTACT_LINE}}``.
    links_line : str
        Links line inserted into ``{{LINKS_LINE}}``.

    Returns
    -------
    str
        Complete LaTeX source ready for compilation.

    Raises
    ------
    FileNotFoundError
        If the requested template does not exist.
    """
    path = TEMPLATES_DIR / f"{template_id}.tex"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {template_id}")

    tex = path.read_text(encoding="utf-8")

    replacements = {
        "{{NAME}}": user_name,
        "{{CONTACT_LINE}}": contact_line,
        "{{LINKS_LINE}}": links_line,
    }
    for key, value in sections.items():
        replacements["{{" + key + "}}"] = value

    for placeholder, value in replacements.items():
        tex = tex.replace(placeholder, value or "")

    return tex
