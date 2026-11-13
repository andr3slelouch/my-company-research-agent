"""Resume text extraction for PDF, DOCX, TXT and Markdown uploads."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

SAMPLE_RESUME_PATH = Path(__file__).resolve().parent.parent / "sample_data" / "sample_resume.md"
MAX_CHARS = 20_000  # keeps prompts bounded; ~5k tokens


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_CHARS]


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    elif name.endswith(".docx"):
        import docx  # python-docx

        doc = docx.Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        text = "\n".join(parts)
    elif name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="ignore")
    else:
        raise ValueError("Unsupported file type. Upload a PDF, DOCX, TXT or MD file.")

    text = _clean(text)
    if len(text) < 200:
        raise ValueError(
            "Couldn't read much text from that file (is it a scanned image?). "
            "Try a text-based PDF or DOCX."
        )
    return text


def load_sample() -> str:
    return _clean(SAMPLE_RESUME_PATH.read_text(encoding="utf-8"))


def resume_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
