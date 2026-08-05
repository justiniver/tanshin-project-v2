"""Docling-only subprocess that converts selected PDFs into page-marked Markdown.

This module is intended to run in the disposable parser environment under
``output/experiments/docling_venv``. It deliberately uses only the standard
library plus Docling, so the primary project virtual environment remains
unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from .common import (
    EXPERIMENT_SCHEMA_VERSION,
    document_block,
    experiment_artifact_paths,
    parsed_json_filename,
    parsed_markdown_filename,
    sha256_json,
    sha256_text,
    validate_experiment_output_root,
    write_json,
    write_text,
)


EXTRACTION_OPTIONS = {
    "do_ocr": False,
    "do_table_structure": True,
    "force_backend_text": True,
    "enable_remote_services": False,
    "allow_external_plugins": False,
    "generate_page_images": False,
    "generate_picture_images": False,
    "export_format": "markdown_per_physical_page",
    "compact_tables": True,
    "image_placeholder": "",
    "document_timeout": 900.0,
}


class DoclingExtractionError(RuntimeError):
    """Raised when the isolated parser cannot produce a valid source corpus."""


def _load_docling() -> tuple[Any, Any, Any, Any]:
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import (
            DocumentConverter,
            PdfFormatOption,
        )
    except ImportError as exc:
        raise DoclingExtractionError(
            "Docling is not installed in the parser environment. Run "
            r".\scripts\setup_docling_experiment.ps1 first."
        ) from exc
    return InputFormat, PdfPipelineOptions, DocumentConverter, PdfFormatOption


def _build_converter(models_dir: Path) -> Any:
    _model_inventory(models_dir)
    (
        InputFormat,
        PdfPipelineOptions,
        DocumentConverter,
        PdfFormatOption,
    ) = _load_docling()
    options = PdfPipelineOptions()
    exporter_only = {
        "export_format",
        "compact_tables",
        "image_placeholder",
    }
    for name, value in EXTRACTION_OPTIONS.items():
        if name in exporter_only:
            continue
        if not hasattr(options, name):
            raise DoclingExtractionError(
                f"Docling {importlib.metadata.version('docling')} does not "
                f"support required parser option {name!r}."
            )
        setattr(options, name, value)
    if not hasattr(options, "artifacts_path"):
        raise DoclingExtractionError(
            "This Docling version cannot be forced to use local model "
            "artifacts."
        )
    options.artifacts_path = models_dir
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options)
        }
    )


def _document_page_count(document: Any) -> int:
    pages = getattr(document, "pages", None)
    if pages is None:
        return 0
    return len(pages)


def _export_page(document: Any, page_number: int) -> str:
    try:
        return document.export_to_markdown(
            page_no=page_number,
            compact_tables=True,
            image_placeholder="",
        )
    except TypeError:
        # Kept only as a narrow compatibility fallback for Docling's exporter.
        return document.export_to_markdown(page_no=page_number)


def _export_document_dict(document: Any) -> dict[str, Any]:
    if hasattr(document, "export_to_dict"):
        payload = document.export_to_dict()
    elif hasattr(document, "model_dump"):
        payload = document.model_dump(mode="json")
    else:
        raise DoclingExtractionError(
            "The Docling document does not expose a JSON export method."
        )
    if not isinstance(payload, dict):
        raise DoclingExtractionError("Docling JSON export was not an object.")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _installed_versions() -> dict[str, str]:
    names = (
        "docling",
        "docling-core",
        "docling-parse",
        "docling-ibm-models",
        "torch",
    )
    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def _model_inventory(models_dir: Path) -> dict[str, Any]:
    if not models_dir.is_dir():
        raise DoclingExtractionError(
            f"Local Docling models are missing: {models_dir}. Run "
            r".\scripts\setup_docling_experiment.ps1 first. The experiment "
            "will not silently download models during report preparation."
        )
    files = sorted(
        path
        for path in models_dir.rglob("*")
        if path.is_file()
    )
    if not files:
        raise DoclingExtractionError(
            f"Local Docling model directory is empty: {models_dir}. Run "
            r".\scripts\setup_docling_experiment.ps1 without "
            "-SkipModelDownload."
        )
    records = [
        {
            "path": path.relative_to(models_dir).as_posix(),
            "size": path.stat().st_size,
            "modified_ns": path.stat().st_mtime_ns,
        }
        for path in files
    ]
    return {
        "file_count": len(records),
        "total_bytes": sum(item["size"] for item in records),
        "inventory_sha256": sha256_json(records),
    }


def _conversion_status_name(result: Any) -> str:
    status = getattr(result, "status", None)
    if status is None:
        return "MISSING"
    value = getattr(status, "value", status)
    return str(value).rsplit(".", 1)[-1].upper()


def _conversion_errors(result: Any) -> list[str]:
    errors = getattr(result, "errors", None) or []
    summaries: list[str] = []
    for error in errors:
        if hasattr(error, "model_dump"):
            value = error.model_dump(mode="json", exclude_none=True)
            summaries.append(
                json.dumps(value, ensure_ascii=False, sort_keys=True)
            )
        else:
            summaries.append(str(error))
    return summaries


def _cache_entry_valid(
    *,
    entry: dict[str, Any] | None,
    source_sha256: str,
    config_sha256: str,
    repository_root: Path,
) -> bool:
    if not entry:
        return False
    if entry.get("source_sha256") != source_sha256:
        return False
    if entry.get("config_sha256") != config_sha256:
        return False
    if entry.get("conversion_status") != "SUCCESS":
        return False
    if entry.get("conversion_errors"):
        return False
    markdown_path = repository_root / str(entry.get("markdown_relative_path", ""))
    json_path = repository_root / str(entry.get("json_relative_path", ""))
    if not markdown_path.is_file() or not json_path.is_file():
        return False
    return bool(
        sha256_text(markdown_path.read_text(encoding="utf-8"))
        == entry.get("markdown_sha256")
        and _sha256_file(json_path) == entry.get("json_sha256")
    )


def extract_selected_filings(
    *,
    repository_root: Path,
    selection_manifest_path: Path,
    output_root: Path,
    models_dir: Path,
    force_reparse: bool,
) -> dict[str, Any]:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    try:
        output_root = validate_experiment_output_root(
            repository_root,
            output_root,
        )
    except ValueError as exc:
        raise DoclingExtractionError(str(exc)) from exc
    selection = json.loads(
        selection_manifest_path.read_text(encoding="utf-8")
    )
    security_code = str(selection["security_code"])
    paths = experiment_artifact_paths(output_root, security_code)
    paths.parsed_sources_dir.mkdir(parents=True, exist_ok=True)

    component_versions = _installed_versions()
    docling_version = component_versions["docling"]
    model_inventory = _model_inventory(models_dir)
    config_payload = {
        "component_versions": component_versions,
        "python_version": platform.python_version(),
        "options": EXTRACTION_OPTIONS,
        "model_inventory": model_inventory,
    }
    config_sha256 = sha256_json(config_payload)
    prior: dict[str, Any] = {}
    if paths.extraction_manifest.is_file():
        prior = json.loads(
            paths.extraction_manifest.read_text(encoding="utf-8")
        )
    prior_files = {
        item["filename"]: item
        for item in prior.get("files", [])
        if isinstance(item, dict) and item.get("filename")
    }

    converter: Any | None = None
    file_entries: list[dict[str, Any]] = []
    corpus_blocks: list[str] = []
    total_started = time.perf_counter()

    for selected in selection["selected_files"]:
        filename = str(selected["filename"])
        source_path = repository_root / str(selected["relative_path"])
        expected_pages = int(selected["page_count"])
        source_sha256 = str(selected["sha256"])
        if not source_path.is_file():
            raise DoclingExtractionError(
                f"Selected source PDF is missing: {source_path}"
            )
        current_source_sha256 = _sha256_file(source_path)
        if current_source_sha256 != source_sha256:
            raise DoclingExtractionError(
                f"{filename}: source PDF changed after filing selection. "
                "Rerun the offline preparation to create a fresh manifest."
            )
        markdown_path = (
            paths.parsed_sources_dir / parsed_markdown_filename(filename)
        )
        json_path = paths.parsed_sources_dir / parsed_json_filename(filename)
        prior_entry = prior_files.get(filename)

        if (
            not force_reparse
            and _cache_entry_valid(
                entry=prior_entry,
                source_sha256=source_sha256,
                config_sha256=config_sha256,
                repository_root=repository_root,
            )
        ):
            entry = dict(prior_entry)
            entry["cache_status"] = "reused"
            file_entries.append(entry)
            corpus_blocks.append(markdown_path.read_text(encoding="utf-8"))
            continue

        if converter is None:
            converter = _build_converter(models_dir)
        started = time.perf_counter()
        try:
            result = converter.convert(source_path)
        except Exception as exc:
            raise DoclingExtractionError(
                f"Docling conversion failed for {filename}: {exc}"
            ) from exc
        conversion_status = _conversion_status_name(result)
        conversion_errors = _conversion_errors(result)
        if conversion_status != "SUCCESS" or conversion_errors:
            detail = "; ".join(conversion_errors[:3]) or "no details"
            raise DoclingExtractionError(
                f"{filename}: Docling conversion status was "
                f"{conversion_status}; errors: {detail}"
            )
        document = result.document
        extracted_pages = _document_page_count(document)
        if extracted_pages != expected_pages:
            raise DoclingExtractionError(
                f"{filename}: Docling returned {extracted_pages} pages, but "
                f"the authoritative PDF has {expected_pages} physical pages."
            )

        page_markdown = [
            _export_page(document, page)
            for page in range(1, expected_pages + 1)
        ]
        if not any(item.strip() for item in page_markdown):
            raise DoclingExtractionError(
                f"{filename}: every Docling page export was empty."
            )
        rendered = document_block(
            filename=filename,
            page_count=expected_pages,
            page_markdown=page_markdown,
        )
        write_text(markdown_path, rendered)
        write_json(json_path, _export_document_dict(document))
        json_sha256 = _sha256_file(json_path)
        page_entries = [
            {
                "page": page,
                "characters": len(text),
                "non_whitespace_characters": len(
                    "".join(text.split())
                ),
                "replacement_characters": text.count("\ufffd"),
                "sha256": sha256_text(text),
            }
            for page, text in enumerate(page_markdown, start=1)
        ]
        entry = {
            "filename": filename,
            "source_relative_path": str(selected["relative_path"]),
            "source_sha256": source_sha256,
            "expected_pdf_pages": expected_pages,
            "extracted_pages": extracted_pages,
            "markdown_relative_path": markdown_path.relative_to(
                repository_root
            ).as_posix(),
            "json_relative_path": json_path.relative_to(
                repository_root
            ).as_posix(),
            "markdown_sha256": sha256_text(rendered),
            "json_sha256": json_sha256,
            "config_sha256": config_sha256,
            "conversion_status": conversion_status,
            "conversion_errors": conversion_errors,
            "cache_status": "parsed",
            "duration_seconds": round(time.perf_counter() - started, 3),
            "empty_pages": [
                item["page"]
                for item, text in zip(page_entries, page_markdown)
                if not text.strip()
            ],
            "page_entries": page_entries,
        }
        file_entries.append(entry)
        corpus_blocks.append(rendered)

    corpus = "\n\n".join(corpus_blocks).rstrip() + "\n"
    write_text(paths.text_corpus, corpus)
    payload = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "security_code": security_code,
        "source_manifest_id": selection["manifest_id"],
        "docling_version": docling_version,
        "component_versions": component_versions,
        "python_version": platform.python_version(),
        "options": EXTRACTION_OPTIONS,
        "config_sha256": config_sha256,
        "model_inventory": model_inventory,
        "models_directory": str(models_dir.resolve()),
        "corpus_relative_path": paths.text_corpus.relative_to(
            repository_root
        ).as_posix(),
        "corpus_sha256": sha256_text(corpus),
        "total_characters": len(corpus),
        "total_non_whitespace_characters": len("".join(corpus.split())),
        "total_duration_seconds": round(
            time.perf_counter() - total_started,
            3,
        ),
        "files": file_entries,
    }
    write_json(paths.extraction_manifest, payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert the selected Tanshin PDFs into page-marked Docling "
            "Markdown without contacting an AI provider."
        )
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--force-reparse", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = extract_selected_filings(
            repository_root=args.repository_root.resolve(),
            selection_manifest_path=args.selection_manifest.resolve(),
            output_root=args.output_root.resolve(),
            models_dir=args.models_dir.resolve(),
            force_reparse=args.force_reparse,
        )
    except DoclingExtractionError as exc:
        print(f"DOCLING EXTRACTION FAILED: {exc}", file=sys.stderr)
        return 2
    print(
        "DOCLING EXTRACTION COMPLETE: "
        f"{len(result['files'])} PDFs, "
        f"{result['total_characters']:,} characters."
    )
    print(
        "No AI request was sent. Corpus: "
        f"{result['corpus_relative_path']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
