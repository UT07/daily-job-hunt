#!/usr/bin/env python3
"""Create Google Doc resume templates from existing LaTeX resume content.

Run this once to create templates:
  python create_templates.py

It will:
1. Parse the LaTeX resumes to extract section content
2. Create 3 Google Doc templates (sre_devops, fullstack, cover_letter)
3. Apply formatting (bold headers, horizontal rules, bullets)
4. Insert {{PLACEHOLDER}} markers for tailorable sections
5. Share templates with you for review
6. Print the doc IDs to paste into config.yaml

After running:
1. Open the Google Doc links and review the formatting
2. Paste the template IDs into config.yaml → google_docs.templates
3. Set resume_format: "google_docs" in config.yaml when ready
"""

from __future__ import annotations
import re
import sys
import yaml
import logging
from pathlib import Path

from google_docs_client import authenticate, share_doc, get_doc_url

logging.basicConfig(level=logging.INFO, format="[%(levelname).1s] %(message)s")
logger = logging.getLogger(__name__)


# ── LaTeX Parsing ────────────────────────────────────────────────────────

def strip_latex(text: str) -> str:
    """Convert LaTeX markup to plain text."""
    # Remove common LaTeX commands
    text = re.sub(r"\\textbf\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textit\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\texttt\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textbar\\?", "|", text)
    text = re.sub(r"\\,", " ", text)
    text = re.sub(r"\\%", "%", text)
    text = re.sub(r"\\&", "&", text)
    text = re.sub(r"\\#", "#", text)
    text = re.sub(r"\\\$", "$", text)
    text = re.sub(r"\\hfill", "    ", text)
    text = re.sub(r"\\\\(\[[\d.]+em\])?", "", text)
    text = re.sub(r"\\vspace\{[^}]*\}", "", text)
    text = re.sub(r"\\item\s*", "• ", text)
    text = re.sub(r"\\Needspace\{[^}]*\}", "", text)
    text = re.sub(r"\{\\(Large|large|normalsize|small|footnotesize)\s+", "", text)
    # Clean up remaining braces
    text = re.sub(r"(?<!\\)[{}]", "", text)
    text = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})*", "", text)
    # Clean up whitespace
    text = re.sub(r"  +", " ", text)
    return text.strip()


