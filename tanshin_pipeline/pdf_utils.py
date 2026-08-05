"""Read-only PDF metadata helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pypdf import PdfReader


class PdfInspectionError(RuntimeError):
    """Raised when a PDF cannot be inspected reliably."""


def inspect_pdf(path: Path) -> tuple[int, int, str]:
    """Return page count, byte size, and SHA-256 without extracting PDF text."""

    try:
        page_count = len(PdfReader(path).pages)
    except Exception as exc:  # pragma: no cover - exact parser exception varies
        raise PdfInspectionError(f"Could not read PDF metadata for {path}: {exc}") from exc
    if page_count < 1:
        raise PdfInspectionError(f"PDF has no pages: {path}")
    data = path.read_bytes()
    return page_count, len(data), hashlib.sha256(data).hexdigest()
