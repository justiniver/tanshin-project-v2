"""Offline comparison of Docling Markdown against native PDF text layers."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from .common import parse_page_blocks, sha256_bytes, sha256_text


NUMBER_RE = re.compile(
    r"(?:△|▲|-)?\d[\d,]*(?:\.\d+)?(?:%|％|円|百万円|億円|兆円)?"
)


def _canonical(value: str) -> str:
    return re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", value),
    )


def _numeric_tokens(value: str) -> Counter[str]:
    return Counter(
        token.replace(",", "").replace("％", "%")
        for token in NUMBER_RE.findall(unicodedata.normalize("NFKC", value))
    )


def _multiset_recall(expected: Counter[str], observed: Counter[str]) -> float:
    denominator = sum(expected.values())
    if denominator == 0:
        return 1.0
    matched = sum(
        min(count, observed.get(token, 0))
        for token, count in expected.items()
    )
    return matched / denominator


def audit_extraction(
    *,
    repository_root: Path,
    selection_manifest: Any,
    extraction_manifest: dict[str, Any],
) -> dict[str, Any]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        fitz = None

    entries = {
        item["filename"]: item
        for item in extraction_manifest.get("files", [])
    }
    issues: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []

    for selected in selection_manifest.selected_files:
        entry = entries.get(selected.filename)
        if entry is None:
            raise ValueError(
                f"Extraction manifest is missing {selected.filename}."
            )
        if entry.get("source_sha256") != selected.sha256:
            raise ValueError(
                f"Extraction cache is stale for {selected.filename}."
            )
        if int(entry.get("extracted_pages", 0)) != selected.page_count:
            raise ValueError(
                f"{selected.filename}: extracted page count does not match "
                "the selected PDF."
            )
        markdown_path = repository_root / entry["markdown_relative_path"]
        json_path = repository_root / entry["json_relative_path"]
        markdown = markdown_path.read_text(encoding="utf-8")
        if sha256_text(markdown) != entry["markdown_sha256"]:
            raise ValueError(
                f"Parsed Markdown hash mismatch for {selected.filename}."
            )
        if (
            not json_path.is_file()
            or sha256_bytes(json_path.read_bytes()) != entry["json_sha256"]
        ):
            raise ValueError(
                f"Parsed Docling JSON hash mismatch for {selected.filename}."
            )
        pages = parse_page_blocks(markdown)
        expected_page_numbers = set(range(1, selected.page_count + 1))
        if set(pages) != expected_page_numbers:
            raise ValueError(
                f"{selected.filename}: physical page markers are incomplete."
            )

        native_characters = 0
        extracted_characters = sum(len(text) for text in pages.values())
        numeric_recalls: list[float] = []
        empty_pages = [
            page for page, text in pages.items() if not text.strip()
        ]
        if fitz is not None:
            document = fitz.open(
                repository_root / selected.relative_path
            )
            try:
                for page_number in range(1, selected.page_count + 1):
                    native = (
                        document.load_page(page_number - 1).get_text("text")
                        or ""
                    )
                    extracted = pages[page_number]
                    native_characters += len(native)
                    numeric_recalls.append(
                        _multiset_recall(
                            _numeric_tokens(native),
                            _numeric_tokens(extracted),
                        )
                    )
            finally:
                document.close()

        mean_numeric_recall = (
            sum(numeric_recalls) / len(numeric_recalls)
            if numeric_recalls
            else None
        )
        if empty_pages:
            issues.append(
                {
                    "severity": "warning",
                    "code": "empty_docling_pages",
                    "filename": selected.filename,
                    "pages": empty_pages,
                }
            )
        if (
            mean_numeric_recall is not None
            and mean_numeric_recall < 0.9
        ):
            issues.append(
                {
                    "severity": "warning",
                    "code": "low_numeric_token_recall",
                    "filename": selected.filename,
                    "mean_numeric_token_recall": round(
                        mean_numeric_recall,
                        4,
                    ),
                }
            )
        file_results.append(
            {
                "filename": selected.filename,
                "physical_pages": selected.page_count,
                "native_text_characters": native_characters,
                "docling_markdown_characters": extracted_characters,
                "character_ratio_vs_native": (
                    round(extracted_characters / native_characters, 4)
                    if native_characters
                    else None
                ),
                "mean_numeric_token_recall": (
                    round(mean_numeric_recall, 4)
                    if mean_numeric_recall is not None
                    else None
                ),
                "empty_docling_pages": empty_pages,
                "canonical_nonempty": bool(_canonical(markdown)),
            }
        )

    return {
        "schema_version": "docling-extraction-audit-v1",
        "source_manifest_id": selection_manifest.manifest_id,
        "docling_config_sha256": extraction_manifest["config_sha256"],
        "files": file_results,
        "warnings": issues,
        "warning_count": len(issues),
        "page_count_verified": True,
        "source_hashes_verified": True,
        "markdown_hashes_verified": True,
        "docling_json_hashes_verified": True,
        "note": (
            "Numeric-token recall is a diagnostic comparison against the "
            "native PDF text layer. It is not a publication gate."
        ),
    }
