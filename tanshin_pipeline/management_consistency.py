"""Auditable management-consistency scoring from model-rated components."""

from __future__ import annotations

import re
from typing import Any

from .schemas import (
    EvidenceRecord,
    ManagementConsistencyAssessment,
    ManagementConsistencyComponent,
    ManagementConsistencyDimension,
    SelectionManifest,
)


METHODOLOGY_VERSION = "management-consistency-v6"
COMPONENT_WEIGHTS = {
    ManagementConsistencyDimension.STRATEGIC_COHERENCE: 0.25,
    ManagementConsistencyDimension.EXECUTION_FOLLOW_THROUGH: 0.25,
    ManagementConsistencyDimension.FORECAST_TARGET_DISCIPLINE: 0.25,
    ManagementConsistencyDimension.ACCOUNTABILITY_TRANSPARENCY: 0.25,
}

_MANAGEMENT_DISCUSSION_RE = re.compile(
    r"経営成績等の概況|当(?:連結)?会計年度(?:における)?(?:業績|の概況)|"
    r"業績の概況|財政状態(?:に関する説明|の概況)|"
    r"キャッシュ.?フロー(?:に関する説明|の概況)|"
    r"業績予想などの将来予測情報に関する説明|将来予測|今後の見通し|"
    r"利益配分|配当方針|株主還元方針|経営目標|"
    r"経営方針|中期経営|セグメントの業績|"
    r"(?:^|・).{1,30}事業(?:の概況|の業績)?$|"
    r"management discussion|results|financial position|cash flows?|"
    r"outlook|forecast|dividend policy|management plan",
    re.IGNORECASE,
)
_RAW_TABLE_SECTION_RE = re.compile(
    r"^(?:連結)?(?:経営成績|財政状態|キャッシュ・フローの状況|"
    r"業績予想|損益計算書|貸借対照表)$"
)


def is_management_discussion_evidence(evidence: EvidenceRecord) -> bool:
    """Return whether an evidence record comes from management commentary."""

    if any(tag.lower() == "management_discussion" for tag in evidence.tags):
        return True
    source_section = evidence.source_section.strip()
    if _RAW_TABLE_SECTION_RE.fullmatch(source_section):
        return False
    return bool(_MANAGEMENT_DISCUSSION_RE.search(source_section))


