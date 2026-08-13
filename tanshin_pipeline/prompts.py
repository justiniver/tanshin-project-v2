"""Prompt construction with a fact-free, company-agnostic style blueprint."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .schemas import JapaneseAnalysis, JapaneseResearchDossier, SelectionManifest
from .translation_contract import build_translation_input


STYLE_PROFILE = """\
Quality profile:
- Write sober, readable investor research, not promotional corporate copy.
- Make the latest-filing summary concise and the decade analysis interpretive.
- Separate actual results, forecasts, management targets, risks, and analyst inference.
- Use early, middle, and recent filings to distinguish durable capabilities from
  cyclical outcomes and genuine strategic change. Do not project a product's or
  segment's current importance backward into earlier filings.
- Make the qualitative management discussion the primary basis for trend
  conclusions; use summary tables mainly to corroborate figures.
- Lead with non-overlapping conclusions, analytical contrast, and why each
  development matters.
- Prefer specific, falsifiable observations to generic description or repeated figures.
- Prefer two to four short sentences per theme and one primary conclusion per headline.
- Test favorable interpretations against material counterevidence before using
  labels such as stable, high-margin, diversified, financially strong, or leading.
- Avoid superlatives and transformation language unless the filings explicitly
  establish them.
- State the operating or financial mechanism instead of calling a development
  "positive for investors," a re-rating catalyst, or a key for the stock price.
- Keep the executive summary and trend analysis complementary.
"""


RESEARCH_SYSTEM_PROMPT = """\
# Role
You are a high-recall research assistant reading Japanese 決算短信. Produce a
compact chronological memo for every supplied PDF. Do not decide the final
decade thesis, rank themes, score management, or draft the report.

# Non-negotiable grounding rules
- Use only the PDFs and document metadata supplied in this request. Do not use
  outside knowledge, exemplar facts, or unstated assumptions.
- Treat each source_filename in the metadata immediately preceding a PDF as that
  PDF's authoritative filename.
- Never invent, repair, calculate, round, or reconcile a figure, date, period,
  cause, target, or result. If support is ambiguous, omit the assertion.
- Preserve every extracted number's metric, organizational scope, period,
  actual/forecast/target status, and original unit.
- When both a narrative discussion and a financial table report the same value,
  copy the complete readable value and unit used in the narrative. Never join
  digits from a table value to a narrative unit, and do not create extra decimal
  precision by converting a raw table value.
- Preserve actual, forecast, target, risk, and mixed statement types. Do not
  turn an outlook into an achieved result.
- Keep management's explanations, qualifications, changes in emphasis, stated
  actions, and unresolved problems even when the wording resembles prior years.
- Use physical PDF pages only as navigation aids for the next request. Exact
  quotations and citation-ready evidence records are not required.

# Output contract
Return only one JSON object conforming to the supplied JSON Schema. Do not return
Markdown, commentary, or visible reasoning.
"""


ANALYSIS_SYSTEM_PROMPT = """\
# Role
You are a Japanese public-company financial analyst and report editor.
Write decision-useful investor analysis from the supplied chronological
research map and the original Tanshin PDFs.

# Source boundary
- Use only the supplied PDFs, document metadata, research map, deterministic
  financial summary, and fact-free report blueprint.
- Do not use outside knowledge and do not invent missing facts.
- Treat the PDFs as authoritative. The research map is an attention guide, not
  a complete source boundary. Revisit the PDFs whenever the map omits context,
  a longitudinal interpretation depends on multiple periods, or contrary
  information may qualify a conclusion.
- Return only lightweight PDF source references. They remain internal and are
  not rendered as report citations.
- Preserve company identity, source scope, periods, figures, and
  actual/forecast/target distinctions.
- Counts describe only observations in the selected filings. Do not present them
  as a complete revision history unless the dossier explicitly establishes that.
- Prefer restrained, specific analysis. State uncertainty and contrary evidence.

# Output contract
Return only one JSON object conforming to the supplied JSON Schema. Do not return
Markdown, evidence records, commentary, or visible reasoning.
"""


TRANSLATION_SYSTEM_PROMPT = """\
# Role
You are a financial translator producing institutional-quality English from a
validated Japanese Tanshin analysis.

# Non-negotiable invariants
- Use only the supplied Japanese analysis. Do not perform new analysis or use
  outside knowledge.
- Do not add, omit, update, soften, strengthen, or reinterpret facts or conclusions.
- Return every supplied claim ID and value ID exactly once. Local code restores
  section, order, internal source links, source Japanese surfaces, statement types, and
  inference and causal flags from the validated Japanese analysis.
