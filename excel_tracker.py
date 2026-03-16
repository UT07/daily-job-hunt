"""Master Excel job tracker.

A single persistent Excel file that accumulates ALL jobs across every run.
Designed to be your central application tracking hub with:
- Application status tracking (Applied, Interview, Offer, etc.)
- 3-score breakdown (ATS, Hiring Manager, Tech Recruiter)
- LinkedIn contact search links
- Color-coded scores and conditional formatting
- Daily Summary sheet with aggregate stats
- Deduplication: same job+company combo is never added twice
"""

from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from typing import List
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from scrapers.base import Job


# ── Column definitions ──────────────────────────────────────────────────────
# (name, width, description)
COLUMNS = [
    ("Date Found", 12),
    ("Score", 8),
    ("ATS", 7),
    ("HM", 7),
    ("TR", 7),
    ("Title", 30),
    ("Company", 22),
    ("Location", 20),
    ("Remote?", 9),
    ("Salary", 18),
    ("Source", 10),
    ("Resume Type", 14),
    ("Match Reasoning", 40),
    ("Apply Link", 15),
    ("Resume PDF", 15),
    ("Cover Letter", 15),
    ("Contact 1", 35),
    ("Contact 2", 35),
    ("Contact 3", 35),
    ("Applied?", 10),
    ("Status", 14),
    ("Interview Date", 14),
    ("Notes", 35),
]

# ── Styles ──────────────────────────────────────────────────────────────────
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)

SCORE_EXCELLENT = PatternFill("solid", fgColor="92D050")  # Green: 85+
SCORE_GOOD = PatternFill("solid", fgColor="C6EFCE")       # Light green: 75-84
SCORE_OK = PatternFill("solid", fgColor="FFEB9C")          # Yellow: 60-74
SCORE_LOW = PatternFill("solid", fgColor="FFC7CE")         # Red: <60

STATUS_COLORS = {
    "New": PatternFill("solid", fgColor="D9E2F3"),          # Light blue
    "Applied": PatternFill("solid", fgColor="B4C6E7"),      # Blue
    "Interview": PatternFill("solid", fgColor="E2EFDA"),     # Green
    "Offer": PatternFill("solid", fgColor="92D050"),         # Bright green
    "Rejected": PatternFill("solid", fgColor="FFC7CE"),      # Red
    "Withdrawn": PatternFill("solid", fgColor="D9D9D9"),     # Gray
}

BODY_FONT = Font(name="Calibri", size=10)
LINK_FONT = Font(name="Calibri", size=10, color="0563C1", underline="single")
BODY_ALIGNMENT = Alignment(vertical="top", wrap_text=True)

THIN_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

ZEBRA_FILL = PatternFill("solid", fgColor="F2F2F2")


