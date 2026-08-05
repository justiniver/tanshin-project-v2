"""Quality-first factual, structural, and bilingual validation."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .english_financials import (
    extract_english_financial_amounts,
    extract_japanese_financial_amounts,
    financial_display_matches,
)
from .evaluation import essential_quality_issues
from .management_consistency import is_management_discussion_evidence
from .normalization import canonical_surface, compact_text, numeric_surfaces
from .pdf_text import PdfTextIndex
from .schemas import (
    AnalysisClaim,
    EnglishTranslation,
    JapaneseAnalysis,
    ManagementConsistencyDimension,
    SectionKey,
    SelectionManifest,
    StatementType,
    SupportedSpan,
    TranslatedClaim,
    TranslatedSpan,
    ValidationIssue,
    ValidationResult,
)

LATEST_SECTIONS = {
    SectionKey.COMPANY_OVERVIEW,
    SectionKey.LATEST_KEY_TAKEAWAY,
    SectionKey.LATEST_BUSINESS_DRIVER,
    SectionKey.LATEST_OUTLOOK,
    SectionKey.LATEST_RISK,
    SectionKey.LATEST_CONTEXT,
}
TREND_SECTIONS = set(SectionKey) - LATEST_SECTIONS
MINIMUM_SECTION_COUNTS = {
    SectionKey.COMPANY_OVERVIEW: 1,
    SectionKey.LATEST_KEY_TAKEAWAY: 3,
    SectionKey.LATEST_BUSINESS_DRIVER: 2,
    SectionKey.LATEST_OUTLOOK: 1,
    SectionKey.LATEST_RISK: 2,
    SectionKey.LATEST_CONTEXT: 1,
    SectionKey.TREND_PERSPECTIVE: 1,
    SectionKey.TREND_CONSISTENT: 2,
    SectionKey.TREND_CHANGE: 2,
    SectionKey.TREND_CAPITAL_ALLOCATION: 1,
    SectionKey.TREND_IMPLICATION: 1,
}
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_CAUSAL_JA = ("ため", "により", "によって", "受けて", "寄与", "背景に", "結果")
_CAUSAL_EN = (
    "because",
    "due to",
    "driven by",
    "as a result",
    "contributed",
    "supported by",
    "reflecting",
)
_SOURCE_CAUSAL_JA = _CAUSAL_JA + ("ことから", "伴い", "伴う", "等により")
_ACHIEVED_JA = (
    "となりました",
    "となった",
    "達成しました",
    "達成した",
    "上回りました",
    "上回った",
    "増加しました",
    "増加した",
    "減少しました",
    "減少した",
    "計上しました",
    "計上した",
)
_ACHIEVED_EN = (
    "reached",
    "achieved",
    "exceeded",
    "increased",
    "decreased",
    "recorded",
    "was ",
    "were ",
)


@dataclass(frozen=True)
class ValidationPolicy:
    # Manual review is the publication authority. The default validator blocks
    # only structural corruption, issuer/source mismatches, and material
    # actual/forecast or cross-language contradictions.
    strict_quality: bool = False
    verify_quote_on_page: bool = False
    manual_review_publication: bool = True
    emit_low_value_diagnostics: bool = False


_MANUAL_REVIEW_BLOCKER_CODES = {
    "duplicate_evidence_id",
    "unselected_source",
    "invalid_pdf_page",
    "security_code_mismatch",
    "latest_filename_mismatch",
    "duplicate_claim_id",
    "unresolved_evidence",
    "achieved_language_for_non_actual",
    "report_contains_unresolved_reference",
    "cross_language_identity_mismatch",
    "duplicate_translated_claim",
    "cross_language_claim_ids",
    "cross_language_section",
    "cross_language_order",
    "cross_language_evidence_ids",
    "cross_language_statement_type",
    "cross_language_is_inference",
    "english_financial_value_mismatch",
}
_LOW_VALUE_DIAGNOSTIC_CODES = {
    "invalid_evidence_id",
    "quote_verification_not_configured",
    "quote_not_found_on_page",
    "quote_page_extraction_failed",
    "duplicate_claim_evidence",
    "historical_non_year_end_source",
    "historical_year_not_cited",
    "unsupported_numeric_surface",
    "causal_flag_missing",
    "unsupported_causal_statement",
    "inference_type_mismatch",
    "trend_year_coverage",
    "trend_implication_evidence_density",
    "unused_evidence",
    "unsupported_english_numeric_surface",
    "english_causal_flag_missing",
}


def _is_low_value_diagnostic(code: str) -> bool:
    return code in _LOW_VALUE_DIAGNOSTIC_CODES or code.startswith(
        (
            "duplicate_figure",
            "duplicate_date",
            "duplicate_qualifier",
            "figure_surface_",
            "date_surface_",
            "qualifier_surface_",
            "figure_evidence_",
            "date_evidence_",
            "qualifier_evidence_",
            "figure_source_",
            "date_source_",
            "qualifier_source_",
            "translated_date_",
            "translated_qualifier_",
            "cross_language_qualifier_",
        )
    )


def _apply_policy(
    issues: list[ValidationIssue],
    policy: ValidationPolicy,
) -> list[ValidationIssue]:
    selected: list[ValidationIssue] = []
    for issue in issues:
        if (
            not policy.emit_low_value_diagnostics
            and _is_low_value_diagnostic(issue.code)
        ):
            continue
        if (
            policy.manual_review_publication
            and issue.severity == "error"
            and issue.code not in _MANUAL_REVIEW_BLOCKER_CODES
        ):
            issue = issue.model_copy(
                update={
                    "severity": "warning",
                    "category": "diagnostic",
                }
            )
        selected.append(issue)
    return selected


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _numbers(text: str) -> set[str]:
    return {canonical_surface(value) for value in numeric_surfaces(text)}


def _english_surface_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = re.sub(r"[-‐‑‒–—]+", " ", normalized)
    tokens = re.findall(r"[a-z0-9.%¥]+", normalized)
    singularizable = {"periods", "years", "units", "cases", "shares"}
    return " ".join(
        token[:-1] if token in singularizable else token for token in tokens
    )


def _issue(
    issues: list[ValidationIssue],
    code: str,
    message: str,
    *,
    claim_id: str | None = None,
    evidence_id: str | None = None,
    severity: str = "error",
    category: str = "factual_integrity",
) -> None:
    issues.append(
        ValidationIssue(
            severity=severity,
            category=category,
            code=code,
            message=message,
            claim_id=claim_id,
            evidence_id=evidence_id,
        )
    )


def _selected_for_evidence(
    evidence: object,
    selected_by_name: dict[str, object],
) -> object | None:
    return selected_by_name.get(getattr(evidence, "source_filename"))


def _date_metadata_support(
    span: SupportedSpan,
    evidence: object,
    selected_by_name: dict[str, object],
) -> bool:
    year_match = re.search(r"(?:19|20)\d{2}", span.claim_surface_ja)
    if year_match is None:
        return False
    year = int(year_match.group())
    period_years = {
        int(value)
        for value in re.findall(
            r"(?:19|20)\d{2}", getattr(evidence, "period_label_ja")
        )
    }
    selected = _selected_for_evidence(evidence, selected_by_name)
    return year in period_years or (
        selected is not None and getattr(selected, "fiscal_year") == year
    )


def _validate_supported_spans(
    claim: AnalysisClaim,
    spans: list[SupportedSpan],
    evidence_by_id: dict[str, object],
    selected_by_name: dict[str, object],
    issues: list[ValidationIssue],
    kind: str,
) -> None:
    combined_claim_text = claim.headline_ja + "\n" + claim.body_ja
    seen: set[str] = set()
    is_diagnostic = kind == "qualifier"
    severity = "warning" if is_diagnostic else "error"
    category = "diagnostic" if is_diagnostic else "factual_integrity"
    for span in spans:
        if span.value_id in seen:
            _issue(
                issues,
                f"duplicate_{kind}_id",
                f"Duplicate {kind} value_id {span.value_id}.",
                claim_id=claim.claim_id,
                severity=severity,
                category=category,
            )
        seen.add(span.value_id)
        if canonical_surface(span.claim_surface_ja) not in canonical_surface(
            combined_claim_text
        ):
            _issue(
                issues,
                f"{kind}_surface_missing_from_claim",
                f"{kind} surface {span.claim_surface_ja!r} is not in the claim.",
                claim_id=claim.claim_id,
                severity=severity,
                category=category,
            )
        if span.evidence_id not in claim.evidence_ids:
            _issue(
                issues,
                f"{kind}_evidence_not_cited",
                f"{kind} cites {span.evidence_id}, which is not a claim evidence ID.",
                claim_id=claim.claim_id,
                evidence_id=span.evidence_id,
                severity=severity,
                category=category,
            )
        evidence = evidence_by_id.get(span.evidence_id)
        if evidence is None:
            continue
        quote = getattr(evidence, "exact_quote_ja")
        source_supported = span.source_surface_ja in quote
        if kind in {"figure", "date"}:
            source_supported = source_supported or (
                canonical_surface(span.source_surface_ja)
                in canonical_surface(quote)
            )
        if kind == "date":
            source_supported = source_supported or _date_metadata_support(
                span, evidence, selected_by_name
            )
        if not source_supported:
            _issue(
                issues,
                f"{kind}_source_surface_missing",
                f"{kind} source surface {span.source_surface_ja!r} is not supported "
                "by the source quote or filing metadata.",
                claim_id=claim.claim_id,
                evidence_id=span.evidence_id,
                severity=severity,
                category=category,
            )


def _result(
    *,
    language: str,
    issues: list[ValidationIssue],
    statistics: dict[str, int | float | str | bool],
    policy: ValidationPolicy,
) -> ValidationResult:
    issues = _apply_policy(issues, policy)
    blocking = [item for item in issues if item.severity == "error"]
    warnings = [item for item in issues if item.severity == "warning"]
    factual_passed = not any(
        item.category == "factual_integrity" for item in blocking
    )
    quality_passed = not any(
        item.category == "essential_quality" for item in blocking
    )
    publishable = factual_passed and quality_passed
    statistics = {
        **statistics,
        "errors": len(blocking),
        "warnings": len(warnings),
        "blocking_errors": len(blocking),
    }
    return ValidationResult(
        valid=publishable,
        publishable=publishable,
        factual_integrity_passed=factual_passed,
        quality_gate_passed=quality_passed,
        blocking_error_count=len(blocking),
        warning_count=len(warnings),
        language=language,
        issues=issues,
        statistics=statistics,
    )


def validate_japanese(
    analysis: JapaneseAnalysis,
    manifest: SelectionManifest,
    *,
    policy: ValidationPolicy = ValidationPolicy(),
    repository_root: Path | None = None,
    generated_report: str | None = None,
    exemplar_text: str | None = None,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    selected_by_name = {item.filename: item for item in manifest.selected_files}
    evidence_by_id: dict[str, object] = {}
    evidence_counts = Counter(item.evidence_id for item in analysis.evidence)
    text_index = (
        PdfTextIndex(repository_root, manifest)
        if repository_root is not None and policy.verify_quote_on_page
        else None
    )
    try:
        for evidence_id, count in evidence_counts.items():
            if count > 1:
                _issue(
                    issues,
                    "duplicate_evidence_id",
                    f"Evidence ID appears {count} times.",
                    evidence_id=evidence_id,
                )
        for evidence in analysis.evidence:
            evidence_by_id.setdefault(evidence.evidence_id, evidence)
            selected = selected_by_name.get(evidence.source_filename)
            if selected is None:
                _issue(
                    issues,
                    "unselected_source",
                    f"Evidence source {evidence.source_filename} is not selected.",
                    evidence_id=evidence.evidence_id,
                )
                continue
            model_pattern = re.escape(evidence.source_filename) + r":s\d{4}"
            repair_pattern = (
                re.escape(evidence.source_filename) + r":r\d{4}-[0-9a-f]{8}"
            )
            if not (
                re.fullmatch(model_pattern, evidence.evidence_id)
                or re.fullmatch(repair_pattern, evidence.evidence_id)
            ):
                _issue(
                    issues,
                    "invalid_evidence_id",
                    "Evidence ID does not use the model or local-repair format.",
                    evidence_id=evidence.evidence_id,
                    severity="warning",
                    category="diagnostic",
                )
            if evidence.pdf_page > selected.page_count:
                _issue(
                    issues,
                    "invalid_pdf_page",
                    f"Page {evidence.pdf_page} exceeds {selected.page_count} pages.",
                    evidence_id=evidence.evidence_id,
                )
            elif policy.verify_quote_on_page:
                if text_index is None:
                    _issue(
                        issues,
                        "quote_verification_not_configured",
                        "repository_root is required for quote verification.",
                        evidence_id=evidence.evidence_id,
                        severity="warning",
                        category="diagnostic",
                    )
                else:
                    try:
                        page_text = text_index.page_text(
                            evidence.source_filename,
                            evidence.pdf_page,
                            include_fallback=True,
                        )
                        quote = compact_text(evidence.exact_quote_ja)
                        if not quote or quote not in compact_text(page_text):
                            _issue(
                                issues,
                                "quote_not_found_on_page",
                                "The exact quote was not found on the cited physical page.",
                                evidence_id=evidence.evidence_id,
                                severity="warning",
                                category="diagnostic",
                            )
                    except Exception as exc:
                        _issue(
                            issues,
                            "quote_page_extraction_failed",
                            f"Could not extract the cited page: {exc}",
                            evidence_id=evidence.evidence_id,
                            severity="warning",
                            category="diagnostic",
                        )
    finally:
        if text_index is not None:
            text_index.close()

    if analysis.identity.security_code != manifest.security_code:
        _issue(
            issues,
            "security_code_mismatch",
            "Identity security code mismatches manifest.",
        )
    if analysis.identity.latest_filename != manifest.latest_filename:
        _issue(
            issues,
            "latest_filename_mismatch",
            "Identity latest filename mismatches manifest.",
        )

    claim_counts = Counter(item.claim_id for item in analysis.claims)
    section_counts = Counter(item.section for item in analysis.claims)
    for claim_id, count in claim_counts.items():
        if count > 1:
            _issue(
                issues,
                "duplicate_claim_id",
                f"Claim ID appears {count} times.",
                claim_id=claim_id,
            )
    section_orders: dict[SectionKey, list[int]] = defaultdict(list)
    used_evidence: set[str] = set()
    total_refs = 0
    trend_evidence_refs = 0
    trend_management_discussion_refs = 0
    for claim in analysis.claims:
        section_orders[claim.section].append(claim.order)
        total_refs += len(claim.evidence_ids)
        used_evidence.update(claim.evidence_ids)
        if len(claim.evidence_ids) != len(set(claim.evidence_ids)):
            _issue(
                issues,
                "duplicate_claim_evidence",
                "Claim contains duplicate evidence IDs.",
                claim_id=claim.claim_id,
                severity="warning",
                category="diagnostic",
            )
        for evidence_id in claim.evidence_ids:
            if evidence_id not in evidence_by_id:
                _issue(
                    issues,
                    "unresolved_evidence",
                    "Claim citation does not resolve to an evidence record.",
                    claim_id=claim.claim_id,
                    evidence_id=evidence_id,
                )

        if claim.section in LATEST_SECTIONS:
            latest_cited = any(
                evidence_by_id.get(evidence_id) is not None
                and getattr(evidence_by_id[evidence_id], "source_filename")
                == manifest.latest_filename
                for evidence_id in claim.evidence_ids
            )
            if not latest_cited:
                _issue(
                    issues,
                    "latest_claim_missing_latest_source",
                    "Latest-summary claim is not grounded in the latest filing.",
                    claim_id=claim.claim_id,
                )
        else:
            for evidence_id in claim.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    continue
                trend_evidence_refs += 1
                if is_management_discussion_evidence(evidence):
                    trend_management_discussion_refs += 1
            cited_years = {
                selected_by_name[getattr(evidence_by_id[evidence_id], "source_filename")].fiscal_year
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence_by_id
                and getattr(evidence_by_id[evidence_id], "source_filename")
                in selected_by_name
            }
            if (
                claim.section != SectionKey.TREND_IMPLICATION
                and len(cited_years) < 2
            ):
                _issue(
                    issues,
                    "trend_year_coverage",
                    "Trend claim must cover at least two distinct fiscal years.",
                    claim_id=claim.claim_id,
                    category="essential_quality",
                )
            if (
                claim.section == SectionKey.TREND_IMPLICATION
                and len(claim.evidence_ids) < 2
            ):
                _issue(
                    issues,
                    "trend_implication_evidence_density",
                    "A strategic implication must synthesize at least two evidence records.",
                    claim_id=claim.claim_id,
                    category="essential_quality",
                )
            for evidence_id in claim.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    continue
                selected = selected_by_name.get(
                    getattr(evidence, "source_filename")
                )
                if (
                    selected is not None
                    and getattr(evidence, "source_filename")
                    != manifest.latest_filename
                    and "trend_year_end" not in selected.roles
                ):
                    _issue(
                        issues,
                        "historical_non_year_end_source",
                        "Historical evidence is not a selected year-end filing.",
                        claim_id=claim.claim_id,
                        evidence_id=evidence_id,
                        severity="warning",
                        category="diagnostic",
                    )

        historical_years = {
            int(year)
            for year in _YEAR_RE.findall(_normalized(claim.body_ja))
            if int(year) < manifest.window.anchor_fiscal_year
        }
        cited_years = {
            selected_by_name[getattr(evidence_by_id[evidence_id], "source_filename")].fiscal_year
            for evidence_id in claim.evidence_ids
            if evidence_id in evidence_by_id
            and getattr(evidence_by_id[evidence_id], "source_filename")
            in selected_by_name
        }
        for year in sorted(historical_years - cited_years):
            _issue(
                issues,
                "historical_year_not_cited",
                f"Claim mentions {year} without same-year evidence.",
                claim_id=claim.claim_id,
                severity="warning",
                category="diagnostic",
            )

        _validate_supported_spans(
            claim,
            claim.figures,
            evidence_by_id,
            selected_by_name,
            issues,
            "figure",
        )
        _validate_supported_spans(
            claim,
            claim.dates,
            evidence_by_id,
            selected_by_name,
            issues,
            "date",
        )
        _validate_supported_spans(
            claim,
            claim.qualifiers,
            evidence_by_id,
            selected_by_name,
            issues,
            "qualifier",
        )
        covered_numbers = {
            number
            for span in [*claim.figures, *claim.dates]
            for number in _numbers(span.claim_surface_ja)
        }
        unsupported = (
            _numbers(claim.headline_ja + "\n" + claim.body_ja) - covered_numbers
        )
        trend_years = len(manifest.window.unique_years)
        unsupported = {
            number
            for number in unsupported
            if not (
                claim.section in TREND_SECTIONS
                and number
                in {
                    str(trend_years),
                    f"{trend_years}年",
                    f"{trend_years}年間",
                }
            )
        }
        for number in sorted(unsupported):
            _issue(
                issues,
                "unsupported_numeric_surface",
                f"Numeric surface {number!r} could not be grounded locally.",
                claim_id=claim.claim_id,
            )

        body = _normalized(claim.body_ja)
        if any(marker in body for marker in _CAUSAL_JA) and not claim.causal:
            _issue(
                issues,
                "causal_flag_missing",
                "Claim uses causal language but causal=false.",
                claim_id=claim.claim_id,
                severity="warning",
                category="diagnostic",
            )
        if claim.causal and not claim.is_inference:
            source_text = " ".join(
                getattr(evidence_by_id[evidence_id], "exact_quote_ja")
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence_by_id
            )
            if not any(marker in source_text for marker in _SOURCE_CAUSAL_JA):
                _issue(
                    issues,
                    "unsupported_causal_statement",
                    "Causal wording is not explicit in the retained source excerpts.",
                    claim_id=claim.claim_id,
                    severity="warning",
                    category="diagnostic",
                )
        if claim.is_inference and claim.statement_type not in {
            StatementType.INFERENCE,
            StatementType.MIXED,
        }:
            _issue(
                issues,
                "inference_type_mismatch",
                "is_inference=true requires inference or mixed statement_type.",
                claim_id=claim.claim_id,
            )
        if (
            claim.statement_type
            in {StatementType.FORECAST, StatementType.TARGET, StatementType.RISK}
            and any(marker in body for marker in _ACHIEVED_JA)
        ):
            _issue(
                issues,
                "achieved_language_for_non_actual",
                "Forecast/target/risk claim uses achieved-result language.",
                claim_id=claim.claim_id,
            )

    for section, orders in section_orders.items():
        if len(orders) != len(set(orders)):
            _issue(
                issues,
                "duplicate_section_order",
                f"Section {section.value} contains duplicate order values.",
                category="essential_quality",
            )
    assessment = analysis.management_consistency
    if assessment is None:
        _issue(
            issues,
            "management_consistency_missing",
            "No calculated management-consistency assessment is available.",
            severity="warning",
            category="diagnostic",
        )
    else:
        if assessment.score is None:
            _issue(
                issues,
                "management_consistency_score_missing",
                "The calculated management-consistency assessment has no "
                "numeric score.",
                severity="warning",
                category="diagnostic",
            )
        expected_dimensions = {
            item.value for item in ManagementConsistencyDimension
        }
        actual_dimensions = {
            component.dimension.value for component in assessment.components
        }
        if actual_dimensions != expected_dimensions:
            _issue(
                issues,
                "management_consistency_components_incomplete",
                "Management-consistency component set is incomplete or duplicated.",
                severity="warning",
                category="diagnostic",
            )
        assessment_evidence_ids = {
            evidence_id
            for component in assessment.components
            for evidence_id in component.evidence_ids
        }
        used_evidence.update(assessment_evidence_ids)
        unresolved_assessment_ids = assessment_evidence_ids - set(evidence_by_id)
        if unresolved_assessment_ids:
            _issue(
                issues,
                "management_consistency_evidence_unresolved",
                "Some management-consistency evidence IDs do not resolve.",
                severity="warning",
                category="diagnostic",
            )
        if (
            assessment.management_discussion_evidence_share is not None
            and assessment.management_discussion_evidence_share < 0.5
        ):
            _issue(
                issues,
                "management_consistency_discussion_coverage_low",
                "Less than half of the score evidence comes from management discussion.",
                severity="warning",
                category="diagnostic",
            )
    trend_discussion_share = (
        trend_management_discussion_refs / trend_evidence_refs
        if trend_evidence_refs
        else 0.0
    )
    if trend_evidence_refs and trend_discussion_share < 0.5:
        _issue(
            issues,
            "trend_management_discussion_coverage_low",
            "Less than half of trend evidence references come from management discussion.",
            severity="warning",
            category="diagnostic",
        )
    if policy.strict_quality:
        for section, minimum in MINIMUM_SECTION_COUNTS.items():
            if section_counts[section] < minimum:
                _issue(
                    issues,
                    "section_underfilled",
                    f"Section {section.value} has {section_counts[section]} claims; "
                    f"minimum is {minimum}.",
                    category="essential_quality",
                )
        if generated_report is not None:
            for code, message in essential_quality_issues(
                generated_report,
                exemplar_text,
                language="ja",
                anchor_fiscal_year=manifest.window.anchor_fiscal_year,
            ):
                _issue(
                    issues,
                    code,
                    message,
                    category="essential_quality",
                )
    for evidence_id in sorted(set(evidence_by_id) - used_evidence):
        _issue(
            issues,
            "unused_evidence",
            "Evidence record is not cited by any claim.",
            evidence_id=evidence_id,
            severity="warning",
            category="diagnostic",
        )
    statistics: dict[str, int | float | str | bool] = {
        "claims": len(analysis.claims),
        "evidence_records": len(analysis.evidence),
        "citation_references": total_refs,
        "trend_management_discussion_share": round(
            trend_discussion_share,
            4,
        ),
    }
    if analysis.management_consistency is not None:
        if analysis.management_consistency.score is not None:
            statistics["management_consistency_score"] = (
                analysis.management_consistency.score
            )
        if analysis.management_consistency.evidence_confidence is not None:
            statistics["management_consistency_evidence_confidence"] = (
                analysis.management_consistency.evidence_confidence
            )
    return _result(
        language="ja",
        issues=issues,
        statistics=statistics,
        policy=policy,
    )


def _span_map(
    spans: list[SupportedSpan] | list[TranslatedSpan],
) -> dict[str, object]:
    return {item.value_id: item for item in spans}


def _validate_translated_spans(
    ja_claim: AnalysisClaim,
    en_claim: TranslatedClaim,
    ja_spans: list[SupportedSpan],
    en_spans: list[TranslatedSpan],
    issues: list[ValidationIssue],
    kind: str,
) -> None:
    ja_map = _span_map(ja_spans)
    en_map = _span_map(en_spans)
    diagnostic = kind == "qualifier"
    severity = "warning" if diagnostic else "error"
    category = "diagnostic" if diagnostic else "factual_integrity"
    if set(ja_map) != set(en_map):
        _issue(
            issues,
            f"cross_language_{kind}_ids",
            f"{kind} value IDs differ between languages.",
            claim_id=ja_claim.claim_id,
            severity=severity,
            category=category,
        )
        return
    combined = en_claim.headline_en + "\n" + en_claim.body_en
    observed_english_financial_amounts = extract_english_financial_amounts(
        combined
    )
    observed_financial_amounts = (
        extract_japanese_financial_amounts(combined)
        + observed_english_financial_amounts
    )
    available_financial_amounts = list(observed_financial_amounts)
    observed_financial_surfaces = [
        amount.source_surface for amount in observed_financial_amounts
    ]
    canonical_combined = canonical_surface(combined)
    for value_id, ja_span in ja_map.items():
        en_span = en_map[value_id]
        if (
            ja_span.source_surface_ja != en_span.source_surface_ja
            or ja_span.evidence_id != en_span.evidence_id
        ):
            _issue(
                issues,
                f"cross_language_{kind}_mapping",
                f"{kind} source/evidence mapping changed for {value_id}.",
                claim_id=ja_claim.claim_id,
                severity=severity,
                category=category,
            )
        expected_surface = (
            ja_span.claim_surface_ja
            if kind == "figure"
            else en_span.claim_surface_en
        )
        expected_financial_amounts = (
            extract_japanese_financial_amounts(expected_surface)
            if kind == "figure"
            else []
        )
        if expected_financial_amounts:
            canonical_expected = canonical_surface(expected_surface)
            if re.search(
                rf"(?<![\d.]){re.escape(canonical_expected)}(?!\d)",
                canonical_combined,
            ):
                continue
            missing_values = []
            for amount in expected_financial_amounts:
                match_index = next(
                    (
                        index
                        for index, observed in enumerate(
                            available_financial_amounts
                        )
                        if observed.yen_value == amount.yen_value
                    ),
                    None,
                )
                if match_index is None:
                    match_index = next(
                        (
                            index
                            for index, observed in enumerate(
                                available_financial_amounts
                            )
                            if financial_display_matches(amount, observed)
                        ),
                        None,
                    )
                if match_index is None:
                    missing_values.append(amount)
                else:
                    available_financial_amounts.pop(match_index)
            if missing_values:
                expected = ", ".join(
                    amount.source_surface for amount in missing_values
                )
                observed = ", ".join(observed_financial_surfaces) or "none"
                _issue(
                    issues,
                    "english_financial_value_mismatch",
                    (
                        f"Structured financial span {value_id} expects an "
                        f"economically equivalent value for {expected!r}, but "
                        f"the English claim contains: {observed}."
                    ),
                    claim_id=ja_claim.claim_id,
                    severity="error",
                    category="factual_integrity",
                )
            elif not any(
                financial_display_matches(expected, observed)
                for expected in expected_financial_amounts
                for observed in observed_english_financial_amounts
            ):
                _issue(
                    issues,
                    "english_financial_surface_not_translated",
                    (
                        f"Structured financial span {value_id} retained Japanese "
                        "notation instead of English yen notation."
                    ),
                    claim_id=ja_claim.claim_id,
                    severity="warning",
                    category="diagnostic",
                )
            continue
        if _english_surface_key(
            expected_surface
        ) not in _english_surface_key(combined):
            _issue(
                issues,
                f"translated_{kind}_surface_missing",
                f"Translated {kind} surface is not in the English claim.",
                claim_id=ja_claim.claim_id,
                severity=severity,
                category=category,
            )
def validate_english(
    translation: EnglishTranslation,
    analysis: JapaneseAnalysis,
    manifest: SelectionManifest,
    *,
    generated_report: str | None = None,
    exemplar_text: str | None = None,
    policy: ValidationPolicy = ValidationPolicy(),
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    if translation.identity.model_dump() != analysis.identity.model_dump():
        _issue(
            issues,
            "cross_language_identity_mismatch",
            "Company identity or reporting period changed during translation.",
        )
    ja_claims = {item.claim_id: item for item in analysis.claims}
    en_counts = Counter(item.claim_id for item in translation.claims)
    for claim_id, count in en_counts.items():
        if count > 1:
            _issue(
                issues,
                "duplicate_translated_claim",
                f"Translated claim ID appears {count} times.",
                claim_id=claim_id,
            )
    en_claims = {item.claim_id: item for item in translation.claims}
    if set(ja_claims) != set(en_claims):
        _issue(
            issues,
            "cross_language_claim_ids",
            "Japanese and English claim ID sets differ.",
        )
    total_refs = 0
    for claim_id in sorted(set(ja_claims) & set(en_claims)):
        ja_claim = ja_claims[claim_id]
        en_claim = en_claims[claim_id]
        total_refs += len(en_claim.evidence_ids)
        scalar_pairs = (
            ("section", ja_claim.section, en_claim.section, "error"),
            ("order", ja_claim.order, en_claim.order, "error"),
            ("evidence_ids", ja_claim.evidence_ids, en_claim.evidence_ids, "error"),
            (
                "statement_type",
                ja_claim.statement_type,
                en_claim.statement_type,
                "error",
            ),
            (
                "is_inference",
                ja_claim.is_inference,
                en_claim.is_inference,
                "error",
            ),
            ("causal", ja_claim.causal, en_claim.causal, "warning"),
        )
        for field, left, right, severity in scalar_pairs:
            if left != right:
                _issue(
                    issues,
                    f"cross_language_{field}",
                    f"Field {field} changed during translation.",
                    claim_id=claim_id,
                    severity=severity,
                    category=(
                        "diagnostic"
                        if severity == "warning"
                        else "factual_integrity"
                    ),
                )
        _validate_translated_spans(
            ja_claim, en_claim, ja_claim.figures, en_claim.figures, issues, "figure"
        )
        _validate_translated_spans(
            ja_claim, en_claim, ja_claim.dates, en_claim.dates, issues, "date"
        )
        _validate_translated_spans(
            ja_claim,
            en_claim,
            ja_claim.qualifiers,
            en_claim.qualifiers,
            issues,
            "qualifier",
        )
        covered_numbers = {
            number
            for span in [*en_claim.figures, *en_claim.dates]
            for number in _numbers(span.claim_surface_en)
        }
        unsupported_numbers = (
            _numbers(en_claim.headline_en + "\n" + en_claim.body_en)
            - covered_numbers
        )
        if en_claim.section in TREND_SECTIONS:
            unsupported_numbers.discard(str(len(manifest.window.unique_years)))
        for number in sorted(unsupported_numbers):
            _issue(
                issues,
                "unsupported_english_numeric_surface",
                f"English numeric surface {number!r} is not protected.",
                claim_id=claim_id,
            )
        body = en_claim.body_en.lower()
        if any(marker in body for marker in _CAUSAL_EN) and not en_claim.causal:
            _issue(
                issues,
                "english_causal_flag_missing",
                "English claim uses causal language but causal=false.",
                claim_id=claim_id,
                severity="warning",
                category="diagnostic",
            )
        if (
            en_claim.statement_type
            in {StatementType.FORECAST, StatementType.TARGET}
            and any(marker in body for marker in _ACHIEVED_EN)
        ):
            _issue(
                issues,
                "english_achieved_language_for_non_actual",
                "Forecast/target/risk translation uses achieved-result language.",
                claim_id=claim_id,
                severity="warning",
                category="diagnostic",
            )

    if translation.evidence_translations:
        _issue(
            issues,
            "legacy_evidence_translations_ignored",
            "Legacy English evidence translations are present but ignored; "
            "the report renders original Japanese quotations.",
            severity="warning",
            category="diagnostic",
        )
    if generated_report is not None:
        for code, message in essential_quality_issues(
            generated_report,
            exemplar_text,
            language="en",
            anchor_fiscal_year=manifest.window.anchor_fiscal_year,
        ):
            _issue(
                issues,
                code,
                message,
                category="essential_quality",
            )
    return _result(
        language="en",
        issues=issues,
        statistics={
            "claims": len(translation.claims),
            "legacy_evidence_translations_ignored": len(
                translation.evidence_translations
            ),
            "citation_references": total_refs,
            "manifest_id": manifest.manifest_id,
        },
        policy=policy,
    )
