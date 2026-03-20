"""
Create Google Doc resume templates (SRE and Fullstack variants).

Usage: .venv/bin/python3 create_templates.py

The script inserts all content and applies formatting via the Google Docs API,
then shares the docs with 254utkarsh@gmail.com.
"""
from __future__ import annotations
import sys
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Credentials / service bootstrap
# ---------------------------------------------------------------------------

CREDENTIALS_PATH = str(
    Path(__file__).parent / "google_credentials.json"
)
SHARE_EMAIL = "254utkarsh@gmail.com"


def _get_services():
    from googleapiclient.discovery import build
    from google_docs_client import _get_credentials

    creds = _get_credentials(CREDENTIALS_PATH)
    docs = build("docs", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return docs, drive


# ---------------------------------------------------------------------------
# Template content definitions
# ---------------------------------------------------------------------------

# Shared header info
NAME = "Utkarsh Singh"
CONTACT_LINE = "Dublin, Ireland | +353 892515620 | 254utkarsh@gmail.com"
LINKS_LINE = "github.com/UT07 | linkedin.com/in/utkarshsingh2001 | utworld.netlify.app"

# Per-variant config
VARIANTS = {
    "sre": {
        "title": "Resume Template — SRE/DevOps",
        "title_line": "{{TITLE_LINE}}",
        "clover_role": "Site Reliability / DevOps Engineer",
        "kraken_role": "Data Analyst Intern",
    },
    "fullstack": {
        "title": "Resume Template — Fullstack",
        "title_line": "{{TITLE_LINE}}",
        "clover_role": "Software Engineer / Site Reliability Engineer",
        "kraken_role": "Data Engineering Intern",
    },
}

# Static education content (same for both)
EDUCATION_BLOCKS = [
    {
        "school": "National College of Ireland",
        "location": "Dublin, Ireland",
        "dates": "Sep 2024 – Jan 2026",
        "degree": "MSc Cloud Computing",
        "bullets": [
            "Coursework: Cloud Architectures, Cloud DevOpsSec, Scalable Cloud Programming, "
            "Cloud Machine Learning, Data Governance/Compliance/Ethics, Research in Computing."
        ],
    },
    {
        "school": "Southern New Hampshire University",
        "location": "Manchester, NH",
        "dates": "Aug 2023 – May 2024",
        "degree": "BS Computer Science",
        "bullets": [
            "Coursework: Software Dev Lifecycle, Full-Stack Development, Database Systems, Software Testing."
        ],
    },
    {
        "school": "The University of Texas at Arlington",
        "location": "Arlington, TX",
        "dates": "Aug 2019 – May 2022",
        "degree": "BS Software Engineering (Coursework, Transferred)",
        "bullets": [
            "Coursework: Algorithms & DS, Operating Systems, Computer Networks, Information Security.",
            "Teaching Assistant: CSE 3318 Data Structures & Algorithms (50+ students).",
        ],
    },
]

CERTIFICATIONS = [
    {
        "text": "AWS Certified Solutions Architect — Professional (SAP-C02)  |  Issued Mar 2024",
        "url": "https://www.credly.com/badges/6e22a6c0-9922-49d5-b59f-34cc593c82c3/public_url",
    },
    {
        "text": "AWS Certified Developer — Associate (DVA-C01)  |  Issued Sep 2022",
        "url": "https://www.credly.com/badges/84671bf4-ce9f-4d8b-a0de-0ef606ad5646/public_url",
    },
    {
        "text": "AWS Certified Cloud Practitioner (CLF-C01)  |  Issued Jun 2022",
        "url": "https://www.credly.com/badges/e671b9de-e72a-48cb-82fc-33776c285174/public_url",
    },
]


# ---------------------------------------------------------------------------
# Helper: build the flat list of (text, style_hints) segments
# ---------------------------------------------------------------------------
# We build a document as a list of "paragraphs".  Each paragraph is a dict:
#   text        – the string to insert (no trailing newline; we add \n)
#   align       – LEFT | CENTER
#   bold        – True/False
#   italic      – True/False
#   size        – font size in pt (half-points in the API = size * 2)
#   heading     – True → treat as section heading (bold, 13pt, underline rule via border)
#   bullet      – True → list item
#   page_break  – True → insert a page break before this paragraph
#   space_before– pt spacing before paragraph
#   space_after – pt spacing after paragraph


def build_paragraphs(variant: str) -> list[dict]:
    v = VARIANTS[variant]
    paras = []

    def p(text, **kw):
        paras.append({"text": text, **kw})

    # ---- HEADER ----
    p(NAME,          align="CENTER", bold=True,  size=16, space_after=2)
    p(v["title_line"], align="CENTER", bold=False, size=11, space_after=2)
    p(CONTACT_LINE,  align="CENTER", bold=False, size=10, space_after=2)
    p(LINKS_LINE,    align="CENTER", bold=False, size=10, space_after=6)

    # ---- SUMMARY ----
    p("Summary",       heading=True, space_before=6,  space_after=4)
    p("{{SUMMARY}}",   size=11, space_after=8)

    # ---- TECHNICAL SKILLS ----
    p("Technical Skills", heading=True, space_before=6, space_after=4)
    p("{{SKILLS}}",       size=11, space_after=8)

    # ---- EXPERIENCE ----
    p("Experience", heading=True, space_before=6, space_after=4)

    # Clover
    p(f"Clover IT Services — New York, NY (Remote)\tJun 2022 – Jul 2024",
      bold=True, size=11, space_after=1)
    p(v["clover_role"], italic=True, size=11, space_after=2)
    p("{{CLOVER_BULLETS}}", size=11, space_after=6)

    # PAGE BREAK before Kraken
    p("Seattle Kraken (NHL) — Seattle, WA\tJun 2021 – May 2022",
      bold=True, size=11, page_break=True, space_after=1)
    p(v["kraken_role"], italic=True, size=11, space_after=2)
    p("{{KRAKEN_BULLETS}}", size=11, space_after=6)

    # ---- FEATURED PROJECTS ----
    p("Featured Projects", heading=True, space_before=6, space_after=4)

    for i in range(1, 4):
        p(f"{{{{PROJECT_{i}_TITLE}}}}", bold=True, size=11, space_after=1)
        p(f"{{{{PROJECT_{i}_BULLETS}}}}", size=11, space_after=6)

    # ---- EDUCATION ----
    p("Education", heading=True, space_before=6, space_after=4)

    for edu in EDUCATION_BLOCKS:
        p(f"{edu['school']}, {edu['location']}\t{edu['dates']}",
          bold=True, size=11, space_after=1)
        p(edu["degree"], bold=True, italic=True, size=11, space_after=2)
        for b in edu["bullets"]:
            p(b, bullet=True, size=11, space_after=2)

    # ---- CERTIFICATIONS ----
    p("Certifications", heading=True, space_before=6, space_after=4)
    for cert in CERTIFICATIONS:
        paras.append({
            "text": cert["text"],
            "url": cert["url"],
            "bullet": True,
            "size": 11,
            "space_after": 2,
        })

    return paras


# ---------------------------------------------------------------------------
# Core: create the doc and populate it
# ---------------------------------------------------------------------------

# Google Docs API uses "half-points" for font size (1pt = 2 half-pts)
PT = lambda pt: {"magnitude": pt, "unit": "PT"}
HALF_PT = lambda pt: pt  # font size field takes integer half-pts directly


def _pt_to_half(pt: float) -> int:
    return int(pt * 2)


FONT = "Calibri"
MARGIN_IN = 0.7 * 72  # 0.7 inches in points


def create_template(variant: str, docs, drive) -> tuple[str, str]:
    """Create one template doc. Returns (doc_id, url)."""
    v = VARIANTS[variant]
    paragraphs = build_paragraphs(variant)

    # 1. Create blank document
    doc = docs.documents().create(body={"title": v["title"]}).execute()
    doc_id = doc["documentId"]
    print(f"  Created doc: {doc_id}")

    # 2. Build all the text as a single string, tracking char ranges
    #    Strategy: insert text top-to-bottom, tracking current index.
    #    After each insertion we apply formatting to that range.

    requests = []

    # We'll build the document via a series of insertText + format requests.
    # Because each insertText shifts the index, we process in REVERSE order.
    # Build the full sequence first, then reverse for insertion.

    # Step A: compute the text blocks with their eventual index ranges
    # New doc starts with one empty paragraph at index 0 (char '\n' at index 0).
    # We insert AFTER index 0 so we keep inserting at index 1 each time
    # (earlier text gets pushed forward).
    # We'll insert in REVERSE order.

    # Build list of segments: each is the text for one paragraph
    segments = []
    for para in paragraphs:
        text = para.get("text", "")
        # Replace tab with spaces for right-alignment simulation via tab stop
        # (We'll use a tab character and set a right-aligned tab stop)
        segments.append((text, para))

    # Reversed: last paragraph inserted first at index 1, earlier ones push it forward
    # After reversing, we insert each at index 1.
    # Final order in doc will be: first paragraph ... last paragraph

    reversed_segments = list(reversed(segments))

    for text, para in reversed_segments:
        line = text + "\n"

        # Insert text first (at index 1)
        requests.append({
            "insertText": {
                "location": {"index": 1},
                "text": line,
            }
        })

        # Then insert page break at index 1 (pushes text forward,
        # so in the final doc the break appears BEFORE this paragraph)
        if para.get("page_break"):
            requests.append({
                "insertPageBreak": {
                    "location": {"index": 1}
                }
            })

    # 3. Execute the insertions
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()
    print(f"  Inserted text blocks")

    # 4. Now apply formatting. Re-read the doc to get actual indices.
    doc_content = docs.documents().get(documentId=doc_id).execute()

    # 5. Set document margins via DocumentStyle
    margin_requests = [{
        "updateDocumentStyle": {
            "documentStyle": {
                "marginTop":    PT(MARGIN_IN),
                "marginBottom": PT(MARGIN_IN),
                "marginLeft":   PT(MARGIN_IN),
                "marginRight":  PT(MARGIN_IN),
            },
            "fields": "marginTop,marginBottom,marginLeft,marginRight",
        }
    }]
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": margin_requests},
    ).execute()

    # 6. Apply paragraph-level and text-level formatting
    #    Walk the document paragraphs in order and match to our para list
    fmt_requests = _build_format_requests(doc_content, paragraphs)

    if fmt_requests:
        # Split into chunks of 100 to avoid request size limits
        chunk_size = 100
        for i in range(0, len(fmt_requests), chunk_size):
            chunk = fmt_requests[i:i + chunk_size]
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": chunk},
            ).execute()
        print(f"  Applied {len(fmt_requests)} formatting requests")

    # 7. Share with user email
    drive.permissions().create(
        fileId=doc_id,
        body={"type": "user", "role": "writer", "emailAddress": SHARE_EMAIL},
        sendNotificationEmail=False,
    ).execute()

    file_info = drive.files().get(fileId=doc_id, fields="webViewLink").execute()
    url = file_info.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")
    print(f"  Shared with {SHARE_EMAIL}")
    return doc_id, url