def parse_latex_resume(tex_path: str) -> dict:
    """Parse a LaTeX resume into sections.

    Returns dict with keys: header_tagline, summary, skills, experience_entries,
    project_entries, education, certifications.
    """
    tex = Path(tex_path).read_text(encoding="utf-8")

    sections = {}

    # Header tagline (line after name)
    tagline_match = re.search(
        r"\\normalsize\s+(.*?)\\\\",
        tex, re.DOTALL,
    )
    if tagline_match:
        sections["header_tagline"] = strip_latex(tagline_match.group(1)).strip("() ")

    # Summary
    summary_match = re.search(
        r"%=+ SUMMARY =+\n\\section\*\{Summary\}\n(.*?)(?=\n%=|\n\\section)",
        tex, re.DOTALL,
    )
    if summary_match:
        sections["summary"] = strip_latex(summary_match.group(1)).strip()

    # Skills
    skills_match = re.search(
        r"%=+ TECHNICAL SKILLS =+\n\\section\*\{Technical Skills\}\n\\begin\{itemize\}(.*?)\\end\{itemize\}",
        tex, re.DOTALL,
    )
    if skills_match:
        items = re.findall(r"\\item\s+(.*?)(?=\\item|$)", skills_match.group(1), re.DOTALL)
        sections["skills_bullets"] = [strip_latex(item).strip() for item in items]

    # Experience entries
    exp_match = re.search(
        r"%=+ EXPERIENCE =+\n\\section\*\{Experience\}\n(.*?)(?=\n%=+ PROJECTS)",
        tex, re.DOTALL,
    )
    if exp_match:
        exp_text = exp_match.group(1)
        entries = re.split(r"\\jobentry", exp_text)
        experience = []
        for entry in entries:
            if not entry.strip():
                continue
            # Parse jobentry args: {Company}{Location}{Dates}{Title}
            args = re.findall(r"\{([^}]*)\}", entry)
            if len(args) >= 4:
                company, location, dates, title = args[0], args[1], args[2], args[3]
                title = strip_latex(title)
                # Extract bullets
                bullets_match = re.search(
                    r"\\begin\{itemize\}(.*?)\\end\{itemize\}", entry, re.DOTALL
                )
                bullets = []
                if bullets_match:
                    bullet_items = re.findall(
                        r"\\item\s+(.*?)(?=\\item|$)", bullets_match.group(1), re.DOTALL
                    )
                    bullets = [strip_latex(b).strip() for b in bullet_items]
                experience.append({
                    "company": strip_latex(company),
                    "location": strip_latex(location),
                    "dates": strip_latex(dates),
                    "title": title,
                    "bullets": bullets,
                })
        sections["experience"] = experience

    # Projects
    proj_match = re.search(
        r"%=+ PROJECTS =+\n\\section\*\{Featured Projects\}\n(.*?)(?=\n%=+ EDUCATION)",
        tex, re.DOTALL,
    )
    if proj_match:
        proj_text = proj_match.group(1)
        entries = re.split(r"\\projectentryurl|\\projectentry", proj_text)
        projects = []
        for entry in entries:
            if not entry.strip():
                continue
            args = re.findall(r"\{([^}]*)\}", entry)
            if len(args) >= 2:
                name = strip_latex(args[0])
                dates = strip_latex(args[1])
                # Tech stack is usually the last arg
                tech = strip_latex(args[-1]) if len(args) >= 5 else ""
                bullets_match = re.search(
                    r"\\begin\{itemize\}(.*?)\\end\{itemize\}", entry, re.DOTALL
                )
                bullets = []
                if bullets_match:
                    bullet_items = re.findall(
                        r"\\item\s+(.*?)(?=\\item|$)", bullets_match.group(1), re.DOTALL
                    )
                    bullets = [strip_latex(b).strip() for b in bullet_items]
                projects.append({
                    "name": name,
                    "dates": dates,
                    "tech": tech,
                    "bullets": bullets,
                })
        sections["projects"] = projects

    # Education
    edu_match = re.search(
        r"%=+ EDUCATION =+\n\\section\*\{Education\}\n(.*?)(?=\n%=+ CERTIFICATIONS)",
        tex, re.DOTALL,
    )
    if edu_match:
        sections["education_raw"] = strip_latex(edu_match.group(1)).strip()

    # Certifications
    cert_match = re.search(
        r"%=+ CERTIFICATIONS =+\n\\section\*\{Certifications\}\n\\begin\{itemize\}(.*?)\\end\{itemize\}",
        tex, re.DOTALL,
    )
    if cert_match:
        items = re.findall(r"\\item\s+(.*?)(?=\\item|$)", cert_match.group(1), re.DOTALL)
        sections["certifications"] = [strip_latex(item).strip() for item in items]

    return sections


# ── Google Doc Template Creation ─────────────────────────────────────────

def _pt(pts: int) -> dict:
    """Helper: magnitude in PT units for Google Docs API."""
    return {"magnitude": pts, "unit": "PT"}


