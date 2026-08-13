"""Deterministic summaries of the model-produced filing extraction dossier."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any, Iterable

from .english_financials import extract_japanese_financial_amounts
from .schemas import (
    CommentaryIntensity,
    JapaneseResearchDossier,
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


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", "", value).casefold()


def _canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"[\s、。・,.;:：；（）()「」『』【】\[\]]+", "", normalized)


def _record_references(dossier: JapaneseResearchDossier) -> set[str]:
    referenced: set[str] = set()
    for coverage in dossier.filing_coverage:
        referenced.update(coverage.management_discussion_record_ids)
        referenced.update(coverage.outlook_record_ids)
        referenced.update(coverage.segment_record_ids)
        referenced.update(coverage.cash_flow_record_ids)
        referenced.update(coverage.capital_allocation_record_ids)
        referenced.update(coverage.footnote_record_ids)
    for observation in dossier.financial_observations:
        referenced.add(observation.source_record_id)
    for observation in dossier.commentary_observations:
        referenced.update(observation.source_record_ids)
    for disclosure in dossier.disclosures:
        referenced.update(disclosure.source_record_ids)
    for commitment in dossier.commitments:
        referenced.update(commitment.source_record_ids)
    return referenced


def validate_research_dossier(
    dossier: JapaneseResearchDossier,
    manifest: SelectionManifest | None = None,
) -> None:
    """Validate extraction structure without turning diagnostics into a gate."""

    identifier_groups = {
        "source record": [item.record_id for item in dossier.source_records],
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
        "commitment": [item.commitment_id for item in dossier.commitments],
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

    records_by_id = {item.record_id: item for item in dossier.source_records}
    unresolved = sorted(_record_references(dossier) - set(records_by_id))
    if unresolved:
        raise ValueError(
            "The research dossier references source-record IDs absent from its "
            "provenance records: " + ", ".join(unresolved)
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
    commitment_by_id = {
        item.commitment_id: item for item in dossier.commitments
    }
    covered_ids: dict[str, set[str]] = {
        "financial": set(),
        "commentary": set(),
        "disclosure": set(),
        "commitment": set(),
    }
    coverage_errors: list[str] = []
    for coverage in dossier.filing_coverage:
        category_record_ids = {
            *coverage.management_discussion_record_ids,
            *coverage.outlook_record_ids,
            *coverage.segment_record_ids,
            *coverage.cash_flow_record_ids,
            *coverage.capital_allocation_record_ids,
            *coverage.footnote_record_ids,
        }
        mismatched = sorted(
            record_id
            for record_id in category_record_ids
            if records_by_id[record_id].source_filename
            != coverage.source_filename
        )
        if mismatched:
            coverage_errors.append(
                f"{coverage.source_filename} lists source records from another "
                f"filing: {', '.join(mismatched)}"
            )
        assignments = (
            (
                "financial",
                coverage.financial_observation_ids,
                financial_by_id,
            ),
            (
                "commentary",
                coverage.commentary_observation_ids,
                commentary_by_id,
            ),
            ("disclosure", coverage.disclosure_ids, disclosure_by_id),
            ("commitment", coverage.commitment_ids, commitment_by_id),
        )
        for label, identifiers, records in assignments:
            for identifier in identifiers:
                item = records.get(identifier)
                if item is None:
                    coverage_errors.append(
                        f"{coverage.source_filename} references unknown {label} "
                        f"record {identifier}"
                    )
                elif item.source_filename != coverage.source_filename:
                    coverage_errors.append(
                        f"{identifier} belongs to {item.source_filename}, not "
                        f"{coverage.source_filename}"
                    )
                covered_ids[label].add(identifier)
    for label, records in (
        ("financial", financial_by_id),
        ("commentary", commentary_by_id),
        ("disclosure", disclosure_by_id),
        ("commitment", commitment_by_id),
    ):
        orphaned = sorted(set(records) - covered_ids[label])
        if orphaned:
            coverage_errors.append(
                f"unassigned {label} records: {', '.join(orphaned)}"
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
        details: list[str] = []
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
        if latest_records[0].source_filename != manifest.latest_filename:
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
            for item in [
                *dossier.source_records,
                *dossier.financial_observations,
                *dossier.commentary_observations,
                *dossier.disclosures,
                *dossier.commitments,
            ]
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
        record = records_by_id[observation.source_record_id]
        if record.source_filename != observation.source_filename:
            source_mismatches.append(observation.observation_id)
        if _normalized(observation.value_surface_ja) not in _normalized(
            record.summary_ja
        ):
            value_surface_mismatches.append(observation.observation_id)
    for observation in dossier.commentary_observations:
        if any(
            records_by_id[record_id].source_filename
            != observation.source_filename
            for record_id in observation.source_record_ids
        ):
            source_mismatches.append(observation.observation_id)
    for disclosure in dossier.disclosures:
        if any(
            records_by_id[record_id].source_filename
            != disclosure.source_filename
            for record_id in disclosure.source_record_ids
        ):
            source_mismatches.append(disclosure.disclosure_id)
    if source_mismatches:
        raise ValueError(
            "Research records reference a different source filing: "
            + ", ".join(sorted(source_mismatches))
        )
    if value_surface_mismatches:
        raise ValueError(
            "Financial observations use value surfaces absent from their "
            "supporting source summaries: "
            + ", ".join(sorted(value_surface_mismatches))
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
        else _normalized(observation.scope_label_ja)
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
    seen: set[tuple[tuple[str, str, str, int, str, str], str, Decimal]] = set()
    for forecast in observations:
        if forecast.statement_type != "forecast":
            continue
        forecast_value = _numeric_value(forecast)
        actuals = actual_by_key.get(_financial_key(forecast), [])
        actual_values = {
            value
            for item in actuals
            if (value := _numeric_value(item)) is not None
        }
        if forecast_value is None or len(actual_values) != 1:
            continue
        identity = (
            _financial_key(forecast),
            forecast.forecast_version.value,
            forecast_value,
        )
        if identity in seen:
            continue
        seen.add(identity)
        actual_value = next(iter(actual_values))
        actual = next(
            item for item in actuals if _numeric_value(item) == actual_value
        )
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
                "target_fiscal_year": forecast.target_fiscal_year,
                "forecast_version": forecast.forecast_version.value,
                "forecast_surface_ja": forecast.value_surface_ja,
                "actual_surface_ja": actual.value_surface_ja,
                "percentage_error": (
                    round(float(error_pct), 4)
                    if error_pct is not None
                    else None
                ),
                "result": result,
                "source_record_ids": [
                    forecast.source_record_id,
                    actual.source_record_id,
                ],
            }
        )
    originals = [
        item
        for item in observations
        if item.statement_type == "forecast"
        and item.forecast_version.value == "original"
    ]
    original_comparisons = [
        item
        for item in comparisons
        if item["forecast_version"] == "original"
    ]
    result_counts = _counts(item["result"] for item in original_comparisons)
    posture = "insufficient_evidence"
    if len(original_comparisons) >= 3:
        above = result_counts.get("actual_above_forecast", 0)
        below = result_counts.get("actual_below_forecast", 0)
        if above / len(original_comparisons) >= 0.67:
            posture = "conservative_tendency"
        elif below / len(original_comparisons) >= 0.67:
            posture = "aggressive_tendency"
        elif above and below:
            posture = "mixed"
        else:
            posture = "broadly_in_line"
    return {
        "original_forecasts_observed": len(originals),
        "original_forecasts_matched_to_actuals": len(original_comparisons),
        "observable_comparisons": len(comparisons),
        "original_result_counts": result_counts,
        "posture_signal": posture,
        "comparisons": comparisons,
    }


def _forecast_revision_metrics(
    observations: list[ResearchFinancialObservation],
) -> dict[str, Any]:
    by_key: dict[
        tuple[str, str, str, int, str, str],
        list[ResearchFinancialObservation],
    ] = defaultdict(list)
    for observation in observations:
        if observation.statement_type == "forecast":
            by_key[_financial_key(observation)].append(observation)
    revisions: list[dict[str, Any]] = []
    for values in by_key.values():
        originals = [
            item for item in values if item.forecast_version.value == "original"
        ]
        revised = [
            item for item in values if item.forecast_version.value == "revised"
        ]
        if len(originals) != 1 or len(revised) != 1:
            continue
        original_value = _numeric_value(originals[0])
        revised_value = _numeric_value(revised[0])
        if original_value is None or revised_value is None:
            continue
        direction = (
            "up"
            if revised_value > original_value
            else "down"
            if revised_value < original_value
            else "unchanged"
        )
        revisions.append(
            {
                "original_observation_id": originals[0].observation_id,
                "revised_observation_id": revised[0].observation_id,
                "metric": originals[0].metric.value,
                "target_fiscal_year": originals[0].target_fiscal_year,
                "direction": direction,
                "source_record_ids": [
                    originals[0].source_record_id,
                    revised[0].source_record_id,
                ],
            }
        )
    return {
        "comparable_revisions": len(revisions),
        "direction_counts": _counts(item["direction"] for item in revisions),
        "revisions": revisions,
    }


def _annual_anchor_series(
    observations: list[ResearchFinancialObservation],
    forecast_metrics: dict[str, Any],
) -> dict[str, Any]:
    actuals_by_metric: dict[str, list[ResearchFinancialObservation]] = defaultdict(
        list
    )
    for observation in observations:
        if (
            observation.statement_type == "actual"
            and observation.scope.value == "consolidated"
            and observation.target_period.value == "FY"
        ):
            actuals_by_metric[observation.metric.value].append(observation)
    if not actuals_by_metric:
        return {
            "metric": None,
            "metric_label_ja": None,
            "actual_years": [],
            "comparable_forecast_pairs": 0,
            "series": [],
        }
    preference = {
        "ordinary_profit": 0,
        "operating_profit": 1,
        "net_income": 2,
        "revenue": 3,
    }
    pair_counts = Counter(
        item["metric"]
        for item in forecast_metrics.get("comparisons", [])
        if item.get("forecast_version") == "original"
    )
    selected_metric = sorted(
        actuals_by_metric,
        key=lambda metric: (
            -pair_counts.get(metric, 0),
            -len(
                {
                    item.target_fiscal_year
                    for item in actuals_by_metric[metric]
                }
            ),
            preference.get(metric, 99),
            metric,
        ),
    )[0]
    by_year: dict[int, list[ResearchFinancialObservation]] = defaultdict(list)
    for observation in actuals_by_metric[selected_metric]:
        by_year[observation.target_fiscal_year].append(observation)
    series: list[dict[str, Any]] = []
    for fiscal_year, year_observations in sorted(by_year.items()):
        values = {
            value
            for item in year_observations
            if (value := _numeric_value(item)) is not None
        }
        if len(values) != 1:
            continue
        value = next(iter(values))
        observation = next(
            item
            for item in year_observations
            if _numeric_value(item) == value
        )
        series.append(
            {
                "target_fiscal_year": fiscal_year,
                "value_surface_ja": observation.value_surface_ja,
                "source_record_id": observation.source_record_id,
            }
        )
    return {
        "metric": selected_metric,
        "metric_label_ja": actuals_by_metric[selected_metric][0].metric_label_ja,
        "actual_years": [item["target_fiscal_year"] for item in series],
        "comparable_forecast_pairs": pair_counts.get(selected_metric, 0),
        "series": series,
    }


def _commentary_metrics(
    observations: list[ResearchCommentaryObservation],
) -> dict[str, Any]:
    by_tag: dict[str, list[ResearchCommentaryObservation]] = defaultdict(list)
    for observation in observations:
        by_tag[observation.canonical_tag].append(observation)
    changes: list[dict[str, Any]] = []
    for tag, values in sorted(by_tag.items()):
        ordered = sorted(
            values,
            key=lambda item: (item.fiscal_year, item.source_filename),
        )
        for previous, current in zip(ordered, ordered[1:]):
            previous_text = _canonical_text(previous.summary_ja)
            current_text = _canonical_text(current.summary_ja)
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
                    "source_record_ids": list(
                        dict.fromkeys(
                            [
                                *previous.source_record_ids,
                                *current.source_record_ids,
                            ]
                        )
                    ),
                }
            )
    changes_by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for change in changes:
        changes_by_tag[change["canonical_tag"]].append(change)
    tracks = [
        {
            "canonical_tag": tag,
            "label_ja": sorted(
                values,
                key=lambda item: (item.fiscal_year, item.source_filename),
            )[-1].label_ja,
            "observation_count": len(values),
            "fiscal_years": sorted({item.fiscal_year for item in values}),
            "tone_counts": _counts(item.tone.value for item in values),
            "intensity_counts": _counts(
                item.intensity.value for item in values
            ),
            "change_counts": _counts(
                item["change_type"]
                for item in changes_by_tag.get(tag, [])
            ),
        }
        for tag, values in sorted(by_tag.items())
    ]
    return {
        "observations": len(observations),
        "distinct_tags": len(by_tag),
        "comparable_tags": len(
            [items for items in by_tag.values() if len(items) >= 2]
        ),
        "multi_period_tags": len(
            [items for items in by_tag.values() if len(items) >= 3]
        ),
        "observations_by_tag": {
            tag: len(items) for tag, items in sorted(by_tag.items())
        },
        "change_counts": _counts(item["change_type"] for item in changes),
        "tracks": tracks,
        "changes": changes,
        "interpretation_guardrail": (
            "Text and tone changes compare only extracted observations in the "
            "selected filings. They are review signals, not proof that an "
            "unrecorded topic disappeared."
        ),
    }


def _build_research_metrics_unchecked(
    dossier: JapaneseResearchDossier,
) -> dict[str, Any]:
    commitments = dossier.commitments
    observable = [
        item
        for item in commitments
        if item.outcome_status.value not in {"pending", "not_observable"}
    ]
    achieved = [
        item
        for item in observable
        if item.outcome_status.value in {"achieved", "exceeded"}
    ]
    partial = [
        item
        for item in observable
        if item.outcome_status.value in {"partly_achieved", "revised"}
    ]
    shortfall = [
        item
        for item in observable
        if item.outcome_status.value in {"missed", "delayed", "withdrawn"}
    ]
    revisions = [
        item
        for item in commitments
        if item.revision_direction.value
        not in {"none_observed", "not_assessable"}
    ]
    latest = next(item for item in dossier.filing_coverage if item.is_latest)
    gaps = [
        {
            "source_filename": item.source_filename,
            "gaps": list(item.coverage_gaps),
        }
        for item in dossier.filing_coverage
        if item.coverage_gaps
    ]
    forecasts = _forecast_metrics(dossier.financial_observations)
    return {
        "filing_coverage": {
            "selected_filings": len(dossier.filing_coverage),
            "status_counts": _counts(
                item.coverage_status.value for item in dossier.filing_coverage
            ),
            "filings_with_explicit_gaps": len(gaps),
            "coverage_gaps": gaps,
            "latest_filing": {
                "source_filename": latest.source_filename,
                "financial_observations": len(
                    latest.financial_observation_ids
                ),
                "management_discussion_records": len(
                    latest.management_discussion_record_ids
                ),
                "outlook_records": len(latest.outlook_record_ids),
                "segment_records": len(latest.segment_record_ids),
                "cash_flow_records": len(latest.cash_flow_record_ids),
                "capital_allocation_records": len(
                    latest.capital_allocation_record_ids
                ),
                "footnote_records": len(latest.footnote_record_ids),
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
            "annual_anchor_series": _annual_anchor_series(
                dossier.financial_observations,
                forecasts,
            ),
            "forecast_accuracy": forecasts,
            "forecast_revisions": _forecast_revision_metrics(
                dossier.financial_observations
            ),
        },
        "commentary": _commentary_metrics(
            dossier.commentary_observations
        ),
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
        "commitments": {
            "total": len(commitments),
            "by_type": _counts(
                item.commitment_type.value for item in commitments
            ),
            "by_outcome": _counts(
                item.outcome_status.value for item in commitments
            ),
            "observable_outcomes": len(observable),
            "target_follow_through": {
                "assessed": len(observable),
                "achieved_or_exceeded": len(achieved),
                "partly_achieved_or_revised": len(partial),
                "missed_delayed_or_withdrawn": len(shortfall),
                "achievement_rate": (
                    round(len(achieved) / len(observable), 4)
                    if observable
                    else None
                ),
            },
            "observed_revision_count": len(revisions),
            "revision_direction_counts": _counts(
                item.revision_direction.value for item in revisions
            ),
            "missed_or_delayed_ids": [
                item.commitment_id for item in shortfall
            ],
        },
        "coverage": {
            "source_records": len(dossier.source_records),
            "research_notes": list(dossier.research_notes),
        },
        "interpretation_guardrail": (
            "Counts and comparisons describe only extracted observations from "
            "the selected latest/year-end Tanshin corpus. Synthesis must not "
            "treat missing extraction records as proof that a topic was absent."
        ),
    }


def build_research_metrics(
    dossier: JapaneseResearchDossier,
    manifest: SelectionManifest | None = None,
    *,
    strict_validation: bool = False,
) -> dict[str, Any]:
    """Produce synthesis metrics without making diagnostics a report gate."""

    validation_warning: str | None = None
    try:
        validate_research_dossier(dossier, manifest)
    except ValueError as exc:
        if strict_validation:
            raise
        validation_warning = str(exc)
    try:
        metrics = _build_research_metrics_unchecked(dossier)
        metrics_complete = True
        metrics_warning = None
    except Exception as exc:
        metrics_complete = False
        metrics_warning = f"{type(exc).__name__}: {exc}"
        metrics = {
            "filing_coverage": {
                "selected_filings": len(dossier.filing_coverage),
            },
            "financial_observations": {
                "total": len(dossier.financial_observations),
            },
            "commentary": {
                "observations": len(dossier.commentary_observations),
            },
            "disclosures": {"total": len(dossier.disclosures)},
            "commitments": {"total": len(dossier.commitments)},
            "coverage": {
                "source_records": len(dossier.source_records),
                "research_notes": list(dossier.research_notes),
            },
            "interpretation_guardrail": (
                "Local comparison metrics were incomplete. Synthesis must rely "
                "only on the persisted extraction dossier."
            ),
        }
    metrics["diagnostics"] = {
        "validation_passed": validation_warning is None,
        "validation_warning": validation_warning,
        "metrics_complete": metrics_complete,
        "metrics_warning": metrics_warning,
        "non_gating": True,
    }
    return metrics