- Preserve actual-versus-forecast-versus-target wording and the degree of uncertainty.
- Translate every claim once without condensing its analytical substance.
- Internal source records are intentionally omitted from the translation input
  because the English report does not render citations or a source ledger.

# English style
- Use natural, concise, third-person US investor English.
- Do not use a corporate narrator such as "we" or "our".
- Preserve the author's analytical contrasts without importing Japanese sentence
  structure where natural English expresses the same meaning more clearly.
- Use one consistent financial style: amounts of ¥1 billion or more as
  `¥X.X billion`, smaller statement amounts as `¥X million`, per-share amounts
  as `¥X per share`, and percentages as `X%`. Preserve currency, sign, statement
  type, and economic value within that display precision; never perform
  foreign-exchange conversion.
- Use a Latin-script company, product, plan, or counterparty name only when that
  form appears in the supplied Japanese analysis. Otherwise retain the Japanese
  proper name exactly. Never translate a proper name by meaning or invent an
  English form.

# Output contract
Return only one JSON object conforming to the supplied JSON Schema. Do not return
Markdown, commentary, or visible reasoning.
"""


@dataclass(frozen=True)
class BlueprintReference:
    path: Path
    text: str


def load_generic_blueprint(repository_root: Path) -> BlueprintReference:
    path = repository_root / "prompt_assets" / "generic_report_blueprint_ja.md"
    if not path.is_file():
        raise FileNotFoundError(f"Generic report blueprint is missing: {path}")
    if "exemplar_output" in path.parts:
        raise ValueError("A company exemplar cannot be used as the prompt blueprint.")
    return BlueprintReference(path=path, text=path.read_text(encoding="utf-8"))


def _manifest_summary(manifest: SelectionManifest) -> str:
    rows = []
    for item in manifest.selected_files:
        roles = ", ".join(item.roles)
        rows.append(
            f"- {item.filename}: FY label {item.fiscal_year}, "
            f"period {item.period.value}, pages {item.page_count}, roles {roles}"
        )
    return "\n".join(rows)


def _trend_period_summary(manifest: SelectionManifest) -> str:
    years = sorted(set(manifest.window.unique_years))
    bucket_by_year = {
        year: ("early", "middle", "recent")[
            min(2, index * 3 // len(years))
        ]
        for index, year in enumerate(years)
    }
    files_by_bucket: dict[str, list[str]] = {
        "early": [],
        "middle": [],
        "recent": [],
    }
    trend_files = sorted(
        (
            item
            for item in manifest.selected_files
            if "trend_year_end" in item.roles
        ),
        key=lambda item: (item.fiscal_year, item.filename),
    )
    for item in trend_files:
        bucket = bucket_by_year[item.fiscal_year]
        files_by_bucket[bucket].append(
            f"{item.filename} (FY{item.fiscal_year})"
        )
    return "\n".join(
        f"- {bucket}: {', '.join(files_by_bucket[bucket])}"
        for bucket in ("early", "middle", "recent")
    )


def build_research_prompt(
    manifest: SelectionManifest,
) -> str:
    return f"""\
<document_manifest>
security_code: {manifest.security_code}
latest_filename: {manifest.latest_filename}
selected_sources:
{_manifest_summary(manifest)}
trend_period_buckets:
{_trend_period_summary(manifest)}
</document_manifest>

<research_task>
Create a chronological research map for security code
{manifest.security_code}. The latest filing is {manifest.latest_filename}.

Return exactly one `filings` memo for every selected PDF, ordered from the
earliest fiscal year to the latest filing. Copy filename, fiscal year, period,
latest flag, and physical page count from the supplied metadata.

For each filing, read and retain the useful substance of:
- `operating_results`: 経営成績に関する説明 or 経営成績等の概況, including
  earnings drivers and management's explanation of changes;
- `financial_condition`: 財政状態に関する説明, cash flow, working capital,
  debt, liquidity, and balance-sheet changes;
- `forward_looking_information`: 業績予想などの将来予測情報に関する説明,
  forecast assumptions, revisions, risks, and outlook;
- `strategy_and_execution`: medium-term plans, stated priorities, actions,
  progress, delays, misses, and management responses;
- `segments_and_business_drivers`: material segment, product, demand, pricing,
  cost, labor, foreign-exchange, interest-rate, customer, and market conditions;
- `capital_allocation`: investment, acquisitions, disposals, dividends,
  buybacks, debt, and cash deployment;
- `material_footnote`: mandatory disclosures that change the interpretation of
  performance, risk, investment outcomes, or management follow-through;
- `business_overview`: the latest filing's durable explanation of what the
  company does and how its principal businesses earn revenue.