def create_resume_template(docs_service, drive_service, parsed: dict,
                           title: str, folder_id: str = None) -> str:
    """Create a Google Doc resume template with formatting and placeholders.

    Returns the new document ID.
    """
    # Create blank doc
    body = {"title": title}
    doc = docs_service.documents().create(body=body).execute()
    doc_id = doc["documentId"]

    # Move to folder if specified
    if folder_id:
        drive_service.files().update(
            fileId=doc_id,
            addParents=folder_id,
            removeParents="root",
            fields="id, parents",
        ).execute()

    # Build the document content as a series of insert requests
    # We insert text from bottom to top (since indices shift after each insert)
    # So we build the full text first, then apply formatting

    lines = []

    # Header
    lines.append("Utkarsh Singh")
    lines.append("{{TAGLINE}}")
    lines.append("Dublin, Ireland | +353 892515620 | 254utkarsh@gmail.com")
    lines.append("github.com/UT07 | linkedin.com/in/utkarshsingh2001 | utworld.netlify.app")
    lines.append("")  # blank line

    # Summary
    lines.append("Summary")
    lines.append("{{SUMMARY}}")
    lines.append("")

    # Technical Skills
    lines.append("Technical Skills")
    lines.append("{{SKILLS_BULLETS}}")
    lines.append("")

    # Experience
    lines.append("Experience")
    if parsed.get("experience"):
        for i, exp in enumerate(parsed["experience"]):
            placeholder_prefix = ["CLOVER", "KRAKEN"][i] if i < 2 else f"EXP_{i+1}"
            lines.append(f"{exp['company']} -- {exp['location']}    {exp['dates']}")
            lines.append(f"{{{{{placeholder_prefix}_TITLE}}}}")
            lines.append(f"{{{{{placeholder_prefix}_BULLETS}}}}")
            lines.append("")

    # Projects
    lines.append("Featured Projects")
    if parsed.get("projects"):
        for i, proj in enumerate(parsed["projects"], 1):
            lines.append(f"{{{{{f'PROJECT_{i}_HEADER'}}}}}")
            lines.append(f"{{{{{f'PROJECT_{i}_BULLETS'}}}}}")
            lines.append("")

    # Education
    lines.append("Education")
    lines.append("{{EDUCATION}}")
    lines.append("")

    # Certifications
    lines.append("Certifications")
    lines.append("{{CERTIFICATIONS}}")

    # Join all text and insert at index 1 (start of doc body)
    full_text = "\n".join(lines)

    requests = [
        {
            "insertText": {
                "location": {"index": 1},
                "text": full_text,
            }
        }
    ]

    # Apply formatting after text insertion
    # First, calculate line offsets
    current_idx = 1
    line_ranges = []
    for line in lines:
        start = current_idx
        end = start + len(line)
        line_ranges.append((start, end, line))
        current_idx = end + 1  # +1 for newline

    # Format the header (name)
    name_start, name_end, _ = line_ranges[0]
    requests.append({
        "updateParagraphStyle": {
            "range": {"startIndex": name_start, "endIndex": name_end + 1},
            "paragraphStyle": {"alignment": "CENTER"},
            "fields": "alignment",
        }
    })
    requests.append({
        "updateTextStyle": {
            "range": {"startIndex": name_start, "endIndex": name_end},
            "textStyle": {"bold": True, "fontSize": _pt(16)},
            "fields": "bold,fontSize",
        }
    })

    # Center tagline + contact lines
    for i in range(1, 4):
        s, e, _ = line_ranges[i]
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": s, "endIndex": e + 1},
                "paragraphStyle": {"alignment": "CENTER"},
                "fields": "alignment",
            }
        })

    # Tagline font size
    tag_s, tag_e, _ = line_ranges[1]
    requests.append({
        "updateTextStyle": {
            "range": {"startIndex": tag_s, "endIndex": tag_e},
            "textStyle": {"fontSize": _pt(10)},
            "fields": "fontSize",
        }
    })

    # Contact lines font size
    for i in range(2, 4):
        s, e, _ = line_ranges[i]
        requests.append({
            "updateTextStyle": {
                "range": {"startIndex": s, "endIndex": e},
                "textStyle": {"fontSize": _pt(9)},
                "fields": "fontSize",
            }
        })

    # Section headers: bold + larger font + bottom border
    section_headers = ["Summary", "Technical Skills", "Experience",
                       "Featured Projects", "Education", "Certifications"]
    for s, e, text in line_ranges:
        if text in section_headers:
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": s, "endIndex": e},
                    "textStyle": {"bold": True, "fontSize": _pt(12)},
                    "fields": "bold,fontSize",
                }
            })
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": s, "endIndex": e + 1},
                    "paragraphStyle": {
                        "borderBottom": {
                            "color": {"color": {"rgbColor": {"red": 0, "green": 0, "blue": 0}}},
                            "width": _pt(0.5),
                            "padding": _pt(2),
                            "dashStyle": "SOLID",
                        },
                        "spaceBelow": _pt(4),
                        "spaceAbove": _pt(8),
                    },
                    "fields": "borderBottom,spaceBelow,spaceAbove",
                }
            })

    # Set default body font
    requests.append({
        "updateTextStyle": {
            "range": {"startIndex": 1, "endIndex": current_idx},
            "textStyle": {
                "weightedFontFamily": {"fontFamily": "Calibri"},
                "fontSize": _pt(10),
            },
            "fields": "weightedFontFamily,fontSize",
        }
    })

    # Re-apply section header formatting AFTER the global font (they need to override)
    for s, e, text in line_ranges:
        if text in section_headers:
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": s, "endIndex": e},
                    "textStyle": {"bold": True, "fontSize": _pt(12)},
                    "fields": "bold,fontSize",
                }
            })
    # Re-apply name formatting
    requests.append({
        "updateTextStyle": {
            "range": {"startIndex": name_start, "endIndex": name_end},
            "textStyle": {"bold": True, "fontSize": _pt(16)},
            "fields": "bold,fontSize",
        }
    })

    # Set page margins (narrow: 0.5in all around)
    requests.append({
        "updateDocumentStyle": {
            "documentStyle": {
                "marginTop": _pt(36),
                "marginBottom": _pt(36),
                "marginLeft": _pt(50),
                "marginRight": _pt(50),
            },
            "fields": "marginTop,marginBottom,marginLeft,marginRight",
        }
    })

    # Execute all requests
    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()

    logger.info(f"[TEMPLATE] Created resume template: {title} → {doc_id}")
    return doc_id


