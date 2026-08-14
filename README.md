# Tanshin management-commentary report pipeline

This repository converts Japanese 決算短信 (Kessan Tanshin) PDFs into structured,
investor-oriented Markdown reports:

- a Japanese report focused on the latest filing and roughly ten years of
  management commentary;
- an optional English translation that preserves the Japanese analysis;
- JSON artifacts for filing selection, prompts, model responses, evidence,
  diagnostics, token usage, cost, and run status.

The pipeline is company-agnostic. PDFs are selected deterministically, then the
selected analysis provider receives them directly in a chronological research
pass. A second request receives both that compact research map and the original
PDFs, using the map to focus its review while keeping the filings authoritative.
Optional English translation is a third request. The default CLI and ordinary
test suite are offline.

For a compact list of model combinations and commands, see
[COMMANDS.md](COMMANDS.md).

## Quick start

These instructions assume Windows PowerShell from the repository root.

### 1. Create or activate the virtual environment

If `.venv` already exists:

```powershell
.\.venv\Scripts\Activate.ps1
```

For a new checkout:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The project pins the official `google-genai` and `openai` Python SDKs and uses
PyMuPDF and pypdf for offline PDF inspection.

### 2. Configure API credentials

1. Create or view a key in
   [Google AI Studio](https://aistudio.google.com/apikey).
2. Follow Google's
   [Gemini API key guidance](https://ai.google.dev/gemini-api/docs/api-key),
   including billing, quota, and key-restriction guidance.
3. Copy the repository template:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Open `.env` in a local text editor and replace the placeholder:

   ```text
   GEMINI_API_KEY=replace-with-your-api-key
   ```

The default profile uses `gemini-3.6-flash` and this primary key for Japanese
research, Japanese synthesis, and English translation. When the key is eligible for
Gemini's free tier, the default report workflow should not incur an API charge,
although quota and rate limits still apply.

To use a second Gemini project or quota pool for translation, also configure:

```text
GEMINI_API_KEY2=replace-with-your-second-api-key
```

The secondary key is used only by explicitly selected stages:

- `--key2-translation` keeps primary-key Flash research and synthesis and uses
  `gemini-3.6-flash` with `GEMINI_API_KEY2` for translation;
- `--pro-translation` keeps primary-key Flash research and synthesis and uses
  `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` for translation;
- `--pro` uses `gemini-3.1-pro-preview` and the secondary key for all stages;
- `--sol` uses OpenAI for Japanese research and synthesis and
  `gemini-3.1-pro-preview` with the
  secondary key for translation.

To enable the hybrid Sol profile, create an
[OpenAI API key](https://platform.openai.com/api-keys) and also configure:

```text
OPENAI_API_KEY=replace-with-your-api-key
```

The `--sol` profile uses `gpt-5.6-sol` and `OPENAI_API_KEY` only for Japanese
research and synthesis. If English is requested, translation still uses
`gemini-3.1-pro-preview` and `GEMINI_API_KEY2`.

Model names are fixed in `tanshin_pipeline/config.py` and selected through the
CLI profile. `.env` stores credentials only. Model and provider names are safe
to display in request plans; no key is written to prompts, logs, reports, or
artifacts.

Do not commit `.env`, paste the key into terminal history, or include it in
console output. The repository ignores `.env`, and artifact writers never
receive or log the key.

To check local configuration without contacting Gemini:

```powershell
.\.venv\Scripts\python.exe .\scripts\check_gemini.py
```

Do not add `--live` unless you intentionally want that script to send a separate
test request. It is unnecessary for normal report generation.

### 3. Add company PDFs

Place Tanshin PDFs under:

```text
data/{security_code}/
```

Examples already present in this repository include `1808`, `3923`, and `6361`.
The selector chooses:

- the latest available filing for the current-period summary;
- approximately ten FY/Q4/year-end filings for the trailing trend period;
- no duplicate when the latest filing is itself the relevant year-end filing.

The selector writes a machine-readable manifest explaining every choice.
Missing or ambiguous year-end coverage is rejected rather than guessed.

#### Acquire Tanshin PDFs from JPX

The repository includes a resumable JPX Listed Company Search downloader. It
targets the latest available Tanshin plus the ten most recent consecutive
FY/Q4 fiscal years. Ten years remains the default minimum. A company directory
is published only after every selected PDF opens successfully and its page
count, byte size, and SHA-256 are recorded.

Offline plan only:

```powershell
.\.venv\Scripts\python.exe -m scripts.download_jpx_universe
```

Run or resume the universe acquisition:

```powershell
.\.venv\Scripts\python.exe -m scripts.download_jpx_universe --execute-downloads
```

Retry recorded technical failures without re-querying short-history companies:

```powershell
.\.venv\Scripts\python.exe -m scripts.download_jpx_universe `
  --execute-downloads --retry-failures --failures-only
```

Explicitly acquire companies whose saved discovery results contain eight or
nine distinct fiscal years:

```powershell
.\.venv\Scripts\python.exe -m scripts.download_jpx_universe `
  --execute-downloads --retry-incomplete `
  --minimum-year-ends 8 --available-year-counts 8 9
```

This is an intentional exception to the default ten-year requirement.
`--available-year-counts` filters the previously recorded
`available_fiscal_years` in `universe_status.json`; it does not use the raw
number of FY disclosures, because revisions and fiscal-year transitions can
produce multiple filings for one fiscal year. The downloader still selects up
to ten years when they are available, records the accepted minimum and actual
selected-year count, rejects a nonconsecutive window, and marks an eight- or
nine-year corpus as a short-window acceptance in its coverage metadata.

Run the complete offline corpus and analysis-preflight audit:

```powershell
.\.venv\Scripts\python.exe -m scripts.download_jpx_universe --audit-only
```

The acquisition workflow never imports or invokes either live model runtime and sets
`TANSHIN_OFFLINE_ONLY` and `TANSHIN_TESTING` itself. Progress and final results
are retained under:

```text
data_acquisition/universe_status.json
data_acquisition/universe_failures.json
data_acquisition/universe_failures.csv
data_acquisition/final_audit.json
```

`universe_failures.csv` is the concise inventory of companies that did not meet
the minimum accepted distinct-year coverage in their latest acquisition pass.
Re-running the same command is safe: verified company directories are rechecked
and skipped, while incomplete staging directories are never published.

### 4. Preview or run reports

Preview two companies without sending an API request:

```powershell
.\scripts\run_reports.ps1 1808 6361 -PreviewOnly
```

Run the interactive workflow:

```powershell
.\scripts\run_reports.ps1 1808 6361
```

For one company:

```powershell
.\scripts\run_reports.ps1 1808
```

Use Gemini Flash for all stages while moving only translation to the secondary key:

```powershell
.\scripts\run_reports.ps1 1878 --key2-translation
```

PowerShell's conventional spelling is also supported:

```powershell
.\scripts\run_reports.ps1 1878 -Key2Translation
```

Run all stages with the secondary Gemini 3.1 Pro Preview profile:

```powershell
.\scripts\run_reports.ps1 1878 --pro
```

PowerShell's conventional spelling is also supported:

```powershell
.\scripts\run_reports.ps1 1878 -Pro
```

Use primary-key Gemini Flash for Japanese research and synthesis and
secondary-key Gemini Pro for English translation:

```powershell
.\scripts\run_reports.ps1 1878 --pro-translation
```

PowerShell's conventional spelling is also supported:

```powershell
.\scripts\run_reports.ps1 1878 -ProTranslation
```

Preview the same Pro batch without sending an API request:

```powershell
.\scripts\run_reports.ps1 1878 --pro -PreviewOnly
```

The Pro request plan is distinct from the default request plan, and its cost
estimate uses Gemini 3.1 Pro Preview's standard paid pricing tier for the
estimated prompt size. The model is currently a preview release and may have
more restrictive rate limits than stable models.

Use GPT-5.6 Sol for Japanese research and synthesis and Gemini Pro for optional
English translation:

```powershell
.\scripts\run_reports.ps1 1878 --sol
```

PowerShell-style `-Sol` and the offline `--sol -PreviewOnly` form are also
supported. `--key2-translation`, `--pro-translation`, `--pro`, and `--sol`
are mutually exclusive.

| Profile | Japanese research + synthesis | English translation |
| --- | --- | --- |
| default | `gemini-3.6-flash` with `GEMINI_API_KEY` | `gemini-3.6-flash` with `GEMINI_API_KEY` |
| `--key2-translation` | `gemini-3.6-flash` with `GEMINI_API_KEY` | `gemini-3.6-flash` with `GEMINI_API_KEY2` |
| `--pro-translation` | `gemini-3.6-flash` with `GEMINI_API_KEY` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` |
| `--pro` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` |
| `--sol` | `gpt-5.6-sol` with `OPENAI_API_KEY` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` |

The runner:

1. prepares every company offline;
2. displays selected PDFs, page counts, models, request IDs, maximum estimated
   costs in yen, intended outputs, and diagnostic files;
3. asks whether to proceed for the entire company list;
4. asks whether English reports should also be generated;
5. archives existing company outputs in a timestamped history directory while
   preserving their filenames;
6. runs the first company's PDF-backed research request with one API attempt;
7. stores the chronological filing map and prepares the Japanese synthesis offline;
8. runs the synthesis request with both that map and the selected PDFs;
9. prepares that company's optional English translation;
10. between consecutive Gemini stages using the same credential, waits
   75 seconds when their combined estimated input and maximum-output allowance
   is at least 225,000 tokens, leaving 10% headroom below the 250,000-token
   planning limit;
11. enforces a 75-second interval between companies' PDF-backed research
   requests, counting synthesis and translation time toward that interval;
12. moves to the next company and repeats the same sequence;
13. prints the API provider and local pipeline status for every stage.

Without a model option, `gemini-3.6-flash` and `GEMINI_API_KEY` perform
research, synthesis, and translation. With `--key2-translation`, Japanese work remains on
primary-key Flash and translation uses the same Flash model with
`GEMINI_API_KEY2`. With
`--pro-translation`, Flash research and synthesis continue to use
`GEMINI_API_KEY`, while
English translation uses the fixed `gemini-3.1-pro-preview` model and
`GEMINI_API_KEY2`. With the Pro option, that secondary Pro setup performs all
stages. With the Sol option, OpenAI performs research and synthesis and the secondary Gemini
Pro setup performs translation. Model names come from
`tanshin_pipeline/config.py`, not `.env`.

The selected company list is batch-wide: there is no later per-company
selection prompt. Companies are processed in the order supplied—for example,
`1808` research, `1808` synthesis, `1808` translation, then `6361` research,
synthesis, and translation. A stage failure stops the workflow before
later companies. API calls cannot be transactional, so any earlier successful
company remains successful if a later stage encounters a service error.

## Outputs

Canonical reports:

```text
final_output/{security_code}/analysis_ja_{security_code}_{YYYYMMDD}.md
final_output/{security_code}/analysis_en_{security_code}_{YYYYMMDD}.md
```

`YYYYMMDD` is the local report-generation date. For example, a Japanese report
for security code `1808` generated on August 10, 2026 is named
`analysis_ja_1808_20260810.md`.

The current pipeline does not maintain a separate draft report. Once a response
passes structured-output parsing and can be normalized and rendered, the
canonical Markdown is written even when deterministic validation reports errors
or warnings. Its review state is recorded separately in:

```text
final_output/{security_code}/artifacts/report_status_ja.json
final_output/{security_code}/artifacts/report_status_en.json
```

Validation findings do not add banners to the report and do not veto the
canonical Markdown. They remain in JSON artifacts for human or AI review.

Before a live batch, existing company output is copied to:

```text
final_output/{security_code}/history/{timestamp}/
```

The timestamped directory prevents archive collisions, so dated Markdown
filenames are preserved unchanged when they move into history.

The pipeline also retires stale current reports when a new analysis invalidates
them or when an API request fails before producing a schema-valid response.

## What the pipeline does

### Filing selection

`tanshin_pipeline/selection.py` discovers company PDFs, identifies the latest
filing, builds the trailing year-end window, deduplicates the latest year-end
filing, and rejects questionable coverage. The resulting
`selection_manifest.json` records filenames, roles, pages, hashes, fiscal years,
and selection reasons.

### Japanese research and synthesis

Japanese generation is deliberately split into two requests. Under the default
profile both use `gemini-3.6-flash`. Under the Pro profile both use the
source-configured `gemini-3.1-pro-preview`; Pro-translation and key2-translation
retain default Flash for both Japanese stages. Under Sol, both Japanese stages
use `gpt-5.6-sol`.

Both Japanese requests receive the selected PDFs inline; neither the Gemini
Files API nor the OpenAI Files API is used. Sol preflight rejects a selection
if any PDF or the combined inline payload reaches 50 MB. Sol uses low PDF image
detail while retaining each PDF's extracted text. The research request is
recorded in `request_plan_research.json`.

The research pass is deliberately an extraction pass, not a preliminary
analysis. It builds a compact chronological map containing:

- company and reporting-period identity;
- exactly one memo for every selected filing, ordered chronologically;
- dense filing-specific observations from operating results, financial
  condition, forward-looking information, strategy and execution, segment and
  business drivers, capital allocation, and material footnotes;
- a durable business overview from the latest filing;
- physical page locations and statement types as navigation aids;
- compact year-end financial anchors that pair one consistently available
  annual actual metric with the next original forecast where available;
- compact cross-filing records for material capital-allocation tracks, identifying
  which business or other use received capital, how its relative priority changed,
  and what later profit, margin, cash, impairment, or disposal outcomes followed.
  Capital inputs, immediate transaction/accounting effects, and subsequent
  economic returns are stored separately. Reported ROIC, ROA, return on
  operating assets, and equivalent capital-efficiency measures are retained
  when disclosed;
- explicit unavailable categories and source limitations rather than silent
  omission.

Request 1 does not rank business drivers, decide decade themes, score management,
or draft report conclusions. Those tasks belong to request 2. The research
response is deliberately compact and does not create IDs, theme graphs,
commentary tags, citation-ready quotations, or an evidence ledger. Closely
related same-page statements may be combined into a dense memo item.

The information budget is intentionally comparison-first:

- historical financial values are compressed into one consistently available
  profitability anchor per year-end filing, pairing the current actual with the
  next original annual forecast where available;
- the response budget is reserved first for the qualitative management
  discussion in each filing, including changes in causes, qualifications,
  actions, confidence, and outlook. Capital-allocation research is the exception:
  reported segment assets, operating assets, working capital, inventory, capacity,
  and other capital employed are primary evidence when disclosed;
- older filings remain concise but independently useful, while the latest memo
  carries more current operating, balance-sheet, outlook, risk, and business
  context;
- material footnotes are retained when they change the interpretation of
  performance, risk, capital deployment, or management follow-through.

Broad historical table duplication, routine footnotes, and polished analytical
conclusions are deliberately deprioritized in request 1.

Before the second request, Python matches comparable original annual forecasts
with later actuals and constructs the selected annual financial anchor series.
It also summarizes filing and category coverage. These calculations and their
limitations are stored in `research_metrics.json`.

The second request receives the research map, those deterministic comparisons,
the fact-free report blueprint, and all selected PDFs. The map directs attention
to likely useful passages but is not a source boundary: the model is instructed
to revisit the filings for omitted context, competing interpretations, and
contrary evidence. Its job is to rank material findings, assess the four
management-consistency dimensions, and write the final analytical claims,
including:

- a concise, plain-language company overview;
- latest-filing management takeaways;
- key business and financial drivers;
- outlook, targets, risks, and uncertainty;
- a multi-period strategic perspective;
- consistent themes, material changes, capital-allocation developments, and an
  assessment of whether the decade's allocation tracks created value;
- detailed explanations beneath each management-consistency subscore.

The trend section uses qualitative management discussion—including 経営成績,
財政状態, cash-flow discussion, future outlook, management-plan progress,
capital allocation, and explanations of risks or misses—to identify claims and
changes in emphasis. It does not adopt management's framing as its conclusion.
Reported results, financial condition, cash flow, mandatory footnotes,
impairments, misses, and later filings are used to test those statements.
Confident or repeated management language is not proof of execution, durability,
or value creation. Summary tables corroborate figures rather than becoming the
trend thesis.
Its capital-allocation value-creation subsection leads with a supported judgment
about where incremental capital went and whether it increasingly favored the
businesses producing the strongest subsequent returns. It follows the decade's
most material organic business investments, acquisitions, disposals,
balance-sheet choices, and shareholder distributions into their later operating
and financial outcomes. When segment invested capital is not disclosed, it uses
directional evidence such as people, marketing, development, capacity,
acquisition spend, and stated priorities without inventing an allocation ratio.
Destination-level or management-linked returns are distinguished from aggregate
segment or group performance. Acquisition prices, proceeds, goodwill, negative
goodwill, disposal gains, financing flows, dividends, and buybacks are immediate
actions or effects rather than subsequent returns. Revenue growth alone does not
establish an attractive return, while recent tracks may appropriately remain
unproven. The assessment prioritizes disclosed ROIC, ROA, return on operating
assets, or equivalent measures. When those ratios are unavailable, it compares
same-scope business assets or capital employed with subsequent profit or cash
generation and makes only a directional capital-efficiency judgment. It never
manufactures an undisclosed ratio or combines incompatible scopes or periods.
Recurring profit, margin, cash generation, useful capacity, and a stronger core
business count in favor; persistent losses, impairments, failed expansion,
disposals after weak performance, and financial strain count against. It also asks
whether management added capital after strong results and reduced or exited
weak uses, rather than making missing disclosure or the mere act of spending and
distributing capital its thesis.
Management-linked return records preserve management's causal explanation but
are not independent verification. Group-wide ROE, EPS, BVPS, share-price
performance, or aggregate profit is not attributed to a particular investment
without destination-level evidence. Paying a dividend or executing a buyback is
a use of capital, not proof by itself that value was created. Claims about where
"most" capital went require comparable amounts or an unambiguous directional
record.
When the selected filings show no meaningful qualitative development, the model
may report that limitation and emphasize the financial, forecast, target,
disclosure, or capital-allocation record rather than forcing a weak theme.

This version remains limited to the selected latest and year-end Tanshin corpus.
It does not add quarterly-commentary analysis, peer comparison, Yuho filings, or
company presentation materials. The research map captures decision-useful
longitudinal financial evidence, but it is not intended to replace a standardized
XBRL-based long-term financial statement table or a full-depth multi-source
company report.

Every company receives the same fact-free structure from
`prompt_assets/generic_report_blueprint_ja.md`. Company exemplars under
`exemplar_output/` are used only for offline comparison; they are never supplied
to either analysis provider.

### Japanese normalization and rendering

Request 2 is citation-free: it returns analytical claims and management ratings,
not source IDs, quotations, page references, or evidence records. Local code
derives value spans from the claim prose so figures, dates, qualifiers, and
financial scale can still be protected through translation. It also calculates
management consistency, records a normalization audit, and renders the Japanese
Markdown. New runs do not create an evidence ledger.

Report prose is never manually patched. Improvements belong in prompts,
schemas, normalization, validation, or rendering.

### Management consistency

The score is a supporting 0–1 measure of whether management's earlier
statements and commitments align with later actions and results. It is not an
investment recommendation or a score of business quality.

The four components are:

- strategic coherence;
- execution and follow-through;
- forecast and target discipline;
- accountability and transparency.

The synthesis pass supplies component ratings after reviewing the chronological
map and the selected PDFs. Python converts the four ratings to 0–1 subscores and
uses their arithmetic mean. A subscore remains blank only in the exceptional
case where the synthesis pass cannot make a defensible assessment. If no
component can be assessed, the overall fallback is `0.50`.
Directly beneath the score, the report shows every original annual forecast that
Python could match to a subsequent actual, including the metric, forecast,
actual, and outcome. These are deterministic comparisons from the research
map, not values regenerated during Markdown rendering or English translation.
For forecast discipline, meeting or exceeding the original annual forecast is
always positive evidence regardless of the size of the upside; falling below
the original forecast is negative evidence. Later revisions do not erase the
comparison with original guidance. When guidance is stated as a range, an
actual below the lower bound is a miss, an actual within the range is met, and
an actual above the upper bound is exceeded. Medium-term target delivery and
the clarity of revisions remain separate evidence within the same component.
The synthesis pass also writes a concise natural-language explanation beneath
every subscore, including supporting and contrary information. The full calculation is stored in
`management_consistency.json`.

### English translation

Under the default profile, the translation stage uses `gemini-3.6-flash` with
the primary Gemini key. The key2-translation profile uses the same model with
the secondary Gemini key. The Pro-translation, Pro, and
Sol profiles use the source-configured `gemini-3.1-pro-preview` with the
secondary Gemini key. The stage receives a compact projection of the validated
Japanese analysis, not the PDFs. That projection contains only issuer context,
claim IDs, Japanese claim prose, and figure/date/qualifier surfaces that require
English rendering. The model returns a translation patch; Python restores
schema version, identity, section, ordering, statement types, flags, and source
Japanese surfaces from the same Japanese analysis snapshot.

The analytical narrative and financial presentation are English. Statement
amounts of at least ¥1 billion use one-decimal billion notation such as
`¥293.2 billion`; smaller amounts use `¥X million`, and per-share amounts use
forms such as `¥95 per share`. Comparable figures should not mix million and
billion scales. Translation preserves currency, sign, scale,
actual-versus-forecast status, and economic value within the displayed
precision; it never performs foreign-exchange conversion. The local validator
accepts legitimate display rounding while continuing to reject material value
or scale errors. Percentages, margins, payout ratios, and other financial ratios
retain their original numeric values. Proper names use an authoritative
Latin-script form only when it is supplied by the analysis; otherwise the Japanese name is
retained rather than translated by meaning.

Neither language contains citation bookkeeping. Grounding happens inside the
PDF-backed research and synthesis requests, while structured artifacts retain
the model's claims, value spans, diagnostics, and normalization audit rather
than a citation graph.
`analysis_en.normalized.json` is retained as a compatibility artifact but is a
semantic pass-through copy, and `normalization_en.json` records
`mode: model_rendered_english_financial_notation`.

### Cost estimates

Model prices are maintained internally in USD, but every user-facing
preview and confirmation screen displays any predicted paid-tier maximum cost
in yen without printing a JPY/USD conversion assumption. A default run uses
only `GEMINI_API_KEY` and should be free when that key is eligible for Gemini's
free tier; its displayed yen figures are conservative paid-tier upper-bound
estimates, not an expected charge. Quota limits and the account's actual tier
still apply.

For `gemini-3.1-pro-preview`, the estimator applies the current standard paid
tier based on estimated prompt size: USD 2 per million input tokens and USD 12
per million output/thinking tokens through 200,000 prompt tokens, or USD 4 and
USD 18 respectively above 200,000 prompt tokens.

For `gpt-5.6-sol`, the estimator uses USD 5 per million input tokens and USD 30
per million output tokens through 272,000 input tokens, or USD 10 and USD 45
respectively above that threshold. Because OpenAI PDF processing includes
extracted text and low-detail page images, offline Sol previews use a
conservative 1,500 input-token estimate per selected PDF page. The exact
provider-reported usage and resulting estimated actual cost are stored after a
completed request.

## Safety model

The Python CLI is offline unless `--execute-api` is explicitly supplied.
Live execution additionally requires:

- a request ID generated by the exact offline request plan;
- `TANSHIN_LIVE_API=MANUAL_USER_RUN`;
- `TANSHIN_OFFLINE_ONLY` to be absent;
- `TANSHIN_TESTING` to be absent;
- a configured API key;
- one explicit stage invocation.

The recommended batch runner manages these controls after the user confirms the
inspected batch. Automatic retries are disabled; every stage uses one request
attempt.

During execution, status is written to:

```text
api_status_research.json
api_status_analysis.json
api_status_translation.json
```

Typical states are:

- `SUCCESS`;
- `RATE_LIMITED`;
- `TEMPORARILY_UNAVAILABLE`;
- `FAILED`.

A provider `503 UNAVAILABLE` response is recorded as temporary and is not
retried automatically. Wait and manually rerun the workflow later. A `429`
should be treated as a quota or rate-limit event.

## Validation philosophy

Report quality is judged primarily by qualitative review against the source
filings, exemplars, and historical runs. Deterministic metrics are diagnostics,
not the authority on whether a report is useful.

The deterministic validation layer is intentionally bypassed as a publication
gate. It is still executed, and its complete results are retained, but
`valid`, `publishable`, error counts, warning counts, and quality scores do not
decide whether the canonical Markdown file is generated. Here, “bypassed” means
non-gating; it does not mean that validation is disabled.

This is deliberate because the reports are evaluated manually for analytical
quality, readability, and fidelity to the filings. A response that can be
parsed, normalized, and rendered is therefore written to the canonical `.md`
path. A negative validator result changes `report_status_*.json` to
`generated_with_diagnostics` and recommends review, but it does not create a
draft-only result or suppress the report.

API failures, malformed structured output that cannot be parsed, missing
required source artifacts, and processing or rendering failures can still stop
the pipeline. Those are execution failures rather than deterministic editorial
judgments.

Default validation is deliberately permissive and still flags high-signal
conditions such as:

- wrong security code, company identity, or latest filing;
- duplicate or unresolved claim/source identities;
- unselected source files or impossible physical pages;
- actual/forecast/target statement-type contradictions;
- material Japanese/English claim-set or internal-source-set changes;
- unresolved references in rendered Markdown.

Source-page discrepancies, summary boundaries, causal flags, section counts,
length heuristics, and similar signals are normally warnings or suppressed
diagnostics. The English validator converts both the original Japanese amount
and the translated English yen expression to an exact yen value for comparison.
Equivalent forms such as `252億円`, `25,200百万円`, and `¥25.2 billion` are
accepted, while a scale change such as `252億円` becoming `¥2.52 billion`
remains a factual-integrity error.

`report_status_*.json` records the validator's advisory `publishable` result,
whether review is recommended, and which run generated the current Markdown.
Even `publishable: false` does not prevent that Markdown from being generated.
This intentional non-gating behavior is expected in the manual-review-first
workflow.

## Artifacts

Important files under `final_output/{security_code}/artifacts/` include:

| Artifact | Purpose |
| --- | --- |
| `selection_manifest.json` | Selected latest and trend filings with reasons |
| `request_plan_research.json` | PDF-backed research model, request ID, files, hashes, and limits |
| `request_plan_analysis.json` | PDF-backed synthesis model, request ID, files, hashes, and limits |
| `request_plan_translation.json` | Translation request plan |
| `prompt_research.txt` / `prompt_analysis.txt` / `prompt_translation.txt` | Complete stage prompts |
| `schema_research.json` / `schema_analysis.json` / `schema_translation.json` | Native structured-output schemas |
| `model_response_research.raw.json` | Raw research-provider response |
| `research.structured.json` | Parsed chronological filing research map |
| `research_metrics.json` | Filing/category coverage, annual anchors, forecast/actual comparisons, capital-allocation track coverage, and extraction diagnostics |
| `validation_research.json` | Non-gating research-map diagnostics; warnings never stop synthesis |
| `model_response_ja.raw.json` / `model_response_en.raw.json` | Raw synthesis and translation responses |
| `analysis_ja.structured.json` | Parsed model-facing synthesis response |
| `analysis_ja.normalized.json` | Locally normalized Japanese analysis |
| `analysis_en.structured.json` | Full English translation materialized locally from the model patch |
| `analysis_en.normalized.json` | English compatibility copy with model-rendered yen notation |
| `normalization_ja.json` / `normalization_en.json` | Normalization audit records |
| `management_consistency.json` | Synthesis ratings, locally calculated subscores, and calculation |
| `validation_ja.json` / `validation_en.json` | Diagnostics and structural checks |
| `report_status_ja.json` / `report_status_en.json` | Current report/run state |
| `token_usage.json` | Model-reported usage for research, synthesis, and translation |
| `cost.json` | Estimated and available actual cost information |
| `run_metadata.json` | Models, manifest, mode, output path, and request count |
| `exemplar_comparison_ja.json` / `exemplar_comparison_en.json` | Advisory offline comparison |

No artifact contains the API key.

## Other commands

### Lower-level single-stage wrapper

Use this when diagnosing or rerunning one specific stage:

```powershell
# Offline inspection
.\scripts\run_gemini_stage.ps1 1808 research

# Human-authorized live stage
.\scripts\run_gemini_stage.ps1 1808 research -Execute

# Inspect or run the same stage with the secondary Pro profile
.\scripts\run_gemini_stage.ps1 1808 research -Pro
.\scripts\run_gemini_stage.ps1 1808 research -Pro -Execute
```

After research succeeds, use `analysis` for the PDF-backed, research-map-guided synthesis;
translation uses `translation`. The live form
requires exact interactive confirmation of the request ID. This legacy
single-stage wrapper remains Gemini-specific; use `run_reports.ps1 --sol` for
the hybrid OpenAI research-and-synthesis workflow.

### Direct offline CLI

```powershell
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage research
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage analysis
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage translation
```

### Reprocess stored structured responses

These commands make no API request. They regenerate the canonical report
through the pipeline and should be used only when intentionally testing new
normalization, validation, or rendering behavior:

```powershell
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage research --reprocess-stored
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage analysis --reprocess-stored
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage translation --reprocess-stored
```

Research reprocessing recovers a complete, schema-valid provider response that
was saved before a local dossier-validation failure. It sends no request and
prepares the stored dossier for the separate synthesis stage.

### Compare an existing report with an available exemplar

```powershell
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --compare-exemplar
```

The comparison covers structure, section coverage, executive breadth,
analytical depth, trend specificity, tone, repetition, readability, and
approximate length. Its score is advisory.

## Testing

The ordinary test suite is fully offline:

```powershell
$env:TANSHIN_OFFLINE_ONLY = "1"
$env:TANSHIN_TESTING = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
Remove-Item Env:TANSHIN_OFFLINE_ONLY
Remove-Item Env:TANSHIN_TESTING
```

Tests use mocks and stored fixtures for Gemini and OpenAI request construction,
structured response parsing, retry behavior, API failures, filing selection,
normalization, validation, rendering, management consistency, and report
status. The live integration placeholder is skipped in the ordinary suite.

## Troubleshooting

### `503 UNAVAILABLE` or high demand

The request did not complete. Confirm the stage status artifact, wait, and rerun
manually. The pipeline does not retry automatically.

### `429` or quota errors

Check AI Studio usage, project quota, and billing. Wait for capacity or quota to
recover before rerunning.

For an OpenAI error that explicitly says one request is larger than the model's
tokens-per-minute limit, waiting alone will not help because the individual
request exceeds the current usage-tier ceiling. Sol uses low-detail PDF page
images by default to reduce this load while retaining extracted PDF text. If a
low-detail request still exceeds the stated ceiling, use an account tier with
sufficient TPM or reduce the selected source volume before manually rerunning.

### Response reached its output-token limit

The provider returned a truncated structured response rather than a complete
JSON object. The raw provider response is retained in the applicable
`model_response_*.raw.json` artifact. Research uses low thinking and bounded
array sizes to reduce this risk; do not repair a partial dossier or publish from
it.

### API key not configured

Confirm that `.env` exists and contains the key required by the selected
profile. Never print the key itself.

For a key2-translation run, confirm that `.env` contains
`GEMINI_API_KEY2`.

For a Pro run, confirm that `.env` contains `GEMINI_API_KEY2`. The model name is
fixed in `tanshin_pipeline/config.py`; it is not configured through `.env`.
Offline preflight constructs no client, displays no key value, and cannot send
a request. The secondary credential is selected only inside the
human-authorized live boundary.

The same secondary key is required for `--pro-translation`, but only its
translation stage uses it.

For a Sol run, also confirm that `.env` contains `OPENAI_API_KEY`. The Sol
preflight does not construct an OpenAI client or inspect the key. If English is
requested, `GEMINI_API_KEY2` is also required for the fixed secondary Gemini Pro
translation profile.

### Request ID mismatch

The prompt, model, schema, filing selection, or source files changed after the
plan was prepared. Generate a fresh preview and use the newly displayed plan.

### A later company was not processed

An earlier company stage did not finish successfully, so the sequential runner
stopped. Inspect that stage's API status artifact before rerunning.

### Filing coverage is ambiguous

Inspect `data/{security_code}/` naming and the selection error. Add or rename
filings so year-end coverage is explicit; do not silently force a questionable
selection.

## Guidance for AI coding agents

AI agents must read [AGENTS.md](AGENTS.md) and `PROJECT_RULES.txt` before
working in this repository. In particular, agents must never:

- initiate a Gemini or OpenAI request;
- manually edit reports under `final_output/`, the legacy `output/` directory,
  or `exemplar_output/`;
- add company-specific production logic.

AI agents may inspect and improve API integration code, run offline previews,
use fake responses, and run the offline test suite.

## Main code locations

| Path | Responsibility |
| --- | --- |
| `tanshin_pipeline/selection.py` | Filing discovery and manifest construction |
| `tanshin_pipeline/prompts.py` | Research, synthesis, and translation prompts |
| `tanshin_pipeline/research.py` | Deterministic dossier summaries and guardrails |
| `tanshin_pipeline/schemas.py` | Structured model and normalized schemas |
| `tanshin_pipeline/request_builder.py` | Request specifications and IDs |
| `tanshin_pipeline/gemini_runtime.py` | Gated Gemini request boundary |
| `tanshin_pipeline/openai_runtime.py` | Gated OpenAI research/synthesis request boundary |
| `tanshin_pipeline/normalization.py` | Japanese normalization |
| `tanshin_pipeline/management_consistency.py` | Consistency calculation |
| `tanshin_pipeline/validation.py` | Diagnostics and structural validation |
| `tanshin_pipeline/render.py` | Japanese and English Markdown rendering |
| `tanshin_pipeline/pipeline.py` | Stage orchestration and persistence |
| `scripts/run_reports.ps1` | Recommended interactive batch runner |
| `scripts/run_gemini_stage.ps1` | Lower-level one-stage runner |
