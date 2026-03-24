"""
Create Google Doc resume templates (SRE and Fullstack variants).

Usage: .venv/bin/python3 create_templates.py

The script inserts all content and applies formatting via the Google Docs API,
then shares the docs with 254utkarsh@gmail.com.
"""
from __future__ import annotations
import re
import sys
import os
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from user_profile import UserProfile

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

# ── Default (hardcoded) header info — used when no UserProfile is provided ──

NAME = "Utkarsh Singh"
CONTACT_LINE = "Dublin, Ireland | +353 892515620 | 254utkarsh@gmail.com"
CONTACT_LINKS = [
    {"text": "254utkarsh@gmail.com", "url": "mailto:254utkarsh@gmail.com"},
]
LINKS_LINE = "github.com/UT07 | linkedin.com/in/utkarshsingh2001 | utworld.netlify.app"
LINKS_LINKS = [
    {"text": "github.com/UT07", "url": "https://github.com/UT07"},
    {"text": "linkedin.com/in/utkarshsingh2001", "url": "https://www.linkedin.com/in/utkarshsingh2001/"},
    {"text": "utworld.netlify.app", "url": "https://utworld.netlify.app"},
]


def _header_from_profile(user: "UserProfile") -> dict:
    """Derive template header values from a UserProfile.

    Returns a dict with keys: name, contact_line, contact_links, links_line,
    links_links, share_email  — matching the module-level defaults.
    """
    # Contact line: "Location | Phone | email"
    contact_parts = []
    if user.location:
        contact_parts.append(user.location)
    if user.phone:
        contact_parts.append(user.phone)
    if user.email:
        contact_parts.append(user.email)
    contact_line = " | ".join(contact_parts)

    contact_links = []
    if user.email:
        contact_links.append({"text": user.email, "url": f"mailto:{user.email}"})

    # Links line: "github.com/X | linkedin.com/in/Y | website"
    link_items = []
    link_links = []
    for url_attr in ("github", "linkedin", "website"):
        url = getattr(user, url_attr, "")
        if url:
            display = re.sub(r"https?://(www\.)?", "", url).rstrip("/")
            link_items.append(display)
            link_links.append({"text": display, "url": url})

    links_line = " | ".join(link_items)

    return {
        "name": user.name or NAME,
        "contact_line": contact_line or CONTACT_LINE,
        "contact_links": contact_links or CONTACT_LINKS,
        "links_line": links_line or LINKS_LINE,
        "links_links": link_links or LINKS_LINKS,
        "share_email": user.email or SHARE_EMAIL,
    }

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
            "Cloud Machine Learning, Data Governance/Compliance/Ethics, and Research in Computing."
        ],
    },
    {
        "school": "Southern New Hampshire University",
        "location": "Manchester, NH",
        "dates": "Aug 2023 – May 2024",
        "degree": "BS Computer Science",
        "bullets": [
            "Coursework: Software Development Lifecycle, Full-Stack Development, Database Systems, "
            "Software Testing, Fog and Edge Computing, Senior Capstone Project."
        ],
    },
    {
        "school": "The University of Texas at Arlington",
        "location": "Arlington, TX",
        "dates": "Aug 2019 – May 2022",
        "degree": "BS Software Engineering (Coursework, Transferred)",
        "bullets": [
            "Coursework: Algorithms & Data Structures, Operating Systems, Computer Networks, "
            "Information Security, Object-Oriented Programming.",
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
#
# For right-aligned date headers, use table_row instead of text:
#   table_row   – True → rendered as a 1×2 borderless table
#   left        – left cell text (left-aligned)
#   right       – right cell text (right-aligned)
#   bold, size, space_after, page_break – same as regular paragraphs


def build_paragraphs(
    variant: str,
    user_profile: Optional["UserProfile"] = None,
) -> list[dict]:
    """Build the flat list of paragraph dicts for a template variant.

    Parameters
    ----------
    user_profile:
        Optional UserProfile. When provided, the header (name, contact,
        links) and share email are derived from the profile instead of
        the module-level defaults.
    """
    v = VARIANTS[variant]
    paras = []

    # Resolve header values
    if user_profile is not None:
        hdr = _header_from_profile(user_profile)
    else:
        hdr = {
            "name": NAME,
            "contact_line": CONTACT_LINE,
            "contact_links": CONTACT_LINKS,
            "links_line": LINKS_LINE,
            "links_links": LINKS_LINKS,
        }

    def p(text, **kw):
        paras.append({"text": text, **kw})

    # ---- HEADER ----
    p(hdr["name"],   align="CENTER", bold=True,  size=16, space_after=2)
    p(v["title_line"], align="CENTER", bold=False, size=11, space_after=2)
    paras.append({"text": hdr["contact_line"], "align": "CENTER", "bold": False, "size": 10,
                  "space_after": 2, "inline_links": hdr["contact_links"]})
    paras.append({"text": hdr["links_line"], "align": "CENTER", "bold": False, "size": 10,
                  "space_after": 6, "inline_links": hdr["links_links"]})

    # ---- SUMMARY ----
    p("Summary",       heading=True, space_before=6,  space_after=4)
    p("{{SUMMARY}}",   size=11, space_after=8)

    # ---- TECHNICAL SKILLS ----
    p("Technical Skills", heading=True, space_before=6, space_after=4)
    p("{{SKILLS}}",       size=11, space_after=8)

    # ---- EXPERIENCE ----
    p("Experience", heading=True, space_before=6, space_after=4)

    # Clover
    paras.append({
        "table_row": True,
        "left": "Clover IT Services — New York, NY (Remote)",
        "right": "Jun 2022 – Jul 2024",
        "bold": True, "size": 11, "space_after": 1,
    })
    p(v["clover_role"], italic=True, size=11, space_after=2)
    p("{{CLOVER_BULLETS}}", size=11, space_after=6)

    # PAGE BREAK before Kraken
    paras.append({
        "table_row": True,
        "left": "Seattle Kraken (NHL) — Seattle, WA",
        "right": "Jun 2021 – May 2022",
        "bold": True, "size": 11, "page_break": True, "space_after": 1,
    })
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
        paras.append({
            "table_row": True,
            "left": f"{edu['school']}, {edu['location']}",
            "right": edu["dates"],
            "bold": True, "size": 11, "space_after": 1,
        })
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


def create_template(
    variant: str,
    docs,
    drive,
    user_profile: Optional["UserProfile"] = None,
) -> tuple[str, str]:
    """Create one template doc. Returns (doc_id, url).

    Parameters
    ----------
    user_profile:
        Optional UserProfile. When provided, header info and share email are
        derived from the profile. Pass ``None`` for the default single-user
        behavior.
    """
    v = VARIANTS[variant]
    paragraphs = build_paragraphs(variant, user_profile=user_profile)

    # 1. Create blank document
    doc = docs.documents().create(body={"title": v["title"]}).execute()
    doc_id = doc["documentId"]
    print(f"  Created doc: {doc_id}")

    # 2. Insert text paragraphs (reverse-order strategy).
    #    For table_row paragraphs, insert a recognisable placeholder so we
    #    can locate them later and replace with real 1x2 tables.
    requests = []

    # Assign each table_row a sequential placeholder tag
    table_row_index = 0
    table_row_paras = []  # (placeholder_tag, para_dict)
    segments = []
    for para in paragraphs:
        if para.get("table_row"):
            tag = f"__TABLE_{table_row_index}__"
            table_row_paras.append((tag, para))
            table_row_index += 1
            segments.append((tag, para))
        else:
            text = para.get("text", "")
            segments.append((text, para))

    reversed_segments = list(reversed(segments))

    for text, para in reversed_segments:
        line = text + "\n"
        requests.append({
            "insertText": {
                "location": {"index": 1},
                "text": line,
            }
        })
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

    # 4. Re-read the doc to get actual indices.
    doc_content = docs.documents().get(documentId=doc_id).execute()

    # 5. Set document margins
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

    # 6. Apply paragraph-level and text-level formatting (skips table_row placeholders)
    fmt_requests = _build_format_requests(doc_content, paragraphs)

    if fmt_requests:
        chunk_size = 100
        for i in range(0, len(fmt_requests), chunk_size):
            chunk = fmt_requests[i:i + chunk_size]
            docs.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": chunk},
            ).execute()
        print(f"  Applied {len(fmt_requests)} formatting requests")

    # 7. Replace placeholder paragraphs with borderless 1x2 tables.
    #    Process in REVERSE document order so earlier indices stay valid.
    if table_row_paras:
        _replace_placeholders_with_tables(docs, doc_id, table_row_paras)
        print(f"  Replaced {len(table_row_paras)} placeholders with tables")

    # 8. Share with user email
    share_email = (
        user_profile.email if user_profile is not None and user_profile.email
        else SHARE_EMAIL
    )
    drive.permissions().create(
        fileId=doc_id,
        body={"type": "user", "role": "writer", "emailAddress": share_email},
        sendNotificationEmail=False,
    ).execute()

    file_info = drive.files().get(fileId=doc_id, fields="webViewLink").execute()
    url = file_info.get("webViewLink", f"https://docs.google.com/document/d/{doc_id}/edit")
    print(f"  Shared with {share_email}")
    return doc_id, url


# ---------------------------------------------------------------------------
# Table insertion helpers
# ---------------------------------------------------------------------------

def _find_placeholder_paragraph(doc_content: dict, tag: str) -> dict | None:
    """Find the paragraph element containing the placeholder tag text.

    Returns the body content element (with startIndex/endIndex) or None.
    """
    for elem in doc_content.get("body", {}).get("content", []):
        if "paragraph" not in elem:
            continue
        para = elem["paragraph"]
        for run in para.get("elements", []):
            if "textRun" in run:
                if tag in run["textRun"].get("content", ""):
                    return elem
    return None


def _find_table_at(doc_content: dict, start_index: int) -> dict | None:
    """Find the table element whose startIndex matches the given index."""
    for elem in doc_content.get("body", {}).get("content", []):
        if "table" in elem and elem.get("startIndex") == start_index:
            return elem
    return None


_INVISIBLE_BORDER = {
    "width": {"magnitude": 0, "unit": "PT"},
    "dashStyle": "SOLID",
    "color": {"color": {"rgbColor": {"red": 1, "green": 1, "blue": 1}}},
}


def _replace_placeholders_with_tables(docs, doc_id: str,
                                       table_row_paras: list[tuple[str, dict]]):
    """Replace placeholder paragraphs with formatted 1x2 borderless tables.

    Each placeholder is processed individually (requires re-reading the doc
    after each structural change).  We process in reverse document order so
    that earlier indices remain stable.
    """
    # First, locate all placeholders and record their startIndex so we can
    # sort in reverse order.
    doc_content = docs.documents().get(documentId=doc_id).execute()

    placeholder_positions = []  # (start_index, tag, para_dict)
    for tag, para in table_row_paras:
        elem = _find_placeholder_paragraph(doc_content, tag)
        if elem:
            placeholder_positions.append((elem["startIndex"], tag, para))

    # Sort by start_index descending so we process from bottom to top
    placeholder_positions.sort(key=lambda x: x[0], reverse=True)

    for _, tag, para in placeholder_positions:
        # Re-read doc each time (indices shift after each table insertion)
        doc_content = docs.documents().get(documentId=doc_id).execute()
        elem = _find_placeholder_paragraph(doc_content, tag)
        if not elem:
            print(f"    Warning: placeholder {tag} not found, skipping")
            continue

        para_start = elem["startIndex"]
        para_end = elem["endIndex"]

        # Step 1: Delete the placeholder paragraph content.
        # We delete from para_start to para_end (includes trailing newline).
        # But we must keep at least one paragraph in the doc, and we can't
        # delete the very last newline.  Deleting the range [start, end-1]
        # removes the text but leaves the empty paragraph; then the table
        # insertion at that index will replace it.
        delete_requests = [{
            "deleteContentRange": {
                "range": {"startIndex": para_start, "endIndex": para_end},
            }
        }]
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": delete_requests},
        ).execute()

        # Step 2: Insert a 1-row, 2-column table at the former paragraph position.
        insert_requests = [{
            "insertTable": {
                "rows": 1,
                "columns": 2,
                "location": {"index": para_start},
            }
        }]
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": insert_requests},
        ).execute()

        # Step 3: Re-read doc to discover cell indices inside the new table.
        doc_content = docs.documents().get(documentId=doc_id).execute()
        table_elem = _find_table_at(doc_content, para_start)
        if not table_elem:
            print(f"    Warning: table not found at index {para_start} for {tag}")
            continue

        _fill_and_format_table(docs, doc_id, table_elem, para)


