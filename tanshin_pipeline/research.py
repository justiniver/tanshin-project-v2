"""Deterministic summaries of the model-produced longitudinal research dossier."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from .schemas import JapaneseResearchDossier


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def validate_research_dossier(dossier: JapaneseResearchDossier) -> None:
    """Reject structurally unusable references before synthesis."""

    evidence_ids = {item.evidence_id for item in dossier.evidence}
    referenced: set[str] = set()
    for driver in dossier.business_drivers:
        referenced.update(driver.evidence_ids)
    for commitment in dossier.commitments:
        referenced.update(commitment.evidence_ids)
    for theme in dossier.management_themes:
        referenced.update(theme.evidence_ids)
    for component in dossier.management_consistency.components:
        referenced.update(component.evidence_ids)
    unresolved = sorted(referenced - evidence_ids)
    if unresolved:
        raise ValueError(
            "The research dossier references evidence IDs absent from its ledger: "
            + ", ".join(unresolved)
        )


def build_research_metrics(dossier: JapaneseResearchDossier) -> dict[str, Any]:
    """Produce auditable counts for the synthesis prompt and stored artifacts."""

    validate_research_dossier(dossier)
    commitments = dossier.commitments
    completed_forecasts = [
        item
        for item in commitments
        if item.commitment_type.value == "annual_forecast"
        and item.outcome_status.value
        not in {"pending", "not_observable"}
    ]
    assessed_targets = [
        item
        for item in commitments
        if item.commitment_type.value == "medium_term_target"
        and item.outcome_status.value
        not in {"pending", "not_observable"}
    ]
    observed_revisions = [
        item
        for item in commitments
        if item.revision_direction.value
        not in {"none_observed", "not_assessable"}
    ]
    return {
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
                if item.outcome_status.value in {"missed", "delayed", "withdrawn"}
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
        "coverage": {
            "evidence_records": len(dossier.evidence),
            "management_consistency_components": len(
                dossier.management_consistency.components
            ),
            "research_notes": list(dossier.research_notes),
        },
        "interpretation_guardrail": (
            "Counts describe only observations present in the selected filings. "
            "They are not a complete revision history unless the selected corpus "
            "contains every relevant revision disclosure."
        ),
    }
