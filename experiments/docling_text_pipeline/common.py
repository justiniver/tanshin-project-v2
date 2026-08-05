"""Dependency-light paths, hashing, and page-marker helpers for the experiment."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXPERIMENT_SCHEMA_VERSION = "docling-text-input-v1"
DEFAULT_EXPERIMENT_OUTPUT_ROOT = Path("output") / "experiments" / "docling_text"
DEFAULT_DOCLING_MODELS_ROOT = (
    Path("output") / "experiments" / "docling_models"
)
DEFAULT_DOCLING_VENV_ROOT = (
    Path("output") / "experiments" / "docling_venv"
)

PAGE_START_RE = re.compile(
    r'<PHYSICAL_PAGE\s+number="(?P<page>\d+)"\s*>'
)
PAGE_END = "</PHYSICAL_PAGE>"


@dataclass(frozen=True)
class ExperimentArtifactPaths:
    output_dir: Path
    artifacts_dir: Path
    parsed_sources_dir: Path
    selection_manifest: Path
    extraction_manifest: Path
    text_corpus: Path
    extraction_audit: Path
    input_size_comparison: Path
    experiment_metadata: Path


def experiment_artifact_paths(
    output_root: Path,
    security_code: str,
) -> ExperimentArtifactPaths:
    output_dir = output_root / security_code
    artifacts_dir = output_dir / "artifacts"
    return ExperimentArtifactPaths(
        output_dir=output_dir,
        artifacts_dir=artifacts_dir,
        parsed_sources_dir=artifacts_dir / "parsed_sources",
        selection_manifest=artifacts_dir / "selection_manifest.json",
        extraction_manifest=artifacts_dir / "docling_extraction_manifest.json",
        text_corpus=artifacts_dir / "docling_text_corpus.md",
        extraction_audit=artifacts_dir / "docling_extraction_audit.json",
        input_size_comparison=artifacts_dir / "input_size_comparison.json",
        experiment_metadata=artifacts_dir / "text_input_experiment.json",
    )


def validate_experiment_output_root(
    repository_root: Path,
    output_root: Path,
) -> Path:
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    experiments_root = (
        repository_root / "output" / "experiments"
    ).resolve()
    try:
        relative = output_root.relative_to(experiments_root)
    except ValueError as exc:
        raise ValueError(
            "The Docling experiment output must stay under "
            f"{experiments_root}."
        ) from exc
    if not relative.parts:
        raise ValueError(
            "Choose a dedicated child directory under output/experiments "
            "for Docling report artifacts."
        )
    reserved = {
        DEFAULT_DOCLING_MODELS_ROOT.name.casefold(),
        DEFAULT_DOCLING_VENV_ROOT.name.casefold(),
    }
    if relative.parts[0].casefold() in reserved:
        raise ValueError(
            "The report output root cannot be the Docling environment or "
            "model directory."
        )
    return output_root


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def parsed_markdown_filename(source_filename: str) -> str:
    return f"{source_filename}.docling.md"


def parsed_json_filename(source_filename: str) -> str:
    return f"{source_filename}.docling.json"


def page_block(page_number: int, markdown: str) -> str:
    clean = markdown.strip()
    return (
        f'<PHYSICAL_PAGE number="{page_number}">\n'
        f"{clean}\n"
        f"{PAGE_END}"
    )


def document_block(
    *,
    filename: str,
    page_count: int,
    page_markdown: list[str],
) -> str:
    if len(page_markdown) != page_count:
        raise ValueError(
            f"{filename}: expected {page_count} pages, got {len(page_markdown)}."
        )
    pages = "\n\n".join(
        page_block(index, text)
        for index, text in enumerate(page_markdown, start=1)
    )
    return (
        "<DOCUMENT_METADATA>\n"
        f"<source_filename>{filename}</source_filename>\n"
        f"<physical_pdf_pages>{page_count}</physical_pdf_pages>\n"
        "<source_representation>docling_markdown</source_representation>\n"
        "</DOCUMENT_METADATA>\n\n"
        f"<DOCUMENT_TEXT>\n{pages}\n</DOCUMENT_TEXT>"
    )


def parse_page_blocks(document_text: str) -> dict[int, str]:
    matches = list(PAGE_START_RE.finditer(document_text))
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        page = int(match.group("page"))
        start = match.end()
        next_start = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(document_text)
        )
        segment = document_text[start:next_start]
        end = segment.find(PAGE_END)
        if end < 0:
            raise ValueError(f"Page {page} is missing {PAGE_END}.")
        if page in pages:
            raise ValueError(f"Duplicate physical page marker: {page}.")
        pages[page] = segment[:end].strip()
    return pages


SOURCE_REPRESENTATION_NOTICE = """\
<experimental_source_representation>
This is an isolated text-input experiment. No PDF file is attached to the
model request. Each selected filing was converted locally into Docling
Markdown and is supplied below between DOCUMENT_TEXT markers.

Treat source_filename as the authoritative original PDF filename. Treat every
PHYSICAL_PAGE number as the original PDF's 1-indexed physical page for
evidence records. Markdown syntax, table delimiters, page markers, and document
metadata are serialization aids rather than source prose. Do not infer missing
content from extraction gaps. All later prompt references to supplied PDFs
mean these page-preserving extracts, while the local pipeline will still
validate returned quotations and page references against the original PDFs.
</experimental_source_representation>"""
