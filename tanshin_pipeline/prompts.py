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
You are a forensic Japanese public-company researcher specializing in 決算短信.
Build a longitudinal evidence dossier from the supplied source PDFs. Do not
write the final report.

# Non-negotiable grounding rules
- Use only the PDFs and document metadata supplied in this request. Do not use
  outside knowledge, exemplar facts, or unstated assumptions.
- Treat each source_filename in the metadata immediately preceding a PDF as that
  PDF's authoritative filename.
- Never invent, repair, calculate, round, or reconcile a figure, date, period,
  cause, target, or result. If support is ambiguous, omit the assertion.
- For every numeric claim, preserve the source metric, organizational scope,
  period, and actual/forecast/target status; state a funding link only when the
  cited evidence and chronology establish it.
- When both a narrative discussion and a financial table report the same value,
  copy the complete readable value and unit used in the narrative. Never join
  digits from a table value to a narrative unit, and do not create extra decimal
  precision by converting a raw table value.
- Preserve the filing's actual, forecast, target, risk, inference, or mixed
  statement type. Do not turn an outlook into an achieved result.
- Analytical inference is allowed only when clearly marked and supported by cited
  filing evidence.
- Do not invent a management response. Price pass-through, selective ordering,
  revised controls, stricter investment criteria, or other corrective action may
  be stated only when the supplied filings explicitly describe it.

# Evidence contract
- Every research record must cite one or more evidence records.
- Each evidence record must contain a sufficiently complete verbatim Japanese
  sentence or short contiguous passage, the authoritative source_filename, and
  the physical 1-indexed PDF page. Do not use a printed footer or contents-page label.
- Use a unique evidence_id in the form <source_filename>:sNNNN.
- Include the exact source wording for every material figure, date, qualifier,
  and causal assertion used by a claim.
- Tag evidence from management discussion sections as "management_discussion".

# Output contract
Return only one JSON object conforming to the supplied JSON Schema. Do not return
Markdown, commentary, or visible reasoning.
"""


ANALYSIS_SYSTEM_PROMPT = """\
# Role
You are a Japanese public-company financial analyst and report editor.
Write decision-useful investor analysis from the supplied, PDF-grounded research
dossier.

# Source boundary
- Use only the supplied research dossier, its deterministic summary, and the
  fact-free report blueprint.
- Do not use outside knowledge and do not invent missing facts.
- Treat the dossier evidence ledger as the complete source record for this pass.
- Every factual claim must cite dossier evidence IDs exactly as supplied.
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
  section, order, evidence links, source Japanese surfaces, statement types, and
  inference and causal flags from the validated Japanese analysis.
- Preserve actual-versus-forecast-versus-target wording and the degree of uncertainty.
- Translate every claim once without condensing its analytical substance.
- Evidence records are intentionally omitted from the translation input. Local
  rendering uses the original Japanese evidence text.

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
Build a reusable longitudinal research dossier for security code
{manifest.security_code}. The latest filing is {manifest.latest_filename}.

Research priorities:
1. Collect a deduplicated evidence ledger sufficient to support a company
   overview, latest-period summary, business drivers, outlook, risks, decade
   perspective, recurring themes, material changes, capital allocation, current
   implications, and management-consistency assessment.
2. Prioritize qualitative management discussion: 経営成績, 財政状態,
   cash-flow discussion, 将来予測情報, plan progress, capital allocation,
   risks, misses, impairments, and changes in assumptions. Use summary tables
   mainly to corroborate exact figures.
3. Extract 4-8 distinct business drivers when supported. Use a short reusable
   canonical_tag such as customer_demand, volume, utilization, pricing,
   product_mix, material_costs, labor_costs, energy_costs, interest_rates,
   foreign_exchange, property_prices, rent, regulation, capacity,
   technology_investment, competition, acquisitions, or other. State direction,
   importance, structural/cyclical nature, affected area, mechanism, periods,
   and evidence.
4. Connect explicit forecasts, medium-term targets, strategic commitments, and
   capital-allocation commitments with later outcomes whenever the selected
   filings permit. Keep original statements and later outcomes in the same
   organizational scope and time horizon. Distinguish achieved, exceeded,
   partly achieved, missed, delayed, revised, withdrawn, pending, and not
   observable.
5. For annual forecasts, classify forecast_posture only when an issued forecast
   can be compared with a later actual result. Conservative means the later
   comparable actual materially exceeded guidance; aggressive means it
   materially missed; balanced means broadly aligned. Use mixed when the
   comparable metrics differ. Otherwise use not_assessable.
6. Record revision_direction only for revisions visible in the selected
   filings. Never infer that none occurred outside the selected corpus.
7. Extract 4-8 decision-useful management themes spanning early, middle, and
   recent periods where possible. Distinguish persistent, introduced,
   strengthened, changed, deprioritized, and abandoned themes. Explain actions,
   later outcomes, and unresolved tensions rather than merely counting repeated
   slogans.
8. Return one management-consistency component for each required dimension.
   The rationale must identify concrete commitments, later actions or results,
   important misses or revisions, and contrary evidence. Rate 0 materially
   inconsistent, 1 weak, 2 mixed, 3 generally consistent, and 4 highly
   consistent. The selected corpus normally provides enough management discussion
   for all four ratings, so make the best evidence-based assessment even when
   coverage is uneven and explain the limitation in the rationale. Use null only
   in the exceptional case where, after reviewing every selected filing, no
   defensible assessment can be made for that dimension; do not use null merely
   because the strongest examples come from one part of the trend period.
