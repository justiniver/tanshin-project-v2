# Tanshin management-commentary report pipeline

This repository converts Japanese 決算短信 (Tanshin) PDFs into structured,
investor-oriented Markdown reports:

- a Japanese report focused on the latest filing and roughly ten years of
  management commentary;
- an optional English translation that preserves the Japanese analysis;
- JSON artifacts for filing selection, prompts, model responses, evidence,
  diagnostics, token usage, cost, and run status.

The pipeline is company-agnostic. PDFs are selected deterministically, then the
selected analysis provider receives them directly and performs the qualitative
analysis. The default CLI and ordinary test suite are offline.

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

The default profile uses `gemini-3.6-flash` and this primary key for both
Japanese analysis and English translation. When the key is eligible for
Gemini's free tier, the default report workflow should not incur an API charge,
although quota and rate limits still apply.

To use a second Gemini project or quota pool for translation, also configure:

```text
GEMINI_API_KEY2=replace-with-your-second-api-key
```

The secondary key is used only by explicitly selected stages:

- `--key2-translation` keeps primary-key Flash analysis and uses
  `gemini-3.6-flash` with `GEMINI_API_KEY2` for translation;
- `--pro-translation` keeps primary-key Flash analysis and uses
  `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` for translation;
- `--pro` uses `gemini-3.1-pro-preview` and the secondary key for both stages;
- `--sol` uses OpenAI for analysis and `gemini-3.1-pro-preview` with the
  secondary key for translation.

