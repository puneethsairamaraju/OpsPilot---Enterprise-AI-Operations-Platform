"""Safe text extraction for uploaded enterprise documents."""

import csv
import io
import json
from pathlib import Path

from docx import Document as WordDocument
from fastapi import HTTPException, UploadFile
from pypdf import PdfReader

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_SUFFIXES = {".txt", ".md", ".csv", ".json", ".pdf", ".docx"}


async def extract_upload(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type {suffix or '(none)'}. "
            f"Use: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
        )
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Files must be 10 MB or smaller")
    try:
        if suffix in {".txt", ".md"}:
            text = data.decode("utf-8-sig")
        elif suffix == ".csv":
            rows = csv.reader(io.StringIO(data.decode("utf-8-sig")))
            text = "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
        elif suffix == ".json":
            payload = json.loads(data.decode("utf-8-sig"))
            text = json.dumps(payload, indent=2, ensure_ascii=False)
        elif suffix == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        else:
            document = WordDocument(io.BytesIO(data))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not read {file.filename}") from exc
    text = text.strip()
    if len(text) < 20:
        raise HTTPException(
            status_code=422,
            detail=f"{file.filename} contains too little extractable text",
        )
    return text
