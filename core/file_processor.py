"""File processing — validate uploads and extract text content."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg"}
TEXT_EXTENSIONS = {".txt", ".md"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


class ProcessedFile(NamedTuple):
    extracted_text: str
    file_type: str
    file_size: int


def validate_file(filename: str, size: int) -> None:
    """Raise ValueError if file is invalid."""
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    if size > MAX_FILE_SIZE:
        raise ValueError(
            f"File too large: {size / 1024 / 1024:.1f}MB. "
            f"Max: {MAX_FILE_SIZE // 1024 // 1024}MB"
        )


def process_file(file_path: Path) -> ProcessedFile:
    """Process an uploaded file and extract text content."""
    ext = file_path.suffix.lower()
    size = file_path.stat().st_size
    file_type = ext.lstrip(".")
    if file_type == "jpeg":
        file_type = "jpg"

    if ext in TEXT_EXTENSIONS:
        text = file_path.read_text(errors="replace")
        return ProcessedFile(extracted_text=text, file_type=file_type, file_size=size)

    if ext == ".pdf":
        text = _extract_pdf(file_path)
        return ProcessedFile(extracted_text=text, file_type="pdf", file_size=size)

    if ext == ".docx":
        text = _extract_docx(file_path)
        return ProcessedFile(extracted_text=text, file_type="docx", file_size=size)

    if ext in IMAGE_EXTENSIONS:
        meta = _image_metadata(file_path)
        return ProcessedFile(extracted_text=meta, file_type=file_type, file_size=size)

    raise ValueError(f"Unsupported file type: {ext}")


def _extract_pdf(file_path: Path) -> str:
    import fitz  # pymupdf

    doc = fitz.open(str(file_path))
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages)


def _extract_docx(file_path: Path) -> str:
    from docx import Document as DocxDocument

    doc = DocxDocument(str(file_path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)


def _image_metadata(file_path: Path) -> str:
    from PIL import Image

    img = Image.open(file_path)
    width, height = img.size
    fmt = img.format or file_path.suffix.upper().lstrip(".")
    size_kb = file_path.stat().st_size / 1024
    return (
        f"Image: {file_path.name} ({fmt}, {width}x{height}px, {size_kb:.1f}KB)"
    )