def create_cover_letter_template(docs_service, drive_service,
                                 title: str = "Cover Letter Template",
                                 folder_id: str = None) -> str:
    """Create a Google Doc cover letter template.

    Returns the new document ID.
    """
    doc = docs_service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]

    if folder_id:
        drive_service.files().update(
            fileId=doc_id,
            addParents=folder_id,
            removeParents="root",
            fields="id, parents",
        ).execute()

    lines = [
        "Utkarsh Singh",
        "Dublin, Ireland | +353 892515620 | 254utkarsh@gmail.com",
        "github.com/UT07 | linkedin.com/in/utkarshsingh2001",
        "",
        "{{DATE}}",
        "",
        "{{COMPANY}} Hiring Team",
        "Re: {{JOB_TITLE}}",
        "",
        "{{BODY}}",
        "",
        "Best regards,",
        "Utkarsh Singh",
    ]

    full_text = "\n".join(lines)
    requests = [
        {"insertText": {"location": {"index": 1}, "text": full_text}},
    ]

    # Calculate ranges
    current_idx = 1
    line_ranges = []
    for line in lines:
        start = current_idx
        end = start + len(line)
        line_ranges.append((start, end, line))
        current_idx = end + 1

    # Name: bold, centered, larger
    name_s, name_e, _ = line_ranges[0]
    requests.append({
        "updateParagraphStyle": {
            "range": {"startIndex": name_s, "endIndex": name_e + 1},
            "paragraphStyle": {"alignment": "CENTER"},
            "fields": "alignment",
        }
    })
    requests.append({
        "updateTextStyle": {
            "range": {"startIndex": name_s, "endIndex": name_e},
            "textStyle": {"bold": True, "fontSize": _pt(14)},
            "fields": "bold,fontSize",
        }
    })

    # Contact info: centered, smaller
    for i in range(1, 3):
        s, e, _ = line_ranges[i]
        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": s, "endIndex": e + 1},
                "paragraphStyle": {"alignment": "CENTER"},
                "fields": "alignment",
            }
        })
        requests.append({
            "updateTextStyle": {
                "range": {"startIndex": s, "endIndex": e},
                "textStyle": {"fontSize": _pt(9)},
                "fields": "fontSize",
            }
        })

    # Horizontal rule after contact info
    hr_s, hr_e, _ = line_ranges[2]
    requests.append({
        "updateParagraphStyle": {
            "range": {"startIndex": hr_s, "endIndex": hr_e + 1},
            "paragraphStyle": {
                "borderBottom": {
                    "color": {"color": {"rgbColor": {"red": 0, "green": 0, "blue": 0}}},
                    "width": _pt(0.5),
                    "padding": _pt(4),
                    "dashStyle": "SOLID",
                },
            },
            "fields": "borderBottom",
        }
    })

    # Global font
    requests.append({
        "updateTextStyle": {
            "range": {"startIndex": 1, "endIndex": current_idx},
            "textStyle": {
                "weightedFontFamily": {"fontFamily": "Calibri"},
                "fontSize": _pt(10),
            },
            "fields": "weightedFontFamily,fontSize",
        }
    })

    # Re-apply name override
    requests.append({
        "updateTextStyle": {
            "range": {"startIndex": name_s, "endIndex": name_e},
            "textStyle": {"bold": True, "fontSize": _pt(14)},
            "fields": "bold,fontSize",
        }
    })

    # Page margins
    requests.append({
        "updateDocumentStyle": {
            "documentStyle": {
                "marginTop": _pt(72),
                "marginBottom": _pt(72),
                "marginLeft": _pt(72),
                "marginRight": _pt(72),
            },
            "fields": "marginTop,marginBottom,marginLeft,marginRight",
        }
    })

    docs_service.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()

    logger.info(f"[TEMPLATE] Created cover letter template → {doc_id}")
    return doc_id


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    config_path = Path("config.yaml")
    if not config_path.exists():
        print("config.yaml not found. Run from the project root.")
        sys.exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f)

    gdocs_config = config.get("google_docs", {})
    creds_path = gdocs_config.get("credentials_path", "google_credentials.json")

    if not Path(creds_path).exists():
        print(f"Credentials not found at {creds_path}")
        print("Create a GCP service account and download the JSON key.")
        sys.exit(1)

    docs_svc, drive_svc = authenticate(creds_path)

    share_email = gdocs_config.get("share_with", "")
    folder_id = gdocs_config.get("folder_id", "") or None

    resumes_config = config.get("resumes", {})
    template_ids = {}

    # Create resume templates
    for resume_key, resume_info in resumes_config.items():
        tex_path = resume_info.get("tex_path", "")
        label = resume_info.get("label", resume_key)

        if not Path(tex_path).exists():
            print(f"  Skipping {resume_key}: {tex_path} not found")
            continue

        print(f"\nParsing {tex_path}...")
        parsed = parse_latex_resume(tex_path)

        print(f"  Sections found: {list(parsed.keys())}")
        print(f"  Experience entries: {len(parsed.get('experience', []))}")
        print(f"  Project entries: {len(parsed.get('projects', []))}")

        print(f"Creating Google Doc template for '{label}'...")
        doc_id = create_resume_template(
            docs_svc, drive_svc, parsed,
            title=f"Resume Template — {label}",
            folder_id=folder_id,
        )

        if share_email:
            share_doc(drive_svc, doc_id, share_email, role="writer")
            print(f"  Shared with {share_email}")

        template_ids[resume_key] = doc_id
        print(f"  Doc ID: {doc_id}")
        print(f"  URL: {get_doc_url(doc_id)}")

    # Create cover letter template
    print(f"\nCreating cover letter template...")
    cl_doc_id = create_cover_letter_template(
        docs_svc, drive_svc,
        title="Cover Letter Template",
        folder_id=folder_id,
    )
    if share_email:
        share_doc(drive_svc, cl_doc_id, share_email, role="writer")
    template_ids["cover_letter"] = cl_doc_id
    print(f"  Doc ID: {cl_doc_id}")
    print(f"  URL: {get_doc_url(cl_doc_id)}")

    # Print config snippet
    print(f"\n{'='*60}")
    print("Paste these into config.yaml → google_docs.templates:")
    print(f"{'='*60}")
    print("  templates:")
    for key, doc_id in template_ids.items():
        print(f"    {key}: \"{doc_id}\"")
    print(f"\nReview the templates at the URLs above.")
    print(f"When satisfied, set resume_format: \"google_docs\" in config.yaml.")


if __name__ == "__main__":
    main()