def _build_format_requests(doc_content: dict, paragraphs: list[dict]) -> list[dict]:
    """Match document paragraphs to our para list and emit format requests."""
    requests = []
    body_content = doc_content.get("body", {}).get("content", [])

    # Collect all paragraph structural elements (skip the initial empty one at top if any)
    doc_paras = []
    for elem in body_content:
        if "paragraph" in elem:
            doc_paras.append(elem)

    # The document will have:
    # - One trailing empty paragraph (always present at end, index after last \n)
    # - Possibly one initial empty paragraph before our text
    # Our text paragraphs come in order, one per line we inserted.
    # Because we inserted page breaks separately, those add structural elements too.
    # We need to match our `paragraphs` list to `doc_paras`.

    # Filter to paragraphs that have actual content (text runs)
    # Page breaks appear as paragraphs with a pageBreak structural element, not inline text
    content_paras = []
    for elem in doc_content.get("body", {}).get("content", []):
        if "paragraph" in elem:
            content_paras.append(elem)

    # Match: skip initial empty paragraph if present
    our_idx = 0  # index into paragraphs list
    doc_idx = 0  # index into content_paras

    # Skip the first empty paragraph (always in new doc)
    # Actually the document starts with our text now since we inserted at index 1.
    # There's always a trailing empty paragraph at the end.

    matched_pairs = []  # (our_para_dict, doc_para_elem)

    # Walk doc paragraphs; for each non-empty one, match to next our_para
    for doc_para in content_paras:
        if our_idx >= len(paragraphs):
            break
        # Check if this paragraph has text content
        para_obj = doc_para["paragraph"]
        text_in_para = ""
        for elem in para_obj.get("elements", []):
            if "textRun" in elem:
                text_in_para += elem["textRun"].get("content", "")
            elif "pageBreak" in elem:
                text_in_para += "\x0c"

        # Skip empty trailing paragraph
        if text_in_para.strip() == "" and text_in_para != "\x0c":
            continue

        # If this is a page break paragraph, skip it (we don't format those separately)
        if "\x0c" in text_in_para and text_in_para.strip() == "\x0c".strip():
            continue

        our_para = paragraphs[our_idx]
        matched_pairs.append((our_para, doc_para))
        our_idx += 1

    # Now emit formatting requests for each matched pair
    for our_para, doc_elem in matched_pairs:
        start = doc_elem["startIndex"]
        end = doc_elem["endIndex"]
        # end includes the trailing \n; text style applies up to end-1
        text_end = end - 1  # exclude the newline

        if text_end <= start:
            continue  # nothing to format

        size_pt = our_para.get("size", 11)
        is_heading = our_para.get("heading", False)
        if is_heading:
            size_pt = 13

        bold = our_para.get("bold", False) or is_heading
        italic = our_para.get("italic", False)
        align = our_para.get("align", "LEFT")
        space_before = our_para.get("space_before", 0)
        space_after = our_para.get("space_after", 0)
        is_bullet = our_para.get("bullet", False)

        # Text style
        requests.append({
            "updateTextStyle": {
                "range": {"startIndex": start, "endIndex": text_end},
                "textStyle": {
                    "weightedFontFamily": {"fontFamily": FONT},
                    "fontSize": {"magnitude": size_pt, "unit": "PT"},
                    "bold": bold,
                    "italic": italic,
                },
                "fields": "weightedFontFamily,fontSize,bold,italic",
            }
        })

        # Paragraph style
        para_style = {
            "alignment": "CENTER" if align == "CENTER" else "START",
            "spaceAbove": {"magnitude": space_before, "unit": "PT"},
            "spaceBelow": {"magnitude": space_after, "unit": "PT"},
            "lineSpacing": 100,  # single spacing = 100%
        }

        # For heading paragraphs, add a bottom border to simulate hrule
        if is_heading:
            para_style["borderBottom"] = {
                "color": {"color": {"rgbColor": {"red": 0, "green": 0, "blue": 0}}},
                "width": {"magnitude": 0.5, "unit": "PT"},
                "padding": {"magnitude": 1, "unit": "PT"},
                "dashStyle": "SOLID",
            }

        # Tab stops for right-aligned dates: tabStops not supported
        # in updateParagraphStyle API — dates stay inline with text

        requests.append({
            "updateParagraphStyle": {
                "range": {"startIndex": start, "endIndex": end},
                "paragraphStyle": para_style,
                "fields": ",".join(para_style.keys()),
            }
        })

        # Bullet list formatting
        if is_bullet:
            requests.append({
                "createParagraphBullets": {
                    "range": {"startIndex": start, "endIndex": end},
                    "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                }
            })

        # Hyperlink (make entire text a link)
        url = our_para.get("url", "")
        if url:
            # Link the text range (excluding trailing newline)
            link_end = end - 1 if end > start + 1 else end
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": link_end},
                    "textStyle": {
                        "link": {"url": url},
                        "foregroundColor": {"color": {"rgbColor": {"red": 0.02, "green": 0.35, "blue": 0.75}}},
                        "underline": True,
                    },
                    "fields": "link,foregroundColor,underline",
                }
            })

    return requests


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _is_docs_api_permission_error(exc) -> bool:
    """Return True if this exception indicates the Docs API isn't enabled/accessible."""
    err = str(exc)
    return (
        "accessNotConfigured" in err
        or "has not been used" in err
        or "disabled" in err
        or "PERMISSION_DENIED" in err
        or (
            "403" in err
            and (
                "does not have permission" in err
                or "caller does not" in err
            )
        )
    )