def create_or_update_tracker(
    jobs: List[Job],
    tracker_path: str,
    run_date: str = None,
) -> str:
    """Create or append to the master job tracker Excel file.

    Deduplicates by title+company: if a job already exists in the tracker,
    it won't be added again (preserving any manual status updates you've made).
    """
    tracker_path = Path(tracker_path)
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")

    if tracker_path.exists():
        wb = load_workbook(str(tracker_path))
        ws = wb.active
        existing_keys = _get_existing_keys(ws)
        start_row = ws.max_row + 1
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Master Tracker"
        _setup_header(ws)
        _add_data_validations(ws)
        start_row = 2
        existing_keys = set()

        # Create summary sheet
        summary = wb.create_sheet("Daily Summary")
        _setup_summary_sheet(summary)

    # Filter out duplicates (jobs already in the tracker)
    new_jobs = []
    for job in jobs:
        key = f"{job.title.lower().strip()}|{job.company.lower().strip()}"
        if key not in existing_keys:
            new_jobs.append(job)
            existing_keys.add(key)

    skipped = len(jobs) - len(new_jobs)
    if skipped > 0:
        print(f"  [EXCEL] Skipped {skipped} duplicate jobs already in tracker")

    # Add new job rows
    for i, job in enumerate(new_jobs):
        row = start_row + i
        is_zebra = (row % 2 == 0)

        # Parse contacts
        contacts = []
        if job.linkedin_contacts:
            try:
                contacts = json.loads(job.linkedin_contacts)
            except json.JSONDecodeError:
                pass

        ws.cell(row=row, column=1, value=run_date)
        ws.cell(row=row, column=2, value=job.match_score)
        ws.cell(row=row, column=3, value=job.ats_score)
        ws.cell(row=row, column=4, value=job.hiring_manager_score)
        ws.cell(row=row, column=5, value=job.tech_recruiter_score)
        ws.cell(row=row, column=6, value=job.title)
        ws.cell(row=row, column=7, value=job.company)
        ws.cell(row=row, column=8, value=job.location)
        ws.cell(row=row, column=9, value="Yes" if job.remote else "No")
        ws.cell(row=row, column=10, value=job.salary or "Not listed")
        ws.cell(row=row, column=11, value=job.source)
        ws.cell(row=row, column=12, value=job.matched_resume)
        ws.cell(row=row, column=13, value=job.match_reasoning[:200] if job.match_reasoning else "")

        # Apply link as hyperlink
        apply_cell = ws.cell(row=row, column=14)
        if job.apply_url:
            apply_cell.value = "Apply"
            apply_cell.hyperlink = job.apply_url
            apply_cell.font = LINK_FONT
        else:
            apply_cell.value = "No link"

        # Resume PDF
        resume_cell = ws.cell(row=row, column=15)
        if job.tailored_pdf_path:
            resume_cell.value = Path(job.tailored_pdf_path).name
        else:
            resume_cell.value = "—"

        # Cover letter
        cl_cell = ws.cell(row=row, column=16)
        if job.cover_letter_pdf_path:
            cl_cell.value = Path(job.cover_letter_pdf_path).name
        else:
            cl_cell.value = "—"

        # LinkedIn contacts (up to 3)
        for ci, contact in enumerate(contacts[:3]):
            col = 17 + ci
            cell = ws.cell(row=row, column=col)
            role = contact.get("role", "")
            url = contact.get("search_url", "")
            if url:
                cell.value = role
                cell.hyperlink = url
                cell.font = LINK_FONT
            else:
                cell.value = role

        # Application tracking columns (user-editable)
        ws.cell(row=row, column=20, value="No")
        ws.cell(row=row, column=21, value="New")
        ws.cell(row=row, column=22, value="")  # Interview date
        ws.cell(row=row, column=23, value="")  # Notes

        # ── Format the row ──
        for col in range(1, len(COLUMNS) + 1):
            cell = ws.cell(row=row, column=col)
            if not cell.font or cell.font == Font():
                cell.font = BODY_FONT
            cell.alignment = BODY_ALIGNMENT
            cell.border = THIN_BORDER
            if is_zebra and col not in (2, 3, 4, 5, 21):  # Don't zebra over colored cells
                cell.fill = ZEBRA_FILL

        # Color-code the match score
        _color_score_cell(ws.cell(row=row, column=2), job.match_score)
        _color_score_cell(ws.cell(row=row, column=3), job.ats_score)
        _color_score_cell(ws.cell(row=row, column=4), job.hiring_manager_score)
        _color_score_cell(ws.cell(row=row, column=5), job.tech_recruiter_score)

        # Color-code status
        status_cell = ws.cell(row=row, column=21)
        status_cell.fill = STATUS_COLORS.get("New", PatternFill())

    # Update summary sheet
    if "Daily Summary" in wb.sheetnames:
        _update_summary(wb["Daily Summary"], new_jobs, run_date)

    # Auto-filter on all columns
    last_row = start_row + len(new_jobs) - 1
    if last_row >= 2:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{last_row}"

    # Freeze header + first few columns
    ws.freeze_panes = "F2"

    wb.save(str(tracker_path))
    print(f"  [EXCEL] Master tracker updated: {tracker_path} ({len(new_jobs)} new jobs added)")
    return str(tracker_path)