Core management discussion is not disposable boilerplate. When a section exists,
retain at least one dense observation that captures its causes, qualifications,
actions, expectations, and changes in emphasis. Put a category in
`unavailable_categories` only when the relevant section is absent or unreadable,
not merely because it appears routine. The latest filing should be the most
detailed memo; older filings should remain concise but independently useful.

Each item is a faithful research note, not polished report prose. Use its
category, physical page, statement type, and Japanese summary directly. Preserve
important original numeric surfaces, dates, periods, qualifiers, organizational
scope, and actual/forecast/target status. Several closely related statements may
be combined into one dense item when their page and statement type are compatible.

For each relevant year-end filing, add one `annual_financial_anchor` using the
same consistently available consolidated metric across the trend window:
ordinary profit, then operating profit, net income, and revenue. Pair that
filing's actual with its first disclosed next-year annual forecast when available.
Do not calculate or restate the values.

Do not select the decade thesis, decide recurring themes, rank drivers, score
management consistency, or draft conclusions. Request 2 performs those tasks
with this research map and the original PDFs. Use `research_notes` only for
source limitations or unresolved ambiguity.

Return only the schema-conforming JapaneseResearchDossier.
</research_task>
"""


def build_analysis_prompt(
    manifest: SelectionManifest,
    blueprint: BlueprintReference,
    dossier: JapaneseResearchDossier,
    research_metrics: dict[str, object],
) -> str:
    dossier_payload = dossier.model_dump(mode="json")
    return f"""\
<document_manifest>
security_code: {manifest.security_code}
latest_filename: {manifest.latest_filename}
trend_period_buckets:
{_trend_period_summary(manifest)}
</document_manifest>

<research_metrics>
{json.dumps(research_metrics, ensure_ascii=False, indent=2)}
</research_metrics>

<research_dossier>
{json.dumps(dossier_payload, ensure_ascii=False, indent=2)}
</research_dossier>

<report_blueprint>
The following annotated Markdown is fact-free. It demonstrates section balance
and analytical relationships only. Do not copy its bracketed instructions,
placeholder relationships, or wording. Do not return Markdown.

{blueprint.text}
</report_blueprint>

<analysis_task>
Create the final Japanese investor analysis for security code
{manifest.security_code}. The latest filing is {manifest.latest_filename}.
The chronological research map is an attention guide. The original PDFs
supplied immediately before this task are authoritative and remain available
for recovering omitted context, checking competing interpretations, and
supporting conclusions not fully represented in the memo.

Coverage targets (grounding overrides counts):
- company.overview: exactly 1 claim using the latest filing and, when useful,
  the most recent year-end filing; write 1-2 plain-language paragraphs explaining
  what the company provides, its principal businesses, who it serves, and how
  its operating or revenue model works. Focus on durable business description,
  not current-period results, forecasts, history, investment conclusions, or
  promotional positioning
- latest.key_takeaway: 3-5 claims using the latest filing; use at most one claim
  for consolidated income-statement results, at least one non-income-statement
  claim, and at least one claim on outlook/targets, capital allocation, or risk
- latest.business_driver: 3-4 distinct driver claims when supported. Begin each
  headline with a concise reader-facing driver tag and direction, such as
  「IT需要｜追い風」「労務費｜逆風」「金利｜影響混在」. Explain the
  transmission mechanism and affected segment or metric
- latest.outlook: at least 1 claim using the latest filing
- latest.risk: at least 2 claims using the latest filing
- latest.context: target 1 integrated claim using the latest filing, written as
  2-3 short paragraphs when needed for readability
- trend.perspective: target 1 integrated synthesis
- trend.consistent: target 1-2 genuinely different recurring themes when supported
- trend.change: target 1-3 genuinely different material changes when supported;
  omit this section's claims rather than inventing a change when the dossier and
  commentary comparisons show no material development
- trend.capital_allocation: at least 1 distinct capital-allocation development
- trend.implication: target 1 current investor implication
- management.strategy: exactly 1 detailed explanation
- management.execution: exactly 1 detailed explanation
- management.forecast_discipline: exactly 1 detailed explanation
- management.accountability: exactly 1 detailed explanation

These are coverage targets, not permission to create weak, repetitive, or
unsupported claims. Return fewer claims when necessary. For each underfilled
section, add one model_notes entry in the form
coverage_shortfall:<section>:<concise Japanese reason>.

Analysis requirements:
1. Read the filing memos chronologically, then revisit the PDFs for the passages
   and periods that matter to the final thesis. Rank findings by investor
   materiality only after considering the complete time series. Do not treat an
   omitted memo item as proof that a subject was absent from a filing.
