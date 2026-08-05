"""Cached local PDF text extraction with an optional PyMuPDF fallback."""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader

from .schemas import SelectionManifest

# pypdf logs unsupported CMap messages at ERROR level even when extraction can
# continue. Keep the ordinary CLI readable and rely on the fallback when needed.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)

try:  # Optional at import time so existing offline environments still work.
    import fitz  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - exercised through injected test doubles
    fitz = None


def normalized_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value)


def canonical_text(value: str) -> str:
    return re.sub(r"\s+", "", normalized_text(value)).replace(",", "")


def sentence_candidates(value: str) -> list[str]:
    """Return readable sentence-sized candidates across PDF line wrapping."""

    lines = [
        re.sub(r"(?<=\d)\s+(?=\d)", "", line.strip())
        for line in normalized_text(value).splitlines()
        if line.strip()
    ]
    joined = "".join(lines)
    candidates = [
        item.strip()
        for item in re.split(r"(?<=[。！？])", joined)
        if item.strip()
    ]

    # PDF extraction often places an unpunctuated table or heading immediately
    # before narrative prose. Contiguous line windows recover the actual
    # sentence without carrying that unrelated prefix into the evidence ledger.
    for start in range(len(lines)):
        window = ""
        for end in range(start, min(start + 8, len(lines))):
            window += lines[end]
            match = re.search(r"[。！？]", window)
            if match is not None:
                candidate = window[: match.end()].strip()
                if len(candidate) >= 8:
                    candidates.append(candidate)
                break

    deduplicated: list[str] = []
    seen: set[str] = set()
    for candidate in candidates or lines:
        key = canonical_text(candidate)
        if key and key not in seen:
            seen.add(key)
            deduplicated.append(candidate)
    return deduplicated


class PdfTextIndex:
    """Read selected PDFs lazily and cache page text for one pipeline run."""

    def __init__(self, repository_root: Path, manifest: SelectionManifest):
        self.repository_root = repository_root
        self.selected = {item.filename: item for item in manifest.selected_files}
        self._pypdf_readers: dict[str, PdfReader] = {}
        self._fitz_documents: dict[str, object] = {}
        self._page_cache: dict[tuple[str, int, bool], str] = {}

    def _path(self, filename: str) -> Path:
        return self.repository_root / self.selected[filename].relative_path

    def _pypdf_text(self, filename: str, page: int) -> str:
        reader = self._pypdf_readers.get(filename)
        if reader is None:
            reader = PdfReader(self._path(filename))
            self._pypdf_readers[filename] = reader
        return reader.pages[page - 1].extract_text() or ""

    def _fitz_text(self, filename: str, page: int) -> str:
        if fitz is None:
            return ""
        document = self._fitz_documents.get(filename)
        if document is None:
            document = fitz.open(self._path(filename))
            self._fitz_documents[filename] = document
        return document.load_page(page - 1).get_text("text") or ""

    def page_text(
        self,
        filename: str,
        page: int,
        *,
        include_fallback: bool = False,
    ) -> str:
        key = (filename, page, include_fallback)
        if key in self._page_cache:
            return self._page_cache[key]
        primary = self._pypdf_text(filename, page)
        fallback = ""
        if not primary.strip() or include_fallback:
            fallback = self._fitz_text(filename, page)
        if fallback and canonical_text(fallback) not in canonical_text(primary):
            text = primary + "\n" + fallback if primary else fallback
        else:
            text = primary or fallback
        self._page_cache[key] = text
        return text

    def sentences(
        self,
        filename: str,
        page: int,
        *,
        include_fallback: bool = False,
    ) -> list[str]:
        return sentence_candidates(
            self.page_text(filename, page, include_fallback=include_fallback)
        )

    def find_quote_pages(self, filename: str, quote: str) -> list[int]:
        item = self.selected[filename]
        target = canonical_text(quote)
        if not target:
            return []
        return [
            page
            for page in range(1, item.page_count + 1)
            if target
            in canonical_text(
                self.page_text(filename, page, include_fallback=True)
            )
        ]

    def close(self) -> None:
        for document in self._fitz_documents.values():
            close = getattr(document, "close", None)
            if callable(close):
                close()
