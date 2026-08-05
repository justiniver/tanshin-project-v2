"""Deterministic filing discovery and trailing-year selection."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from .config import SCHEMA_VERSION
from .pdf_utils import inspect_pdf
from .schemas import (
    DiscoveredFiling,
    FilingPeriod,
    SelectedFiling,
    SelectionManifest,
    SelectionWindow,
)


class SelectionError(RuntimeError):
    """Raised when filing coverage or classification is ambiguous."""


_ORDINAL_RE = re.compile(r"^(?P<ordinal>\d+)_")
_YEAR_RE = re.compile(
    r"(?P<year>20\d{2})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?"
)
_QUARTER_RE = re.compile(r"(?:^|_)(Q[1-4])(?:_|$)", re.IGNORECASE)
MINIMUM_TREND_FISCAL_YEARS = 8


@dataclass(frozen=True)
class _Candidate:
    path: Path
    ordinal: int | None
    fiscal_year: int
    fiscal_month_hint: int | None
    filing_date: str | None
    period: FilingPeriod
    period_explicit: bool
    year_end_inferred: bool
    classification_reason: str
    page_count: int
    byte_size: int
    sha256: str


def _parse_candidate(path: Path, repository_root: Path) -> _Candidate:
    name = path.name
    ordinal_match = _ORDINAL_RE.search(name)
    ordinal = int(ordinal_match.group("ordinal")) if ordinal_match else None
    year_match = _YEAR_RE.search(name)
    if not year_match:
        raise SelectionError(f"Filename has no recognizable fiscal year: {name}")
    year = int(year_match.group("year"))
    month_text = year_match.group("month")
    day_text = year_match.group("day")
    month = int(month_text) if month_text else None
    filing_date = (
        f"{year:04d}-{int(month_text):02d}-{int(day_text):02d}"
        if month_text and day_text
        else None
    )

    upper = name.upper()
    quarter_match = _QUARTER_RE.search(upper)
    if "FY" in upper or (quarter_match and quarter_match.group(1).upper() == "Q4"):
        period = FilingPeriod.FY
        explicit = True
        reason = "Filename contains an explicit FY or Q4/FY marker."
    elif quarter_match:
        token = quarter_match.group(1).upper()
        period = FilingPeriod(token)
        explicit = True
        reason = f"Filename contains an explicit {token} marker."
    else:
        period = FilingPeriod.UNKNOWN
        explicit = False
        reason = "Filename is date-only and requires cadence inference."

    pages, size, digest = inspect_pdf(path)
    return _Candidate(
        path=path,
        ordinal=ordinal,
        fiscal_year=year,
        fiscal_month_hint=month if day_text is None else None,
        filing_date=filing_date,
        period=period,
        period_explicit=explicit,
        year_end_inferred=False,
        classification_reason=reason,
        page_count=pages,
        byte_size=size,
        sha256=digest,
    )


def _infer_date_only_periods(candidates: list[_Candidate]) -> list[_Candidate]:
    unknown = [item for item in candidates if item.period == FilingPeriod.UNKNOWN]
    if not unknown:
        return candidates
    if any(item.ordinal is None for item in candidates):
        names = ", ".join(item.path.name for item in unknown)
        raise SelectionError(
            "Date-only filing classification requires numeric filename ordinals; "
            f"missing for: {names}"
        )

    explicit_fy_ordinals = sorted(
        item.ordinal
        for item in candidates
        if item.period == FilingPeriod.FY and item.ordinal is not None
    )
    residue_support: Counter[int] = Counter()
    for left, right in zip(explicit_fy_ordinals, explicit_fy_ordinals[1:]):
        if right - left == 4 and left % 4 == right % 4:
            residue_support[left % 4] += 1
    if not residue_support:
        raise SelectionError(
            "Could not infer legacy date-only year-end filings: at least two "
            "explicit FY filings four ordinals apart are required."
        )
    best_count = max(residue_support.values())
    best_residues = [key for key, value in residue_support.items() if value == best_count]
    if len(best_residues) != 1:
        raise SelectionError(
            "Legacy date-only year-end cadence is ambiguous across ordinal residues: "
            f"{dict(residue_support)}"
        )
    fy_residue = best_residues[0]

    inferred_fy = [
        item
        for item in unknown
        if item.ordinal is not None and item.ordinal % 4 == fy_residue
    ]
    if not inferred_fy:
        raise SelectionError("Cadence inference found no date-only year-end filings.")
    filing_months = {
        int(item.filing_date[5:7])
        for item in inferred_fy
        if item.filing_date is not None
    }
    if len(filing_months) != 1:
        raise SelectionError(
            "Inferred date-only year-end filings do not share one filing month: "
            f"{sorted(filing_months)}"
        )

    result: list[_Candidate] = []
    quarter_by_offset = {1: FilingPeriod.Q3, 2: FilingPeriod.Q2, 3: FilingPeriod.Q1}
    for item in candidates:
        if item.period != FilingPeriod.UNKNOWN or item.ordinal is None:
            result.append(item)
            continue
        offset = (item.ordinal - fy_residue) % 4
        if offset == 0:
            result.append(
                replace(
                    item,
                    period=FilingPeriod.FY,
                    year_end_inferred=True,
                    classification_reason=(
                        "Inferred as year-end from the validated four-filing cadence "
                        f"(ordinal residue {fy_residue} mod 4; filing month "
                        f"{next(iter(filing_months)):02d})."
                    ),
                )
            )
        else:
            inferred_period = quarter_by_offset[offset]
            result.append(
                replace(
                    item,
                    period=inferred_period,
                    classification_reason=(
                        f"Inferred as {inferred_period.value} from the validated "
                        "four-filing cadence."
                    ),
                )
            )
    return result


def _chronology_key(item: _Candidate) -> tuple[int, int]:
    if item.ordinal is not None:
        return (-item.ordinal, 0)
    rank = {
        FilingPeriod.Q1: 1,
        FilingPeriod.Q2: 2,
        FilingPeriod.Q3: 3,
        FilingPeriod.FY: 4,
        FilingPeriod.UNKNOWN: 0,
    }[item.period]
    return (item.fiscal_year, rank)


def _as_discovered(item: _Candidate, repository_root: Path) -> DiscoveredFiling:
    return DiscoveredFiling(
        filename=item.path.name,
        relative_path=item.path.relative_to(repository_root).as_posix(),
        ordinal=item.ordinal,
        fiscal_year=item.fiscal_year,
        fiscal_month_hint=item.fiscal_month_hint,
        filing_date=item.filing_date,
        period=item.period,
        period_explicit=item.period_explicit,
        year_end_inferred=item.year_end_inferred,
        classification_reason=item.classification_reason,
        page_count=item.page_count,
        byte_size=item.byte_size,
        sha256=item.sha256,
    )


def _manifest_digest(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def select_filings(
    repository_root: Path,
    security_code: str,
    *,
    trend_years: int = 10,
) -> SelectionManifest:
    """Select the latest filing and year-ends spanning the trailing trend window."""

    normalized_code = security_code.strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{4,6}", normalized_code):
        raise SelectionError(
            "Security code must contain 4-6 uppercase letters or digits, "
            f"received {security_code!r}."
        )
    security_code = normalized_code
    data_dir = repository_root / "data" / security_code
    if not data_dir.is_dir():
        raise SelectionError(f"Company data directory does not exist: {data_dir}")
    pdfs = sorted(data_dir.glob("*.pdf"))
    if not pdfs:
        raise SelectionError(f"No PDFs found under {data_dir}")

    candidates = [_parse_candidate(path, repository_root) for path in pdfs]
    ordinals = [item.ordinal for item in candidates if item.ordinal is not None]
    if ordinals and len(ordinals) != len(set(ordinals)):
        raise SelectionError("Duplicate numeric filename ordinals were found.")
    candidates = _infer_date_only_periods(candidates)
    latest = max(candidates, key=_chronology_key)
    if latest.period == FilingPeriod.UNKNOWN:
        raise SelectionError(
            f"The latest filing period is ambiguous: {latest.path.name}"
        )

    year_ends = [item for item in candidates if item.period == FilingPeriod.FY]
    if not year_ends:
        raise SelectionError("No year-end/FY/Q4 filings were identified.")
    latest_year_end_year = max(item.fiscal_year for item in year_ends)
    start_year = latest.fiscal_year - trend_years
    selected_year_ends = [
        item
        for item in year_ends
        if start_year <= item.fiscal_year <= latest.fiscal_year
    ]
    years = sorted({item.fiscal_year for item in selected_year_ends})
    if len(years) < MINIMUM_TREND_FISCAL_YEARS:
        raise SelectionError(
            "Insufficient year-end coverage for longitudinal analysis: "
            f"identified {len(years)} distinct fiscal years; at least "
            f"{MINIMUM_TREND_FISCAL_YEARS} are required."
        )

    expected_start = years[0]
    expected_end = latest_year_end_year
    expected_years = list(range(expected_start, expected_end + 1))
    missing_years = sorted(set(expected_years) - set(years))
    if missing_years:
        raise SelectionError(
            "Year-end coverage is incomplete; refusing to silently bridge missing "
            f"years: {missing_years}"
        )
    grouped: dict[int, list[_Candidate]] = defaultdict(list)
    for item in selected_year_ends:
        grouped[item.fiscal_year].append(item)
    transition_years = sorted(year for year, items in grouped.items() if len(items) > 1)
    for year in transition_years:
        hints = {
            (item.fiscal_month_hint, item.filing_date)
            for item in grouped[year]
        }
        if len(hints) != len(grouped[year]):
            raise SelectionError(
                f"Duplicate indistinguishable year-end filings for fiscal year {year}."
            )

    selected_map: dict[str, tuple[_Candidate, list[str], list[str]]] = {}
    for item in selected_year_ends:
        selected_map[item.path.name] = (
            item,
            ["trend_year_end"],
            [
                "Selected as a year-end filing within the trailing "
                f"{trend_years}-year window ({start_year}-{latest.fiscal_year})."
            ],
        )
    if latest.path.name in selected_map:
        item, roles, reasons = selected_map[latest.path.name]
        roles.insert(0, "latest")
        reasons.insert(0, "Selected as the chronologically latest available filing.")
    else:
        selected_map[latest.path.name] = (
            latest,
            ["latest"],
            ["Selected as the chronologically latest available filing."],
        )

    selected_candidates = sorted(
        (value for value in selected_map.values()),
        key=lambda value: _chronology_key(value[0]),
        reverse=True,
    )
    selected_files = [
        SelectedFiling(
            **_as_discovered(item, repository_root).model_dump(),
            roles=roles,
            selection_reasons=reasons,
        )
        for item, roles, reasons in selected_candidates
    ]
    selected_names = {item.filename for item in selected_files}
    unselected = [
        _as_discovered(item, repository_root)
        for item in sorted(candidates, key=_chronology_key, reverse=True)
        if item.path.name not in selected_names
    ]

    digest_payload = {
        "security_code": security_code,
        "latest": latest.path.name,
        "selected": [
            {
                "filename": item.filename,
                "sha256": item.sha256,
                "page_count": item.page_count,
                "roles": item.roles,
            }
            for item in selected_files
        ],
    }
    notes = [
        "Lower numeric filename ordinals were validated as more recent where present.",
        "The latest filing is deduplicated when it is also a selected year-end.",
    ]
    if transition_years:
        notes.append(
            "Multiple year-ends were retained in transition fiscal years: "
            + ", ".join(map(str, transition_years))
            + "."
        )
    if any(item.year_end_inferred for item in selected_files):
        notes.append(
            "Legacy date-only year-ends were inferred from a validated four-filing "
            "cadence anchored by explicit FY filenames."
        )
    if len(years) < trend_years:
        notes.append(
            f"The available consecutive history contains {len(years)} distinct "
            f"year-end fiscal years, meeting the {MINIMUM_TREND_FISCAL_YEARS}-year "
            "minimum but falling short of the requested trend length."
        )

    return SelectionManifest(
        schema_version=SCHEMA_VERSION,
        security_code=security_code,
        data_directory=data_dir.relative_to(repository_root).as_posix(),
        latest_filename=latest.path.name,
        window=SelectionWindow(
            anchor_fiscal_year=latest.fiscal_year,
            start_fiscal_year=start_year,
            latest_year_end_fiscal_year=latest_year_end_year,
            unique_years=years,
            expected_unique_years=expected_years,
            transition_years_with_multiple_year_ends=transition_years,
        ),
        selected_files=selected_files,
        unselected_files=unselected,
        total_selected_pages=sum(item.page_count for item in selected_files),
        total_selected_bytes=sum(item.byte_size for item in selected_files),
        selection_notes=notes,
        manifest_id=_manifest_digest(digest_payload),
    )
