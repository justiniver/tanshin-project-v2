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


def fake_research_dossier(
    repository_root: Path,
    security_code: str = "1808",
) -> JapaneseResearchDossier:
    """Build a small, internally consistent dossier from the legacy fixture."""

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
    first_id = evidence[0]["evidence_id"]
    second_id = evidence[-1]["evidence_id"]
    dimensions = (
        "strategic_coherence",
        "execution_follow_through",
        "forecast_target_discipline",
        "accountability_transparency",
    )
    evidence_by_filename: dict[str, list[str]] = {}
    for item in evidence:
        evidence_by_filename.setdefault(item["source_filename"], []).append(
            item["evidence_id"]
        )
    filing_coverage = []
    for selected in manifest.selected_files:
        evidence_ids = evidence_by_filename.get(selected.filename, [])
        filing_coverage.append(
            {
                "source_filename": selected.filename,
                "fiscal_year": selected.fiscal_year,
                "period": selected.period,
                "period_label_ja": f"{selected.fiscal_year}年3月期",
                "is_latest": selected.filename == manifest.latest_filename,
                "coverage_status": (
                    "complete" if evidence_ids else "no_material_disclosure"
                ),
                "management_discussion_evidence_ids": evidence_ids[:3],
                "outlook_evidence_ids": [],
                "segment_evidence_ids": [],
                "cash_flow_evidence_ids": [],
                "capital_allocation_evidence_ids": [],
                "footnote_evidence_ids": [],
                "financial_observation_ids": [],
                "commentary_observation_ids": [],
                "disclosure_ids": [],
                "coverage_gaps": (
                    []
                    if evidence_ids
                    else ["Offline fixture contains no observations for this filing."]
                ),
            }
        )
    return JapaneseResearchDossier.model_validate(
        {
            "schema_version": "2.0-test",
            "identity": payload["identity"],
            "evidence": evidence,
            "filing_coverage": filing_coverage,
            "financial_observations": [],
            "commentary_observations": [],
            "disclosures": [],
            "business_drivers": [
                {
                    "driver_id": "driver-001",
                    "canonical_tag": "customer_demand",
                    "label_ja": "顧客需要",
                    "direction": "mixed",
                    "importance": "primary",
                    "nature": "cyclical",
                    "affected_area_ja": "主要事業",
                    "summary_ja": "需要動向が主要事業の業績に影響します。",
                    "observed_periods_ja": ["2025/3期", "2026/3期"],
                    "evidence_ids": [first_id, second_id],
                }
            ],
            "commitments": [],
            "management_themes": [
                {
                    "theme_id": "theme-001",
                    "label_ja": "事業基盤",
                    "development": "persistent",
                    "early_period_ja": "選定期間前半",
                    "middle_period_ja": "選定期間中盤",
                    "recent_period_ja": "選定期間後半",
                    "interpretation_ja": "事業基盤の重視が継続しています。",
                    "evidence_ids": [first_id, second_id],
                }
            ],
            "management_consistency": {
                "components": [
                    {
                        "dimension": dimension,
                        "rating": 2,
                        "evidence_sufficiency": "sufficient",
                        "rationale_ja": "選定資料では方針と結果の両方を確認できます。",
                        "evidence_ids": [first_id, second_id],
                    }
                    for dimension in dimensions
                ],
                "overall_rationale_ja": "選定資料の範囲では一貫性は混在しています。",
            },
            "research_notes": ["Stored offline test dossier."],
        }
    )


