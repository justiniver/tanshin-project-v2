"""Quality-first offline normalization of model-produced Japanese analysis."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .english_financials import extract_japanese_financial_amounts
from .japanese_financials import normalize_japanese_financials
from .management_consistency import calculate_management_consistency
from .pdf_text import PdfTextIndex, canonical_text
from .schemas import (
    AnalysisClaim,
    EvidenceRecord,
    JapaneseAnalysis,
    JapaneseModelResponse,
    SectionKey,
    SelectionManifest,
    StatementType,
    SupportedSpan,
    materialize_japanese_analysis,
)

_PERIOD_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}年(?:\d{1,2}月期|度)")
_YEAR_ONLY_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}年")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[¥￥$])?-?\d[\d,]*(?:\.\d+)?"
    r"(?:兆\d[\d,]*(?:\.\d+)?億円|ポイント|百万円|千円|兆円|億円|"
    r"年間|か月|ヶ月|%|％|倍|円|年|月|日|期|人|件|戸|社)?"
)
_CAUSAL_MARKERS = ("ため", "により", "によって", "受けて", "寄与", "背景", "伴う")
_FORECAST_MARKERS = ("予想", "予定", "見込", "思われ")
_TARGET_MARKERS = ("目標", "方針", "設定", "計画")
_RISK_MARKERS = ("リスク", "不確実", "必要があります", "注視")
_ACTUAL_RESULT_MARKERS = (
    "となりました",
    "としております",
    "計上しました",
    "実績",
)
_QUALIFIER_FAMILIES = (
    (
        ("見込んでいる", "見込む", "見込み", "思われる", "思われます"),
        ("見込んでいる", "見込む", "見込み", "思われる", "思われます"),
    ),
    (
        (
            "向き合う必要がある",
            "向き合っていく必要がある",
            "注視する必要がある",
            "注視していく必要がある",
            "必要がある",
            "必要があります",
        ),
        (
            "向き合う必要があります",
            "向き合っていく必要があります",
            "注視する必要があります",
            "注視していく必要があります",
            "必要があります",
        ),
    ),
    (("可能性",), ("可能性",)),
    (("不確実性",), ("不確実性",)),
    (("程度",), ("程度",)),
    (("約",), ("約",)),
    (("予定", "予想"), ("予定", "予想")),
)
_LATEST_SECTIONS = {
    SectionKey.COMPANY_OVERVIEW,
    SectionKey.LATEST_KEY_TAKEAWAY,
    SectionKey.LATEST_BUSINESS_DRIVER,
    SectionKey.LATEST_OUTLOOK,
    SectionKey.LATEST_RISK,
    SectionKey.LATEST_CONTEXT,
}
_SAFE_TEXT_REPLACEMENTS = {
    "超画して": "超過して",
    "计": "計",
    "超算しました": "上回りました",
    "超算": "上回り",
}


def canonical_surface(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace(",", "").replace(" ", "")


def compact_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    return "".join(
        char
        for char in value
        if char.isalnum()
        or "\u3040" <= char <= "\u30ff"
        or "\u4e00" <= char <= "\u9fff"
    )


def numeric_surfaces(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value)
    periods = list(_PERIOD_RE.finditer(normalized))
    blocked = {index for match in periods for index in range(*match.span())}
    matches = [(match.start(), match.group()) for match in periods]
    matches.extend(
        (match.start(), match.group())
        for match in _NUMBER_RE.finditer(normalized)
        if not any(index in blocked for index in range(*match.span()))
    )
    return list(dict.fromkeys(surface for _, surface in sorted(matches)))


@dataclass(frozen=True)
class NormalizationResult:
    analysis: JapaneseAnalysis
    changes: list[dict[str, Any]]


def _source_numeric_surface(quote: str, surface: str) -> str | None:
    target = canonical_surface(surface)
    exact = next(
        (item for item in numeric_surfaces(quote) if canonical_surface(item) == target),
        None,
    )
    if exact is not None:
        return exact
    target_amounts = extract_japanese_financial_amounts(surface)
    if len(target_amounts) != 1:
        return None
    equivalent = [
        amount.source_surface
        for amount in extract_japanese_financial_amounts(quote)
        if amount.yen_value == target_amounts[0].yen_value
    ]
    return equivalent[0] if equivalent else None


def _next_value_id(claim_id: str, kind: str, spans: list[SupportedSpan]) -> str:
    existing = {span.value_id for span in spans}
    index = 1
    while f"{claim_id}:{kind}:{index:02d}" in existing:
        index += 1
    return f"{claim_id}:{kind}:{index:02d}"


def _character_bigrams(value: str) -> set[str]:
    value = compact_text(value)
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _overlap_score(left: str, right: str) -> float:
    left_items = _character_bigrams(left)
    right_items = _character_bigrams(right)
    if not left_items or not right_items:
        return 0.0
    return len(left_items & right_items) / len(left_items | right_items)


def _derived_evidence_id(filename: str, page: int, quote: str) -> str:
    digest = hashlib.sha256(canonical_text(quote).encode("utf-8")).hexdigest()[:8]
    return f"{filename}:r{page:04d}-{digest}"


def _statement_type_for_sentence(
    sentence: str,
    claim: AnalysisClaim,
) -> StatementType:
    if any(marker in sentence for marker in _FORECAST_MARKERS):
        return StatementType.FORECAST
    if "当期" in sentence and any(
        marker in sentence for marker in _ACTUAL_RESULT_MARKERS
    ):
        return StatementType.ACTUAL
    if any(marker in sentence for marker in _TARGET_MARKERS):
        return StatementType.TARGET
    if any(marker in sentence for marker in _RISK_MARKERS):
        return StatementType.RISK
    if claim.is_inference:
        return StatementType.INFERENCE
    return StatementType.ACTUAL


def _period_labels(
    claim: AnalysisClaim,
    selected_year: int,
    fallback: EvidenceRecord,
) -> tuple[str, str]:
    for period in _PERIOD_RE.findall(claim.headline_ja + "\n" + claim.body_ja):
        year_match = re.search(r"(?:19|20)\d{2}", period)
        if year_match and int(year_match.group()) == selected_year:
            return period, f"FY{selected_year}"
    return fallback.period_label_ja, fallback.period_label_en


def _add_derived_evidence(
    analysis: JapaneseAnalysis,
    claim: AnalysisClaim,
    base: EvidenceRecord,
    sentence: str,
    manifest: SelectionManifest,
    changes: list[dict[str, Any]],
) -> EvidenceRecord:
    evidence_id = _derived_evidence_id(
        base.source_filename, base.pdf_page, sentence
    )
    existing = next(
        (item for item in analysis.evidence if item.evidence_id == evidence_id),
        None,
    )
    if existing is not None:
        if evidence_id not in claim.evidence_ids:
            claim.evidence_ids.append(evidence_id)
        return existing
    selected = next(
        item
        for item in manifest.selected_files
        if item.filename == base.source_filename
    )
    period_ja, period_en = _period_labels(claim, selected.fiscal_year, base)
    derived = EvidenceRecord(
        evidence_id=evidence_id,
        source_filename=base.source_filename,
        pdf_page=base.pdf_page,
        exact_quote_ja=sentence,
        period_label_ja=period_ja,
        period_label_en=period_en,
        statement_type=_statement_type_for_sentence(sentence, claim),
        source_section=base.source_section,
        tags=list(dict.fromkeys([*base.tags, "local_repair"])),
    )
    analysis.evidence.append(derived)
    if evidence_id not in claim.evidence_ids:
        claim.evidence_ids.append(evidence_id)
    changes.append(
        {
            "type": "derived_evidence_added",
            "claim_id": claim.claim_id,
            "evidence_id": evidence_id,
            "source_filename": base.source_filename,
            "pdf_page": base.pdf_page,
            "quote": sentence,
        }
    )
    return derived


def _best_unique_sentence(
    claim: AnalysisClaim,
    bases: Iterable[EvidenceRecord],
    index: PdfTextIndex,
    *,
    surface: str | None = None,
    require_causal: bool = False,
) -> tuple[EvidenceRecord, str] | None:
    candidates: list[tuple[float, EvidenceRecord, str]] = []
    target = canonical_surface(surface) if surface is not None else None
    combined = claim.headline_ja + "\n" + claim.body_ja
    seen: set[tuple[str, int, str]] = set()
    for base in bases:
        for sentence in index.sentences(
            base.source_filename,
            base.pdf_page,
            include_fallback=True,
        ):
            key = (base.source_filename, base.pdf_page, canonical_text(sentence))
            if key in seen:
                continue
            seen.add(key)
            if target is not None and target not in canonical_surface(sentence):
                continue
            if require_causal and not any(
                marker in sentence for marker in _CAUSAL_MARKERS
            ):
                continue
            score = _overlap_score(combined, sentence)
            if score >= (0.02 if target is not None else 0.08):
                candidates.append((score, base, sentence))
    if not candidates:
        return None

    if target is not None:
        # Multiple line windows can represent the same unique physical
        # occurrence. If the value appears once on one cited page, retain the
        # shortest complete window around that occurrence.
        unique_pages = {
            (base.source_filename, base.pdf_page)
            for _, base, _ in candidates
            if canonical_surface(
                index.page_text(
                    base.source_filename,
                    base.pdf_page,
                    include_fallback=True,
                )
            ).count(target)
            == 1
        }
        if len(unique_pages) == 1:
            filename, page = next(iter(unique_pages))
            page_candidates = [
                item
                for item in candidates
                if item[1].source_filename == filename
                and item[1].pdf_page == page
            ]
            _, base, sentence = min(
                page_candidates,
                key=lambda item: (len(item[2]), -item[0]),
            )
            return base, sentence

    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score = candidates[0][0]
    competitive = [
        item for item in candidates if item[0] >= best_score - 0.06
    ]
    source_pages = {
        (item[1].source_filename, item[1].pdf_page) for item in competitive
    }
    if len(source_pages) > 1:
        ranked_pages = sorted(
            (
                max(item[0] for item in competitive if item[1].source_filename == filename and item[1].pdf_page == page),
                filename,
                page,
            )
            for filename, page in source_pages
        )
        if (
            len(ranked_pages) > 1
            and ranked_pages[-1][0] - ranked_pages[-2][0] < 0.025
        ):
            return None
        winning_page = (ranked_pages[-1][1], ranked_pages[-1][2])
        competitive = [
            item
            for item in competitive
            if (item[1].source_filename, item[1].pdf_page) == winning_page
        ]
    _, base, sentence = min(
        competitive,
        key=lambda item: (len(item[2]), -item[0]),
    )
    return base, sentence


def _repair_evidence_locations(
    analysis: JapaneseAnalysis,
    manifest: SelectionManifest,
    index: PdfTextIndex,
    changes: list[dict[str, Any]],
) -> None:
    selected = {item.filename: item for item in manifest.selected_files}
    for evidence in analysis.evidence:
        item = selected.get(evidence.source_filename)
        if item is None or evidence.pdf_page > item.page_count:
            continue
        page_text = index.page_text(
            evidence.source_filename,
            evidence.pdf_page,
            include_fallback=True,
        )
        quote = compact_text(evidence.exact_quote_ja)
        if quote and quote in compact_text(page_text):
            continue
        exact_hits = index.find_quote_pages(
            evidence.source_filename, evidence.exact_quote_ja
        )
        if exact_hits:
            ranked = sorted(exact_hits, key=lambda page: abs(page - evidence.pdf_page))
            if len(ranked) == 1 or (
                abs(ranked[0] - evidence.pdf_page)
                < abs(ranked[1] - evidence.pdf_page)
            ):
                old_page = evidence.pdf_page
                evidence.pdf_page = ranked[0]
                changes.append(
                    {
                        "type": "physical_pdf_page_resolved",
                        "evidence_id": evidence.evidence_id,
                        "from": old_page,
                        "to": evidence.pdf_page,
                    }
                )
                continue
        anchor = re.sub(
            r"\s+",
            "",
            unicodedata.normalize("NFKC", evidence.exact_quote_ja),
        )[:24]
        candidates: list[tuple[float, int, str]] = []
        for page in range(1, item.page_count + 1):
            for sentence in index.sentences(
                evidence.source_filename, page, include_fallback=True
            ):
                if anchor and anchor not in re.sub(r"\s+", "", sentence):
                    continue
                score = SequenceMatcher(
                    None,
                    compact_text(evidence.exact_quote_ja),
                    compact_text(sentence),
                ).ratio()
                if score >= 0.78:
                    candidates.append((score, page, sentence))
        candidates.sort(reverse=True)
        if candidates and (
            len(candidates) == 1
            or candidates[0][0] - candidates[1][0] >= 0.08
        ):
            score, page, sentence = candidates[0]
            old_quote, old_page = evidence.exact_quote_ja, evidence.pdf_page
            evidence.exact_quote_ja, evidence.pdf_page = sentence, page
            changes.append(
                {
                    "type": "exact_quote_completed",
                    "evidence_id": evidence.evidence_id,
                    "from_page": old_page,
                    "to_page": page,
                    "similarity": round(score, 4),
                    "original_quote": old_quote,
                    "resolved_quote": sentence,
                }
            )


def _period_supported_by_evidence(
    surface: str,
    evidence: EvidenceRecord,
    manifest: SelectionManifest,
) -> bool:
    match = re.search(r"(?:19|20)\d{2}", surface)
    if match is None:
        return False
    year = int(match.group())
    selected = next(
        (
            item
            for item in manifest.selected_files
            if item.filename == evidence.source_filename
        ),
        None,
    )
    return (
        year in {int(value) for value in re.findall(r"(?:19|20)\d{2}", evidence.period_label_ja)}
        or selected is not None
        and selected.fiscal_year == year
    )


def _date_source_surface(
    surface: str,
    evidence: EvidenceRecord,
    manifest: SelectionManifest,
) -> str | None:
    exact = _source_numeric_surface(evidence.exact_quote_ja, surface)
    if exact is not None:
        return exact
    match = re.fullmatch(r"((?:19|20)\d{2})年3月期", surface)
    if match is not None:
        fiscal_year_surface = f"{int(match.group(1)) - 1}年度"
        source = _source_numeric_surface(
            evidence.exact_quote_ja, fiscal_year_surface
        )
        if source is not None:
            return source
    if _period_supported_by_evidence(surface, evidence, manifest):
        return evidence.period_label_ja
    return None


def _yen_value(surface: str) -> Decimal | None:
    normalized = canonical_surface(surface)
    match = re.fullmatch(
        r"(-?\d+(?:\.\d+)?)(百万円|千円|億円|円)",
        normalized,
    )
    if match is None:
        return None
    try:
        amount = Decimal(match.group(1))
    except InvalidOperation:
        return None
    multiplier = {
        "億円": Decimal("100000000"),
        "百万円": Decimal("1000000"),
        "千円": Decimal("1000"),
        "円": Decimal("1"),
    }[match.group(2)]
    return amount * multiplier


def _rounded_threshold_source(
    quote: str,
    surface: str,
    claim_text: str,
) -> str | None:
    if f"{canonical_surface(surface)}超" not in canonical_surface(claim_text):
        return None
    threshold = _yen_value(surface)
    if threshold is None or threshold <= 0:
        return None
    candidates = [
        (value, source)
        for source in numeric_surfaces(quote)
        if (value := _yen_value(source)) is not None
        and threshold < value <= threshold * Decimal("1.20")
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[1]


def _derive_numeric_spans(
    analysis: JapaneseAnalysis,
    claim: AnalysisClaim,
    manifest: SelectionManifest,
    index: PdfTextIndex,
    changes: list[dict[str, Any]],
) -> None:
    evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
    combined = claim.headline_ja + "\n" + claim.body_ja
    trend_years = len(manifest.window.unique_years)
    for surface in numeric_surfaces(combined):
        if (
            claim.section not in _LATEST_SECTIONS
            and canonical_surface(surface)
            in {
                str(trend_years),
                f"{trend_years}年",
                f"{trend_years}年間",
            }
        ):
            continue
        is_date = bool(
            _PERIOD_RE.fullmatch(surface) or _YEAR_ONLY_RE.fullmatch(surface)
        )
        candidates: list[tuple[EvidenceRecord, str]] = []
        for evidence_id in claim.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            source = (
                _date_source_surface(surface, evidence, manifest)
                if is_date
                else _source_numeric_surface(evidence.exact_quote_ja, surface)
            )
            if source is None and not is_date:
                source = _rounded_threshold_source(
                    evidence.exact_quote_ja,
                    surface,
                    combined,
                )
            if source is not None:
                candidates.append((evidence, source))
        if not candidates and not is_date:
            bases = [
                evidence_by_id[evidence_id]
                for evidence_id in claim.evidence_ids
                if evidence_id in evidence_by_id
            ]
            recovered = _best_unique_sentence(
                claim, bases, index, surface=surface
            )
            if recovered is not None:
                base, sentence = recovered
                evidence = _add_derived_evidence(
                    analysis, claim, base, sentence, manifest, changes
                )
                candidates.append(
                    (evidence, _source_numeric_surface(sentence, surface) or surface)
                )
                evidence_by_id[evidence.evidence_id] = evidence
        if not candidates and not is_date:
            global_hits = [
                (evidence, source)
                for evidence in analysis.evidence
                if (source := _source_numeric_surface(
                    evidence.exact_quote_ja, surface
                ))
                is not None
            ]
            if len(global_hits) == 1:
                evidence, source = global_hits[0]
                if evidence.evidence_id not in claim.evidence_ids:
                    claim.evidence_ids.append(evidence.evidence_id)
                    changes.append(
                        {
                            "type": "uniquely_supported_evidence_added",
                            "claim_id": claim.claim_id,
                            "evidence_id": evidence.evidence_id,
                            "surface": surface,
                        }
                    )
                candidates = [(evidence, source)]
        if not candidates:
            continue
        target = claim.dates if is_date else claim.figures
        kind = "date" if is_date else "figure"
        evidence, source = candidates[0]
        target.append(
            SupportedSpan(
                value_id=_next_value_id(claim.claim_id, kind, target),
                claim_surface_ja=surface,
                source_surface_ja=source,
                evidence_id=evidence.evidence_id,
            )
        )
        changes.append(
            {
                "type": "supported_span_added",
                "claim_id": claim.claim_id,
                "kind": kind,
                "surface": surface,
                "evidence_id": evidence.evidence_id,
            }
        )


def _derive_qualifiers(
    analysis: JapaneseAnalysis,
    claim: AnalysisClaim,
    changes: list[dict[str, Any]],
) -> None:
    if claim.is_inference:
        return
    evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
    combined = claim.headline_ja + "\n" + claim.body_ja
    for claim_variants, source_variants in _QUALIFIER_FAMILIES:
        claim_surface = next(
            (variant for variant in claim_variants if variant in combined),
            None,
        )
        if claim_surface is None:
            continue
        match: tuple[EvidenceRecord, str] | None = None
        for evidence_id in claim.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            source_surface = next(
                (
                    variant
                    for variant in source_variants
                    if variant in evidence.exact_quote_ja
                ),
                None,
            )
            if source_surface is not None:
                match = evidence, source_surface
                break
        if match is None:
            continue
        evidence, source_surface = match
        claim.qualifiers.append(
            SupportedSpan(
                value_id=_next_value_id(
                    claim.claim_id, "qualifier", claim.qualifiers
                ),
                claim_surface_ja=claim_surface,
                source_surface_ja=source_surface,
                evidence_id=evidence.evidence_id,
            )
        )
        changes.append(
            {
                "type": "qualifier_span_added",
                "claim_id": claim.claim_id,
                "claim_surface": claim_surface,
                "source_surface": source_surface,
                "evidence_id": evidence.evidence_id,
            }
        )


def _recover_causal_support(
    analysis: JapaneseAnalysis,
    claim: AnalysisClaim,
    manifest: SelectionManifest,
    index: PdfTextIndex,
    changes: list[dict[str, Any]],
) -> None:
    if not claim.causal or claim.is_inference:
        return
    evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
    cited = [
        evidence_by_id[evidence_id]
        for evidence_id in claim.evidence_ids
        if evidence_id in evidence_by_id
    ]
    if any(
        marker in evidence.exact_quote_ja
        for evidence in cited
        for marker in _CAUSAL_MARKERS
    ):
        return
    recovered = _best_unique_sentence(
        claim, cited, index, require_causal=True
    )
    if recovered is None:
        return
    base, sentence = recovered
    _add_derived_evidence(
        analysis, claim, base, sentence, manifest, changes
    )


def _reconcile_statement_type(
    analysis: JapaneseAnalysis,
    claim: AnalysisClaim,
    changes: list[dict[str, Any]],
) -> None:
    if claim.is_inference:
        return
    evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
    source_types = {
        evidence_by_id[evidence_id].statement_type
        for evidence_id in claim.evidence_ids
        if evidence_id in evidence_by_id
        and evidence_by_id[evidence_id].statement_type
        in {
            StatementType.ACTUAL,
            StatementType.FORECAST,
            StatementType.TARGET,
            StatementType.RISK,
        }
    }
    if len(source_types) > 1 and claim.statement_type != StatementType.MIXED:
        old = claim.statement_type
        claim.statement_type = StatementType.MIXED
        changes.append(
            {
                "type": "statement_type_reconciled",
                "claim_id": claim.claim_id,
                "from": old.value,
                "to": StatementType.MIXED.value,
                "source_types": sorted(item.value for item in source_types),
            }
            )


def _repair_claim_text(
    analysis: JapaneseAnalysis,
    changes: list[dict[str, Any]],
) -> None:
    for claim in analysis.claims:
        for field_name in ("headline_ja", "body_ja"):
            value = getattr(claim, field_name)
            repaired = value
            applied: list[dict[str, str]] = []
            for incorrect, corrected in _SAFE_TEXT_REPLACEMENTS.items():
                if incorrect in repaired:
                    repaired = repaired.replace(incorrect, corrected)
                    applied.append({"from": incorrect, "to": corrected})
            if repaired != value:
                setattr(claim, field_name, repaired)
                changes.append(
                    {
                        "type": "safe_text_typo_corrected",
                        "claim_id": claim.claim_id,
                        "field": field_name,
                        "replacements": applied,
                    }
                )


def normalize_japanese_analysis(
    analysis: JapaneseModelResponse | JapaneseAnalysis,
    manifest: SelectionManifest,
    repository_root: Path,
) -> NormalizationResult:
    normalized = materialize_japanese_analysis(analysis)
    changes: list[dict[str, Any]] = []
    index = PdfTextIndex(repository_root, manifest)
    try:
        _repair_evidence_locations(normalized, manifest, index, changes)
        _repair_claim_text(normalized, changes)
        changes.extend(normalize_japanese_financials(normalized, index))
        for claim in normalized.claims:
            prior_counts = {
                "figures": len(claim.figures),
                "dates": len(claim.dates),
                "qualifiers": len(claim.qualifiers),
            }
            claim.figures = []
            claim.dates = []
            claim.qualifiers = []
            if any(prior_counts.values()):
                changes.append(
                    {
                        "type": "model_support_spans_rebuilt",
                        "claim_id": claim.claim_id,
                        "discarded_counts": prior_counts,
                    }
                )
            _derive_numeric_spans(
                normalized, claim, manifest, index, changes
            )
            _recover_causal_support(
                normalized, claim, manifest, index, changes
            )
            _derive_qualifiers(normalized, claim, changes)
            _reconcile_statement_type(normalized, claim, changes)
        (
            normalized.management_consistency,
            consistency_changes,
        ) = calculate_management_consistency(
            normalized.management_consistency,
            normalized.evidence,
            manifest,
        )
        changes.extend(consistency_changes)
    finally:
        index.close()
    return NormalizationResult(normalized, changes)
