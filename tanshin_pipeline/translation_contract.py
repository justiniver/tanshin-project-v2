"""Compact model-facing translation contract and deterministic materialization."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from .schemas import (
    AnalysisClaim,
    EnglishTranslation,
    EnglishTranslationPatch,
    JapaneseAnalysis,
    SupportedSpan,
    TranslatedClaim,
    TranslatedClaimPatch,
    TranslatedSpan,
    TranslatedSpanPatch,
    TranslationInput,
    TranslationInputClaim,
    TranslationInputIdentity,
    TranslationInputSpan,
)


class TranslationContractError(ValueError):
    """Raised when a translation patch does not exactly cover its source analysis."""


T = TypeVar("T")


def build_translation_input(analysis: JapaneseAnalysis) -> TranslationInput:
    """Project an analysis down to only the fields the model must translate."""

    return TranslationInput(
        identity_context=TranslationInputIdentity(
            company_name_ja=analysis.identity.company_name_ja,
            company_name_en=analysis.identity.company_name_en,
            latest_period_ja=analysis.identity.latest_period_ja,
            latest_period_en=analysis.identity.latest_period_en,
        ),
        claims=[
            TranslationInputClaim(
                claim_id=claim.claim_id,
                section=claim.section,
                headline_ja=claim.headline_ja,
                body_ja=claim.body_ja,
                figures=[
                    TranslationInputSpan(
                        value_id=span.value_id,
                        claim_surface_ja=span.claim_surface_ja,
                    )
                    for span in claim.figures
                ],
                dates=[
                    TranslationInputSpan(
                        value_id=span.value_id,
                        claim_surface_ja=span.claim_surface_ja,
                    )
                    for span in claim.dates
                ],
                qualifiers=[
                    TranslationInputSpan(
                        value_id=span.value_id,
                        claim_surface_ja=span.claim_surface_ja,
                    )
                    for span in claim.qualifiers
                ],
            )
            for claim in analysis.claims
        ],
    )


def _index_unique(
    items: Sequence[T],
    *,
    attribute: str,
    label: str,
) -> dict[str, T]:
    indexed: dict[str, T] = {}
    duplicates: list[str] = []
    for item in items:
        item_id = str(getattr(item, attribute))
        if item_id in indexed:
            duplicates.append(item_id)
        indexed[item_id] = item
    if duplicates:
        raise TranslationContractError(
            f"Duplicate {label} IDs: {', '.join(sorted(set(duplicates)))}."
        )
    return indexed


def _require_exact_ids(
    source_ids: set[str],
    patch_ids: set[str],
    *,
    label: str,
) -> None:
    missing = sorted(source_ids - patch_ids)
    unknown = sorted(patch_ids - source_ids)
    if not missing and not unknown:
        return
    details: list[str] = []
    if missing:
        details.append(f"missing {label} IDs: {', '.join(missing)}")
    if unknown:
        details.append(f"unknown {label} IDs: {', '.join(unknown)}")
    raise TranslationContractError("; ".join(details) + ".")


def _materialize_spans(
    source_spans: Sequence[SupportedSpan],
    patch_spans: Sequence[TranslatedSpanPatch],
    *,
    claim_id: str,
    span_kind: str,
) -> list[TranslatedSpan]:
    source_by_id = _index_unique(
        source_spans,
        attribute="value_id",
        label=f"{claim_id} {span_kind} source",
    )
    patch_by_id = _index_unique(
        patch_spans,
        attribute="value_id",
        label=f"{claim_id} {span_kind} patch",
    )
    _require_exact_ids(
        set(source_by_id),
        set(patch_by_id),
        label=f"{claim_id} {span_kind}",
    )
    return [
        TranslatedSpan(
            value_id=source.value_id,
            claim_surface_en=patch_by_id[source.value_id].claim_surface_en,
            source_surface_ja=source.source_surface_ja,
            evidence_id=source.evidence_id,
        )
        for source in source_spans
    ]


def _materialize_claim(
    source: AnalysisClaim,
    patch: TranslatedClaimPatch,
) -> TranslatedClaim:
    return TranslatedClaim(
        claim_id=source.claim_id,
        section=source.section,
        order=source.order,
        headline_en=patch.headline_en,
        body_en=patch.body_en,
        evidence_ids=list(source.evidence_ids),
        statement_type=source.statement_type,
        is_inference=source.is_inference,
        causal=source.causal,
        figures=_materialize_spans(
            source.figures,
            patch.figures,
            claim_id=source.claim_id,
            span_kind="figure",
        ),
        dates=_materialize_spans(
            source.dates,
            patch.dates,
            claim_id=source.claim_id,
            span_kind="date",
        ),
        qualifiers=_materialize_spans(
            source.qualifiers,
            patch.qualifiers,
            claim_id=source.claim_id,
            span_kind="qualifier",
        ),
    )


def materialize_english_translation(
    analysis: JapaneseAnalysis,
    patch: EnglishTranslationPatch,
) -> EnglishTranslation:
    """Merge a compact model response with immutable source-analysis metadata."""

    source_by_id = _index_unique(
        analysis.claims,
        attribute="claim_id",
        label="source claim",
    )
    patch_by_id = _index_unique(
        patch.claims,
        attribute="claim_id",
        label="translation patch claim",
    )
    _require_exact_ids(
        set(source_by_id),
        set(patch_by_id),
        label="claim",
    )
    return EnglishTranslation(
        schema_version=analysis.schema_version,
        identity=analysis.identity.model_copy(deep=True),
        claims=[
            _materialize_claim(
                source,
                patch_by_id[source.claim_id],
            )
            for source in analysis.claims
        ],
        evidence_translations=[],
        model_notes=list(patch.model_notes),
    )
