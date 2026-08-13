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


def _memo_items(
    *,
    source_filename: str,
    is_latest: bool,
    evidence: list[dict],
) -> list[dict]:
    matching = [
        item for item in evidence if item["source_filename"] == source_filename
    ]
    seed = matching[0] if matching else None
    page = int(seed["pdf_page"]) if seed else 1
    statement_type = seed["statement_type"] if seed else "actual"
    summary = (
        seed["exact_quote_ja"]
        if seed
        else f"{source_filename} のオフライン検証用経営説明。"
    )
    items = [
        {
            "category": "operating_results",
            "pdf_page": page,
            "statement_type": statement_type,
            "summary_ja": summary,
        },
        {
            "category": "financial_condition",
            "pdf_page": page,
            "statement_type": "actual",
            "summary_ja": f"{summary} 財政状態を含む。",
        },
        {
            "category": "forward_looking_information",
            "pdf_page": page,
            "statement_type": "forecast",
            "summary_ja": f"{summary} 将来見通しを含む。",
        },
    ]
    if is_latest:
        items.append(
            {
                "category": "business_overview",
                "pdf_page": page,
                "statement_type": "actual",
                "summary_ja": f"{summary} 主要事業の概要を含む。",
            }
        )
    return items


def _research_payload_from_analysis(
    payload: dict,
    repository_root: Path,
) -> dict:
    security_code = payload["identity"]["security_code"]
    manifest = select_filings(repository_root, security_code)
    identity = dict(payload["identity"])
    identity["latest_filename"] = manifest.latest_filename
    evidence = payload.get("evidence", [])
    selected_names = {item.filename for item in manifest.selected_files}
    usable_evidence = [
        item for item in evidence if item["source_filename"] in selected_names
    ]
    filings = []
    for selected in sorted(
        manifest.selected_files,
        key=lambda item: (item.fiscal_year, item.filename),
    ):
        is_latest = selected.filename == manifest.latest_filename
        filings.append(
            {
                "source_filename": selected.filename,
                "fiscal_year": selected.fiscal_year,
                "period": selected.period,
                "period_label_ja": f"FY{selected.fiscal_year}",
                "is_latest": is_latest,
                "pdf_page_count": selected.page_count,
                "items": _memo_items(
                    source_filename=selected.filename,
                    is_latest=is_latest,
                    evidence=usable_evidence,
                ),
                "annual_financial_anchor": None,
                "unavailable_categories": [],
                "notes": [],
            }
        )
    return {
        "schema_version": "2.3-test",
        "identity": identity,
        "filings": filings,
        "research_notes": ["Stored offline chronological research map."],
    }


def fake_research_dossier(
    repository_root: Path,
    security_code: str = "1808",
) -> JapaneseResearchDossier:
    """Build a small, internally consistent chronological research map."""

    payload = read_json(
        repository_root / "tests" / "fixtures" / "fake_analysis_ja.json"
    )
    payload["identity"]["security_code"] = security_code
    manifest = select_filings(repository_root, security_code)
    payload["identity"]["latest_filename"] = manifest.latest_filename
    if security_code != "1808":
        for item in payload.get("evidence", []):
            item["source_filename"] = manifest.latest_filename
    return JapaneseResearchDossier.model_validate(
        _research_payload_from_analysis(payload, repository_root)
    )


def dossier_from_analysis_payload(
    payload: dict,
    repository_root: Path | None = None,
) -> JapaneseResearchDossier:
    """Adapt a stored full-analysis fixture to the research-map contract."""

    root = repository_root or Path(__file__).resolve().parents[1]
    return JapaneseResearchDossier.model_validate(
        _research_payload_from_analysis(payload, root)
    )


def _source_reference(evidence: dict) -> dict:
    return {
        "source_filename": evidence["source_filename"],
        "pdf_page": evidence["pdf_page"],
        "source_section": evidence["source_section"],
        "statement_type": evidence["statement_type"],
        "support_summary_ja": evidence["exact_quote_ja"],
    }


def synthesis_from_analysis_payload(
    payload: dict,
) -> JapaneseSynthesisResponse:
    evidence_by_id = {
        item["evidence_id"]: item for item in payload.get("evidence", [])
    }
    fallback = next(iter(evidence_by_id.values()))
    claims = []
    for claim in payload["claims"]:
        linked = [
            evidence_by_id[value]
            for value in claim.get("evidence_ids", [])
            if value in evidence_by_id
        ] or [fallback]
        claims.append(
            {
                "claim_id": claim["claim_id"],
                "section": claim["section"],
                "order": claim["order"],
                "headline_ja": claim["headline_ja"],
                "body_ja": claim["body_ja"],
                "sources": [_source_reference(item) for item in linked],
                "statement_type": claim["statement_type"],
                "is_inference": claim.get("is_inference", False),
                "causal": claim.get("causal", False),
            }
        )

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
            evidence_by_id[value]
            for value in source.get("evidence_ids", [])
            if value in evidence_by_id
        ] or [fallback]
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
                "sources": [_source_reference(item) for item in linked],
            }
        )

    return JapaneseSynthesisResponse.model_validate(
        {
            "schema_version": "2.3-test",
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
    write_json(paths.research_structured, fake_research_dossier(repository_root))


def persist_research_for_payload(paths: object, payload: dict) -> None:
    write_json(paths.research_structured, dossier_from_analysis_payload(payload))
