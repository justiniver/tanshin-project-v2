"""Download and verify selected Tanshin filings for a security-code universe.

The command is resumable and publishes each company directory atomically. By
default it stores only the latest available Tanshin plus the ten most recent
consecutive FY/Q4 fiscal years. An explicit minimum can admit a shorter
available window while retaining ten years as the acquisition target. The
process is completely independent of Gemini.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from scripts.download_jpx_tanshin import (
    JpxCoverageError,
    JpxDownloadError,
    JpxNoPrimaryTanshinError,
    RequiredFilingSelection,
    discover_tanshin_with_retries,
    download_company,
    select_required_disclosures,
)
from tanshin_pipeline.selection import SelectionError, select_filings
from tanshin_pipeline.pipeline import prepare_research


SCHEMA_VERSION = "1.0"
REQUESTED_TREND_YEARS = 10
MINIMUM_SUPPORTED_TREND_YEARS = 8
TERMINAL_STATUSES = {
    "complete",
    "skipped_existing",
    "incomplete_history",
    "failed",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_failure_files(
    status: dict[str, object],
    *,
    json_path: Path,
    csv_path: Path,
) -> None:
    companies = status.get("companies", [])
    failures = [
        item
        for item in companies
        if item.get("status") in {"incomplete_history", "failed"}
    ]
    _atomic_write_json(
        json_path,
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _utc_now(),
            "failure_count": len(failures),
            "companies": failures,
        },
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = csv_path.with_name(f".{csv_path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "input_index",
                "security_code",
                "company_name",
                "status",
                "reason_code",
                "reason",
                "available_fiscal_years",
            ],
        )
        writer.writeheader()
        for item in failures:
            coverage = item.get("coverage") or {}
            writer.writerow(
                {
                    "input_index": item.get("input_index"),
                    "security_code": item.get("security_code"),
                    "company_name": item.get("company_name") or "",
                    "status": item.get("status"),
                    "reason_code": item.get("reason_code") or "",
                    "reason": item.get("reason") or "",
                    "available_fiscal_years": ",".join(
                        str(year)
                        for year in coverage.get("available_fiscal_years", [])
                    ),
                }
            )
    temporary.replace(csv_path)


def _load_universe(path: Path) -> tuple[list[str], str]:
    content = path.read_bytes()
    codes = [
        line.strip().upper()
        for line in content.decode("utf-8-sig").splitlines()
        if line.strip()
    ]
    if len(codes) != len(set(codes)):
        raise RuntimeError("The universe contains duplicate security codes.")
    invalid = [
        code
        for code in codes
        if not (4 <= len(code) <= 6 and code.isalnum() and code.isascii())
    ]
    if invalid:
        raise RuntimeError(f"Invalid security codes in universe: {invalid}")
    return codes, hashlib.sha256(content).hexdigest()


def _status_summary(companies: list[dict[str, object]]) -> dict[str, int]:
    counts = {
        "complete": 0,
        "skipped_existing": 0,
        "incomplete_history": 0,
        "failed": 0,
        "pending": 0,
    }
    for item in companies:
        status = str(item.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _save_status(
    status: dict[str, object],
    *,
    status_path: Path,
    failures_json_path: Path,
    failures_csv_path: Path,
) -> None:
    companies = sorted(
        status["companies"],
        key=lambda item: int(item["input_index"]),
    )
    status["companies"] = companies
    status["summary"] = _status_summary(companies)
    status["updated_at"] = _utc_now()
    _atomic_write_json(status_path, status)
    _write_failure_files(
        status,
        json_path=failures_json_path,
        csv_path=failures_csv_path,
    )


def _initial_status(
    *,
    universe_path: Path,
    universe_sha256: str,
    codes: list[str],
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "JPX Listed Company Search",
        "mode": "latest_plus_consecutive_year_ends",
        "requested_trend_year_count": REQUESTED_TREND_YEARS,
        "default_minimum_trend_year_count": REQUESTED_TREND_YEARS,
        "minimum_supported_trend_year_count": MINIMUM_SUPPORTED_TREND_YEARS,
        "universe_path": universe_path.as_posix(),
        "universe_sha256": universe_sha256,
        "started_at": _utc_now(),
        "updated_at": _utc_now(),
        "completed_at": None,
        "requested_count": len(codes),
        "companies": [
            {
                "input_index": index,
                "security_code": code,
                "status": "pending",
            }
            for index, code in enumerate(codes, start=1)
        ],
        "summary": {"pending": len(codes)},
    }


def _load_status(
    status_path: Path,
    *,
    universe_path: Path,
    universe_sha256: str,
    codes: list[str],
) -> dict[str, object]:
    if not status_path.exists():
        return _initial_status(
            universe_path=universe_path,
            universe_sha256=universe_sha256,
            codes=codes,
        )
    status = json.loads(status_path.read_text(encoding="utf-8"))
    # Migrate older resumable status files to the generic policy description.
    # Per-company coverage records retain the actual minimum used for each run.
    status["mode"] = "latest_plus_consecutive_year_ends"
    status["requested_trend_year_count"] = REQUESTED_TREND_YEARS
    status["default_minimum_trend_year_count"] = REQUESTED_TREND_YEARS
    status["minimum_supported_trend_year_count"] = (
        MINIMUM_SUPPORTED_TREND_YEARS
    )
    saved_codes = [
        str(item["security_code"])
        for item in sorted(
            status.get("companies", []),
            key=lambda item: int(item["input_index"]),
        )
    ]
    if (
        status.get("universe_sha256") == universe_sha256
        and saved_codes == codes
    ):
        return status

    saved_code_set = set(saved_codes)
    removed_codes = [code for code in saved_codes if code not in codes]
    retained_codes = [code for code in codes if code in saved_code_set]
    if removed_codes or retained_codes != saved_codes:
        raise RuntimeError(
            "The saved acquisition status cannot be reconciled with universe.txt "
            "because existing codes were removed or reordered."
        )

    existing_by_code = {
        str(item["security_code"]): dict(item)
        for item in status.get("companies", [])
    }
    added_codes = [code for code in codes if code not in saved_code_set]
    reconciled_companies: list[dict[str, object]] = []
    for index, code in enumerate(codes, start=1):
        item = existing_by_code.get(
            code,
            {
                "security_code": code,
                "status": "pending",
            },
        )
        item["input_index"] = index
        reconciled_companies.append(item)

    status["companies"] = reconciled_companies
    status["universe_path"] = universe_path.as_posix()
    status["universe_sha256"] = universe_sha256
    status["requested_count"] = len(codes)
    status["summary"] = _status_summary(reconciled_companies)
    if added_codes:
        status["completed_at"] = None
        updates = status.setdefault("universe_updates", [])
        assert isinstance(updates, list)
        updates.append(
            {
                "updated_at": _utc_now(),
                "added_codes": added_codes,
            }
        )
    return status


def _manifest_file_map(source_manifest: dict[str, object]) -> dict[str, dict]:
    return {
        str(item["filename"]): item
        for item in source_manifest.get("files", [])
    }


def _verify_downloaded_company(
    repository_root: Path,
    security_code: str,
    *,
    expected_trend_years: tuple[int, ...] | None,
    require_source_manifest: bool,
    require_all_pdfs_selected: bool = True,
) -> dict[str, object]:
    data_dir = repository_root / "data" / security_code
    pdfs = sorted(data_dir.glob("*.pdf"))
    if not pdfs:
        raise RuntimeError("No PDF files were present after download.")

    pipeline_manifest = select_filings(repository_root, security_code)
    all_pipeline_files = (
        list(pipeline_manifest.selected_files)
        + list(pipeline_manifest.unselected_files)
    )
    pipeline_rows = {
        item.filename: item
        for item in all_pipeline_files
    }
    disk_names = {path.name for path in pdfs}
    if set(pipeline_rows) != disk_names:
        raise RuntimeError(
            "The offline selector inventory does not match the PDF directory."
        )

    source_path = data_dir / "source_manifest.json"
    source_manifest: dict[str, object] | None = None
    identity_unverified: list[str] = []
    if source_path.exists():
        source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
        if source_manifest.get("security_code") != security_code:
            raise RuntimeError("The source manifest security code does not match.")
        rows = _manifest_file_map(source_manifest)
        if set(rows) != disk_names:
            raise RuntimeError(
                "The source manifest file list does not match the PDF directory."
            )
        seen_hashes: set[str] = set()
        for filename, row in rows.items():
            pipeline_row = pipeline_rows[filename]
            if pipeline_row.sha256 != row.get("sha256"):
                raise RuntimeError(f"SHA-256 mismatch: {filename}")
            if pipeline_row.byte_size != row.get("byte_size"):
                raise RuntimeError(f"Byte-size mismatch: {filename}")
            if pipeline_row.page_count != row.get("page_count"):
                raise RuntimeError(f"Page-count mismatch: {filename}")
            if pipeline_row.sha256 in seen_hashes:
                raise RuntimeError(
                    f"Duplicate PDF content was selected: {filename}"
                )
            seen_hashes.add(pipeline_row.sha256)
            if not row.get("security_code_found_in_first_two_pages", False):
                identity_unverified.append(filename)
    elif require_source_manifest:
        raise RuntimeError("The JPX source manifest is missing.")

    selected_names = {item.filename for item in pipeline_manifest.selected_files}
    if require_all_pdfs_selected and selected_names != disk_names:
        raise RuntimeError(
            "The offline selector did not select every downloaded PDF."
        )
    if not selected_names.issubset(disk_names):
        raise RuntimeError(
            "The offline selector referenced a PDF absent from the company directory."
        )

    trend_years = tuple(
        sorted(
            {
                item.fiscal_year
                for item in pipeline_manifest.selected_files
                if "trend_year_end" in item.roles
            }
        )
    )
    if expected_trend_years is not None and trend_years != expected_trend_years:
        raise RuntimeError(
            "The offline selector trend years differ from the acquisition plan: "
            f"{trend_years} != {expected_trend_years}."
        )

    return {
        "offline_selector_passed": True,
        "selection_manifest_id": pipeline_manifest.manifest_id,
        "latest_filename": pipeline_manifest.latest_filename,
        "selected_file_count": len(pipeline_manifest.selected_files),
        "selected_page_count": pipeline_manifest.total_selected_pages,
        "selected_byte_count": pipeline_manifest.total_selected_bytes,
        "trend_fiscal_years": list(trend_years),
        "identity_text_unverified_files": identity_unverified,
        "source_manifest_present": source_manifest is not None,
    }


def _coverage_from_discovery(
    disclosures: list,
    selection: RequiredFilingSelection | None = None,
    *,
    requested_trend_years: int = REQUESTED_TREND_YEARS,
    minimum_trend_years: int | None = None,
) -> dict[str, object]:
    minimum_trend_years = (
        requested_trend_years
        if minimum_trend_years is None
        else minimum_trend_years
    )
    available_years = sorted(
        {
            item.fiscal_year
            for item in disclosures
            if item.period == "FY"
        }
    )
    payload: dict[str, object] = {
        "available_fiscal_years": available_years,
        "available_distinct_fiscal_year_count": len(available_years),
        "available_year_end_count": sum(
            item.period == "FY" for item in disclosures
        ),
        "requested_trend_year_count": requested_trend_years,
        "minimum_trend_year_count": minimum_trend_years,
    }
    if selection is not None:
        selected_count = len(selection.trend_fiscal_years)
        selection_requested = int(
            getattr(
                selection,
                "requested_trend_year_count",
                requested_trend_years,
            )
        )
        selection_minimum = int(
            getattr(
                selection,
                "minimum_trend_year_count",
                minimum_trend_years,
            )
        )
        payload.update(
            {
                "latest_disclosure_id": selection.latest.disclosure_id,
                "latest_disclosure_date": selection.latest.disclosure_date,
                "latest_fiscal_year": selection.latest.fiscal_year,
                "latest_period": selection.latest.period,
                "selected_fiscal_years": list(selection.trend_fiscal_years),
                "selected_trend_year_count": selected_count,
                "requested_trend_year_count": selection_requested,
                "minimum_trend_year_count": selection_minimum,
                "minimum_year_requirement_met": (
                    selected_count >= selection_minimum
                ),
                "ten_year_requirement_met": (
                    selected_count >= REQUESTED_TREND_YEARS
                ),
                "short_window_accepted": (
                    selection_minimum
                    <= selected_count
                    < selection_requested
                ),
            }
        )
    return payload


def _recorded_available_year_count(item: dict[str, object]) -> int:
    """Return the distinct FY count recorded during an earlier discovery."""

    coverage = item.get("coverage") or {}
    years = {
        int(year)
        for year in coverage.get("available_fiscal_years", [])
    }
    return len(years)


def _filter_by_recorded_available_year_counts(
    codes: list[str],
    by_code: dict[str, dict[str, object]],
    requested_counts: set[int],
) -> list[str]:
    """Filter codes without making a new JPX request.

    Raw year-end filing counts are deliberately ignored because revisions and
    fiscal-year transitions can create multiple FY disclosures for one year.
    """

    return [
        code
        for code in codes
        if _recorded_available_year_count(by_code[code]) in requested_counts
    ]


def _classify_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, JpxCoverageError):
        return exc.reason_code, str(exc)
    if isinstance(exc, SelectionError):
        return "offline_selection_failed", str(exc)
    if isinstance(exc, JpxDownloadError):
        message = str(exc)
        if "no primary Tanshin" in message:
            return "no_primary_tanshin_found", message
        if "one unique listed-company result" in message:
            return "company_search_not_unique", message
        if "parse a fiscal year" in message:
            return "unparseable_title", message
        return "jpx_download_failed", message
    return "unexpected_error", f"{type(exc).__name__}: {exc}"


def _existing_result(
    repository_root: Path,
    code: str,
    input_index: int,
) -> dict[str, object]:
    source_manifest = repository_root / "data" / code / "source_manifest.json"
    validation = _verify_downloaded_company(
        repository_root,
        code,
        expected_trend_years=None,
        require_source_manifest=False,
        require_all_pdfs_selected=False,
    )
    return {
        "input_index": input_index,
        "security_code": code,
        "company_name": None,
        "status": "skipped_existing",
        "reason_code": None,
        "reason": "A pre-existing company directory passed offline selection.",
        "provenance_status": (
            "verified_jpx_manifest"
            if source_manifest.exists()
            else "legacy_without_source_manifest"
        ),
        "validation": validation,
        "completed_at": _utc_now(),
    }


def _process_company(
    *,
    repository_root: Path,
    code: str,
    input_index: int,
    timeout: float,
    request_delay_seconds: float,
    attempts: int,
    retry_delay_seconds: float,
    minimum_trend_years: int,
) -> dict[str, object]:
    output_root = repository_root / "data"
    destination = output_root / code
    if destination.exists():
        return _existing_result(repository_root, code, input_index)

    disclosures = []
    company_name: str | None = None
    excluded: list[dict[str, str]] = []
    selection: RequiredFilingSelection | None = None
    created_now = False
    started_at = _utc_now()
    try:
        disclosures, excluded = discover_tanshin_with_retries(
            code,
            timeout=timeout,
            attempts=attempts,
            retry_delay_seconds=retry_delay_seconds,
        )
        company_name = disclosures[0].company_name
        selection = select_required_disclosures(
            disclosures,
            trend_years=REQUESTED_TREND_YEARS,
            minimum_trend_years=minimum_trend_years,
        )
        download_company(
            code,
            list(selection.selected),
            excluded,
            output_root=output_root,
            timeout=timeout,
            delay_seconds=request_delay_seconds,
            selection=selection,
            available_disclosures=disclosures,
            download_attempts=attempts,
            retry_delay_seconds=retry_delay_seconds,
        )
        created_now = True
        validation = _verify_downloaded_company(
            repository_root,
            code,
            expected_trend_years=selection.trend_fiscal_years,
            require_source_manifest=True,
        )
        warning_codes: list[str] = []
        if excluded:
            warning_codes.append("excluded_non_primary_disclosures")
        if selection.superseded_primary_disclosures:
            warning_codes.append("superseded_primary_disclosures")
        if validation["identity_text_unverified_files"]:
            warning_codes.append("pdf_identity_text_not_extractable")
        if len(selection.trend_fiscal_years) < REQUESTED_TREND_YEARS:
            warning_codes.append("shorter_than_ten_year_window")
        return {
            "input_index": input_index,
            "security_code": code,
            "company_name": company_name,
            "manager_code": disclosures[0].manager_code,
            "status": "complete",
            "reason_code": None,
            "reason": None,
            "warning_codes": warning_codes,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "discovery": {
                "primary_count": len(disclosures),
                "excluded_non_primary_count": len(excluded),
                "first_disclosure_date": disclosures[-1].disclosure_date,
                "latest_disclosure_date": disclosures[0].disclosure_date,
            },
            "coverage": _coverage_from_discovery(
                disclosures,
                selection,
                requested_trend_years=REQUESTED_TREND_YEARS,
                minimum_trend_years=minimum_trend_years,
            ),
            "download": {
                "directory": destination.relative_to(repository_root).as_posix(),
                "file_count": len(selection.selected),
            },
            "validation": validation,
        }
    except JpxCoverageError as exc:
        coverage = _coverage_from_discovery(
            disclosures,
            requested_trend_years=REQUESTED_TREND_YEARS,
            minimum_trend_years=minimum_trend_years,
        )
        coverage.update(exc.details)
        return {
            "input_index": input_index,
            "security_code": code,
            "company_name": company_name,
            "status": "incomplete_history",
            "reason_code": exc.reason_code,
            "reason": str(exc),
            "started_at": started_at,
            "completed_at": _utc_now(),
            "discovery": {
                "primary_count": len(disclosures),
                "excluded_non_primary_count": len(excluded),
            },
            "coverage": coverage,
        }
    except JpxNoPrimaryTanshinError as exc:
        return {
            "input_index": input_index,
            "security_code": code,
            "company_name": exc.company_name,
            "manager_code": exc.manager_code,
            "status": "incomplete_history",
            "reason_code": "no_primary_tanshin_found",
            "reason": str(exc),
            "started_at": started_at,
            "completed_at": _utc_now(),
            "discovery": {
                "primary_count": 0,
                "excluded_non_primary_count": 0,
            },
            "coverage": {
                "available_fiscal_years": [],
                "available_distinct_fiscal_year_count": 0,
                "available_year_end_count": 0,
                "requested_trend_year_count": REQUESTED_TREND_YEARS,
                "minimum_trend_year_count": minimum_trend_years,
            },
        }
    except Exception as exc:
        if created_now and destination.exists():
            resolved_destination = destination.resolve()
            resolved_data = output_root.resolve()
            if resolved_destination.parent != resolved_data:
                raise RuntimeError(
                    f"Refusing unsafe cleanup path: {resolved_destination}"
                ) from exc
            shutil.rmtree(resolved_destination)
        reason_code, reason = _classify_failure(exc)
        return {
            "input_index": input_index,
            "security_code": code,
            "company_name": company_name,
            "status": "failed",
            "reason_code": reason_code,
            "reason": reason,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "discovery": {
                "primary_count": len(disclosures),
                "excluded_non_primary_count": len(excluded),
            },
            "coverage": _coverage_from_discovery(
                disclosures,
                requested_trend_years=REQUESTED_TREND_YEARS,
                minimum_trend_years=minimum_trend_years,
            ),
        }


def _display_result(position: int, total: int, result: dict[str, object]) -> None:
    code = result["security_code"]
    status = str(result["status"]).upper()
    if result["status"] in {"complete", "skipped_existing"}:
        validation = result.get("validation") or {}
        years = validation.get("trend_fiscal_years", [])
        print(
            f"[{position}/{total}] {code} {status} "
            f"files={validation.get('selected_file_count', '?')} "
            f"years={years}",
            flush=True,
        )
    else:
        print(
            f"[{position}/{total}] {code} {status} "
            f"{result.get('reason_code')}: {result.get('reason')}",
            flush=True,
        )


def _run_final_audit(
    *,
    repository_root: Path,
    status: dict[str, object],
    audit_path: Path,
) -> bool:
    companies = status["companies"]
    errors: list[dict[str, str]] = []
    verified: list[dict[str, object]] = []
    expected_directories = {
        str(item["security_code"])
        for item in companies
        if item.get("status") in {"complete", "skipped_existing"}
    }
    actual_directories = {
        path.name
        for path in (repository_root / "data").iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    if actual_directories != expected_directories:
        errors.append(
            {
                "security_code": "*",
                "error": (
                    "Data-directory inventory mismatch. "
                    f"Missing={sorted(expected_directories - actual_directories)}; "
                    f"unexpected={sorted(actual_directories - expected_directories)}"
                ),
            }
        )

    staging_directories = sorted(
        path.name
        for path in (repository_root / "data").glob(".jpx_*")
        if path.is_dir()
    )
    if staging_directories:
        errors.append(
            {
                "security_code": "*",
                "error": f"Staging directories remain: {staging_directories}",
            }
        )

    successful = [
        item
        for item in companies
        if item.get("status") in {"complete", "skipped_existing"}
    ]
    temporary_parent = repository_root / "tmp"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    temporary_output_root = temporary_parent / "tanshin_universe_preflight_audit"
    if temporary_output_root.exists():
        raise RuntimeError(
            f"Offline preflight directory already exists: {temporary_output_root}"
        )
    temporary_output_root.mkdir()
    try:
        for position, item in enumerate(successful, start=1):
            code = str(item["security_code"])
            try:
                expected = None
                if item.get("status") == "complete":
                    expected = tuple(
                        int(year)
                        for year in (item.get("coverage") or {}).get(
                            "selected_fiscal_years",
                            [],
                        )
                    )
                validation = _verify_downloaded_company(
                    repository_root,
                    code,
                    expected_trend_years=expected,
                    require_source_manifest=item.get("status") == "complete",
                    require_all_pdfs_selected=item.get("status") == "complete",
                )
                prepared = prepare_research(
                    repository_root,
                    code,
                    output_root=temporary_output_root,
                )
                if (
                    prepared.manifest.manifest_id
                    != validation["selection_manifest_id"]
                ):
                    raise RuntimeError(
                        "Offline preflight used a different selection manifest."
                    )
                validation["offline_research_preflight_passed"] = True
                validation["research_request_id"] = prepared.plan.request_id
                validation["estimated_input_tokens"] = (
                    prepared.cost.research.estimated_input_tokens
                )
                verified.append(
                    {
                        "security_code": code,
                        "status": item.get("status"),
                        "validation": validation,
                    }
                )
            except Exception as exc:
                errors.append(
                    {
                        "security_code": code,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if position % 25 == 0 or position == len(successful):
                print(
                    f"AUDIT {position}/{len(successful)} "
                    f"errors={len(errors)}",
                    flush=True,
                )
    finally:
        shutil.rmtree(temporary_output_root, ignore_errors=True)

    for item in companies:
        if item.get("status") not in {"incomplete_history", "failed"}:
            continue
        code = str(item["security_code"])
        if (repository_root / "data" / code).exists():
            errors.append(
                {
                    "security_code": code,
                    "error": (
                        "A failed or insufficient-history company has a published "
                        "data directory."
                    ),
                }
            )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "valid": not errors,
        "universe_sha256": status.get("universe_sha256"),
        "status_summary": status.get("summary"),
        "expected_directory_count": len(expected_directories),
        "actual_directory_count": len(actual_directories),
        "verified_company_count": len(verified),
        "error_count": len(errors),
        "errors": errors,
        "companies": verified,
    }
    _atomic_write_json(audit_path, payload)
    return not errors


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-file", type=Path, default=Path("universe.txt"))
    parser.add_argument(
        "--status-path",
        type=Path,
        default=Path("data_acquisition/universe_status.json"),
    )
    parser.add_argument(
        "--failures-json",
        type=Path,
        default=Path("data_acquisition/universe_failures.json"),
    )
    parser.add_argument(
        "--failures-csv",
        type=Path,
        default=Path("data_acquisition/universe_failures.csv"),
    )
    parser.add_argument(
        "--audit-path",
        type=Path,
        default=Path("data_acquisition/final_audit.json"),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--request-delay-seconds", type=float, default=0.35)
    parser.add_argument("--company-delay-seconds", type=float, default=0.5)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=3.0)
    parser.add_argument("--start-at")
    parser.add_argument(
        "--codes",
        nargs="+",
        help="Restrict processing to these universe security codes.",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failures", action="store_true")
    parser.add_argument("--retry-incomplete", action="store_true")
    parser.add_argument(
        "--minimum-year-ends",
        type=int,
        default=REQUESTED_TREND_YEARS,
        help=(
            "Minimum consecutive distinct FY/Q4 years accepted when downloading "
            f"(default: {REQUESTED_TREND_YEARS}; supported: "
            f"{MINIMUM_SUPPORTED_TREND_YEARS}-{REQUESTED_TREND_YEARS})."
        ),
    )
    parser.add_argument(
        "--available-year-counts",
        type=int,
        nargs="+",
        help=(
            "Process only companies whose saved discovery status records one of "
            "these distinct FY counts. This filter makes no JPX request itself."
        ),
    )
    parser.add_argument(
        "--failures-only",
        action="store_true",
        help="Process only companies currently recorded with status=failed.",
    )
    parser.add_argument("--execute-downloads", action="store_true")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Perform a full offline audit without making any JPX request.",
    )
    args = parser.parse_args(argv)
    if not (
        MINIMUM_SUPPORTED_TREND_YEARS
        <= args.minimum_year_ends
        <= REQUESTED_TREND_YEARS
    ):
        parser.error(
            "--minimum-year-ends must be between "
            f"{MINIMUM_SUPPORTED_TREND_YEARS} and {REQUESTED_TREND_YEARS}."
        )
    if args.available_year_counts and any(
        count < 0 for count in args.available_year_counts
    ):
        parser.error("--available-year-counts cannot contain negative values.")

    repository_root = Path(__file__).resolve().parents[1]
    os.environ["TANSHIN_OFFLINE_ONLY"] = "1"
    os.environ["TANSHIN_TESTING"] = "1"
    logging.getLogger("pypdf").setLevel(logging.ERROR)

    universe_path = (
        args.universe_file
        if args.universe_file.is_absolute()
        else repository_root / args.universe_file
    )
    status_path = (
        args.status_path
        if args.status_path.is_absolute()
        else repository_root / args.status_path
    )
    failures_json_path = (
        args.failures_json
        if args.failures_json.is_absolute()
        else repository_root / args.failures_json
    )
    failures_csv_path = (
        args.failures_csv
        if args.failures_csv.is_absolute()
        else repository_root / args.failures_csv
    )
    audit_path = (
        args.audit_path
        if args.audit_path.is_absolute()
        else repository_root / args.audit_path
    )
    codes, universe_sha256 = _load_universe(universe_path)

    print("JPX Tanshin universe acquisition", flush=True)
    print(f"Universe: {len(codes)} companies", flush=True)
    print(
        "Mode: latest filing plus up to ten consecutive FY/Q4 years "
        f"(minimum accepted: {args.minimum_year_ends})",
        flush=True,
    )
    print("Gemini access: disabled by TANSHIN_OFFLINE_ONLY and TANSHIN_TESTING", flush=True)
    if args.audit_only:
        if not status_path.exists():
            raise RuntimeError("No acquisition status exists to audit.")
        status = _load_status(
            status_path,
            universe_path=universe_path.relative_to(repository_root),
            universe_sha256=universe_sha256,
            codes=codes,
        )
        valid = _run_final_audit(
            repository_root=repository_root,
            status=status,
            audit_path=audit_path,
        )
        print(
            f"OFFLINE AUDIT: {'PASS' if valid else 'FAIL'} "
            f"artifact={audit_path}",
            flush=True,
        )
        return 0 if valid else 1
    if not args.execute_downloads:
        print("PLAN ONLY: no JPX requests or file downloads were made.", flush=True)
        return 0

    status = _load_status(
        status_path,
        universe_path=universe_path.relative_to(repository_root),
        universe_sha256=universe_sha256,
        codes=codes,
    )
    by_code = {
        str(item["security_code"]): item
        for item in status["companies"]
    }

    selected_codes = codes
    if args.codes:
        requested_codes = [code.strip().upper() for code in args.codes]
        missing_codes = [code for code in requested_codes if code not in codes]
        if missing_codes:
            raise RuntimeError(
                f"--codes contains values outside the universe: {missing_codes}"
            )
        selected_codes = requested_codes
    if args.start_at:
        start_code = args.start_at.strip().upper()
        if start_code not in codes:
            raise RuntimeError(f"--start-at code is not in the universe: {start_code}")
        selected_codes = codes[codes.index(start_code) :]
    if args.limit is not None:
        if args.limit < 1:
            raise RuntimeError("--limit must be positive.")
        selected_codes = selected_codes[: args.limit]
    if args.failures_only:
        selected_codes = [
            code
            for code in selected_codes
            if by_code[code].get("status") == "failed"
        ]
    if args.available_year_counts:
        requested_counts = set(args.available_year_counts)
        selected_codes = _filter_by_recorded_available_year_counts(
            selected_codes,
            by_code,
            requested_counts,
        )
        print(
            "Recorded distinct-FY filter: "
            f"{sorted(requested_counts)} -> {len(selected_codes)} companies",
            flush=True,
        )

    try:
        for position, code in enumerate(selected_codes, start=1):
            prior = by_code[code]
            prior_status = prior.get("status")
            if prior_status in {"complete", "skipped_existing"}:
                try:
                    expected = None
                    coverage = prior.get("coverage") or {}
                    selected_years = coverage.get("selected_fiscal_years")
                    if prior_status == "complete" and selected_years:
                        expected = tuple(int(year) for year in selected_years)
                    validation = _verify_downloaded_company(
                        repository_root,
                        code,
                        expected_trend_years=expected,
                        require_source_manifest=prior_status == "complete",
                        require_all_pdfs_selected=prior_status == "complete",
                    )
                    prior["validation"] = validation
                    _display_result(position, len(selected_codes), prior)
                    continue
                except Exception as exc:
                    prior_status = "failed"
                    prior["status"] = "failed"
                    prior["reason_code"] = "existing_directory_invalid"
                    prior["reason"] = f"{type(exc).__name__}: {exc}"
            if (
                prior_status == "incomplete_history"
                and not args.retry_incomplete
            ):
                _display_result(position, len(selected_codes), prior)
                continue
            if prior_status == "failed" and not args.retry_failures:
                _display_result(position, len(selected_codes), prior)
                continue

            result = _process_company(
                repository_root=repository_root,
                code=code,
                input_index=int(prior["input_index"]),
                timeout=args.timeout,
                request_delay_seconds=args.request_delay_seconds,
                attempts=args.attempts,
                retry_delay_seconds=args.retry_delay_seconds,
                minimum_trend_years=args.minimum_year_ends,
            )
            by_code[code] = result
            status["companies"] = [
                by_code[item_code]
                for item_code in codes
            ]
            _save_status(
                status,
                status_path=status_path,
                failures_json_path=failures_json_path,
                failures_csv_path=failures_csv_path,
            )
            _display_result(position, len(selected_codes), result)
            if args.company_delay_seconds and position < len(selected_codes):
                time.sleep(args.company_delay_seconds)
    except KeyboardInterrupt:
        status["companies"] = [by_code[code] for code in codes]
        _save_status(
            status,
            status_path=status_path,
            failures_json_path=failures_json_path,
            failures_csv_path=failures_csv_path,
        )
        print("Interrupted; progress was saved and can be resumed.", flush=True)
        return 130

    status["companies"] = [by_code[code] for code in codes]
    if all(
        item.get("status") in TERMINAL_STATUSES
        for item in status["companies"]
    ):
        status["completed_at"] = _utc_now()
    _save_status(
        status,
        status_path=status_path,
        failures_json_path=failures_json_path,
        failures_csv_path=failures_csv_path,
    )
    print(
        "Acquisition pass complete: "
        + json.dumps(status["summary"], ensure_ascii=False),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
