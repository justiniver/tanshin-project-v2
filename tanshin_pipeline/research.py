"""Deterministic summaries of the model-produced chronological research map."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .english_financials import extract_japanese_financial_amounts
from .schemas import (
    JapaneseResearchDossier,
    ResearchFilingMemo,
    ResearchMemoCategory,
    ResearchMemoFinancialAnchor,
    ResearchMemoFinancialPoint,
    SelectionManifest,
)


_NUMBER_RE = re.compile(r"[+\-△▲]?\s*\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"([+\-△▲]?)\s*(\d[\d,]*(?:\.\d+)?)\s*[％%]")
_CORE_CATEGORIES: tuple[ResearchMemoCategory, ...] = (
    "operating_results",
    "financial_condition",
    "forward_looking_information",
)


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _memo_by_filename(
    dossier: JapaneseResearchDossier,
) -> dict[str, ResearchFilingMemo]:
    return {item.source_filename: item for item in dossier.filings}


def validate_research_dossier(
    dossier: JapaneseResearchDossier,
    manifest: SelectionManifest | None = None,
) -> None:
    """Check the research map without making it a publication gate."""

    problems: list[str] = []
    filenames = [item.source_filename for item in dossier.filings]
    duplicate_filenames = sorted(
        name for name, count in Counter(filenames).items() if count > 1
    )
    if duplicate_filenames:
        problems.append(
            "Research map contains duplicate filing memos: "
            + ", ".join(duplicate_filenames)
        )

    latest = [item.source_filename for item in dossier.filings if item.is_latest]
    if len(latest) != 1:
        problems.append(
            "Research map must identify exactly one latest filing memo."
        )

    if manifest is not None:
        selected = {item.filename: item for item in manifest.selected_files}
        supplied = set(filenames)
        missing = sorted(set(selected) - supplied)
        unexpected = sorted(supplied - set(selected))
        if missing:
            problems.append(
                "Research map is missing selected filings: " + ", ".join(missing)
            )
        if unexpected:
            problems.append(
                "Research map contains unselected filings: "
                + ", ".join(unexpected)
            )
        for memo in dossier.filings:
            source = selected.get(memo.source_filename)
            if source is None:
                continue
            if memo.fiscal_year != source.fiscal_year:
                problems.append(
                    f"{memo.source_filename} has the wrong fiscal year."
                )
            if memo.period != source.period:
                problems.append(f"{memo.source_filename} has the wrong period.")
            if memo.pdf_page_count != source.page_count:
                problems.append(
                    f"{memo.source_filename} has the wrong PDF page count."
                )
            expected_latest = memo.source_filename == manifest.latest_filename
            if memo.is_latest != expected_latest:
                problems.append(
                    f"{memo.source_filename} has the wrong latest-filing flag."
                )

        for track in dossier.capital_allocation_tracks:
            if track.end_fiscal_year < track.start_fiscal_year:
                problems.append(
                    f"{track.track_label_ja} ends before it starts."
                )
            observations = [
                *track.capital_inputs,
                *track.immediate_effects,
                *track.subsequent_returns,
            ]
            for observation in observations:
                source = selected.get(observation.source_filename)
                if source is None:
                    problems.append(
                        "Capital-allocation track references an unselected filing: "
                        f"{observation.source_filename}."
                    )
                    continue
                if observation.fiscal_year != source.fiscal_year:
                    problems.append(
                        f"{track.track_label_ja} has an observation with the wrong "
                        "fiscal year."
                    )
                if not (
                    track.start_fiscal_year
                    <= observation.fiscal_year
                    <= track.end_fiscal_year
                ):
                    problems.append(
                        f"{track.track_label_ja} has an observation outside its "
                        "stated fiscal-year range."
                    )

    for memo in dossier.filings:
        categories = [item.category for item in memo.items]
        unavailable = set(memo.unavailable_categories)
        overlap = sorted(set(categories) & unavailable)
        if overlap:
            problems.append(
                f"{memo.source_filename} marks extracted categories unavailable: "
                + ", ".join(overlap)
            )
        for category in _CORE_CATEGORIES:
            if category not in categories and category not in unavailable:
                problems.append(
                    f"{memo.source_filename} does not cover core category {category}."
                )
        if memo.is_latest and "business_overview" not in categories:
            problems.append(
                f"{memo.source_filename} lacks a latest-filing business overview."
            )
        for item in memo.items:
            if item.pdf_page > memo.pdf_page_count:
                problems.append(
                    f"{memo.source_filename} references invalid page {item.pdf_page}."
                )

        anchor = memo.annual_financial_anchor
        if anchor is None:
            continue
        for label, point in (
            ("actual", anchor.actual),
            ("next_original_forecast", anchor.next_original_forecast),
        ):
            if point is None:
                continue
            if point.pdf_page > memo.pdf_page_count:
                problems.append(
                    f"{memo.source_filename} {label} anchor references invalid "
                    f"page {point.pdf_page}."
                )

    if problems:
        raise ValueError(" ".join(problems))


def _numeric_bounds(
    point: ResearchMemoFinancialPoint,
    anchor: ResearchMemoFinancialAnchor,
) -> tuple[Decimal, Decimal] | None:
    surface = unicodedata.normalize("NFKC", point.value_surface_ja)
    if anchor.value_kind.value in {"monetary", "per_share"}:
        amounts = extract_japanese_financial_amounts(surface)
        values = [amount.yen_value for amount in amounts]
    elif anchor.value_kind.value == "percentage":
        values = []
        for match in _PERCENT_RE.finditer(surface):
            value = Decimal(match.group(2).replace(",", ""))
            values.append(
                -value
                if match.group(1) in {"-", "△", "▲"}
                else value
            )
    else:
        values = []
        for match in _NUMBER_RE.finditer(surface):
            raw = re.sub(r"\s+", "", match.group()).replace(",", "")
            negative = raw.startswith(("-", "△", "▲"))
            raw = raw.lstrip("+-△▲")
            try:
                value = Decimal(raw)
            except InvalidOperation:
                continue
            values.append(-value if negative else value)
    if len(values) not in {1, 2}:
        return None
    return min(values), max(values)


def _numeric_value(
    point: ResearchMemoFinancialPoint,
    anchor: ResearchMemoFinancialAnchor,
) -> Decimal | None:
    bounds = _numeric_bounds(point, anchor)
    if bounds is None or bounds[0] != bounds[1]:
        return None
    return bounds[0]


def _anchor_key(
    anchor: ResearchMemoFinancialAnchor,
    point: ResearchMemoFinancialPoint,
) -> tuple[str, str, str, str, int, str]:
    scope_label = (
        ""
        if anchor.scope.value in {"consolidated", "company_only"}
        else _compact(anchor.scope_label_ja)
    )
    return (
        anchor.metric.value,
        anchor.scope.value,
        scope_label,
        anchor.value_kind.value,
        point.target_fiscal_year,
        point.target_period.value,
    )


def _forecast_metrics(
    dossier: JapaneseResearchDossier,
) -> dict[str, Any]:
    actuals: dict[
        tuple[str, str, str, str, int, str],
        list[tuple[ResearchFilingMemo, ResearchMemoFinancialAnchor, ResearchMemoFinancialPoint]],
    ] = {}
    forecasts: list[
        tuple[ResearchFilingMemo, ResearchMemoFinancialAnchor, ResearchMemoFinancialPoint]
    ] = []
    for memo in dossier.filings:
        anchor = memo.annual_financial_anchor
        if anchor is None:
            continue
        if anchor.actual is not None:
            actuals.setdefault(
                _anchor_key(anchor, anchor.actual),
                [],
            ).append((memo, anchor, anchor.actual))
        if anchor.next_original_forecast is not None:
            forecasts.append((memo, anchor, anchor.next_original_forecast))

    comparisons: list[dict[str, Any]] = []
    for forecast_memo, forecast_anchor, forecast in forecasts:
        matched = actuals.get(_anchor_key(forecast_anchor, forecast), [])
        values = {
            value
            for _, anchor, point in matched
            if (value := _numeric_value(point, anchor)) is not None
        }
        forecast_bounds = _numeric_bounds(forecast, forecast_anchor)
        if forecast_bounds is None or len(values) != 1:
            continue
        forecast_lower, forecast_upper = forecast_bounds
        actual_value = next(iter(values))
        actual_memo, _, actual = next(
            item
            for item in matched
            if _numeric_value(item[2], item[1]) == actual_value
        )
        if actual_value < forecast_lower:
            comparison_value = forecast_lower
        elif actual_value > forecast_upper:
            comparison_value = forecast_upper
        else:
            comparison_value = actual_value
        delta = actual_value - comparison_value
        error_pct = (
            delta / abs(comparison_value) * Decimal("100")
            if comparison_value != 0
            else None
        )
        # Forecast discipline is deliberately asymmetric: delivering at least
        # the originally forecast value is positive regardless of the size of
        # the upside, while any shortfall is negative. The raw percentage
        # difference remains available for analytical context.
        result = (
            "met_or_exceeded"
            if actual_value >= forecast_lower
            else "missed"
        )
        comparisons.append(
            {
                "metric": forecast_anchor.metric.value,
                "metric_label_ja": forecast_anchor.metric_label_ja,
                "scope": forecast_anchor.scope.value,
                "scope_label_ja": forecast_anchor.scope_label_ja,
                "value_kind": forecast_anchor.value_kind.value,
                "target_fiscal_year": forecast.target_fiscal_year,
                "target_period": forecast.target_period.value,
                "forecast_surface_ja": forecast.value_surface_ja,
                "actual_surface_ja": actual.value_surface_ja,
                "percentage_error": (
                    round(float(error_pct), 4)
                    if error_pct is not None
                    else None
                ),
                "result": result,
                "source_filenames": [
                    forecast_memo.source_filename,
                    actual_memo.source_filename,
                ],
            }
        )

    result_counts = _counts(item["result"] for item in comparisons)
    posture = "insufficient_evidence"
    if len(comparisons) >= 3:
        met = result_counts.get("met_or_exceeded", 0)
        missed = result_counts.get("missed", 0)
        if met / len(comparisons) >= 0.67:
            posture = "met_or_exceeded_tendency"
        elif missed / len(comparisons) >= 0.67:
            posture = "miss_tendency"
        elif met and missed:
            posture = "mixed"
    return {
        "original_forecasts_observed": len(forecasts),
        "original_forecasts_matched_to_actuals": len(comparisons),
        "observable_comparisons": len(comparisons),
        "original_result_counts": result_counts,
        "met_or_exceeded_count": result_counts.get("met_or_exceeded", 0),
        "missed_count": result_counts.get("missed", 0),
        "success_rate": (
            round(
                result_counts.get("met_or_exceeded", 0)
                / len(comparisons),
                4,
            )
            if comparisons
            else None
        ),
        "posture_signal": posture,
        "comparisons": comparisons,
    }


def _annual_series(dossier: JapaneseResearchDossier) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    metrics: Counter[str] = Counter()
    anchor_count = 0
    value_count = 0
    for memo in sorted(dossier.filings, key=lambda item: item.fiscal_year):
        anchor = memo.annual_financial_anchor
        if anchor is None:
            continue
        anchor_count += 1
        metrics[anchor.metric.value] += 1
        for kind, point in (
            ("actual", anchor.actual),
            ("forecast", anchor.next_original_forecast),
        ):
            if point is None:
                continue
            value_count += 1
            points.append(
                {
                    "source_filename": memo.source_filename,
                    "kind": kind,
                    "metric": anchor.metric.value,
                    "metric_label_ja": anchor.metric_label_ja,
                    "target_fiscal_year": point.target_fiscal_year,
                    "target_period": point.target_period.value,
                    "value_surface_ja": point.value_surface_ja,
                    "pdf_page": point.pdf_page,
                }
            )
    return {
        "annual_anchor_count": anchor_count,
        "annual_anchor_value_count": value_count,
        "by_metric": dict(sorted(metrics.items())),
        "series": points,
        "forecast_accuracy": _forecast_metrics(dossier),
    }


def _capital_allocation_metrics(
    dossier: JapaneseResearchDossier,
) -> dict[str, Any]:
    tracks = dossier.capital_allocation_tracks
    capital_base_input_types = {
        "segment_or_operating_assets",
        "working_capital_or_inventory",
        "capacity_or_fixed_assets",
        "acquisition_or_investment_spend",
    }
    capital_return_types = {
        "profit_or_loss",
        "cash_generation",
        "return_on_capital_or_assets",
    }

    def _has_capital_base(track: Any) -> bool:
        return any(
            item.input_type.value in capital_base_input_types
            for item in track.capital_inputs
        )

    def _has_direct_capital_return(track: Any) -> bool:
        return any(
            outcome.attribution.value == "direct"
            and outcome.return_type.value in capital_return_types
            for outcome in track.subsequent_returns
        )

    attribution_counts = Counter(
        outcome.attribution.value
        for track in tracks
        for outcome in track.subsequent_returns
    )
    signal_counts = Counter(
        outcome.signal.value
        for track in tracks
        for outcome in track.subsequent_returns
    )
    return {
        "track_records": len(tracks),
        "tracks_with_subsequent_returns": sum(
            bool(item.subsequent_returns) for item in tracks
        ),
        "tracks_with_direct_return_evidence": sum(
            any(
                outcome.attribution.value == "direct"
                for outcome in item.subsequent_returns
            )
            for item in tracks
        ),
        "reported_return_on_capital_or_assets_records": sum(
            outcome.return_type.value == "return_on_capital_or_assets"
            for item in tracks
            for outcome in item.subsequent_returns
        ),
        "tracks_with_reported_return_on_capital_or_assets": sum(
            any(
                outcome.return_type.value == "return_on_capital_or_assets"
                for outcome in item.subsequent_returns
            )
            for item in tracks
        ),
        "tracks_with_capital_base_and_direct_return_evidence": sum(
            _has_capital_base(item) and _has_direct_capital_return(item)
            for item in tracks
        ),
        "tracks_with_only_management_or_aggregate_returns": sum(
            bool(item.subsequent_returns)
            and not any(
                outcome.attribution.value == "direct"
                for outcome in item.subsequent_returns
            )
            for item in tracks
        ),
        "capital_input_records": sum(
            len(item.capital_inputs) for item in tracks
        ),
        "immediate_effect_records": sum(
            len(item.immediate_effects) for item in tracks
        ),
        "subsequent_return_records": sum(
            len(item.subsequent_returns) for item in tracks
        ),
        "by_capital_input_type": _counts(
            input_item.input_type.value
            for item in tracks
            for input_item in item.capital_inputs
        ),
        "by_immediate_effect_type": _counts(
            effect.effect_type.value
            for item in tracks
            for effect in item.immediate_effects
        ),
        "by_return_type": _counts(
            outcome.return_type.value
            for item in tracks
            for outcome in item.subsequent_returns
        ),
        "by_track_type": _counts(
            item.track_type.value for item in tracks
        ),
        "by_record_maturity": _counts(
            item.record_maturity.value for item in tracks
        ),
        "by_return_attribution": dict(sorted(attribution_counts.items())),
        "by_return_signal": dict(sorted(signal_counts.items())),
        "records": [
            {
                "track_label_ja": item.track_label_ja,
                "track_type": item.track_type.value,
                "capital_destination_ja": item.capital_destination_ja,
                "start_fiscal_year": item.start_fiscal_year,
                "end_fiscal_year": item.end_fiscal_year,
                "capital_input_count": len(item.capital_inputs),
                "capital_inputs_with_relative_priority": sum(
                    input_item.relative_priority_ja is not None
                    for input_item in item.capital_inputs
                ),
                "record_maturity": item.record_maturity.value,
                "immediate_effect_count": len(item.immediate_effects),
                "subsequent_return_count": len(item.subsequent_returns),
                "direct_return_count": sum(
                    outcome.attribution.value == "direct"
                    for outcome in item.subsequent_returns
                ),
                "management_linked_return_count": sum(
                    outcome.attribution.value == "management_linked"
                    for outcome in item.subsequent_returns
                ),
                "non_attributable_return_count": sum(
                    outcome.attribution.value in {"aggregate_only", "unattributed"}
                    for outcome in item.subsequent_returns
                ),
                "has_destination_level_return_evidence": any(
                    outcome.attribution.value == "direct"
                    for outcome in item.subsequent_returns
                ),
                "has_reported_return_on_capital_or_assets": any(
                    outcome.return_type.value == "return_on_capital_or_assets"
                    for outcome in item.subsequent_returns
                ),
                "has_capital_base_observation": _has_capital_base(item),
                "has_capital_base_and_direct_return_evidence": (
                    _has_capital_base(item)
                    and _has_direct_capital_return(item)
                ),
                "return_attribution_counts": _counts(
                    outcome.attribution.value
                    for outcome in item.subsequent_returns
                ),
                "has_adverse_evidence": bool(item.adverse_evidence_ja),
                "has_disclosure_limit": item.disclosure_limit_ja is not None,
            }
            for item in tracks
        ],
        "interpretation_guardrail": (
            "These are allocation-track extraction records, not value-creation "
            "verdicts. Immediate transaction or accounting effects are not "
            "subsequent returns. Management-linked returns preserve management's "
            "attribution but do not independently verify it. Aggregate-only or "
            "unattributed returns, group-wide ROE/EPS/BVPS, and the execution of "
            "a shareholder distribution cannot establish that a specific "
            "destination created value. Prefer disclosed ROIC/ROA or compatible "
            "destination-level capital and profit/cash observations; never "
            "manufacture an undisclosed ratio."
        ),
    }


def _build_research_metrics_unchecked(
    dossier: JapaneseResearchDossier,
) -> dict[str, Any]:
    category_counts = Counter(
        item.category
        for filing in dossier.filings
        for item in filing.items
    )
    per_filing = [
        {
            "source_filename": filing.source_filename,
            "fiscal_year": filing.fiscal_year,
            "period": filing.period.value,
            "is_latest": filing.is_latest,
            "memo_items": len(filing.items),
            "category_counts": _counts(item.category for item in filing.items),
            "unavailable_categories": list(filing.unavailable_categories),
            "has_annual_financial_anchor": (
                filing.annual_financial_anchor is not None
            ),
        }
        for filing in sorted(
            dossier.filings,
            key=lambda item: (item.fiscal_year, item.source_filename),
        )
    ]
    return {
        "filing_coverage": {
            "selected_filings": len(dossier.filings),
            "memo_items": sum(len(item.items) for item in dossier.filings),
            "category_counts": dict(sorted(category_counts.items())),
            "per_filing": per_filing,
        },
        "financial_observations": _annual_series(dossier),
        "capital_allocation": _capital_allocation_metrics(dossier),
        "coverage": {
            "research_notes": list(dossier.research_notes),
        },
        "interpretation_guardrail": (
            "The research map is an attention guide, not the source boundary. "
            "The synthesis request receives the selected PDFs and must inspect "
            "them whenever the memo is incomplete or a conclusion requires context."
        ),
    }


def build_research_metrics(
    dossier: JapaneseResearchDossier,
    manifest: SelectionManifest | None = None,
) -> dict[str, Any]:
    """Build useful local comparisons while retaining non-gating diagnostics."""

    warning: str | None = None
    try:
        validate_research_dossier(dossier, manifest)
    except ValueError as exc:
        warning = str(exc)
    metrics = _build_research_metrics_unchecked(dossier)
    metrics["diagnostics"] = {
        "validation_passed": warning is None,
        "validation_warning": warning,
        "metrics_complete": True,
        "metrics_warning": None,
        "non_gating": True,
    }
    return metrics