def _period_bucket_map(years: list[int]) -> dict[int, str]:
    ordered = sorted(set(years))
    if not ordered:
        return {}
    return {
        year: ("early", "middle", "recent")[
            min(2, index * 3 // len(ordered))
        ]
        for index, year in enumerate(ordered)
    }


def _confidence_label(value: float) -> str:
    if value >= 0.75:
        return "high"
    if value >= 0.50:
        return "moderate"
    return "low"


def calculate_management_consistency(
    assessment: ManagementConsistencyAssessment | None,
    evidence: list[EvidenceRecord],
    manifest: SelectionManifest,
) -> tuple[ManagementConsistencyAssessment, list[dict[str, Any]]]:
    """Apply fixed weights and calculate a separate evidence-confidence score.

    Gemini supplies component ratings, sufficiency judgments, and rationales.
    Python owns the weights, evidence resolution, confidence measure, and the
    published 0-1 scale. A model rating supported by at least one selected,
    resolvable evidence record remains scored; uneven period coverage lowers
    evidence confidence instead of erasing the rating. Components remain blank
    only when the research pass explicitly returns no rating or supplies no
    usable evidence. If every component is unavailable, the overall neutral
    fallback is 0.50 while all component scores remain blank.
    """

    if assessment is None:
        assessment = ManagementConsistencyAssessment(
            methodology_version="management-consistency-pending",
            components=[],
            overall_rationale_ja=(
                "経営一貫性のモデル評価入力がないため、全項目を中立値で補完しています。"
            ),
        )

    evidence_by_id = {item.evidence_id: item for item in evidence}
    filing_by_name = {item.filename: item for item in manifest.selected_files}
    bucket_by_year = _period_bucket_map(manifest.window.unique_years)
    supplied_by_dimension: dict[
        ManagementConsistencyDimension, ManagementConsistencyComponent
    ] = {}
    for component in assessment.components:
        supplied_by_dimension.setdefault(component.dimension, component)

    completed_components: list[ManagementConsistencyComponent] = []
    all_valid_evidence_ids: set[str] = set()
    all_years: set[int] = set()
    management_discussion_ids: set[str] = set()
    for dimension, weight in COMPONENT_WEIGHTS.items():
        supplied = supplied_by_dimension.get(dimension)
        if supplied is None:
            supplied = ManagementConsistencyComponent(
                dimension=dimension,
                rating=None,
                evidence_sufficiency="insufficient",
                normalized_score=None,
                weight=weight,
                rationale_ja="当該項目を評価する十分な比較情報がありません。",
                evidence_ids=[],
            )
        valid_ids = {
            evidence_id
            for evidence_id in supplied.evidence_ids
            if evidence_id in evidence_by_id
        }
        years = {
            filing_by_name[evidence_by_id[evidence_id].source_filename].fiscal_year
            for evidence_id in valid_ids
            if evidence_by_id[evidence_id].source_filename in filing_by_name
        }
        management_ids = {
            evidence_id
            for evidence_id in valid_ids
            if is_management_discussion_evidence(evidence_by_id[evidence_id])
        }
        covered_buckets = {
            bucket_by_year[year] for year in years if year in bucket_by_year
        }
        period_bucket_coverage = len(covered_buckets) / 3
        distinct_year_coverage = min(len(years) / 3, 1.0)
        evidence_count_coverage = min(len(valid_ids) / 3, 1.0)
        management_share = (
            len(management_ids) / len(valid_ids) if valid_ids else 0.0
        )
        component_confidence = (
            0.55 * period_bucket_coverage
            + 0.20 * distinct_year_coverage
            + 0.15 * evidence_count_coverage
            + 0.10 * management_share
        )
        if dimension not in supplied_by_dimension:
            component_confidence = 0.0
        # The research request has already reviewed the complete selected
        # filing set and made the substantive rating. Local coverage metrics
        # are valuable confidence diagnostics, but they must not contradict
        # that rating merely because its strongest cited examples fall into
        # one period bucket. This was the source of numeric explanations being
        # rendered beside blank subscores.
        locally_scorable = supplied.rating is not None and bool(valid_ids)
        evidence_sufficiency = (
            "sufficient"
            if locally_scorable
            else "insufficient"
        )
        rating = supplied.rating if locally_scorable else None
        all_valid_evidence_ids.update(valid_ids)
        all_years.update(years)
        management_discussion_ids.update(management_ids)
        completed_components.append(
            ManagementConsistencyComponent(
                dimension=dimension,
                rating=rating,
                evidence_sufficiency=evidence_sufficiency,
                normalized_score=(
                    round(rating / 4, 4) if rating is not None else None
                ),
                weight=weight,
                rationale_ja=supplied.rationale_ja,
                evidence_ids=list(supplied.evidence_ids),
                distinct_fiscal_years=sorted(years),
                management_discussion_evidence_count=len(management_ids),
                covered_period_buckets=sorted(
                    covered_buckets,
                    key=("early", "middle", "recent").index,
                ),
                evidence_confidence=round(component_confidence, 4),
            )
        )

    available_scores = [
        component.normalized_score
        for component in completed_components
        if (
            component.evidence_sufficiency == "sufficient"
            and component.normalized_score is not None
        )
    ]
    excluded_dimensions = [
        component.dimension
        for component in completed_components
        if component.normalized_score is None
    ]
    all_components_missing = not available_scores
    raw_score = (
        sum(available_scores) / len(available_scores)
        if available_scores
        else 0.5
    )
    discussion_share = (
        len(management_discussion_ids) / len(all_valid_evidence_ids)
        if all_valid_evidence_ids
        else 0.0
    )
    evidence_confidence = sum(
        component.evidence_confidence * component.weight
        for component in completed_components
    )
    completed = ManagementConsistencyAssessment(
        methodology_version=METHODOLOGY_VERSION,
        score=round(raw_score, 2),
        raw_score=round(raw_score, 4),
        evidence_confidence=round(evidence_confidence, 4),
        confidence_label=_confidence_label(evidence_confidence),
        evidence_coverage=round(evidence_confidence, 4),
        distinct_fiscal_years=sorted(all_years),
        evidence_count=len(all_valid_evidence_ids),
        management_discussion_evidence_share=round(discussion_share, 4),
        components=completed_components,
        overall_rationale_ja=assessment.overall_rationale_ja,
    )
    changes = [
        {
            "type": "management_consistency_calculated",
            "methodology_version": METHODOLOGY_VERSION,
            "score": completed.score,
            "raw_score": completed.raw_score,
            "evidence_confidence": completed.evidence_confidence,
            "confidence_label": completed.confidence_label,
            "evidence_coverage": completed.evidence_coverage,
            "distinct_fiscal_years": completed.distinct_fiscal_years,
            "evidence_count": completed.evidence_count,
            "management_discussion_evidence_share": (
                completed.management_discussion_evidence_share
            ),
            "insufficient_dimensions": [
                component.dimension.value
                for component in completed.components
                if component.evidence_sufficiency == "insufficient"
            ],
            "excluded_dimensions": [
                dimension.value for dimension in excluded_dimensions
            ],
            "score_calculation_method": (
                "neutral_0.50_fallback_no_scorable_components"
                if all_components_missing
                else "arithmetic_mean_of_available_normalized_components"
            ),
            "available_component_count": len(available_scores),
            "weights": {
                dimension.value: weight
                for dimension, weight in COMPONENT_WEIGHTS.items()
            },
        }
    ]
    return completed, changes