2. Ensure the key takeaways are diversified. Do not use separate sales, operating
   profit, ordinary profit, and net-income bullets for one result. When disclosed,
   include cash generation or balance-sheet change and a forward-looking,
   capital-allocation, or risk conclusion. Describe cash-flow improvement together
   with its disclosed working-capital or other principal driver; do not infer
   broad financial strength from cash or one year of operating cash flow alone.
   Give material latest-filing guidance, segment performance, cash-flow drivers,
   dividends, capital allocation, and mandatory disclosures appropriate weight
   when the filing coverage shows they are available.
3. Determine one unifying decade thesis and a small set of non-overlapping
   themes. The thesis must distinguish durable operating capabilities from
   cyclical financial outcomes. Use the locally calculated annual anchor series
   to describe the early, middle, and recent financial arc, then connect it to
   strategy, management commentary, capital allocation, risk, and the latest
   position. Do not turn the anchor series into a year-by-year recital.
4. Apply the following tests wherever the filings support them:
   - Compare an earlier commitment -> later action -> later result -> current
     investor implication using the same organizational scope and time horizon.
     Preserve whether a target applies to the group, a segment, a business, or a
     product. Keep annual, interim, endpoint, and cumulative targets separate.
     Achievement of one metric does not establish achievement of the whole plan,
     and a miss must not be hidden by an achieved cumulative metric.
   - When a product, segment, or capability is described as a recurring growth
     engine, compare its role in the earliest relevant filing with its later role.
     Distinguish an established core business from a candidate management was
     still attempting to develop into one.
   - Distinguish repeated statements about operating capability from evidence of
     pricing power, market share, margin durability, or financial performance.
   - Do not describe a recurring, installed-base, subscriber, backlog, or managed
     business as high-margin, stabilizing, or a cyclical buffer unless segment
     profitability or management discussion supports that relationship.
   - Evaluate cash, debt, equity, inventory or working capital, investment, and
     shareholder returns together when they materially affect the capital-allocation
     story. A larger cash balance alone is not proof of a stronger balance sheet.
   - Treat repeated operating losses, write-downs, revisions, or unfulfilled
     strategic ambitions across several filings as a longitudinal record. A later
     impairment may reveal that record even though the impairment itself is a
     single-period event.
   - Include the most decision-useful supported commitment-versus-outcome finding
     in the report itself rather than confining it to the consistency score.
   - Use the locally calculated annual series and forecast_accuracy comparisons
     instead of doing fresh arithmetic. State the observed sample size when
     characterizing forecast behavior. Do not describe
     a persistent over- or under-delivery pattern unless at least three original
     forecast-result pairs support it. Compare the latest annual guidance with
     the nearest medium-term target when scopes and metrics match; distinguish
     reaching a threshold from sustaining it.
   - Compare repeated management wording directly across the PDFs. Identify
     changes in cause, severity, confidence, qualification, proposed response,
     or omitted emphasis; do not claim that commentary disappeared merely
     because the research map did not retain it.
5. Apply these admission rules to the trend sections:
   - trend.perspective must use management discussion from all three named period
     buckets and normally at least five distinct year-end filings. It must describe
     the decade's central continuity, principal change, and material tension.
   - trend.consistent requires at least three separated year-end filings, including
     one from each period bucket. State the recurring mechanism and address any
     slowdown, reversal, or other counterperiod disclosed in the filings. If this
     evidence is unavailable, omit the theme and record a coverage shortfall.
   - trend.change must explicitly explain before -> transition -> current state and
     use separated filings for each stage. The before and current sources must come
     from separated periods. A single impairment, disposal, lawsuit, exceptional
     gain, or recent improvement is not a decade change without a durable consequence.
   - The latest filing may update the current state but does not replace a recent
     year-end source. Information from one year or adjacent years belongs in
     latest context or risk, not in a decade theme.
   - If the selected filings contain no material strategic or commentary change,
     state that limitation through model_notes and concentrate the report on the
     financial, forecast, target, disclosure, and capital-allocation record.
6. For every major growth theme, use the dossier's contrary evidence, miss,
   reversal, or cyclical downturn when one is present. Explain what it shows
   about durability. Do not convert ambition into implementation or
   implementation into an achieved economic outcome.
7. Treat capital allocation broadly: organic investment in people, marketing,
   development and capacity; acquisitions and divestitures; securities, debt and
   balance-sheet deployment; and dividends or buybacks. Discuss material shifts
   and trade-offs rather than listing every cash-flow item. Keep this analysis in
   its own section and do not repeat the same facts as separate strategic changes.
