from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from tanshin_pipeline.persistence import read_json, write_json
from tanshin_pipeline.schemas import (
    JapaneseResearchDossier,
    JapaneseSynthesisResponse,
)
from tanshin_pipeline.selection import select_filings


@contextmanager
def workspace_temp_directory(repository_root: Path) -> Iterator[Path]:
    base = (repository_root / ".test_tmp").resolve()
    base.mkdir(parents=True, exist_ok=True)
    path = (base / uuid.uuid4().hex).resolve()
    if path.parent != base:
        raise RuntimeError("Refusing to create a test directory outside .test_tmp.")
    path.mkdir()
    try:
        yield path
    finally:
        if path.parent == base and path.exists():
            shutil.rmtree(path)


def _source_records_from_evidence(evidence: list[dict]) -> list[dict]:
    return [
        {
            "record_id": item["evidence_id"],
            "source_filename": item["source_filename"],
            "pdf_page": item["pdf_page"],
            "period_label_ja": item["period_label_ja"],
            "period_label_en": item["period_label_en"],
            "statement_type": item["statement_type"],
            "source_section": item["source_section"],
            "summary_ja": item["exact_quote_ja"],
            "tags": item.get("tags", []),
        }
        for item in evidence
    ]


def _filing_coverage(
    manifest: object,
    evidence: list[dict],
    empty_message: str,
) -> list[dict]:
    records_by_filename: dict[str, list[str]] = {}
    for item in evidence:
        records_by_filename.setdefault(item["source_filename"], []).append(
            item["evidence_id"]
        )
    coverage = []
    for selected in manifest.selected_files:
        record_ids = records_by_filename.get(selected.filename, [])
        absent = {
            "status": "not_available",
            "source_record_ids": [],
            "coverage_note": empty_message,
        }
        coverage.append(
            {
                "source_filename": selected.filename,
                "fiscal_year": selected.fiscal_year,
                "period": selected.period,
                "period_label_ja": f"FY{selected.fiscal_year}",
                "is_latest": selected.filename == manifest.latest_filename,
                "coverage_status": (
                    "complete" if record_ids else "no_material_disclosure"
                ),
                "operating_results": (
                    {
                        "status": "extracted",
                        "source_record_ids": record_ids[:3],
                        "coverage_note": None,
                    }
                    if record_ids
                    else absent
                ),
                "financial_condition": absent,
                "forward_looking_information": absent,
                "strategy_and_plan_progress": absent,
                "segment_and_business_conditions": absent,
                "capital_allocation": absent,
                "material_footnotes": absent,
                "annual_financial_anchor_ids": [],
                "financial_observation_ids": [],
                "commentary_observation_ids": [],
                "disclosure_ids": [],
                "commitment_ids": [],
                "coverage_gaps": [] if record_ids else [empty_message],
            }
        )
    return coverage


def fake_research_dossier(
    repository_root: Path,
    security_code: str = "1808",
) -> JapaneseResearchDossier:
    """Build a small, internally consistent extraction dossier."""

    payload = read_json(
        repository_root / "tests" / "fixtures" / "fake_analysis_ja.json"
    )
    manifest = select_filings(repository_root, security_code)
    payload["identity"]["security_code"] = security_code
    payload["identity"]["latest_filename"] = manifest.latest_filename
    evidence = payload["evidence"]
    if security_code != "1808":
        for sequence, item in enumerate(evidence, start=1):
            item["source_filename"] = manifest.latest_filename
            item["evidence_id"] = (
                f"{manifest.latest_filename}:s{sequence:04d}"
            )
    return JapaneseResearchDossier.model_validate(
        {
            "schema_version": "2.1-test",
            "identity": payload["identity"],
            "source_records": _source_records_from_evidence(evidence),
            "filing_coverage": _filing_coverage(
                manifest,
                evidence,
                "Offline fixture contains no observations for this filing.",
            ),
            "annual_financial_anchors": [],
            "financial_observations": [],
            "commentary_observations": [],
            "disclosures": [],
            "commitments": [],
            "research_notes": ["Stored offline test dossier."],
        }
    )


def dossier_from_analysis_payload(
    payload: dict,
    repository_root: Path | None = None,
) -> JapaneseResearchDossier:
    """Adapt a stored full-analysis fixture to the extraction-side test contract."""

    evidence = payload["evidence"]
    root = repository_root or Path(__file__).resolve().parents[1]
    manifest = select_filings(root, payload["identity"]["security_code"])
    return JapaneseResearchDossier.model_validate(
        {
            "schema_version": "2.1-test",
            "identity": payload["identity"],
            "source_records": _source_records_from_evidence(evidence),
            "filing_coverage": _filing_coverage(
                manifest,
                evidence,
                "Adapted fixture has no observations for this filing.",
            ),
            "annual_financial_anchors": [],
            "financial_observations": [],
            "commentary_observations": [],
            "disclosures": [],
            "commitments": [],
            "research_notes": ["Adapted offline regression fixture."],
        }
    )


def synthesis_from_analysis_payload(
    payload: dict,
) -> JapaneseSynthesisResponse:
    evidence_ids = [
        item["evidence_id"] for item in payload.get("evidence", [])
    ]
    evidence_set = set(evidence_ids)
    fallback_ids = evidence_ids[:2]
    claims = []
    for claim in payload["claims"]:
        item = dict(claim)
        item["source_record_ids"] = [
            value
            for value in item.pop("evidence_ids", [])
            if value in evidence_set
        ] or fallback_ids
        item.pop("figures", None)
        item.pop("dates", None)
        item.pop("qualifiers", None)
        claims.append(item)

    management = payload.get("management_consistency") or {}
    by_dimension = {
        item.get("dimension"): item
        for item in management.get("components", [])
    }
    dimensions = (
        "strategic_coherence",
        "execution_follow_through",
        "forecast_target_discipline",
        "accountability_transparency",
    )
    components = []
    for dimension in dimensions:
        source = by_dimension.get(dimension) or {}
        linked = [
            value
            for value in source.get("evidence_ids", [])
            if value in evidence_set
        ] or fallback_ids
        components.append(
            {
                "dimension": dimension,
                "rating": source.get("rating", 2),
                "evidence_sufficiency": source.get(
                    "evidence_sufficiency",
                    "sufficient",
                ),
                "rationale_ja": source.get(
                    "rationale_ja",
                    "Selected filings support a mixed but assessable result.",
                ),
                "source_record_ids": linked,
            }
        )

    return JapaneseSynthesisResponse.model_validate(
        {
            "schema_version": "2.1-test",
            "claims": claims,
            "management_consistency": {
                "components": components,
                "overall_rationale_ja": management.get(
                    "overall_rationale_ja",
                    "The selected filings permit a balanced consistency assessment.",
                ),
            },
            "model_notes": payload.get("model_notes", []),
        }
    )


def fake_synthesis_response(repository_root: Path) -> JapaneseSynthesisResponse:
    payload = read_json(
        repository_root / "tests" / "fixtures" / "fake_analysis_ja.json"
    )
    return synthesis_from_analysis_payload(payload)


def persist_fake_research(repository_root: Path, paths: object) -> None:
    dossier = fake_research_dossier(repository_root)
    write_json(paths.research_structured, dossier)


def persist_research_for_payload(paths: object, payload: dict) -> None:
    write_json(paths.research_structured, dossier_from_analysis_payload(payload))
