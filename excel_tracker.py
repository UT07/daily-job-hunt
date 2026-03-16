"""Excel job tracker generator.

Creates a professional, formatted Excel spreadsheet tracking all matched jobs,
their scores, artifacts, and apply links.
"""

from __future__ import annotations
from pathlib import Path
from datetime import datetime
from typing import List
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from scrapers.base import Job


# Column configuration
COLUMNS = [
    ("Date", 12),
    ("Score", 8),
    ("Title", 30),
    ("Company", 22),
    ("Location", 20),
    ("Remote?", 9),
    ("Salary", 20),
    ("Source", 10),
    ("Resume Type", 14),
    ("Match Reasoning", 45),
    ("Apply Link", 40),
    ("Resume PDF", 40),
    ("Cover Letter PDF", 40),
    ("Status", 14),
    ("Notes", 30),
]

# Styles
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill("solid", fgColor="2F5496")
HEADER_ALIGNMENT = Alignment(horizontal="center", vertical="center", wrap_text=True)

SCORE_HIGH = PatternFill("solid", fgColor="C6EFCE")    # Green: 80+
SCORE_MED = PatternFill("solid", fgColor="FFEB9C")     # Yellow: 60-79
SCORE_LOW = PatternFill("solid", fgColor="FFC7CE")     # Red: <60

BODY_FONT = Font(name="Arial", size=10)
LINK_FONT = Font(name="Arial", size=10, color="0563C1", underline="single")
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
    """Create or append to the job tracker Excel file.

    If the file exists, adds new rows. If not, creates it fresh.
    Returns the path to the tracker.
    """
    tracker_path = Path(tracker_path)
    run_date = run_date or datetime.now().strftime("%Y-%m-%d")

    if tracker_path.exists():
        wb = load_workbook(str(tracker_path))
        ws = wb.active
        start_row = ws.max_row + 1
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Job Tracker"
        _setup_header(ws)
        start_row = 2

        # Also create a summary sheet
        summary = wb.create_sheet("Daily Summary")
        _setup_summary_sheet(summary)

    # Add job rows
    for i, job in enumerate(jobs):
        row = start_row + i
        is_zebra = (row % 2 == 0)

        ws.cell(row=row, column=1, value=run_date)
        ws.cell(row=row, column=2, value=job.match_score)
        ws.cell(row=row, column=3, value=job.title)
        ws.cell(row=row, column=4, value=job.company)
        ws.cell(row=row, column=5, value=job.location)
        ws.cell(row=row, column=6, value="Yes" if job.remote else "No")
        ws.cell(row=row, column=7, value=job.salary or "Not listed")
        ws.cell(row=row, column=8, value=job.source)
        ws.cell(row=row, column=9, value=job.matched_resume)
        ws.cell(row=row, column=10, value=job.match_reasoning[:200])

        # Apply link as hyperlink
        apply_cell = ws.cell(row=row, column=11)
        if job.apply_url:
            apply_cell.value = "Apply Here"
            apply_cell.hyperlink = job.apply_url
            apply_cell.font = LINK_FONT
        else:
            apply_cell.value = "No link"

        # Resume PDF link
        resume_cell = ws.cell(row=row, column=12)
        if job.tailored_pdf_path:
            resume_cell.value = Path(job.tailored_pdf_path).name
            resume_cell.font = BODY_FONT
        else:
            resume_cell.value = "Pending"

        # Cover letter PDF link
        cl_cell = ws.cell(row=row, column=13)
        if job.cover_letter_pdf_path:
            cl_cell.value = Path(job.cover_letter_pdf_path).name
            cl_cell.font = BODY_FONT
        else:
            cl_cell.value = "Pending"

        ws.cell(row=row, column=14, value="New")
        ws.cell(row=row, column=15, value="")

        # Format the row
        for col in range(1, len(COLUMNS) + 1):
            cell = ws.cell(row=row, column=col)
            if cell.font == Font():  # Don't override link font
                cell.font = BODY_FONT
            cell.alignment = BODY_ALIGNMENT
            cell.border = THIN_BORDER
            if is_zebra:
                cell.fill = ZEBRA_FILL

        # Color code score
        score_cell = ws.cell(row=row, column=2)
        if job.match_score >= 80:
            score_cell.fill = SCORE_HIGH
        elif job.match_score >= 60:
            score_cell.fill = SCORE_MED
        else:
            score_cell.fill = SCORE_LOW

    # Update summary sheet
    if "Daily Summary" in wb.sheetnames:
        _update_summary(wb["Daily Summary"], jobs, run_date)

    # Auto-filter
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{start_row + len(jobs) - 1}"

    # Freeze top row
    ws.freeze_panes = "A2"

    wb.save(str(tracker_path))
    print(f"  [EXCEL] Tracker updated: {tracker_path} ({len(jobs)} jobs added)")
    return str(tracker_path)


def _setup_header(ws):
    """Set up the header row with formatting."""
    for col_idx, (name, width) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 30


def _setup_summary_sheet(ws):
    """Set up the daily summary sheet."""
    headers = ["Date", "Total Found", "Matched (>60%)", "High Match (>80%)",
               "Avg Score", "Top Company", "Top Role", "Resumes Generated", "Cover Letters Generated"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = HEADER_ALIGNMENT
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col)].width = 18
    ws.freeze_panes = "A2"


def _update_summary(ws, jobs: List[Job], run_date: str):
    """Add a summary row for today's run."""
    row = ws.max_row + 1
    matched = [j for j in jobs if j.match_score >= 60]
    high = [j for j in jobs if j.match_score >= 80]
    avg_score = sum(j.match_score for j in jobs) / len(jobs) if jobs else 0
    top_job = max(jobs, key=lambda j: j.match_score) if jobs else None

    ws.cell(row=row, column=1, value=run_date)
    ws.cell(row=row, column=2, value=len(jobs))
    ws.cell(row=row, column=3, value=len(matched))
    ws.cell(row=row, column=4, value=len(high))
    ws.cell(row=row, column=5, value=round(avg_score, 1))
    ws.cell(row=row, column=6, value=top_job.company if top_job else "N/A")
    ws.cell(row=row, column=7, value=top_job.title if top_job else "N/A")
    ws.cell(row=row, column=8, value=sum(1 for j in jobs if j.tailored_pdf_path))
    ws.cell(row=row, column=9, value=sum(1 for j in jobs if j.cover_letter_pdf_path))

    for col in range(1, 10):
        cell = ws.cell(row=row, column=col)
        cell.font = BODY_FONT
        cell.alignment = BODY_ALIGNMENT
        cell.border = THIN_BORDER