9. Use model notes to disclose incomplete forecast-revision coverage, ambiguous
   targets, missing outcomes, or other material research limitations.

Evidence requirements:
- Use exact authoritative filenames from the metadata.
- Use physical 1-indexed PDF pages.
- Evidence quotes must be sufficiently complete verbatim Japanese sentences or
  short contiguous passages.
- Use unique IDs in the form <source_filename>:sNNNN.
- Include support for both the original commitment and later result when an
  outcome is classified.
- Tag management-commentary evidence with management_discussion.

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
{manifest.security_code} from the supplied research dossier. The latest filing
is {manifest.latest_filename}.

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
- latest.business_driver: 4-6 distinct driver claims when supported. Begin each
  headline with a concise reader-facing driver tag and direction, such as
  「IT需要｜追い風」「労務費｜逆風」「金利｜影響混在」. Explain the
  transmission mechanism and affected segment or metric
- latest.outlook: at least 1 claim using the latest filing
- latest.risk: at least 2 claims using the latest filing
- latest.context: target 1 integrated claim using the latest filing, written as
  2-3 short paragraphs when needed for readability
- trend.perspective: target 1 integrated synthesis
- trend.consistent: at least 2 genuinely different recurring themes
- trend.change: 2-3 genuinely different material changes
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
1. Rank the dossier findings by investor materiality before selecting claims.
   Do not split one financial result into several takeaways merely to satisfy a
   claim count. Treat the dossier as the full evidence boundary.
2. Ensure the key takeaways are diversified. Do not use separate sales, operating
   profit, ordinary profit, and net-income bullets for one result. When disclosed,
   include cash generation or balance-sheet change and a forward-looking,
   capital-allocation, or risk conclusion. Describe cash-flow improvement together
   with its disclosed working-capital or other principal driver; do not infer
   broad financial strength from cash or one year of operating cash flow alone.
3. Determine one unifying decade thesis and a small set of non-overlapping
   themes. The thesis must distinguish durable operating capabilities from
   cyclical financial outcomes, and connect strategy, profitability, capital
   allocation, risk, and the latest position.
4. Apply the following tests wherever the dossier supports them:
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
5. Apply these admission rules to the trend sections:
   - trend.perspective must use management discussion from all three named period
     buckets and normally at least five distinct year-end filings. It must describe
     the decade's central continuity, principal change, and material tension.
   - trend.consistent requires at least three separated year-end filings, including
     one from each period bucket. State the recurring mechanism and address any
     slowdown, reversal, or other counterperiod disclosed in the filings. If this
     evidence is unavailable, omit the theme and record a coverage shortfall.
   - trend.change must explicitly explain before -> transition -> current state and
     cite evidence for each stage. The before and current evidence must come from
     separated periods. A single impairment, disposal, lawsuit, exceptional gain,
     or recent improvement is not a decade change without a durable consequence.
   - The latest filing may update the current state but does not replace a recent
     year-end source. Evidence from one year or adjacent years belongs in latest
     context or risk, not in a decade theme.
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
   Never add repetition, generic background, or weak themes merely to reach a length.

Management-consistency explanations:
- The dossier already contains the four ratings used by local scoring. Do not
  recalculate or change them.
- Produce one claim for each management.* section. Explain the corresponding
  rating through concrete examples: targets achieved or missed, observed
  revisions, forecast posture, implementation outcomes, commentary changes,
  and management's treatment of setbacks.
- Use the deterministic research counts when useful, but state that revision
  counts cover only the selected filings. Never create a complete-history claim
  from incomplete coverage.
- Mention the strongest supporting observation and material contrary evidence.
- A repeated priority without a later action or outcome is not execution.

{STYLE_PROFILE}

Response details:
- period_label_ja and period_label_en must be concise citation labels, not prose.
- Cite only evidence IDs present in the dossier.
- Do not return evidence records, management ratings, or figure/date/qualifier
  mapping arrays; local code supplies or derives them.

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
- Do not return section, order, evidence IDs, source Japanese surfaces, statement
  types, inference flags, causal flags, identity, or evidence translations.
  Python restores those immutable fields from the validated Japanese analysis.

Return only the schema-conforming JSON object.
</translation_task>
"""


def analysis_prompt_template() -> str:
    return """\
The prompt contains the selection manifest, deterministic research metrics, the
complete JapaneseResearchDossier, and the fact-free report blueprint. Produce
only JapaneseSynthesisResponse claims that cite dossier evidence IDs. The
research dossier may be as large as the configured research maximum output.
"""


def translation_prompt_template() -> str:
    return """\
The prompt contains a translation-specific JSON projection with issuer context,
claim IDs, Japanese headlines and bodies, and the Japanese figure, date, and
qualifier surfaces that require English rendering. Translate it without new
analysis. Return only translated claim prose and translated span surfaces keyed
by the supplied IDs. Python restores immutable metadata and the original Japanese
evidence. Render financial amounts in English yen notation without FX conversion
and retain unsupported proper names in Japanese.
Return only the EnglishTranslationPatch schema. The source analysis may be as
large as the configured Japanese maximum output.
"""