def dossier_from_analysis_payload(
    payload: dict,
    repository_root: Path | None = None,
) -> JapaneseResearchDossier:
    """Adapt a stored full-analysis fixture to the research-side test contract."""

    evidence = payload["evidence"]
    first_id = evidence[0]["evidence_id"]
    last_id = evidence[-1]["evidence_id"]
    management = payload.get("management_consistency") or {}
    components = management.get("components") or []
    if len(components) != 4:
        dimensions = (
            "strategic_coherence",
            "execution_follow_through",
            "forecast_target_discipline",
            "accountability_transparency",
        )
        components = [
            {
                "dimension": dimension,
                "rating": 2,
                "evidence_sufficiency": "sufficient",
                "rationale_ja": "選定資料の範囲で方針と結果を比較しました。",
                "evidence_ids": [first_id, last_id],
            }
            for dimension in dimensions
        ]
    root = repository_root or Path(__file__).resolve().parents[1]
    manifest = select_filings(root, payload["identity"]["security_code"])
    evidence_by_filename: dict[str, list[str]] = {}
    for item in evidence:
        evidence_by_filename.setdefault(item["source_filename"], []).append(
            item["evidence_id"]
        )
    filing_coverage = []
    for selected in manifest.selected_files:
        evidence_ids = evidence_by_filename.get(selected.filename, [])
        filing_coverage.append(
            {
                "source_filename": selected.filename,
                "fiscal_year": selected.fiscal_year,
                "period": selected.period,
                "period_label_ja": f"{selected.fiscal_year}年3月期",
                "is_latest": selected.filename == manifest.latest_filename,
                "coverage_status": (
                    "complete" if evidence_ids else "no_material_disclosure"
                ),
                "management_discussion_evidence_ids": evidence_ids[:3],
                "outlook_evidence_ids": [],
                "segment_evidence_ids": [],
                "cash_flow_evidence_ids": [],
                "capital_allocation_evidence_ids": [],
                "footnote_evidence_ids": [],
                "financial_observation_ids": [],
                "commentary_observation_ids": [],
                "disclosure_ids": [],
                "coverage_gaps": (
                    []
                    if evidence_ids
                    else ["Adapted fixture has no observations for this filing."]
                ),
            }
        )
    return JapaneseResearchDossier.model_validate(
        {
            "schema_version": payload.get("schema_version", "2.0-test"),
            "identity": payload["identity"],
            "evidence": evidence,
            "filing_coverage": filing_coverage,
            "financial_observations": [],
            "commentary_observations": [],
            "disclosures": [],
            "business_drivers": [
                {
                    "driver_id": "driver-fixture-001",
                    "canonical_tag": "customer_demand",
                    "label_ja": "顧客需要",
                    "direction": "mixed",
                    "importance": "primary",
                    "nature": "mixed",
                    "affected_area_ja": "主要事業",
                    "summary_ja": "選定資料で説明された主要な需要要因です。",
                    "observed_periods_ja": ["選定期間"],
                    "evidence_ids": [first_id],
                }
            ],
            "commitments": [],
            "management_themes": [
                {
                    "theme_id": "theme-fixture-001",
                    "label_ja": "長期テーマ",
                    "development": "persistent",
                    "early_period_ja": "選定期間前半",
                    "middle_period_ja": "選定期間中盤",
                    "recent_period_ja": "選定期間後半",
                    "interpretation_ja": "選定資料にまたがるテーマです。",
                    "evidence_ids": list(dict.fromkeys([first_id, last_id])),
                }
            ],
            "management_consistency": {
                "components": components,
                "overall_rationale_ja": management.get(
                    "overall_rationale_ja",
                    "選定資料の範囲で経営説明と後続結果を比較しました。",
                ),
            },
            "research_notes": ["Adapted offline regression fixture."],
        }
    )


def synthesis_from_analysis_payload(
    payload: dict,
) -> JapaneseSynthesisResponse:
    return JapaneseSynthesisResponse.model_validate(
        {
            "schema_version": payload.get("schema_version", "2.0-test"),
            "identity": payload["identity"],
            "claims": payload["claims"],
            "model_notes": payload.get("model_notes", []),
        }
    )


def fake_synthesis_response(repository_root: Path) -> JapaneseSynthesisResponse:
    payload = read_json(
        repository_root / "tests" / "fixtures" / "fake_analysis_ja.json"
    )
    return JapaneseSynthesisResponse.model_validate(payload)


def persist_fake_research(repository_root: Path, paths: object) -> None:
    dossier = fake_research_dossier(repository_root)
    write_json(paths.research_structured, dossier)


def persist_research_for_payload(paths: object, payload: dict) -> None:
    write_json(paths.research_structured, dossier_from_analysis_payload(payload))