def _get_existing_keys(ws) -> set:
    """Extract title|company keys from existing rows for deduplication."""
    keys = set()
    for row in range(2, ws.max_row + 1):
        title = ws.cell(row=row, column=6).value or ""
        company = ws.cell(row=row, column=7).value or ""
        key = f"{str(title).lower().strip()}|{str(company).lower().strip()}"
        keys.add(key)
    return keys


def _color_score_cell(cell, score):
    """Apply color based on score value."""
    if score >= 85:
        cell.fill = SCORE_EXCELLENT
    elif score >= 75:
        cell.fill = SCORE_GOOD
    elif score >= 60:
        cell.fill = SCORE_OK
    else:
        cell.fill = SCORE_LOW


def _setup_header(ws):
    """Set up the header row with formatting."""
    for col_idx, (name, width) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 32


def _add_data_validations(ws):
    """Add dropdown validations for user-editable columns."""
    # "Applied?" column — Yes/No dropdown
    applied_dv = DataValidation(
        type="list",
        formula1='"Yes,No"',
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Invalid",
        error="Please select Yes or No",
    )
    applied_dv.sqref = "T2:T5000"
    ws.add_data_validation(applied_dv)

    # "Status" column — dropdown
    status_dv = DataValidation(
        type="list",
        formula1='"New,Applied,Interview,Offer,Rejected,Withdrawn"',
        allow_blank=True,
        showErrorMessage=True,
        errorTitle="Invalid",
        error="Select a valid status",
    )
    status_dv.sqref = "U2:U5000"
    ws.add_data_validation(status_dv)


def _setup_summary_sheet(ws):
    """Set up the daily summary sheet."""
    headers = [
        "Date", "New Jobs Found", "Already Tracked", "Avg Match Score",
        "Avg ATS", "Avg HM", "Avg TR", "All 85+ Count",
        "Resumes Generated", "Cover Letters", "Top Company", "Top Role",
    ]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col)].width = 16
    ws.freeze_panes = "A2"


def _update_summary(ws, jobs: List[Job], run_date: str):
    """Add a summary row for today's run."""
    if not jobs:
        return

    row = ws.max_row + 1
    avg_score = sum(j.match_score for j in jobs) / len(jobs) if jobs else 0
    avg_ats = sum(j.ats_score for j in jobs) / len(jobs) if jobs else 0
    avg_hm = sum(j.hiring_manager_score for j in jobs) / len(jobs) if jobs else 0
    avg_tr = sum(j.tech_recruiter_score for j in jobs) / len(jobs) if jobs else 0
    all_85 = sum(1 for j in jobs if j.ats_score >= 85 and j.hiring_manager_score >= 85 and j.tech_recruiter_score >= 85)
    top_job = max(jobs, key=lambda j: j.match_score) if jobs else None

    ws.cell(row=row, column=1, value=run_date)
    ws.cell(row=row, column=2, value=len(jobs))
    ws.cell(row=row, column=3, value=0)  # Filled by caller if known
    ws.cell(row=row, column=4, value=round(avg_score, 1))
    ws.cell(row=row, column=5, value=round(avg_ats, 1))
    ws.cell(row=row, column=6, value=round(avg_hm, 1))
    ws.cell(row=row, column=7, value=round(avg_tr, 1))
    ws.cell(row=row, column=8, value=all_85)
    ws.cell(row=row, column=9, value=sum(1 for j in jobs if j.tailored_pdf_path))
    ws.cell(row=row, column=10, value=sum(1 for j in jobs if j.cover_letter_pdf_path))
    ws.cell(row=row, column=11, value=top_job.company if top_job else "N/A")
    ws.cell(row=row, column=12, value=top_job.title if top_job else "N/A")

    for col in range(1, 13):
        cell = ws.cell(row=row, column=col)
        cell.font = BODY_FONT
        cell.alignment = BODY_ALIGNMENT
        cell.border = THIN_BORDER
