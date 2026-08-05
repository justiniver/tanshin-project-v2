"""Prompt construction with a fact-free, company-agnostic style blueprint."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .schemas import JapaneseAnalysis, SelectionManifest
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


ANALYSIS_SYSTEM_PROMPT = """\
# Role
You are a Japanese public-company financial analyst specializing in 決算短信.
Produce decision-useful investor research from the supplied source PDFs.

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
- Every factual claim must cite one or more evidence records.
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


def build_analysis_prompt(
    manifest: SelectionManifest,
    blueprint: BlueprintReference,
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

<report_blueprint>
The following annotated Markdown is fact-free. It demonstrates section balance
and analytical relationships only. Do not copy its bracketed instructions,
placeholder relationships, or wording. Do not return Markdown.

{blueprint.text}
</report_blueprint>

<analysis_task>
Create a Japanese investor report for security code {manifest.security_code}.
The latest filing is {manifest.latest_filename}. The trend period consists only
of the selected year-end sources identified in the manifest.

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
- latest.business_driver: at least 2 claims using the latest filing
- latest.outlook: at least 1 claim using the latest filing
- latest.risk: at least 2 claims using the latest filing
- latest.context: target 1 integrated claim using the latest filing, written as
  2-3 short paragraphs when needed for readability
- trend.perspective: target 1 integrated synthesis
- trend.consistent: at least 2 genuinely different recurring themes
- trend.change: 2-3 genuinely different material changes
- trend.capital_allocation: at least 1 distinct capital-allocation development
- trend.implication: target 1 current investor implication

These are coverage targets, not permission to create weak, repetitive, or
unsupported claims. Return fewer claims when necessary. For each underfilled
section, add one model_notes entry in the form
coverage_shortfall:<section>:<concise Japanese reason>.

Analysis requirements:
1. First identify relevant evidence across the supplied PDFs internally. Rank it
   by investor materiality before selecting claims. Do not split one financial
   result into several key takeaways merely to satisfy the claim count.
   For the trend analysis, begin with the qualitative management discussion:
   経営成績に関する説明 or 経営成績等の概況, 財政状態に関する説明,
   cash-flow discussion, 業績予想などの将来予測情報に関する説明,
   management-plan progress, capital allocation, and management's discussion
   of risks. Use financial-summary tables and notes as corroboration rather than
   allowing a sequence of headline figures to become the trend thesis.
2. Before choosing the trend themes, internally test each candidate using four
   questions: what management said or prioritized; what subsequently happened;
   what material evidence limits or contradicts the interpretation; and why that
   record matters now. Reject themes that cannot answer the first three questions
   with management-discussion evidence from the required period buckets.
3. Ensure the key takeaways are diversified. Do not use separate sales, operating
   profit, ordinary profit, and net-income bullets for one result. When disclosed,
   include cash generation or balance-sheet change and a forward-looking,
   capital-allocation, or risk conclusion. Describe cash-flow improvement together
   with its disclosed working-capital or other principal driver; do not infer
   broad financial strength from cash or one year of operating cash flow alone.
4. Determine one unifying decade thesis and a small set of non-overlapping
   themes. The thesis must distinguish durable operating capabilities from
   cyclical financial outcomes, and connect strategy, profitability, capital
   allocation, risk, and the latest position.
5. Apply the following source tests wherever the relevant disclosures exist:
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
6. Apply these admission rules to the trend sections:
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
7. For every major growth theme, actively look for a contraction, miss, reversal,
   or cyclical downturn in the supplied period. If one exists, explain what it
   shows about durability rather than presenting only favorable years. Explain
   what would confirm or disconfirm the latest interpretation. Do not convert an
   ambition into implementation, implementation into an outcome, or one favorable
   result into proof of durable change. A completed reorganization, acquisition,
   divestiture, or investment establishes that the action occurred, not that its
   intended economic outcome has been achieved.
8. Treat capital allocation broadly: organic investment in people, marketing,
   development and capacity; acquisitions and divestitures; securities, debt and
   balance-sheet deployment; and dividends or buybacks. Discuss material shifts
   and trade-offs rather than listing every cash-flow item. Keep this analysis in
   its own section and do not repeat the same facts as separate strategic changes.
9. Use restrained language when evidence is mixed. Avoid promotional expressions
   equivalent to "overwhelming," "revolutionary," "a major milestone," "evolved,"
   "completed a transformation," or "normalized" unless multi-period filing
   evidence establishes that conclusion.
10. Give more space to material themes and less to secondary ones. As advisory
   guidance, the complete trend analysis should normally total about 1,500-2,000
   Japanese characters and the integrated perspective about 350-475 characters.
   Never add repetition, generic background, or weak themes merely to reach a length.

Management-consistency assessment:
- Return one component for each required dimension.
- Use 0 for materially inconsistent, 1 for weak, 2 for mixed, 3 for generally
  consistent, and 4 for highly consistent. Compare early, middle, and recent
  management commentary and cite evidence from all three periods for each rating
  when available. A rating of 4 requires convincing early-, middle-, and
  recent-period follow-through and no omitted material counterexample.
- If longitudinal evidence is insufficient, set evidence_sufficiency to
  "insufficient", set rating to null, cite whatever relevant evidence exists,
  and explain the limitation. Use rating 2 only when the evidence itself is mixed.
- Otherwise set evidence_sufficiency to "sufficient" and provide a 0-4 rating.
- strategic_coherence: whether stated priorities and the logic of successive
  plans remain coherent; explained adaptation is not automatically inconsistency
- execution_follow_through: whether announced initiatives are later implemented
  and associated operational or financial outcomes are discussed
- forecast_target_discipline: whether forecasts and targets are subsequently met,
  credibly revised, or transparently reconciled with outcomes; evaluate annual,
  endpoint, and cumulative commitments separately
- accountability_transparency: whether misses, trade-offs, changed assumptions,
  and abandoned priorities are explained rather than silently replaced
- Base these ratings primarily on management-discussion passages, not on raw
  financial-table sequences or the mere repetition of plan slogans.
- Each rationale must state the strongest supporting evidence and any material
  contrary evidence. Repetition of a strategic priority without a later outcome
  is not execution follow-through.

{STYLE_PROFILE}

Response details:
- period_label_ja and period_label_en must be concise citation labels, not prose.
- Do not return figure, date, or qualifier mapping arrays; local code derives them.

Based only on the supplied PDFs, return the schema-conforming JSON. Omit any
assertion that cannot be grounded; grounding takes priority over coverage and length.
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
