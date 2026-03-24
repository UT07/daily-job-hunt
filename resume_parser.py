"""Resume parser — extract text from PDF and parse into structured sections using AI.

Uses PyPDF2 or pdfplumber for text extraction, then AI to structure into sections.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text from a PDF file bytes."""
    try:
        import io
        # Try pdfplumber first (better text extraction)
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                text = ""
                for page in pdf.pages:
                    text += page.extract_text() or ""
                    text += "\n"
                return text.strip()
        except ImportError:
            pass

        # Fallback to PyPDF2
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
                text += "\n"
            return text.strip()
        except ImportError:
            pass

        logger.error("No PDF library available. Install pdfplumber or PyPDF2.")
        return ""
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return ""


def parse_resume_sections(text: str, ai_client=None) -> Dict[str, str]:
    """Parse resume text into structured sections using AI.

    Returns dict with keys like:
    - name
    - title_line
    - summary
    - skills
    - experience (array of objects)
    - education (array of objects)
    - certifications (array of strings)
    """
    if not text:
        return {"raw_text": ""}

    if not ai_client:
        # Without AI, return raw text
        return {"raw_text": text}

    prompt = f"""Parse this resume text into structured sections. Return a JSON object with these keys:
- "name": the person's name
- "title_line": their professional title/headline
- "summary": professional summary (if present)
- "skills": technical skills section (each category on its own line as "Category: item1, item2")
- "experience": array of objects [{{"company": "...", "role": "...", "dates": "...", "bullets": "• bullet1\\n• bullet2"}}]
- "education": array of objects [{{"school": "...", "degree": "...", "dates": "..."}}]
- "certifications": array of strings

Resume text:
{text[:6000]}

Return ONLY valid JSON, no markdown fences."""

    try:
        result = ai_client.complete(
            prompt=prompt,
            system="You are a resume parser. Extract structured data from resume text. Return only valid JSON.",
            temperature=0.1,
        )

        # Strip markdown fences
        result = result.strip()
        if result.startswith("```"):
            lines = result.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            result = "\n".join(lines).strip()

        return json.loads(result)
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"AI resume parsing failed: {e}")
        return {"raw_text": text}