def _print_docs_api_enable_instructions():
    project = "job-automation-490716"
    print(f"\nGoogle Docs API is NOT enabled for project: {project}")
    print(f"\nEnable it here (click Enable, wait ~1 minute):")
    print(f"  https://console.cloud.google.com/apis/library/docs.googleapis.com?project={project}")
    print(f"\nThen re-run:  .venv/bin/python3 create_templates.py")


def main():
    print("Connecting to Google APIs...")
    docs, drive = _get_services()

    results = {}

    for variant in ("sre", "fullstack"):
        print(f"\nCreating {variant.upper()} template...")
        try:
            doc_id, url = create_template(variant, docs, drive)
            results[variant] = {"id": doc_id, "url": url}
            print(f"  Done: {url}")
        except Exception as exc:
            if _is_docs_api_permission_error(exc):
                _print_docs_api_enable_instructions()
                sys.exit(1)
            raise

    print("\n" + "=" * 60)
    print("TEMPLATE CREATION COMPLETE")
    print("=" * 60)
    for variant, info in results.items():
        print(f"\n{variant.upper()}:")
        print(f"  Doc ID : {info['id']}")
        print(f"  URL    : {info['url']}")
    print("\nUpdate config.yaml with these template IDs.")

    # Optionally write IDs to a file for reference
    ids_path = Path(__file__).parent / "template_ids.txt"
    with open(ids_path, "w") as f:
        for variant, info in results.items():
            f.write(f"{variant}_template_id={info['id']}\n")
            f.write(f"{variant}_template_url={info['url']}\n")
    print(f"\nTemplate IDs saved to: {ids_path}")


if __name__ == "__main__":
    main()
