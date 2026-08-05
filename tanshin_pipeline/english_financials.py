"""Parse yen-denominated financial expressions without FX conversion."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .schemas import EnglishTranslation, JapaneseAnalysis


_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_JA_FINANCIAL_RE = re.compile(
    rf"(?P<sign>[+\-△▲]?)\s*(?:"
    rf"(?P<trillion>{_NUMBER})兆"
    rf"(?:(?P<trillion_oku>{_NUMBER})億)?"
    rf"(?:(?P<trillion_million>{_NUMBER})百万円|円)"
    rf"|(?P<oku>{_NUMBER})億"
    rf"(?:(?P<oku_million>{_NUMBER})百万円|円)"
    rf"|(?P<million>{_NUMBER})百万円"
    rf"|(?P<thousand>{_NUMBER})千円"
    rf"|(?P<yen>{_NUMBER})円"
    rf")"
)
_EN_FINANCIAL_RE = re.compile(
    rf"(?P<sign>[+\-]?)\s*(?:"
    rf"(?:¥|JPY\s*)(?P<prefix>{_NUMBER})"
    rf"(?:\s*(?P<prefix_unit>trillion|billion|million|thousand))?"
    rf"(?:\s+yen)?"
    rf"|(?P<suffix>{_NUMBER})"
    rf"(?:\s*(?P<suffix_unit>trillion|billion|million|thousand))?"
    rf"\s+yen"
    rf")",
    re.IGNORECASE,
)
_EN_UNIT_SCALE = {
    None: Decimal("1"),
    "thousand": Decimal("1e3"),
    "million": Decimal("1e6"),
    "billion": Decimal("1e9"),
    "trillion": Decimal("1e12"),
}


@dataclass(frozen=True)
class FinancialAmount:
    """A monetary surface, its yen value, and any explicit display rounding."""

    source_surface: str
    yen_value: Decimal
    source_kind: str
    rounding_tolerance_yen: Decimal = Decimal("0")


@dataclass(frozen=True)
class EnglishNormalizationResult:
    """Compatibility result for the non-converting English audit stage."""

    translation: EnglishTranslation
    changes: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def _display_rounding_tolerance(number: str, unit: str | None) -> Decimal:
    """Return half a displayed unit at the written decimal precision."""

    if unit is None or "." not in number:
        return Decimal("0")
    decimal_places = len(number.rsplit(".", 1)[1])
    return (
        _EN_UNIT_SCALE[unit]
        * Decimal("0.5")
        * (Decimal("10") ** -decimal_places)
    )


def financial_display_matches(
    expected: FinancialAmount,
    observed: FinancialAmount,
) -> bool:
    """Whether an English display preserves a source value at its shown precision."""

    return (
        abs(expected.yen_value - observed.yen_value)
        <= observed.rounding_tolerance_yen
    )


def extract_japanese_financial_amounts(value: str) -> list[FinancialAmount]:
    """Extract Japanese source amounts used by Japanese-side normalization."""

    normalized = unicodedata.normalize("NFKC", value)
    amounts: list[FinancialAmount] = []
    for match in _JA_FINANCIAL_RE.finditer(normalized):
        groups = match.groupdict()
        if groups["trillion"] is not None:
            yen_value = _decimal(groups["trillion"]) * Decimal("1e12")
            if groups["trillion_oku"] is not None:
                yen_value += _decimal(groups["trillion_oku"]) * Decimal("1e8")
            if groups["trillion_million"] is not None:
                yen_value += (
                    _decimal(groups["trillion_million"]) * Decimal("1e6")
                )
            source_kind = "trillion"
        elif groups["oku"] is not None:
            yen_value = _decimal(groups["oku"]) * Decimal("1e8")
            if groups["oku_million"] is not None:
                yen_value += _decimal(groups["oku_million"]) * Decimal("1e6")
            source_kind = "oku"
        elif groups["million"] is not None:
            yen_value = _decimal(groups["million"]) * Decimal("1e6")
            source_kind = "million"
        elif groups["thousand"] is not None:
            yen_value = _decimal(groups["thousand"]) * Decimal("1e3")
            source_kind = "thousand"
        else:
            yen_value = _decimal(groups["yen"])
            source_kind = "yen"
        if groups["sign"] in {"-", "△", "▲"}:
            yen_value = -yen_value
        amounts.append(
            FinancialAmount(
                source_surface=match.group(),
                yen_value=yen_value,
                source_kind=source_kind,
            )
        )
    return amounts


def extract_english_financial_amounts(value: str) -> list[FinancialAmount]:
    """Extract English-language yen amounts such as ``¥83.4 billion``."""

    normalized = unicodedata.normalize("NFKC", value)
    amounts: list[FinancialAmount] = []
    for match in _EN_FINANCIAL_RE.finditer(normalized):
        groups = match.groupdict()
        number = groups["prefix"] or groups["suffix"]
        unit = groups["prefix_unit"] or groups["suffix_unit"]
        normalized_unit = unit.lower() if unit is not None else None
        yen_value = _decimal(number) * _EN_UNIT_SCALE[normalized_unit]
        if groups["sign"] == "-":
            yen_value = -yen_value
        amounts.append(
            FinancialAmount(
                source_surface=match.group(),
                yen_value=yen_value,
                source_kind=f"english_{normalized_unit or 'yen'}",
                rounding_tolerance_yen=_display_rounding_tolerance(
                    number,
                    normalized_unit,
                ),
            )
        )
    return amounts


def preserve_english_translation(
    translation: EnglishTranslation,
) -> EnglishNormalizationResult:
    """Return an independent copy without prose-level financial rewriting."""

    return EnglishNormalizationResult(
        translation=translation.model_copy(deep=True),
        changes=[],
        unresolved=[],
    )


def normalize_english_financials(
    analysis: JapaneseAnalysis,
    translation: EnglishTranslation,
) -> EnglishNormalizationResult:
    """Backward-compatible pass-through for model-rendered English figures."""

    del analysis
    return preserve_english_translation(translation)
