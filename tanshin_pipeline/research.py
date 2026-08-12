"""Deterministic summaries of the model-produced longitudinal research dossier."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Iterable

from .english_financials import extract_japanese_financial_amounts
from .management_consistency import calculate_management_consistency
from .schemas import (
    CommentaryIntensity,
    JapaneseResearchDossier,
    ManagementConsistencyAssessment,
    ManagementConsistencyComponent,
    ManagementConsistencyDimension,
    ResearchCommentaryObservation,
    ResearchFinancialObservation,
    SelectionManifest,
)


_NUMBER_RE = re.compile(r"[+\-△▲]?\s*\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"([+\-△▲]?)\s*(\d[\d,]*(?:\.\d+)?)\s*[％%]")
_INTENSITY_ORDER = {
    CommentaryIntensity.LOW: 1,
    CommentaryIntensity.MODERATE: 2,
    CommentaryIntensity.HIGH: 3,
    CommentaryIntensity.NOT_ASSESSABLE: 0,
}


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _duplicates(values: Iterable[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _canonical_quote(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"[\s、。・,.;:：；（）()「」『』【】\[\]]+", "", normalized)


def _evidence_references(dossier: JapaneseResearchDossier) -> set[str]:
    referenced: set[str] = set()
    for coverage in dossier.filing_coverage:
        referenced.update(coverage.management_discussion_evidence_ids)
        referenced.update(coverage.outlook_evidence_ids)
        referenced.update(coverage.segment_evidence_ids)
        referenced.update(coverage.cash_flow_evidence_ids)
        referenced.update(coverage.capital_allocation_evidence_ids)
        referenced.update(coverage.footnote_evidence_ids)
    for observation in dossier.financial_observations:
        referenced.add(observation.evidence_id)
    for observation in dossier.commentary_observations:
        referenced.update(observation.evidence_ids)
    for disclosure in dossier.disclosures:
        referenced.update(disclosure.evidence_ids)
    for driver in dossier.business_drivers:
        referenced.update(driver.evidence_ids)
    for commitment in dossier.commitments:
        referenced.update(commitment.evidence_ids)
    for theme in dossier.management_themes:
        referenced.update(theme.evidence_ids)
    for component in dossier.management_consistency.components:
        referenced.update(component.evidence_ids)
    return referenced


def validate_research_dossier(
    dossier: JapaneseResearchDossier,
    manifest: SelectionManifest | None = None,
) -> None:
    """Reject structurally unusable or silently incomplete research output."""

    expected_dimensions = set(ManagementConsistencyDimension)
    supplied_dimensions = [
        component.dimension
        for component in dossier.management_consistency.components
    ]
    if (
        len(supplied_dimensions) != len(expected_dimensions)
        or set(supplied_dimensions) != expected_dimensions
    ):
        missing = sorted(
            dimension.value
            for dimension in expected_dimensions - set(supplied_dimensions)
        )
        duplicates = sorted(
            dimension.value
            for dimension, count in Counter(supplied_dimensions).items()
            if count > 1
        )
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if duplicates:
            details.append("duplicated " + ", ".join(duplicates))
        raise ValueError(
            "The research dossier must contain exactly one management-consistency "
            "component for each required dimension"
            + (": " + "; ".join(details) if details else ".")
        )

    identifier_groups = {
        "evidence": [item.evidence_id for item in dossier.evidence],
        "filing coverage": [
            item.source_filename for item in dossier.filing_coverage
        ],
        "financial observation": [
            item.observation_id for item in dossier.financial_observations
        ],
        "commentary observation": [
            item.observation_id for item in dossier.commentary_observations
        ],
        "disclosure": [item.disclosure_id for item in dossier.disclosures],
        "business driver": [item.driver_id for item in dossier.business_drivers],
        "commitment": [item.commitment_id for item in dossier.commitments],
        "management theme": [
            item.theme_id for item in dossier.management_themes
        ],
    }
    duplicate_messages = [
        f"{label}: {', '.join(duplicates)}"
        for label, values in identifier_groups.items()
        if (duplicates := _duplicates(values))
    ]
    if duplicate_messages:
        raise ValueError(
            "The research dossier contains duplicate identifiers: "
            + "; ".join(duplicate_messages)
        )

    evidence_by_id = {item.evidence_id: item for item in dossier.evidence}
    unresolved = sorted(_evidence_references(dossier) - set(evidence_by_id))
    if unresolved:
        raise ValueError(
            "The research dossier references evidence IDs absent from its ledger: "
            + ", ".join(unresolved)
        )

    financial_by_id = {
        item.observation_id: item for item in dossier.financial_observations
    }
    commentary_by_id = {
        item.observation_id: item for item in dossier.commentary_observations
    }
    disclosure_by_id = {
        item.disclosure_id: item for item in dossier.disclosures
    }
    covered_financial_ids: set[str] = set()
    covered_commentary_ids: set[str] = set()
    covered_disclosure_ids: set[str] = set()
    coverage_errors: list[str] = []
    for coverage in dossier.filing_coverage:
        coverage_evidence_ids = {
            *coverage.management_discussion_evidence_ids,
            *coverage.outlook_evidence_ids,
            *coverage.segment_evidence_ids,
            *coverage.cash_flow_evidence_ids,
            *coverage.capital_allocation_evidence_ids,
            *coverage.footnote_evidence_ids,
        }
        mismatched_evidence = sorted(
            evidence_id
            for evidence_id in coverage_evidence_ids
            if evidence_by_id[evidence_id].source_filename
            != coverage.source_filename
        )
        if mismatched_evidence:
            coverage_errors.append(
                f"{coverage.source_filename} lists evidence from another filing: "
                + ", ".join(mismatched_evidence)
            )
        for observation_id in coverage.financial_observation_ids:
            observation = financial_by_id.get(observation_id)
            if observation is None:
                coverage_errors.append(
                    f"{coverage.source_filename} references unknown financial "
                    f"observation {observation_id}"
                )
            elif observation.source_filename != coverage.source_filename:
                coverage_errors.append(
                    f"{observation_id} belongs to {observation.source_filename}, "
                    f"not {coverage.source_filename}"
                )
            covered_financial_ids.add(observation_id)
        for observation_id in coverage.commentary_observation_ids:
            observation = commentary_by_id.get(observation_id)
            if observation is None:
                coverage_errors.append(
                    f"{coverage.source_filename} references unknown commentary "
                    f"observation {observation_id}"
                )
            elif observation.source_filename != coverage.source_filename:
                coverage_errors.append(
                    f"{observation_id} belongs to {observation.source_filename}, "
                    f"not {coverage.source_filename}"
                )
            covered_commentary_ids.add(observation_id)
        for disclosure_id in coverage.disclosure_ids:
            disclosure = disclosure_by_id.get(disclosure_id)
            if disclosure is None:
                coverage_errors.append(
                    f"{coverage.source_filename} references unknown disclosure "
                    f"{disclosure_id}"
                )
            elif disclosure.source_filename != coverage.source_filename:
                coverage_errors.append(
                    f"{disclosure_id} belongs to {disclosure.source_filename}, "
                    f"not {coverage.source_filename}"
                )
            covered_disclosure_ids.add(disclosure_id)
    orphaned = {
        "financial": sorted(set(financial_by_id) - covered_financial_ids),
        "commentary": sorted(set(commentary_by_id) - covered_commentary_ids),
        "disclosure": sorted(set(disclosure_by_id) - covered_disclosure_ids),
    }
    for label, identifiers in orphaned.items():
        if identifiers:
            coverage_errors.append(
                f"unassigned {label} records: {', '.join(identifiers)}"
            )
    if coverage_errors:
        raise ValueError(
            "The filing coverage ledger is inconsistent: "
            + "; ".join(coverage_errors)
        )

    selected_names = (
        {item.filename for item in manifest.selected_files}
        if manifest is not None
        else {item.source_filename for item in dossier.filing_coverage}
    )
    coverage_names = {
        item.source_filename for item in dossier.filing_coverage
    }
    if coverage_names != selected_names:
        missing = sorted(selected_names - coverage_names)
        extra = sorted(coverage_names - selected_names)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unselected " + ", ".join(extra))
        raise ValueError(
            "The filing coverage ledger must contain exactly one record for every "
            "selected filing: " + "; ".join(details)
        )

    latest_records = [
        item for item in dossier.filing_coverage if item.is_latest
    ]
    if len(latest_records) != 1:
        raise ValueError(
            "The filing coverage ledger must mark exactly one filing as latest."
        )
    if manifest is not None:
        latest = latest_records[0]
        if latest.source_filename != manifest.latest_filename:
            raise ValueError(
                "The filing coverage ledger marks the wrong latest filing."
            )
        manifest_by_name = {
            item.filename: item for item in manifest.selected_files
        }
        for coverage in dossier.filing_coverage:
            selected = manifest_by_name[coverage.source_filename]
            if (
                coverage.fiscal_year != selected.fiscal_year
                or coverage.period != selected.period
            ):
                raise ValueError(
                    "The filing coverage period does not match the selection "
                    f"manifest for {coverage.source_filename}."
                )
        invalid_sources = sorted(
            {
                item.source_filename
                for item in dossier.evidence
                if item.source_filename not in selected_names
            }
            | {
                item.source_filename
                for item in dossier.financial_observations
                if item.source_filename not in selected_names
            }
            | {
                item.source_filename
                for item in dossier.commentary_observations
                if item.source_filename not in selected_names
            }
            | {
                item.source_filename
                for item in dossier.disclosures
                if item.source_filename not in selected_names
            }
        )
        if invalid_sources:
            raise ValueError(
                "The research dossier uses unselected source files: "
                + ", ".join(invalid_sources)
            )

    source_mismatches: list[str] = []
    value_surface_mismatches: list[str] = []
    for observation in dossier.financial_observations:
        evidence = evidence_by_id[observation.evidence_id]
        if evidence.source_filename != observation.source_filename:
            source_mismatches.append(observation.observation_id)
        source_text = _normalized_label(evidence.exact_quote_ja)
        value_surface = _normalized_label(observation.value_surface_ja)
        if value_surface not in source_text:
            value_surface_mismatches.append(observation.observation_id)
    for observation in dossier.commentary_observations:
        if any(
            evidence_by_id[evidence_id].source_filename
            != observation.source_filename
            for evidence_id in observation.evidence_ids
        ):
            source_mismatches.append(observation.observation_id)
    for disclosure in dossier.disclosures:
        if any(
            evidence_by_id[evidence_id].source_filename
            != disclosure.source_filename
            for evidence_id in disclosure.evidence_ids
        ):
            source_mismatches.append(disclosure.disclosure_id)
    if source_mismatches:
        raise ValueError(
            "Research records cite evidence from a different source filing: "
            + ", ".join(sorted(source_mismatches))
        )
    if value_surface_mismatches:
        raise ValueError(
            "Financial observations use value surfaces absent from their cited "
            "evidence: " + ", ".join(sorted(value_surface_mismatches))
        )


def _numeric_value(
    observation: ResearchFinancialObservation,
) -> Decimal | None:
    surface = unicodedata.normalize("NFKC", observation.value_surface_ja)
    if observation.value_kind.value in {"monetary", "per_share"}:
        amounts = extract_japanese_financial_amounts(surface)
        return amounts[0].yen_value if len(amounts) == 1 else None
    if observation.value_kind.value == "percentage":
        match = _PERCENT_RE.search(surface)
        if match is None:
            return None
        value = Decimal(match.group(2).replace(",", ""))
        return -value if match.group(1) in {"-", "△", "▲"} else value
    if observation.value_kind.value in {"count", "ratio"}:
        match = _NUMBER_RE.search(surface)
        if match is None:
            return None
        raw = re.sub(r"\s+", "", match.group()).replace(",", "")
        negative = raw.startswith(("-", "△", "▲"))
        raw = raw.lstrip("+-△▲")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return None
        return -value if negative else value
    return None


def _financial_key(
    observation: ResearchFinancialObservation,
) -> tuple[str, str, str, int, str, str]:
    scope_label = (
        ""
        if observation.scope.value in {"consolidated", "company_only"}
        else _normalized_label(observation.scope_label_ja)
    )
    return (
        observation.metric.value,
        observation.scope.value,
        scope_label,
        observation.target_fiscal_year,
        observation.target_period.value,
        observation.value_kind.value,
    )


def _forecast_metrics(
    observations: list[ResearchFinancialObservation],
) -> dict[str, Any]:
    actual_by_key: dict[
        tuple[str, str, str, int, str, str],
        list[ResearchFinancialObservation],
    ] = defaultdict(list)
    for observation in observations:
        if observation.statement_type == "actual":
            actual_by_key[_financial_key(observation)].append(observation)

    comparisons: list[dict[str, Any]] = []
    seen_forecasts: set[
        tuple[tuple[str, str, str, int, str, str], str, Decimal]
    ] = set()
    for forecast in observations:
        if forecast.statement_type != "forecast":
            continue
        actuals = actual_by_key.get(_financial_key(forecast), [])
        forecast_value = _numeric_value(forecast)
        actual_values = {
            value
            for item in actuals
            if (value := _numeric_value(item)) is not None
        }
        if forecast_value is None or len(actual_values) != 1:
            continue
        actual_value = next(iter(actual_values))
        actual = next(
            item
            for item in actuals
            if _numeric_value(item) == actual_value
        )
        forecast_identity = (
            _financial_key(forecast),
            forecast.forecast_version.value,
            forecast_value,
        )
        if forecast_identity in seen_forecasts:
            continue
        seen_forecasts.add(forecast_identity)
        delta = actual_value - forecast_value
        error_pct = (
            delta / abs(forecast_value) * Decimal("100")
            if forecast_value != 0
            else None
        )
        if error_pct is None:
            result = "not_comparable"
        elif error_pct > Decimal("3"):
            result = "actual_above_forecast"
        elif error_pct < Decimal("-3"):
            result = "actual_below_forecast"
        else:
            result = "broadly_in_line"
        comparisons.append(
            {
                "forecast_observation_id": forecast.observation_id,
                "actual_observation_id": actual.observation_id,
                "metric": forecast.metric.value,
                "metric_label_ja": forecast.metric_label_ja,
                "scope": forecast.scope.value,
                "scope_label_ja": forecast.scope_label_ja,
                "target_fiscal_year": forecast.target_fiscal_year,
                "target_period": forecast.target_period.value,
                "forecast_version": forecast.forecast_version.value,
                "forecast_surface_ja": forecast.value_surface_ja,
                "actual_surface_ja": actual.value_surface_ja,
                "difference": float(delta),
                "percentage_error": (
                    round(float(error_pct), 4)
                    if error_pct is not None
                    else None
                ),
                "result": result,
                "evidence_ids": [
                    forecast.evidence_id,
                    actual.evidence_id,
                ],
            }
        )

    errors = [
        abs(item["percentage_error"])
        for item in comparisons
        if item["percentage_error"] is not None
    ]
    return {
        "observable_comparisons": len(comparisons),
        "result_counts": _counts(item["result"] for item in comparisons),
        "mean_absolute_percentage_error": (
            round(sum(errors) / len(errors), 4) if errors else None
        ),
        "by_metric": _counts(item["metric"] for item in comparisons),
        "comparisons": comparisons,
    }


def _observation_text(
    observation: ResearchCommentaryObservation,
    evidence_by_id: dict[str, Any],
) -> str:
    return " ".join(
        evidence_by_id[evidence_id].exact_quote_ja
        for evidence_id in observation.evidence_ids
        if evidence_id in evidence_by_id
    )


def _commentary_metrics(dossier: JapaneseResearchDossier) -> dict[str, Any]:
    evidence_by_id = {item.evidence_id: item for item in dossier.evidence}
    by_tag: dict[str, list[ResearchCommentaryObservation]] = defaultdict(list)
    for observation in dossier.commentary_observations:
        by_tag[observation.canonical_tag].append(observation)

    changes: list[dict[str, Any]] = []
    for tag, observations in sorted(by_tag.items()):
        ordered = sorted(
            observations,
            key=lambda item: (
                item.fiscal_year,
                item.source_filename,
            ),
        )
        for previous, current in zip(ordered, ordered[1:]):
            previous_text = _canonical_quote(
                _observation_text(previous, evidence_by_id)
            )
            current_text = _canonical_quote(
                _observation_text(current, evidence_by_id)
            )
            similarity = (
                SequenceMatcher(None, previous_text, current_text).ratio()
                if previous_text and current_text
                else 0.0
            )
            previous_intensity = _INTENSITY_ORDER[previous.intensity]
            current_intensity = _INTENSITY_ORDER[current.intensity]
            if (
                previous.intensity != CommentaryIntensity.NOT_ASSESSABLE
                and current.intensity != CommentaryIntensity.NOT_ASSESSABLE
                and current_intensity > previous_intensity
            ):
                change_type = "intensified"
            elif (
                previous.intensity != CommentaryIntensity.NOT_ASSESSABLE
                and current.intensity != CommentaryIntensity.NOT_ASSESSABLE
                and current_intensity < previous_intensity
            ):
                change_type = "softened"
            elif previous.tone != current.tone:
                change_type = "tone_changed"
            elif similarity >= 0.97:
                change_type = "substantially_unchanged"
            elif similarity >= 0.72:
                change_type = "wording_changed"
            else:
                change_type = "reframed"
            changes.append(
                {
                    "canonical_tag": tag,
                    "label_ja": current.label_ja,
                    "from_observation_id": previous.observation_id,
                    "to_observation_id": current.observation_id,
                    "from_period_ja": previous.period_label_ja,
                    "to_period_ja": current.period_label_ja,
                    "from_tone": previous.tone.value,
                    "to_tone": current.tone.value,
                    "from_intensity": previous.intensity.value,
                    "to_intensity": current.intensity.value,
                    "lexical_similarity": round(similarity, 4),
                    "change_type": change_type,
                    "evidence_ids": list(
                        dict.fromkeys(
                            [
                                *previous.evidence_ids,
                                *current.evidence_ids,
                            ]
                        )
                    ),
                }
            )
    return {
        "observations": len(dossier.commentary_observations),
        "distinct_tags": len(by_tag),
        "observations_by_tag": {
            tag: len(items) for tag, items in sorted(by_tag.items())
        },
        "change_counts": _counts(item["change_type"] for item in changes),
        "changes": changes,
        "interpretation_guardrail": (
            "Lexical and tone changes compare only extracted observations in "
            "selected filings. They identify review signals, not proof that an "
            "omitted topic disappeared from management discussion."
        ),
    }


def _management_consistency_metrics(
    dossier: JapaneseResearchDossier,
    manifest: SelectionManifest | None,
) -> dict[str, Any]:
    pending = ManagementConsistencyAssessment(
        methodology_version="management-consistency-research-input",
        components=[
            ManagementConsistencyComponent(
                dimension=component.dimension,
                rating=component.rating,
                evidence_sufficiency=component.evidence_sufficiency,
                normalized_score=(
                    component.rating / 4
                    if component.rating is not None
                    else None
                ),
                weight=0,
                rationale_ja=component.rationale_ja,
                evidence_ids=list(component.evidence_ids),
            )
            for component in dossier.management_consistency.components
        ],
        overall_rationale_ja=(
            dossier.management_consistency.overall_rationale_ja
        ),
    )
    if manifest is not None:
        calculated, _ = calculate_management_consistency(
            pending,
            dossier.evidence,
            manifest,
        )
        return {
            "score": calculated.score,
            "evidence_confidence": calculated.evidence_confidence,
            "components": [
                {
                    "dimension": component.dimension.value,
                    "subscore": component.normalized_score,
                    "evidence_sufficiency": component.evidence_sufficiency,
                    "evidence_confidence": component.evidence_confidence,
                }
                for component in calculated.components
            ],
        }
    available = [
        component.normalized_score
        for component in pending.components
        if component.normalized_score is not None
    ]
    return {
        "score": (
            round(sum(available) / len(available), 2)
            if available
            else 0.5
        ),
        "evidence_confidence": None,
        "components": [
            {
                "dimension": component.dimension.value,
                "subscore": component.normalized_score,
                "evidence_sufficiency": component.evidence_sufficiency,
                "evidence_confidence": None,
            }
            for component in pending.components
        ],
    }


def build_research_metrics(
    dossier: JapaneseResearchDossier,
    manifest: SelectionManifest | None = None,
) -> dict[str, Any]:
    """Produce auditable comparisons and coverage summaries for synthesis."""

    validate_research_dossier(dossier, manifest)
    commitments = dossier.commitments
    completed_forecasts = [
        item
        for item in commitments
        if item.commitment_type.value == "annual_forecast"
        and item.outcome_status.value not in {"pending", "not_observable"}
    ]
    assessed_targets = [
        item
        for item in commitments
        if item.commitment_type.value == "medium_term_target"
        and item.outcome_status.value not in {"pending", "not_observable"}
    ]
    observed_revisions = [
        item
        for item in commitments
        if item.revision_direction.value
        not in {"none_observed", "not_assessable"}
    ]
    observable_commitments = [
        item
        for item in commitments
        if item.outcome_status.value not in {"pending", "not_observable"}
    ]
    achieved_commitments = [
        item
        for item in observable_commitments
        if item.outcome_status.value in {"achieved", "exceeded"}
    ]
    partial_commitments = [
        item
        for item in observable_commitments
        if item.outcome_status.value in {"partly_achieved", "revised"}
    ]
    shortfall_commitments = [
        item
        for item in observable_commitments
        if item.outcome_status.value in {"missed", "delayed", "withdrawn"}
    ]
    latest_coverage = next(
        item for item in dossier.filing_coverage if item.is_latest
    )
    coverage_status_counts = _counts(
        item.coverage_status.value for item in dossier.filing_coverage
    )
    coverage_gaps = [
        {
            "source_filename": item.source_filename,
            "gaps": list(item.coverage_gaps),
        }
        for item in dossier.filing_coverage
        if item.coverage_gaps
    ]
    return {
        "filing_coverage": {
            "selected_filings": len(dossier.filing_coverage),
            "status_counts": coverage_status_counts,
            "filings_with_explicit_gaps": len(coverage_gaps),
            "coverage_gaps": coverage_gaps,
            "latest_filing": {
                "source_filename": latest_coverage.source_filename,
                "financial_observations": len(
                    latest_coverage.financial_observation_ids
                ),
                "management_discussion_evidence": len(
                    latest_coverage.management_discussion_evidence_ids
                ),
                "outlook_evidence": len(latest_coverage.outlook_evidence_ids),
                "segment_evidence": len(latest_coverage.segment_evidence_ids),
                "cash_flow_evidence": len(latest_coverage.cash_flow_evidence_ids),
                "capital_allocation_evidence": len(
                    latest_coverage.capital_allocation_evidence_ids
                ),
                "footnote_evidence": len(
                    latest_coverage.footnote_evidence_ids
                ),
            },
        },
        "financial_observations": {
            "total": len(dossier.financial_observations),
            "by_metric": _counts(
                item.metric.value for item in dossier.financial_observations
            ),
            "by_statement_type": _counts(
                item.statement_type
                for item in dossier.financial_observations
            ),
            "forecast_version_counts": _counts(
                item.forecast_version.value
                for item in dossier.financial_observations
                if item.statement_type == "forecast"
            ),
            "explicit_revised_forecast_observations": len(
                [
                    item
                    for item in dossier.financial_observations
                    if item.statement_type == "forecast"
                    and item.forecast_version.value == "revised"
                ]
            ),
            "forecast_accuracy": _forecast_metrics(
                dossier.financial_observations
            ),
        },
        "commentary": _commentary_metrics(dossier),
        "disclosures": {
            "total": len(dossier.disclosures),
            "by_category": _counts(
                item.category.value for item in dossier.disclosures
            ),
            "primary_ids": [
                item.disclosure_id
                for item in dossier.disclosures
                if item.importance == "primary"
            ],
        },
        "business_drivers": {
            "total": len(dossier.business_drivers),
            "by_direction": _counts(
                item.direction.value for item in dossier.business_drivers
            ),
            "by_nature": _counts(
                item.nature.value for item in dossier.business_drivers
            ),
            "primary_driver_ids": [
                item.driver_id
                for item in dossier.business_drivers
                if item.importance == "primary"
            ],
        },
        "commitments": {
            "total": len(commitments),
            "by_type": _counts(
                item.commitment_type.value for item in commitments
            ),
            "by_outcome": _counts(
                item.outcome_status.value for item in commitments
            ),
            "observable_outcomes": len(observable_commitments),
            "observable_outcome_share": (
                round(len(observable_commitments) / len(commitments), 4)
                if commitments
                else None
            ),
            "target_follow_through": {
                "assessed": len(observable_commitments),
                "achieved_or_exceeded": len(achieved_commitments),
                "partly_achieved_or_revised": len(partial_commitments),
                "missed_delayed_or_withdrawn": len(shortfall_commitments),
                "achievement_rate": (
                    round(
                        len(achieved_commitments)
                        / len(observable_commitments),
                        4,
                    )
                    if observable_commitments
                    else None
                ),
            },
            "annual_forecasts_with_observable_outcomes": len(completed_forecasts),
            "medium_term_targets_with_observable_outcomes": len(assessed_targets),
            "observed_revision_count": len(observed_revisions),
            "revision_direction_counts": _counts(
                item.revision_direction.value for item in observed_revisions
            ),
            "forecast_posture_counts": _counts(
                item.forecast_posture.value
                for item in completed_forecasts
                if item.forecast_posture.value != "not_assessable"
            ),
            "missed_or_delayed_ids": [
                item.commitment_id
                for item in commitments
                if item.outcome_status.value
                in {"missed", "delayed", "withdrawn", "partly_achieved"}
            ],
        },
        "management_themes": {
            "total": len(dossier.management_themes),
            "by_development": _counts(
                item.development.value for item in dossier.management_themes
            ),
            "changed_or_abandoned_ids": [
                item.theme_id
                for item in dossier.management_themes
                if item.development.value
                in {"changed", "deprioritized", "abandoned"}
            ],
        },
        "management_consistency": _management_consistency_metrics(
            dossier,
            manifest,
        ),
        "coverage": {
            "evidence_records": len(dossier.evidence),
            "management_consistency_components": len(
                dossier.management_consistency.components
            ),
            "research_notes": list(dossier.research_notes),
        },
        "interpretation_guardrail": (
            "Counts and comparisons describe only observations present in the "
            "selected filings in the year-end/latest corpus. They are not a complete "
            "revision history and do not include peer or quarterly analysis."
        ),
    }