def _fill_and_format_table(docs, doc_id: str, table_elem: dict, para: dict):
    """Insert text into cells, apply formatting, and hide borders."""
    table = table_elem["table"]
    table_start = table_elem["startIndex"]
    table_end = table_elem["endIndex"]

    left_text = para["left"]
    right_text = para["right"]
    bold = para.get("bold", False)
    size_pt = para.get("size", 11)
    space_after = para.get("space_after", 0)

    # Navigate table structure: table -> tableRows[0] -> tableCells[0,1]
    row = table["tableRows"][0]
    cell_left = row["tableCells"][0]
    cell_right = row["tableCells"][1]

    # Each cell contains at least one paragraph with a trailing \n.
    # The paragraph's first element startIndex is where we insert text.
    left_para = cell_left["content"][0]["paragraph"]
    right_para = cell_right["content"][0]["paragraph"]

    # Insert at the start of each cell's paragraph (before the existing \n)
    left_insert_idx = left_para["elements"][0]["startIndex"]
    right_insert_idx = right_para["elements"][0]["startIndex"]

    requests = []

    # Insert text into cells (right cell first so left indices stay valid,
    # since right cell comes later in the document)
    requests.append({
        "insertText": {
            "location": {"index": right_insert_idx},
            "text": right_text,
        }
    })
    requests.append({
        "insertText": {
            "location": {"index": left_insert_idx},
            "text": left_text,
        }
    })

    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": requests},
    ).execute()

    # Re-read to get updated indices after text insertion
    doc_content = docs.documents().get(documentId=doc_id).execute()
    table_elem = _find_table_at(doc_content, table_start)
    if not table_elem:
        return

    table = table_elem["table"]
    row = table["tableRows"][0]
    cell_left = row["tableCells"][0]
    cell_right = row["tableCells"][1]
    left_para = cell_left["content"][0]["paragraph"]
    right_para = cell_right["content"][0]["paragraph"]

    # Compute ranges for text styling (exclude trailing \n)
    left_start = left_para["elements"][0]["startIndex"]
    left_end = left_start + len(left_text)
    right_start = right_para["elements"][0]["startIndex"]
    right_end = right_start + len(right_text)

    fmt_requests = []

    # Text style: bold, font, size for left cell
    fmt_requests.append({
        "updateTextStyle": {
            "range": {"startIndex": left_start, "endIndex": left_end},
            "textStyle": {
                "weightedFontFamily": {"fontFamily": FONT},
                "fontSize": {"magnitude": size_pt, "unit": "PT"},
                "bold": bold,
            },
            "fields": "weightedFontFamily,fontSize,bold",
        }
    })
    # Text style for right cell
    fmt_requests.append({
        "updateTextStyle": {
            "range": {"startIndex": right_start, "endIndex": right_end},
            "textStyle": {
                "weightedFontFamily": {"fontFamily": FONT},
                "fontSize": {"magnitude": size_pt, "unit": "PT"},
                "bold": bold,
            },
            "fields": "weightedFontFamily,fontSize,bold",
        }
    })

    # Paragraph style: left cell left-aligned, right cell right-aligned
    left_para_start = cell_left["content"][0]["startIndex"]
    left_para_end = cell_left["content"][0]["endIndex"]
    right_para_start = cell_right["content"][0]["startIndex"]
    right_para_end = cell_right["content"][0]["endIndex"]

    fmt_requests.append({
        "updateParagraphStyle": {
            "range": {"startIndex": left_para_start, "endIndex": left_para_end},
            "paragraphStyle": {
                "alignment": "START",
                "spaceAbove": {"magnitude": 0, "unit": "PT"},
                "spaceBelow": {"magnitude": space_after, "unit": "PT"},
                "lineSpacing": 100,
            },
            "fields": "alignment,spaceAbove,spaceBelow,lineSpacing",
        }
    })
    fmt_requests.append({
        "updateParagraphStyle": {
            "range": {"startIndex": right_para_start, "endIndex": right_para_end},
            "paragraphStyle": {
                "alignment": "END",
                "spaceAbove": {"magnitude": 0, "unit": "PT"},
                "spaceBelow": {"magnitude": space_after, "unit": "PT"},
                "lineSpacing": 100,
            },
            "fields": "alignment,spaceAbove,spaceBelow,lineSpacing",
        }
    })

    # Remove all table borders (make them invisible/white)
    fmt_requests.append({
        "updateTableCellStyle": {
            "tableRange": {
                "tableCellLocation": {
                    "tableStartLocation": {"index": table_start},
                    "rowIndex": 0,
                    "columnIndex": 0,
                },
                "rowSpan": 1,
                "columnSpan": 2,
            },
            "tableCellStyle": {
                "borderTop": _INVISIBLE_BORDER,
                "borderBottom": _INVISIBLE_BORDER,
                "borderLeft": _INVISIBLE_BORDER,
                "borderRight": _INVISIBLE_BORDER,
                "paddingTop": {"magnitude": 0, "unit": "PT"},
                "paddingBottom": {"magnitude": 0, "unit": "PT"},
                "paddingLeft": {"magnitude": 0, "unit": "PT"},
                "paddingRight": {"magnitude": 0, "unit": "PT"},
            },
            "fields": "borderTop,borderBottom,borderLeft,borderRight,paddingTop,paddingBottom,paddingLeft,paddingRight",
        }
    })

    # Set column widths: left cell gets ~75% of page width, right cell ~25%
    # Page width = 8.5in = 612pt, minus margins (0.7in * 2 = 100.8pt) = 511.2pt
    page_content_width = 612 - (2 * MARGIN_IN)
    left_col_width = page_content_width * 0.75
    right_col_width = page_content_width * 0.25

    fmt_requests.append({
        "updateTableColumnProperties": {
            "tableStartLocation": {"index": table_start},
            "columnIndices": [0],
            "tableColumnProperties": {
                "widthType": "FIXED_WIDTH",
                "width": {"magnitude": left_col_width, "unit": "PT"},
            },
            "fields": "widthType,width",
        }
    })
    fmt_requests.append({
        "updateTableColumnProperties": {
            "tableStartLocation": {"index": table_start},
            "columnIndices": [1],
            "tableColumnProperties": {
                "widthType": "FIXED_WIDTH",
                "width": {"magnitude": right_col_width, "unit": "PT"},
            },
            "fields": "widthType,width",
        }
    })

    if fmt_requests:
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": fmt_requests},
        ).execute()


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
        # Skip table_row placeholders — they'll be replaced with real tables
        if our_para.get("table_row"):
            continue

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

        # Inline links (multiple links within one paragraph)
        inline_links = our_para.get("inline_links", [])
        for link_info in inline_links:
            link_text = link_info["text"]
            link_url = link_info["url"]
            # Find the text within the paragraph content
            offset = text_in_para.find(link_text)
            if offset >= 0:
                link_start = start + offset
                link_end_pos = link_start + len(link_text)
                requests.append({
                    "updateTextStyle": {
                        "range": {"startIndex": link_start, "endIndex": link_end_pos},
                        "textStyle": {
                            "link": {"url": link_url},
                            "foregroundColor": {"color": {"rgbColor": {"red": 0.02, "green": 0.35, "blue": 0.75}}},
                            "underline": True,
                        },
                        "fields": "link,foregroundColor,underline",
                    }
                })

        # Hyperlink (make entire text a link — for certifications etc.)
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