To enable the hybrid Sol profile, create an
[OpenAI API key](https://platform.openai.com/api-keys) and also configure:

```text
OPENAI_API_KEY=replace-with-your-api-key
```

The `--sol` profile uses `gpt-5.6-sol` and `OPENAI_API_KEY` only for Japanese
analysis. If English is requested, translation still uses
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

Use Gemini Flash for both stages while moving translation to the secondary key:

```powershell
.\scripts\run_reports.ps1 1878 --key2-translation
```

PowerShell's conventional spelling is also supported:

```powershell
.\scripts\run_reports.ps1 1878 -Key2Translation
```

Run both stages with the secondary Gemini 3.1 Pro Preview profile:

```powershell
.\scripts\run_reports.ps1 1878 --pro
```

PowerShell's conventional spelling is also supported:

```powershell
.\scripts\run_reports.ps1 1878 -Pro
```

Use primary-key Gemini Flash for Japanese analysis and secondary-key Gemini Pro
for English translation:

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

Use GPT-5.6 Sol for Japanese analysis and Gemini Pro for optional English
translation:

```powershell
.\scripts\run_reports.ps1 1878 --sol
```

PowerShell-style `-Sol` and the offline `--sol -PreviewOnly` form are also
supported. `--key2-translation`, `--pro-translation`, `--pro`, and `--sol`
are mutually exclusive.

| Profile | Japanese analysis | English translation |
| --- | --- | --- |
| default | `gemini-3.6-flash` with `GEMINI_API_KEY` | `gemini-3.6-flash` with `GEMINI_API_KEY` |
| `--key2-translation` | `gemini-3.6-flash` with `GEMINI_API_KEY` | `gemini-3.6-flash` with `GEMINI_API_KEY2` |
| `--pro-translation` | `gemini-3.6-flash` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` |
| `--pro` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` |
| `--sol` | `gpt-5.6-sol` with `OPENAI_API_KEY` | `gemini-3.1-pro-preview` with `GEMINI_API_KEY2` |

The runner:

1. prepares every company offline;
2. displays selected PDFs, page counts, models, request IDs, maximum estimated
   costs in yen, intended outputs, and diagnostic files;
3. asks whether to proceed for the entire company list;
4. asks whether English reports should also be generated;
5. archives existing outputs;
6. runs the first company's Japanese analysis with one API attempt;
7. prepares that company's optional English translation;
8. when both stages share a Gemini credential, waits 75 seconds before
   translation if their combined estimated input and maximum-output allowance
   is at least 225,000 tokens, leaving 10% headroom below the 250,000-token
   planning limit;
9. enforces a 75-second cooldown between company analysis requests, counting
   any time spent translating toward that interval;
10. moves to the next company and repeats the same sequence;
11. prints the API provider and local pipeline status for every stage.

Without a model option, `gemini-3.6-flash` and `GEMINI_API_KEY` perform both
analysis and translation. With `--key2-translation`, analysis remains on
primary-key Flash and translation uses the same Flash model with
`GEMINI_API_KEY2`. With
`--pro-translation`, Flash analysis continues to use `GEMINI_API_KEY`, while
English translation uses the fixed `gemini-3.1-pro-preview` model and
`GEMINI_API_KEY2`. With the Pro option, that secondary Pro setup performs both
stages. With the Sol option, OpenAI performs analysis and the secondary Gemini
Pro setup performs translation. Model names come from
`tanshin_pipeline/config.py`, not `.env`.

The selected company list is batch-wide: there is no later per-company
selection prompt. Companies are processed in the order supplied—for example,
`1808` analysis, `1808` translation, cooldown if still required, `6361`
analysis, then `6361` translation. A stage failure stops the workflow before
later companies. API calls cannot be transactional, so any earlier successful
company remains successful if a later stage encounters a service error.

## Outputs

Canonical reports:

```text
output/{security_code}/analysis_ja_{security_code}.md
output/{security_code}/analysis_en_{security_code}.md
```

The current pipeline does not maintain a separate draft report. A schema-valid
response is rendered to the canonical Markdown path, and its review state is
recorded separately in:

```text
output/{security_code}/artifacts/report_status_ja.json
output/{security_code}/artifacts/report_status_en.json
```

Validation findings do not add banners to the report. They remain in JSON
artifacts for human or AI review.

Before a live batch, existing company output is copied to:

```text
output/{security_code}/history/{timestamp}/
```

The pipeline also retires stale current reports when a new analysis invalidates
them or when an API request fails before producing a schema-valid response.

## What the pipeline does

### Filing selection

`tanshin_pipeline/selection.py` discovers company PDFs, identifies the latest
filing, builds the trailing year-end window, deduplicates the latest year-end
filing, and rejects questionable coverage. The resulting
`selection_manifest.json` records filenames, roles, pages, hashes, fiscal years,
and selection reasons.

### Japanese analysis

Under the default profile, the analysis stage uses `gemini-3.6-flash`. Under
the Pro profile, it uses the source-configured `gemini-3.1-pro-preview`. The
Pro-translation profile keeps the default Flash analysis model, as does the
key2-translation profile. Under the Sol profile, it uses `gpt-5.6-sol`.
Selected PDFs are sent inline as PDF parts; neither the Gemini Files API nor the
OpenAI Files API is used. Sol preflight rejects a selected file set if any PDF
or the combined inline file payload reaches 50 MB. Sol uses OpenAI's low PDF
detail level by default: every PDF and its extracted text remain in the request,
while page images use fewer input tokens than high detail. The selected detail
level is displayed during preflight and recorded in
`request_plan_analysis.json`.

The prompt asks for:

- a concise, plain-language company overview explaining what the company does,
  who it serves, its principal businesses, and how its business model works;
- company and reporting-period identity;
- latest-filing management takeaways;
- business and financial drivers;
- outlook, targets, risks, and uncertainty;
- a multi-period strategic perspective;
- consistent themes, material changes, and capital allocation;
- evidence records tied to source filenames and physical PDF pages;
- management-consistency component assessments.

The trend section prioritizes qualitative management discussion, including
経営成績, 財政状態, cash-flow discussion, future outlook, management-plan
progress, capital allocation, and management explanations of risks or misses.
Summary tables mainly corroborate figures rather than becoming the trend thesis.

Every company receives the same fact-free structure from
`prompt_assets/generic_report_blueprint_ja.md`. Company exemplars under
`exemplar_output/` are used only for offline comparison; they are never supplied
to either analysis provider.

### Japanese normalization and rendering

Local code normalizes the structured Japanese response, repairs supported PDF
extraction issues when uniquely resolvable, derives stable evidence mappings,
calculates management consistency, records an audit trail, and renders the
Japanese Markdown.

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

The analysis model supplies evidence-based component ratings. Python verifies whether each
component has enough longitudinal and management-discussion evidence, converts
supported ratings to 0–1 subscores, leaves unsupported subscores blank, and
uses the arithmetic mean of the available subscores. If no component can be
assessed, the overall fallback is `0.50`. The full calculation is stored in
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
schema version, identity, section, ordering, evidence links, statement types,
flags, and source Japanese surfaces from the same validated analysis snapshot.

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

The collapsed English evidence ledger displays the original Japanese
quotations. Gemini is not asked to translate the evidence ledger, which avoids
introducing monetary-scale and proper-name errors into the source record.
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

Default validation is deliberately permissive. It focuses on reliable
structural failures such as:

- wrong security code, company identity, or latest filing;
- duplicate or unresolved claim/evidence identities;
- unselected source files or impossible physical pages;
- actual/forecast/target statement-type contradictions;
- material Japanese/English claim-set or evidence-set changes;
- unresolved references in rendered Markdown.

Citation formatting, exact-quote boundaries, causal flags, section counts,
length heuristics, and similar signals are normally warnings or suppressed
diagnostics. The English validator converts both the original Japanese amount
and the translated English yen expression to an exact yen value for comparison.
Equivalent forms such as `252億円`, `25,200百万円`, and `¥25.2 billion` are
accepted, while a scale change such as `252億円` becoming `¥2.52 billion`
remains a factual-integrity error.

`report_status_*.json` records whether the validator considers the report
publishable, whether review is recommended, and which run generated the current
Markdown. A schema-valid report may still be marked
`generated_with_diagnostics`; that is expected in this manual-review-first
workflow.

## Artifacts

Important files under `output/{security_code}/artifacts/` include:

| Artifact | Purpose |
| --- | --- |
| `selection_manifest.json` | Selected latest and trend filings with reasons |
| `request_plan_analysis.json` | Analysis model, request ID, files, hashes, and limits |
| `request_plan_translation.json` | Translation request plan |
| `prompt_analysis.txt` / `prompt_translation.txt` | Complete model prompts |
| `schema_analysis.json` / `schema_translation.json` | Native analysis schema and compact translation-patch schema |
| `model_response_ja.raw.json` / `model_response_en.raw.json` | Raw SDK responses |
| `analysis_ja.structured.json` | Parsed model-facing Japanese response |
| `analysis_ja.normalized.json` | Locally normalized Japanese analysis |
| `analysis_en.structured.json` | Full English translation materialized locally from the model patch |
| `analysis_en.normalized.json` | English compatibility copy with model-rendered yen notation |
| `normalization_ja.json` / `normalization_en.json` | Normalization audit records |
| `management_consistency.json` | Score inputs, subscores, evidence, and calculation |
| `validation_ja.json` / `validation_en.json` | Diagnostics and structural checks |
| `report_status_ja.json` / `report_status_en.json` | Current report/run state |
| `evidence_ledger.json` | Original Japanese evidence used by both reports |
| `token_usage.json` | Model-reported usage |
| `cost.json` | Estimated and available actual cost information |
| `run_metadata.json` | Models, manifest, mode, output path, and request count |
| `exemplar_comparison_ja.json` / `exemplar_comparison_en.json` | Advisory offline comparison |

No artifact contains the API key.

## Other commands

### Lower-level single-stage wrapper

Use this when diagnosing or rerunning one specific stage:

```powershell
# Offline inspection
.\scripts\run_gemini_stage.ps1 1808 analysis

# Human-authorized live stage
.\scripts\run_gemini_stage.ps1 1808 analysis -Execute

# Inspect or run the same stage with the secondary Pro profile
.\scripts\run_gemini_stage.ps1 1808 analysis -Pro
.\scripts\run_gemini_stage.ps1 1808 analysis -Pro -Execute
```

Translation is the same command with `translation` as the stage. The live form
requires exact interactive confirmation of the request ID. This legacy
single-stage wrapper remains Gemini-specific; use `run_reports.ps1 --sol` for
the hybrid OpenAI-analysis workflow.

### Direct offline CLI

```powershell
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage analysis
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage translation
```

### Reprocess stored structured responses

These commands make no API request. They regenerate the canonical report
through the pipeline and should be used only when intentionally testing new
normalization, validation, or rendering behavior:

```powershell
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage analysis --reprocess-stored
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --stage translation --reprocess-stored
```

### Compare an existing report with an available exemplar

```powershell
.\.venv\Scripts\python.exe -m tanshin_pipeline 1808 --compare-exemplar
```

The comparison covers structure, section coverage, executive breadth,
analytical depth, trend specificity, evidence density, tone, repetition,
readability, and approximate length. Its score is advisory.

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
- run a command containing `--execute-api`, `-Execute`, or
  `scripts/check_gemini.py --live`;
- read, display, or modify `.env`;
- manually edit reports under `output/` or `exemplar_output/`;
- add company-specific production logic.

AI agents may inspect and improve API integration code, run offline previews,
use fake responses, and run the offline test suite.

## Main code locations

| Path | Responsibility |
| --- | --- |
| `tanshin_pipeline/selection.py` | Filing discovery and manifest construction |
| `tanshin_pipeline/prompts.py` | Analysis and translation prompts |
| `tanshin_pipeline/schemas.py` | Structured model and normalized schemas |
| `tanshin_pipeline/request_builder.py` | Request specifications and IDs |
| `tanshin_pipeline/gemini_runtime.py` | Gated Gemini request boundary |
| `tanshin_pipeline/openai_runtime.py` | Gated OpenAI analysis request boundary |
| `tanshin_pipeline/normalization.py` | Japanese normalization |
| `tanshin_pipeline/management_consistency.py` | Consistency calculation |
| `tanshin_pipeline/validation.py` | Diagnostics and structural validation |
| `tanshin_pipeline/render.py` | Japanese and English Markdown rendering |
| `tanshin_pipeline/pipeline.py` | Stage orchestration and persistence |
| `scripts/run_reports.ps1` | Recommended interactive batch runner |
| `scripts/run_gemini_stage.ps1` | Lower-level one-stage runner |
