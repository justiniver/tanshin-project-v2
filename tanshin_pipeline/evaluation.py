"""Offline exemplar comparison and quality rubric."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any


_CITATION_RE = re.compile(r"<sup>\[([^]]+)\]</sup>")
_EVIDENCE_SUFFIX_RE = re.compile(r"/(?:s\d{4}|r\d{4}-[0-9a-f]{8})")
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_SENTENCE_SPLIT_RE = re.compile(r"[。.!?！？]\s*")
_TREND_HEADINGS = ("## 2. トレンド分析", "## 2. Trend analysis")
_EVIDENCE_HEADINGS = ("## 根拠一覧", "## Evidence ledger")
_PERSPECTIVE_HEADINGS = (
    "### 10年間を通じた見方",
    "### The decade in perspective",
)
_KEY_TAKEAWAY_HEADINGS = (
    "### 経営者コメントの要点",
    "### Key management takeaways",
)

_SUMMARY_CATEGORY_PATTERNS = {
    "earnings_profitability": re.compile(
        r"売上高|営業利益|経常利益|純利益|利益率|収益性|"
        r"\b(revenue|sales|operating profit|ordinary profit|net income|margin)\b",
        re.IGNORECASE,
    ),
    "cash_flow_balance_sheet": re.compile(
        r"キャッシュ.?フロー|現金|純資産|自己資本|総資産|有利子負債|"
        r"\b(cash flow|liquidity|net assets|equity|total assets|debt)\b",
        re.IGNORECASE,
    ),
    "operations": re.compile(
        r"受注|管理戸数|販売戸数|稼働率|案件|取扱量|数量|価格|ミックス|"
        r"\b(orders?|backlog|units?|volume|utilization|mix|pricing)\b",
        re.IGNORECASE,
    ),
    "outlook_targets": re.compile(
        r"予想|見通し|目標|計画進捗|予定|"
        r"\b(forecast|outlook|guidance|target|management plan)\b",
        re.IGNORECASE,
    ),
    "capital_allocation": re.compile(
        r"配当|自己株式|株主還元|総還元|成長投資|資本配分|"
        r"\b(dividend|buyback|share repurchase|shareholder return|"
        r"capital allocation|growth investment)\b",
        re.IGNORECASE,
    ),
    "risk_uncertainty": re.compile(
        r"リスク|不確実|懸念|高騰|不足|規制|"
        r"\b(risk|uncertainty|constraint|shortage|regulation)\b",
        re.IGNORECASE,
    ),
}
_PROMOTIONAL_RE = re.compile(
    r"\b(world[- ]class|unmatched|exceptional|outstanding|revolutionary|"
    r"best[- ]in[- ]class|game[- ]changing|transformational)\b|"
    r"圧倒的|卓越した|革新的|高水準を誇|大台|飛躍|"
    r"進化させ|転換を果た|正常化|ストレートに.*結実",
    re.IGNORECASE,
)
_CONTRAST_RE = re.compile(
    r"\b(however|while|rather than|not merely|yet|although|whereas|"
    r"by contrast|despite)\b|一方|しかし|ではなく|対して|ものの|"
    r"反面|他方|異な|にもかかわらず",
    re.IGNORECASE,
)

_EXEMPLAR_TREND_MINIMUM_RATIO = 0.9375
_EXEMPLAR_PERSPECTIVE_MINIMUM_RATIO = 1.0
_GENERIC_TREND_MINIMUM = {"ja": 1500, "en": 2750}
_GENERIC_PERSPECTIVE_MINIMUM = {"ja": 350, "en": 600}


def _main_body(text: str) -> str:
    for marker in ("\n## 根拠一覧", "\n## Evidence ledger"):
        if marker in text:
            return text.split(marker, 1)[0]
    return text


def _trend_section(text: str) -> str:
    start = next(
        (text.find(marker) for marker in _TREND_HEADINGS if marker in text),
        -1,
    )
    if start < 0:
        return ""
    end_candidates = [
        text.find(marker, start)
        for marker in _EVIDENCE_HEADINGS
        if text.find(marker, start) >= 0
    ]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end]


def _trend_perspective_section(text: str) -> str:
    trend = _trend_section(text)
    start = next(
        (trend.find(marker) for marker in _PERSPECTIVE_HEADINGS if marker in trend),
        -1,
    )
    if start < 0:
        return ""
    next_heading = trend.find("\n### ", start + 4)
    end = next_heading if next_heading >= 0 else len(trend)
    return trend[start:end]


def _subsection(text: str, headings: tuple[str, ...]) -> str:
    body = _main_body(text)
    start = next(
        (body.find(marker) for marker in headings if marker in body),
        -1,
    )
    if start < 0:
        return ""
    next_heading = body.find("\n### ", start + 4)
    end = next_heading if next_heading >= 0 else len(body)
    return body[start:end]


def _narrative_characters(text: str) -> int:
    without_citations = _CITATION_RE.sub("", text)
    without_markdown = re.sub(r"(?m)^#{1,6}\s+.*$", "", without_citations)
    without_markdown = re.sub(r"[*_`>\-\s]", "", without_markdown)
    return len(without_markdown)


def _narrative_sentences(text: str) -> list[str]:
    clean = _CITATION_RE.sub("", re.sub(r"[*#>`-]", " ", text))
    return [
        re.sub(r"\s+", " ", sentence).strip()
        for sentence in _SENTENCE_SPLIT_RE.split(clean)
        if len(sentence.strip()) >= 25
    ]


def _summary_categories(text: str) -> list[str]:
    takeaway = _subsection(text, _KEY_TAKEAWAY_HEADINGS)
    return [
        name
        for name, pattern in _SUMMARY_CATEGORY_PATTERNS.items()
        if pattern.search(takeaway)
    ]


def _trend_theme_blocks(text: str) -> list[str]:
    return [
        line.strip()
        for line in _trend_section(text).splitlines()
        if line.strip().startswith("**")
    ]


def _without_evidence_suffixes(text: str) -> str:
    return _EVIDENCE_SUFFIX_RE.sub("", text)


def _semantic_overlap_pairs(blocks: list[str]) -> int:
    def ngrams(value: str) -> set[str]:
        value = _CITATION_RE.sub("", value)
        value = re.sub(r"\d+(?:[.,]\d+)*", "0", value)
        value = re.sub(r"[\s*_`>#\[\]();:：、。,.・\-]", "", value).lower()
        return {
            value[index : index + 4]
            for index in range(max(0, len(value) - 3))
        }

    prepared = [ngrams(block) for block in blocks]
    overlaps = 0
    for index, left in enumerate(prepared):
        if len(left) < 30:
            continue
        for right in prepared[index + 1 :]:
            if len(right) < 30:
                continue
            union = left | right
            if union and len(left & right) / len(union) >= 0.35:
                overlaps += 1
    return overlaps


def report_metrics(
    text: str,
    *,
    anchor_fiscal_year: int | None = None,
) -> dict[str, Any]:
    body = _main_body(text)
    trend = _trend_section(text)
    perspective = _trend_perspective_section(text)
    citation_groups = _CITATION_RE.findall(body)
    citation_refs = sum(len(group.split("; ")) for group in citation_groups)
    headings = [line.strip() for line in body.splitlines() if line.startswith("#")]
    year_text = _without_evidence_suffixes(body)
    all_years = sorted({int(value) for value in _YEAR_RE.findall(year_text)})
    years = (
        [year for year in all_years if year <= anchor_fiscal_year]
        if anchor_fiscal_year is not None
        else all_years
    )
    future_years = (
        [year for year in all_years if year > anchor_fiscal_year]
        if anchor_fiscal_year is not None
        else []
    )
    bold_themes = len(re.findall(r"^\*\*.+?\*\*", body, re.MULTILINE))
    sentences = _narrative_sentences(body)
    counts = Counter(sentence.lower() for sentence in sentences)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    promotional = len(_PROMOTIONAL_RE.findall(body))
    first_person = len(
        re.findall(
            r"\b(?:I|we|our)\b|"
            r"(?<![\u3040-\u30ff\u3400-\u9fff])(?:当方|筆者)"
            r"(?![\u3040-\u30ff\u3400-\u9fff])",
            body,
            re.IGNORECASE,
        )
    )
    contrast_terms = len(_CONTRAST_RE.findall(body))
    summary_categories = _summary_categories(body)
    theme_blocks = _trend_theme_blocks(body)
    theme_year_sets = [
        {
            int(value)
            for value in _YEAR_RE.findall(_without_evidence_suffixes(block))
            if anchor_fiscal_year is None or int(value) <= anchor_fiscal_year
        }
        for block in theme_blocks
    ]
    theme_year_counts = [len(values) for values in theme_year_sets]
    average_theme_years = (
        sum(theme_year_counts) / len(theme_year_counts)
        if theme_year_counts
        else 0.0
    )
    middle_years = (
        [
            year
            for year in years
            if anchor_fiscal_year - 6 <= year <= anchor_fiscal_year - 3
        ]
        if anchor_fiscal_year is not None
        else []
    )
    japanese_characters = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff]", body))
    is_japanese = japanese_characters / max(len(body), 1) >= 0.05
    sentence_lengths = [len(sentence) for sentence in sentences]
    long_threshold = 150 if is_japanese else 260
    return {
        "main_characters": len(body),
        "trend_narrative_characters": _narrative_characters(trend),
        "trend_perspective_narrative_characters": _narrative_characters(
            perspective
        ),
        "headings": headings,
        "citation_groups": len(citation_groups),
        "citation_references": citation_refs,
        "citation_references_per_1000_chars": round(
            citation_refs / max(len(body), 1) * 1000, 3
        ),
        "unique_years": years,
        "future_years_excluded_from_trend_score": future_years,
        "year_span": (max(years) - min(years)) if len(years) >= 2 else 0,
        "bold_analytical_themes": bold_themes,
        "contrast_terms": contrast_terms,
        "sentence_count": len(sentences),
        "average_sentence_characters": round(
            sum(sentence_lengths) / max(len(sentence_lengths), 1),
            2,
        ),
        "long_sentence_count": sum(
            length > long_threshold for length in sentence_lengths
        ),
        "repeated_sentence_count": repeated,
        "semantic_overlap_pairs": _semantic_overlap_pairs(theme_blocks),
        "promotional_terms": promotional,
        "first_person_terms": first_person,
        "key_takeaway_categories": summary_categories,
        "key_takeaway_category_count": len(summary_categories),
        "middle_period_years": middle_years,
        "trend_theme_average_years": round(average_theme_years, 2),
        "trend_themes_with_three_periods": sum(
            count >= 3 for count in theme_year_counts
        ),
    }


def _clamp(value: float) -> float:
    return round(max(0.0, min(5.0, value)), 2)


def essential_quality_issues(
    generated: str,
    exemplar: str | None,
    *,
    language: str,
    anchor_fiscal_year: int | None = None,
) -> list[tuple[str, str]]:
    metrics = report_metrics(
        generated, anchor_fiscal_year=anchor_fiscal_year
    )
    exemplar_metrics = (
        report_metrics(exemplar, anchor_fiscal_year=anchor_fiscal_year)
        if exemplar is not None
        else None
    )
    issues: list[tuple[str, str]] = []
    required = (
        ("Executive summary", "Trend analysis")
        if language == "en"
        else ("エグゼクティブサマリー", "トレンド分析")
    )
    for fragment in required:
        if fragment not in generated:
            issues.append(
                ("required_report_structure_missing", f"Missing report section: {fragment}")
            )
    minimum_length = (
        int(exemplar_metrics["main_characters"] * 0.55)
        if exemplar_metrics is not None
        else 2750
    )
    if metrics["main_characters"] < minimum_length:
        issues.append(
            (
                "report_severely_short",
                f"Main report body has {metrics['main_characters']} characters; "
                f"minimum is {minimum_length}.",
            )
        )
    generic_trend_minimum = _GENERIC_TREND_MINIMUM[language]
    minimum_trend_length = (
        max(
            generic_trend_minimum,
            int(
                exemplar_metrics["trend_narrative_characters"]
                * _EXEMPLAR_TREND_MINIMUM_RATIO
            ),
        )
        if exemplar_metrics is not None
        else generic_trend_minimum
    )
    if metrics["trend_narrative_characters"] < minimum_trend_length:
        issues.append(
            (
                "trend_analysis_too_short",
                "Trend-analysis narrative has "
                f"{metrics['trend_narrative_characters']} characters; "
                f"minimum is {minimum_trend_length}.",
            )
        )
    generic_perspective_minimum = _GENERIC_PERSPECTIVE_MINIMUM[language]
    minimum_perspective_length = (
        max(
            generic_perspective_minimum,
            int(
                exemplar_metrics["trend_perspective_narrative_characters"]
                * _EXEMPLAR_PERSPECTIVE_MINIMUM_RATIO
            ),
        )
        if exemplar_metrics is not None
        else generic_perspective_minimum
    )
    if (
        metrics["trend_perspective_narrative_characters"]
        < minimum_perspective_length
    ):
        issues.append(
            (
                "trend_perspective_too_short",
                "Decade-perspective narrative has "
                f"{metrics['trend_perspective_narrative_characters']} "
                f"characters; minimum is {minimum_perspective_length}.",
            )
        )
    repetition_ratio = metrics["repeated_sentence_count"] / max(
        metrics["sentence_count"], 1
    )
    if repetition_ratio > 0.15:
        issues.append(
            (
                "report_severe_repetition",
                f"Repeated-sentence ratio is {repetition_ratio:.1%}; maximum is 15%.",
            )
        )
    if "UNRESOLVED " in generated:
        issues.append(
            ("report_contains_unresolved_reference", "Report contains an unresolved reference.")
        )
    return issues


def compare_reports(
    generated: str,
    exemplar: str | None,
    *,
    anchor_fiscal_year: int | None = None,
) -> dict[str, Any]:
    generated_metrics = report_metrics(
        generated, anchor_fiscal_year=anchor_fiscal_year
    )
    exemplar_metrics = (
        report_metrics(exemplar, anchor_fiscal_year=anchor_fiscal_year)
        if exemplar is not None
        else None
    )
    required_fragments = (
        ("Executive summary", "エグゼクティブサマリー"),
        ("Trend analysis", "トレンド分析"),
    )
    structure_hits = sum(
        any(fragment in generated for fragment in alternatives)
        for alternatives in required_fragments
    )
    structure_score = 5 * structure_hits / len(required_fragments)
    section_score = min(
        5.0,
        len(generated_metrics["headings"]) / 12 * 4
        + min(generated_metrics["key_takeaway_category_count"] / 4, 1),
    )
    contrast_density = generated_metrics["contrast_terms"] / max(
        generated_metrics["sentence_count"], 1
    )
    depth_score = min(
        5.0,
        min(generated_metrics["bold_analytical_themes"] * 0.3, 2.0)
        + min(contrast_density / 0.12 * 1.5, 1.5)
        + min(generated_metrics["trend_theme_average_years"] / 3 * 1.5, 1.5),
    )
    theme_count = max(generated_metrics["bold_analytical_themes"], 1)
    trend_score = min(
        5.0,
        min(generated_metrics["year_span"] / 9 * 2, 2)
        + min(len(generated_metrics["unique_years"]) / 8, 1)
        + min(len(generated_metrics["middle_period_years"]) / 3, 1)
        + min(
            generated_metrics["trend_themes_with_three_periods"] / theme_count,
            1,
        ),
    )
    if exemplar_metrics:
        length_ratio = generated_metrics["main_characters"] / max(
            exemplar_metrics["main_characters"], 1
        )
        trend_length_ratio = generated_metrics[
            "trend_narrative_characters"
        ] / max(exemplar_metrics["trend_narrative_characters"], 1)
        perspective_length_ratio = generated_metrics[
            "trend_perspective_narrative_characters"
        ] / max(
            exemplar_metrics["trend_perspective_narrative_characters"],
            1,
        )
        if 0.85 <= length_ratio <= 1.20:
            length_score = 5.0
        else:
            length_score = 5 - min(abs(length_ratio - 1), 1) * 5
    else:
        length = generated_metrics["main_characters"]
        length_score = 5 - abs(6500 - length) / 1800
        length_ratio = None
        trend_length_ratio = None
        perspective_length_ratio = None
    tone_score = 5 - generated_metrics["promotional_terms"] * 0.6 - (
        generated_metrics["first_person_terms"] * 0.5
    )
    repetition_ratio = generated_metrics["repeated_sentence_count"] / max(
        generated_metrics["sentence_count"], 1
    )
    repetition_score = (
        5
        - repetition_ratio * 20
        - generated_metrics["semantic_overlap_pairs"] * 0.5
    )
    executive_breadth_score = min(
        5.0,
        generated_metrics["key_takeaway_category_count"] / 4 * 5,
    )
    average_sentence_characters = generated_metrics[
        "average_sentence_characters"
    ]
    japanese_report = bool(
        re.search(r"[\u3040-\u30ff\u3400-\u9fff]", _main_body(generated))
    )
    sentence_target = 90 if japanese_report else 180
    readability_score = (
        5
        - max(0, average_sentence_characters - sentence_target)
        / max(sentence_target * 0.3, 1)
        - generated_metrics["long_sentence_count"] * 0.25
    )
    scores = {
        "structure": _clamp(structure_score),
        "section_coverage": _clamp(section_score),
        "executive_breadth": _clamp(executive_breadth_score),
        "analytical_depth": _clamp(depth_score),
        "trend_specificity": _clamp(trend_score),
        "tone": _clamp(tone_score),
        "repetition": _clamp(repetition_score),
        "readability": _clamp(readability_score),
        "approximate_length": _clamp(length_score),
    }
    return {
        "scores_0_to_5": scores,
        "overall_score_0_to_5": round(sum(scores.values()) / len(scores), 2),
        "generated_metrics": generated_metrics,
        "exemplar_metrics": exemplar_metrics,
        "length_ratio_to_exemplar": round(length_ratio, 3)
        if length_ratio is not None
        else None,
        "trend_length_ratio_to_exemplar": round(trend_length_ratio, 3)
        if trend_length_ratio is not None
        else None,
        "perspective_length_ratio_to_exemplar": round(
            perspective_length_ratio, 3
        )
        if perspective_length_ratio is not None
        else None,
        "rubric_notes": [
            "Scores are deterministic heuristics intended for iteration, not a "
            "substitute for human financial-analysis review.",
            "Length receives no additional credit inside the broad exemplar-relative "
            "target range; breadth, contrast, historical distribution, tone, and "
            "readability are scored separately.",
            "Semantic factual correctness requires source review; citation-free "
            "validation provides only limited structural and numeric diagnostics.",
        ],
    }


def compare_files(
    generated_path: Path,
    exemplar_path: Path | None,
    *,
    anchor_fiscal_year: int | None = None,
) -> dict[str, Any]:
    generated = generated_path.read_text(encoding="utf-8")
    exemplar = (
        exemplar_path.read_text(encoding="utf-8")
        if exemplar_path is not None and exemplar_path.is_file()
        else None
    )
    return compare_reports(
        generated,
        exemplar,
        anchor_fiscal_year=anchor_fiscal_year,
    )
