"""Deterministic formatting of Japanese financial statement amounts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .english_financials import FinancialAmount, extract_japanese_financial_amounts
from .schemas import JapaneseAnalysis


_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_MALFORMED_TRILLION_MILLION_RE = re.compile(
    rf"(?P<trillion>{_NUMBER})兆(?P<million>\d[\d,]{{3,}})百万円"
)
_UNGROUPED_MILLION_RE = re.compile(r"(?<![\d,])(?P<number>\d{4,})百万円")
_STATEMENT_AMOUNT_RE = re.compile(
    rf"(?P<trillion>{_NUMBER})兆(?:(?P<trillion_oku>{_NUMBER})億)?円"
    rf"|(?P<oku>{_NUMBER})億円"
    rf"|(?P<million>{_NUMBER})百万円"
)
_NARRATIVE_ROUNDING_TOLERANCE_YEN = Decimal("1e8")


@dataclass(frozen=True)
class _AmountMention:
    start: int
    end: int
    amount: FinancialAmount


def _decimal(value: str) -> Decimal:
    return Decimal(unicodedata.normalize("NFKC", value).replace(",", ""))


def _group_integer(value: int | Decimal) -> str:
    return f"{int(value):,}"


def _amount_mentions(value: str) -> list[_AmountMention]:
    normalized = unicodedata.normalize("NFKC", value)
    mentions: list[_AmountMention] = []
    for match in _STATEMENT_AMOUNT_RE.finditer(normalized):
        groups = match.groupdict()
        if groups["trillion"] is not None:
            yen_value = _decimal(groups["trillion"]) * Decimal("1e12")
            if groups["trillion_oku"] is not None:
                yen_value += _decimal(groups["trillion_oku"]) * Decimal("1e8")
            source_kind = "trillion"
        elif groups["oku"] is not None:
            yen_value = _decimal(groups["oku"]) * Decimal("1e8")
            source_kind = "oku"
        else:
            yen_value = _decimal(groups["million"]) * Decimal("1e6")
            source_kind = "million"
        mentions.append(
            _AmountMention(
                start=match.start(),
                end=match.end(),
                amount=FinancialAmount(
                    source_surface=match.group(),
                    yen_value=yen_value,
                    source_kind=source_kind,
                ),
            )
        )
    return mentions


def _repair_malformed_surfaces(
    value: str,
) -> tuple[str, list[dict[str, str]]]:
    changes: list[dict[str, str]] = []

    def repair_trillion_million(match: re.Match[str]) -> str:
        trillion = _decimal(match.group("trillion"))
        million = _decimal(match.group("million"))
        replacement = (
            f"{_group_integer(trillion * Decimal('1000000') + million)}百万円"
        )
        changes.append(
            {
                "reason": "mixed_trillion_million_token_repaired",
                "from": match.group(),
                "to": replacement,
            }
        )
        return replacement

    repaired = _MALFORMED_TRILLION_MILLION_RE.sub(
        repair_trillion_million,
        value,
    )

    def group_million(match: re.Match[str]) -> str:
        replacement = f"{_group_integer(_decimal(match.group('number')))}百万円"
        if replacement != match.group():
            changes.append(
                {
                    "reason": "million_yen_digit_grouping",
                    "from": match.group(),
                    "to": replacement,
                }
            )
        return replacement

    return _UNGROUPED_MILLION_RE.sub(group_million, repaired), changes


def _format_exact_oku_value(yen_value: Decimal) -> str | None:
    total_oku = yen_value / Decimal("1e8")
    if total_oku != total_oku.to_integral_value():
        return None
    total_oku_int = int(total_oku)
    trillion, oku = divmod(total_oku_int, 10_000)
    if trillion and oku:
        return f"{trillion:,}兆{oku:,}億円"
    if trillion:
        return f"{trillion:,}兆円"
    return f"{total_oku_int:,}億円"


def _preferred_narrative_surface(
    amount: FinancialAmount,
    evidence_texts: list[str],
) -> str | None:
    candidates: set[str] = set()
    for evidence_text in evidence_texts:
        for candidate in extract_japanese_financial_amounts(evidence_text):
            if candidate.source_kind not in {"trillion", "oku"}:
                continue
            if (
                abs(candidate.yen_value - amount.yen_value)
                < _NARRATIVE_ROUNDING_TOLERANCE_YEN
            ):
                candidates.add(unicodedata.normalize("NFKC", candidate.source_surface))
    return next(iter(candidates)) if len(candidates) == 1 else None


def _normalize_statement_amounts(
    value: str,
    evidence_texts: list[str],
) -> tuple[str, list[dict[str, str]]]:
    replacements: list[tuple[int, int, str, str]] = []
    for mention in _amount_mentions(value):
        amount = mention.amount
        if amount.source_kind != "million":
            continue
        replacement = _preferred_narrative_surface(amount, evidence_texts)
        reason = "source_narrative_unit"
        if replacement is None:
            replacement = _format_exact_oku_value(amount.yen_value)
            reason = "exact_million_to_oku_conversion"
        if replacement is None or replacement == amount.source_surface:
            continue
        replacements.append(
            (
                mention.start,
                mention.end,
                replacement,
                reason,
            )
        )

    normalized = value
    changes: list[dict[str, str]] = []
    for start, end, replacement, reason in sorted(replacements, reverse=True):
        original = normalized[start:end]
        normalized = normalized[:start] + replacement + normalized[end:]
        changes.append(
            {
                "reason": reason,
                "from": original,
                "to": replacement,
            }
        )
    changes.reverse()
    return normalized, changes


def normalize_japanese_financials(
    analysis: JapaneseAnalysis,
    page_index: object | None = None,
) -> list[dict[str, Any]]:
    """Normalize claim monetary surfaces from their cited Japanese evidence.

    Only financial-statement amounts expressed in 百万円, 億円, or 兆円 are
    touched. Dividends, percentages, dates, and prose are left unchanged.
    """

    evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
    audit: list[dict[str, Any]] = []
    for claim in analysis.claims:
        evidence_texts = [
            evidence_by_id[evidence_id].exact_quote_ja
            for evidence_id in claim.evidence_ids
            if evidence_id in evidence_by_id
        ]
        if page_index is not None:
            for evidence_id in claim.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    continue
                evidence_texts.extend(
                    page_index.sentences(
                        evidence.source_filename,
                        evidence.pdf_page,
                        include_fallback=True,
                    )
                )
        for field_name in ("headline_ja", "body_ja"):
            original = getattr(claim, field_name)
            repaired, repair_changes = _repair_malformed_surfaces(original)
            normalized, amount_changes = _normalize_statement_amounts(
                repaired,
                evidence_texts,
            )
            if normalized == original:
                continue
            setattr(claim, field_name, normalized)
            for change in [*repair_changes, *amount_changes]:
                audit.append(
                    {
                        "type": "japanese_financial_surface_normalized",
                        "claim_id": claim.claim_id,
                        "field": field_name,
                        **change,
                    }
                )
    return audit
