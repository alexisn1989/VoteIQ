from __future__ import annotations

import json
import os
from io import BytesIO

import requests
from dotenv import load_dotenv
from pypdf import PdfReader

from voteiq.utils import _clean_json_text

try:
    from google import genai as _genai
    from google.genai import types as _genai_types
    _GENAI_AVAILABLE = True
except ImportError:
    _genai_types = None
    _GENAI_AVAILABLE = False

GEMINI_MODEL = "gemini-2.5-flash"

load_dotenv()

EO_EXTRACTION_PROMPT = """Extract structured data from this executive order PDF.

Return ONLY valid JSON with these fields when available:
{
  "title": "executive order title",
  "order_number": "executive order number or null",
  "signed_date": "YYYY-MM-DD or null",
  "governor": "governor name or null",
  "jurisdiction": "state or federal jurisdiction",
  "summary": "2-4 sentence plain-English summary",
  "policy_topics": ["topic 1", "topic 2"],
  "agencies": ["agency names mentioned"],
  "actions_required": ["required action 1"],
  "effective_date": "YYYY-MM-DD or null",
  "source_type": "executive order"
}
"""


def _gemini_client(api_key: str | None = None):
    if not _GENAI_AVAILABLE:
        raise RuntimeError("google-genai is not installed")
    api_key = api_key or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    return _genai.Client(api_key=api_key)


def extract_eo_from_pdf(
    pdf_url: str,
    prompt: str = EO_EXTRACTION_PROMPT,
    api_key: str | None = None,
) -> dict:
    response = requests.get(pdf_url, timeout=30)
    response.raise_for_status()
    pdf_bytes = response.content

    client = _gemini_client(api_key)
    pdf_part = _genai_types.Part.from_bytes(
        data=pdf_bytes,
        mime_type="application/pdf",
    )
    result = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            pdf_part,
            prompt,
        ],
    )

    text = _clean_json_text(result.text)
    return json.loads(text)


def extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages).strip()


def fetch_pdf_bytes(pdf_url: str) -> bytes:
    response = requests.get(pdf_url, timeout=30)
    response.raise_for_status()
    return response.content