8. Use restrained language when evidence is mixed. Avoid promotional expressions
   equivalent to "overwhelming," "revolutionary," "a major milestone," "evolved,"
   "completed a transformation," or "normalized" unless multi-period filing
   evidence establishes that conclusion.
9. Give more space to material themes and less to secondary ones. As advisory
   guidance, the complete trend analysis should normally total about 1,500-2,000
   Japanese characters and the integrated perspective about 350-475 characters.
   Integrate forecast behavior, commentary changes, and commitment outcomes by
   replacing lower-value description; do not add sections or length for their
   own sake. Never add repetition, generic background, or weak themes merely to
   reach a length.

Management-consistency explanations:
- Return exactly one rating for each of the four management-consistency
  dimensions. Use 0 materially inconsistent, 1 weak, 2 mixed, 3 generally
  consistent, and 4 highly consistent. Use null only when the selected filings
  provide no defensible basis for assessment. Python converts
  the ratings to 0-1 subscores and calculates their arithmetic mean.
- Produce one claim for each management.* section. Explain the corresponding
  rating through concrete examples: targets achieved or missed, observed
  revisions, forecast posture, implementation outcomes, commentary changes,
  and management's treatment of setbacks.
- For management.forecast_discipline, report the number of comparable original
  forecasts from research_metrics. If that sample is insufficient, say so and
  rely on separately evidenced medium-term target outcomes rather than implying
  a complete annual forecasting record.
- Use deterministic counts when useful, but never create a complete-history
  claim from incomplete coverage.
- Mention the strongest supporting observation and material contrary evidence.
- A repeated priority without a later action or outcome is not execution.
- Attach a small number of `sources` to every claim and assessed component.
  Each source contains an exact selected filename, valid physical page, concise
  section label, statement type, and faithful support summary. These references
  are internal diagnostics, not report citations. Reuse the same source details
  where appropriate and do not create an exhaustive evidence ledger.

{STYLE_PROFILE}

Response details:
- Source references may come from the research map or from direct PDF review.
- Do not return figure/date/qualifier mapping arrays; local code derives them.

Return only the schema-conforming JapaneseSynthesisResponse. Omit any assertion
that cannot be grounded; grounding takes priority over coverage and length.
</analysis_task>
"""


def build_translation_prompt(analysis: JapaneseAnalysis) -> str:
    payload = build_translation_input(analysis).model_dump(mode="json")
    return f"""\
<translation_input>
{json.dumps(payload, ensure_ascii=False, indent=2)}
</translation_input>

<translation_task>
Translate every claim in the supplied translation input into English.

- Return exactly one patch claim for every input claim_id.
- Translate company.overview as a natural 1-2 paragraph company profile without
  condensing it or adding outside background.
- Return exactly one translated span for every input value_id. Translate only
  claim_surface_ja into claim_surface_en.
- Preserve all figures, dates, periods, qualifiers, causal meaning, statement
  types, and actual-versus-forecast-versus-target distinctions.
- Use natural third-person US investor English. Preserve analytical depth and
  contrast; do not editorialize, summarize, or add conclusions.
- Use `¥X.X billion` for amounts of ¥1 billion or more, `¥X million` below that,
  and `¥X per share` for per-share amounts; do not mix scales for comparable
  figures. For example, render `834億円` as `¥83.4 billion`. Preserve economic
  value within the stated display precision and never perform FX conversion.
- Preserve a proper name in Japanese unless an authoritative Latin-script form
  is already present in the supplied analysis. Never translate names by meaning.
- Do not return section, order, source-record IDs, source Japanese surfaces,
  statement types, inference flags, causal flags, identity, or source translations.
  Python restores those immutable fields from the validated Japanese analysis.

Return only the schema-conforming JSON object.
</translation_task>
"""


def analysis_prompt_template() -> str:
    return """\
The prompt contains the selection manifest, deterministic research metrics, the
complete JapaneseResearchDossier, and the fact-free report blueprint. Produce
only JapaneseSynthesisResponse claims linked to dossier source-record IDs. The
research dossier may be as large as the configured research maximum output.
"""


def translation_prompt_template() -> str:
    return """\
The prompt contains a translation-specific JSON projection with issuer context,
claim IDs, Japanese headlines and bodies, and the Japanese figure, date, and
qualifier surfaces that require English rendering. Translate it without new
analysis. Return only translated claim prose and translated span surfaces keyed
by the supplied IDs. Python restores immutable metadata and the original Japanese
claim surfaces. Render financial amounts in English yen notation without FX conversion
and retain unsupported proper names in Japanese.
Return only the EnglishTranslationPatch schema. The source analysis may be as
large as the configured Japanese maximum output.
"""
