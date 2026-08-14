"""Deterministic Japanese and English Markdown rendering."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .schemas import (
    EnglishTranslation,
    JapaneseAnalysis,
    ManagementConsistencyAssessment,
    ManagementConsistencyDimension,
    SectionKey,
    ValidationResult,
)


JA_HEADINGS = {
    SectionKey.LATEST_KEY_TAKEAWAY: "### 経営者コメントの要点",
    SectionKey.LATEST_BUSINESS_DRIVER: "### 主要な事業ドライバー",
    SectionKey.LATEST_OUTLOOK: "### 見通しと経営目標",
    SectionKey.LATEST_RISK: "### リスクと不確実性",
    SectionKey.LATEST_CONTEXT: "### 最新資料の位置づけ",
    SectionKey.TREND_PERSPECTIVE: "### 10年間を通じた見方",
    SectionKey.TREND_CONSISTENT: "### 変わらなかったこと",
    SectionKey.TREND_CHANGE: "### 変化したこと",
    SectionKey.TREND_CAPITAL_ALLOCATION: "### 資本配分の変化",
    SectionKey.TREND_CAPITAL_VALUE_CREATION: "### 資本配分は価値を創出したか",
    SectionKey.TREND_IMPLICATION: "### 最新コメントから読み取れる現在地",
}
EN_HEADINGS = {
    SectionKey.LATEST_KEY_TAKEAWAY: "### Key management takeaways",
    SectionKey.LATEST_BUSINESS_DRIVER: "### Major business and financial drivers",
    SectionKey.LATEST_OUTLOOK: "### Outlook and management targets",
    SectionKey.LATEST_RISK: "### Risks and uncertainty",
    SectionKey.LATEST_CONTEXT: "### Latest filing in context",
    SectionKey.TREND_PERSPECTIVE: "### The decade in perspective",
    SectionKey.TREND_CONSISTENT: "### What remained consistent",
    SectionKey.TREND_CHANGE: "### What materially changed",
    SectionKey.TREND_CAPITAL_ALLOCATION: "### Capital-allocation developments",
    SectionKey.TREND_CAPITAL_VALUE_CREATION: "### Did capital allocation create value?",
    SectionKey.TREND_IMPLICATION: "### What the latest commentary now implies",
}
LATEST_SECTION_ORDER = (
    SectionKey.LATEST_KEY_TAKEAWAY,
    SectionKey.LATEST_BUSINESS_DRIVER,
    SectionKey.LATEST_OUTLOOK,
    SectionKey.LATEST_RISK,
    SectionKey.LATEST_CONTEXT,
)
TREND_SECTION_ORDER = (
    SectionKey.TREND_PERSPECTIVE,
    SectionKey.TREND_CONSISTENT,
    SectionKey.TREND_CHANGE,
    SectionKey.TREND_CAPITAL_ALLOCATION,
    SectionKey.TREND_CAPITAL_VALUE_CREATION,
    SectionKey.TREND_IMPLICATION,
)
MANAGEMENT_SECTION_ORDER = (
    (
        SectionKey.MANAGEMENT_STRATEGY,
        ManagementConsistencyDimension.STRATEGIC_COHERENCE,
    ),
    (
        SectionKey.MANAGEMENT_EXECUTION,
        ManagementConsistencyDimension.EXECUTION_FOLLOW_THROUGH,
    ),
    (
        SectionKey.MANAGEMENT_FORECAST_DISCIPLINE,
        ManagementConsistencyDimension.FORECAST_TARGET_DISCIPLINE,
    ),
    (
        SectionKey.MANAGEMENT_ACCOUNTABILITY,
        ManagementConsistencyDimension.ACCOUNTABILITY_TRANSPARENCY,
    ),
)

_JA_CONSISTENCY_NOTE = (
    "<small>&#42; 経営一貫性スコアは、過去の経営説明で示された方針・約束と、その後の"
    "行動・結果がどの程度つながっているかを0～1で表す補助指標です。"
    "「戦略」は、会社の基本方針が長期的に筋の通った形で続いているか、変更した場合に"
    "納得できる説明があるかを見ます。「実行」は、掲げた施策を実際に進め、意図した"
    "成果につなげているかを見ます。「予想・目標規律」は、業績予想や中期目標を後年の"
    "実績と照合し、達成・未達・修正を一貫して扱っているかを見ます。「説明責任」は、"
    "好調な結果だけでなく、未達、損失、前提の変化についても具体的に説明しているかを"
    "見ます。<br>算定では、選定した複数年の決算短信、とくに経営成績・財政状態・"
    "業績予想に関する説明を読み、過去の発言を後の行動や結果と結び付け、整合する材料と"
    "反する材料の両方を確認します。各項目を0～1で評価し、評価可能な項目を単純平均"
    "します。証拠の年代や量に偏りがある場合も、その偏りを踏まえて最も妥当な評価を"
    "行い、信頼度は別途記録します。選定資料からどうしても評価できない項目だけを空欄"
    "として計算から除外し、全項目を評価できない場合に限り総合スコアを中立値0.50と"
    "します。高いスコアは説明と実行の一貫性が"
    "高いことを示しますが、戦略そのものの良し悪しや投資魅力度を示すものではありません。"
    "</small>"
)
_EN_CONSISTENCY_NOTE = (
    "<small>&#42; The management consistency score is a 0–1 indicator of how closely "
    "management's earlier statements and commitments line up with its later actions "
    "and results. “Strategy” asks whether the company's central direction remains "
    "coherent over time, or whether major changes are clearly explained. “Execution” "
    "asks whether announced initiatives were actually carried out and produced the "
    "intended progress. “Forecast discipline” compares forecasts and medium-term "
    "targets with later results, including whether achievements, misses, and revisions "
    "are treated consistently. “Accountability” asks whether management explains "
    "setbacks, losses, and changed assumptions as clearly as favorable outcomes."
    "<br>The process reviews the selected years of management commentary—especially "
    "the discussions of operating results, financial condition, and outlook—then "
    "connects earlier commitments with later actions and outcomes. Both supporting "
    "and contradictory evidence are considered. Each assessable component receives "
    "a 0–1 score, and the overall score is their simple average. Uneven timing or "
    "quantity of evidence lowers the separately recorded confidence rather than "
    "automatically erasing a score. A component remains blank only when the selected "
    "filings provide no defensible basis for assessment; 0.50 is used for the overall "
    "score only when no component can be assessed. A higher score indicates "
    "more consistent communication and follow-through, not necessarily a better "
    "strategy, stronger business, or more attractive investment."
    "</small>"
)
_JA_CONSISTENCY_LABELS = {
    ManagementConsistencyDimension.STRATEGIC_COHERENCE: "戦略",
    ManagementConsistencyDimension.EXECUTION_FOLLOW_THROUGH: "実行",
    ManagementConsistencyDimension.FORECAST_TARGET_DISCIPLINE: "予想・目標規律",
    ManagementConsistencyDimension.ACCOUNTABILITY_TRANSPARENCY: "説明責任",
}
_EN_CONSISTENCY_LABELS = {
    ManagementConsistencyDimension.STRATEGIC_COHERENCE: "strategy",
    ManagementConsistencyDimension.EXECUTION_FOLLOW_THROUGH: "execution",
    ManagementConsistencyDimension.FORECAST_TARGET_DISCIPLINE: "forecast discipline",
    ManagementConsistencyDimension.ACCOUNTABILITY_TRANSPARENCY: "accountability",
}


def _group_claims(claims: Iterable[object]) -> dict[SectionKey, list[object]]:
    result: dict[SectionKey, list[object]] = defaultdict(list)
    for claim in claims:
        result[claim.section].append(claim)
    for values in result.values():
        values.sort(key=lambda claim: (claim.order, claim.claim_id))
    return result


def _consistency_breakdown(
    assessment: ManagementConsistencyAssessment,
    *,
    language: str,
) -> str:
    by_dimension = {item.dimension: item for item in assessment.components}
    labels = (
        _JA_CONSISTENCY_LABELS
        if language == "ja"
        else _EN_CONSISTENCY_LABELS
    )
    values: list[str] = []
    assessed_count = 0
    for dimension in ManagementConsistencyDimension:
        component = by_dimension.get(dimension)
        if component is None or component.normalized_score is None:
            value = "—"
        else:
            value = f"{component.normalized_score:.2f}"
            assessed_count += 1
        values.append(f"{labels[dimension]} {value}")
    prefix = "内訳：" if language == "ja" else "Breakdown: "
    coverage = (
        f"（評価済み {assessed_count}/4項目）"
        if language == "ja"
        else f" ({assessed_count} of 4 dimensions assessed)"
    )
    return prefix + "｜".join(values) + coverage


def _render_section_ja(
    section: SectionKey,
    claims: list[object],
) -> list[str]:
    lines = [JA_HEADINGS[section], ""]
    is_bullet = section in {
        SectionKey.LATEST_KEY_TAKEAWAY,
        SectionKey.LATEST_BUSINESS_DRIVER,
        SectionKey.LATEST_OUTLOOK,
        SectionKey.LATEST_RISK,
    }
    is_theme = section in {
        SectionKey.TREND_CONSISTENT,
        SectionKey.TREND_CHANGE,
        SectionKey.TREND_CAPITAL_ALLOCATION,
        SectionKey.TREND_CAPITAL_VALUE_CREATION,
        SectionKey.TREND_IMPLICATION,
    }
    for claim in claims:
        if section == SectionKey.LATEST_BUSINESS_DRIVER:
            lines.append(
                f"- **{claim.headline_ja}：** {claim.body_ja}"
            )
        elif is_bullet:
            lines.append(f"- {claim.body_ja}")
        elif is_theme:
            lines.append(f"**{claim.headline_ja}**<br>")
            lines.append(claim.body_ja)
            lines.append("")
        else:
            lines.append(claim.body_ja)
            lines.append("")
    if lines[-1] != "":
        lines.append("")
    return lines


def _render_overview_ja(
    claims: list[object],
) -> list[str]:
    if not claims:
        return []
    lines = ["## 企業概要", ""]
    for claim in claims:
        lines.extend([claim.body_ja, ""])
    return lines


def _render_management_details_ja(
    assessment: ManagementConsistencyAssessment,
    grouped: dict[SectionKey, list[object]],
) -> list[str]:
    components = {item.dimension: item for item in assessment.components}
    lines: list[str] = []
    for section, dimension in MANAGEMENT_SECTION_ORDER:
        component = components.get(dimension)
        score = (
            f"{component.normalized_score:.2f}"
            if component is not None and component.normalized_score is not None
            else "—"
        )
        label = _JA_CONSISTENCY_LABELS[dimension]
        claims = grouped.get(section, [])
        if claims:
            claim = claims[0]
            lines.append(f"- **{label} {score}：** {claim.body_ja}")
        elif component is not None:
            lines.append(
                f"- **{label} {score}：** {component.rationale_ja}"
            )
    if lines:
        lines.append("")
    return lines


def render_japanese(analysis: JapaneseAnalysis) -> str:
    grouped = _group_claims(analysis.claims)
    lines = [
        f"# {analysis.identity.company_name_ja}"
        f"（{analysis.identity.security_code}）",
        "",
    ]
    lines.extend(
        _render_overview_ja(
            grouped.get(SectionKey.COMPANY_OVERVIEW, []),
        )
    )
    lines.extend(["## 1. エグゼクティブサマリー", ""])
    for section in LATEST_SECTION_ORDER:
        lines.extend(_render_section_ja(section, grouped.get(section, [])))
    lines.extend(["## 2. トレンド分析", ""])
    for section in TREND_SECTION_ORDER:
        lines.extend(_render_section_ja(section, grouped.get(section, [])))
    if analysis.management_consistency is not None:
        score = (
            analysis.management_consistency.score
            if analysis.management_consistency.score is not None
            else 0.5
        )
        lines.extend(
            [
                f"**経営一貫性スコア：{score:.2f}**"
                "<sup>*</sup><br>",
                _consistency_breakdown(
                    analysis.management_consistency,
                    language="ja",
                ),
                "",
            ]
        )
        lines.extend(
            _render_management_details_ja(
                analysis.management_consistency,
                grouped,
            )
        )
    if analysis.management_consistency is not None:
        lines.extend([_JA_CONSISTENCY_NOTE, ""])
    return "\n".join(lines)


def render_japanese_draft(
    analysis: JapaneseAnalysis, validation: ValidationResult
) -> str:
    del validation
    return render_japanese(analysis)


def _render_section_en(
    section: SectionKey,
    claims: list[object],
) -> list[str]:
    lines = [EN_HEADINGS[section], ""]
    is_bullet = section in {
        SectionKey.LATEST_KEY_TAKEAWAY,
        SectionKey.LATEST_BUSINESS_DRIVER,
        SectionKey.LATEST_OUTLOOK,
        SectionKey.LATEST_RISK,
    }
    is_theme = section in {
        SectionKey.TREND_CONSISTENT,
        SectionKey.TREND_CHANGE,
        SectionKey.TREND_CAPITAL_ALLOCATION,
        SectionKey.TREND_CAPITAL_VALUE_CREATION,
        SectionKey.TREND_IMPLICATION,
    }
    for claim in claims:
        if section == SectionKey.LATEST_BUSINESS_DRIVER:
            lines.append(
                f"- **{claim.headline_en}:** {claim.body_en}"
            )
        elif is_bullet:
            lines.append(f"- {claim.body_en}")
        elif is_theme:
            lines.append(f"**{claim.headline_en}**<br>")
            lines.append(claim.body_en)
            lines.append("")
        else:
            lines.append(claim.body_en)
            lines.append("")
    if lines[-1] != "":
        lines.append("")
    return lines


def _render_overview_en(
    claims: list[object],
) -> list[str]:
    if not claims:
        return []
    lines = ["## Company overview", ""]
    for claim in claims:
        lines.extend([claim.body_en, ""])
    return lines


def _render_management_details_en(
    assessment: ManagementConsistencyAssessment,
    grouped: dict[SectionKey, list[object]],
) -> list[str]:
    components = {item.dimension: item for item in assessment.components}
    lines: list[str] = []
    for section, dimension in MANAGEMENT_SECTION_ORDER:
        component = components.get(dimension)
        score = (
            f"{component.normalized_score:.2f}"
            if component is not None and component.normalized_score is not None
            else "—"
        )
        label = _EN_CONSISTENCY_LABELS[dimension].capitalize()
        claims = grouped.get(section, [])
        if not claims:
            continue
        claim = claims[0]
        lines.append(f"- **{label} {score}:** {claim.body_en}")
    if lines:
        lines.append("")
    return lines


def render_english(
    analysis: JapaneseAnalysis,
    translation: EnglishTranslation,
) -> str:
    grouped = _group_claims(translation.claims)
    lines = [
        f"# {translation.identity.company_name_en} "
        f"({translation.identity.security_code})",
        "",
    ]
    lines.extend(
        _render_overview_en(
            grouped.get(SectionKey.COMPANY_OVERVIEW, []),
        )
    )
    lines.extend(["## 1. Executive summary", ""])
    for section in LATEST_SECTION_ORDER:
        lines.extend(_render_section_en(section, grouped.get(section, [])))
    lines.extend(["## 2. Trend analysis", ""])
    for section in TREND_SECTION_ORDER:
        lines.extend(_render_section_en(section, grouped.get(section, [])))
    if analysis.management_consistency is not None:
        score = (
            analysis.management_consistency.score
            if analysis.management_consistency.score is not None
            else 0.5
        )
        lines.extend(
            [
                "**Management consistency score: "
                f"{score:.2f}**"
                "<sup>*</sup><br>",
                _consistency_breakdown(
                    analysis.management_consistency,
                    language="en",
                ),
                "",
            ]
        )
        lines.extend(
            _render_management_details_en(
                analysis.management_consistency,
                grouped,
            )
        )
    if analysis.management_consistency is not None:
        lines.extend([_EN_CONSISTENCY_NOTE, ""])
    return "\n".join(lines)


def render_english_draft(
    analysis: JapaneseAnalysis,
    translation: EnglishTranslation,
    validation: ValidationResult,
) -> str:
    del validation
    return render_english(analysis, translation)
