# Instructions for AI contributors

Read `PROJECT_RULES.txt` and `README.md` before changing this repository.

## Hard safety boundaries

- Never initiate a Gemini or OpenAI request.
- Keep every implementation company-agnostic. Company-specific fixtures are
  acceptable for regression tests, but production logic must not special-case
  a security code or company name.

## Safe commands

Default CLI commands are offline:

```powershell
$env:TANSHIN_OFFLINE_ONLY = "1"
$env:TANSHIN_TESTING = "1"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
Remove-Item Env:TANSHIN_OFFLINE_ONLY
Remove-Item Env:TANSHIN_TESTING
```

The batch preview is also offline and exits before any live branch:

```powershell
.\scripts\run_reports.ps1 1808 6361 -PreviewOnly
.\scripts\run_reports.ps1 1878 --key2-translation -PreviewOnly
.\scripts\run_reports.ps1 1878 --pro-translation -PreviewOnly
```

The hybrid OpenAI-analysis and secondary Pro-profile previews are also offline.
Model names are fixed in `tanshin_pipeline/config.py`, so no model environment
override is needed:

```powershell
.\scripts\run_reports.ps1 1878 --sol -PreviewOnly
.\scripts\run_reports.ps1 1878 --pro -PreviewOnly
```

Use mocks or stored fixtures for request construction, response parsing,
normalization, validation, rendering, retry, and failure tests.

## Repository map

- `tanshin_pipeline/selection.py`: filing discovery and manifest construction.
- `tanshin_pipeline/prompts.py`: research, synthesis, and translation prompts.
- `tanshin_pipeline/research.py`: deterministic summaries of the research dossier.
- `tanshin_pipeline/schemas.py`: model-facing and normalized data contracts.
- `tanshin_pipeline/request_builder.py`: inspectable request specifications.
- `tanshin_pipeline/gemini_runtime.py`: gated Gemini request boundary.
- `tanshin_pipeline/openai_runtime.py`: gated OpenAI research/synthesis boundary.
- `tanshin_pipeline/normalization.py`: Japanese response normalization.
- `tanshin_pipeline/management_consistency.py`: consistency scoring.
- `tanshin_pipeline/validation.py`: diagnostic and structural validation.
- `tanshin_pipeline/render.py`: deterministic Japanese and English Markdown.
- `tanshin_pipeline/pipeline.py`: stage orchestration and persistence.
- `scripts/run_reports.ps1`: recommended human-run batch workflow.
- `scripts/run_gemini_stage.ps1`: lower-level single-stage workflow.
- `tests/`: completely offline ordinary test suite.

## Working conventions

- Preserve historical runs and unrelated user changes.
- Use `apply_patch` for source edits.
- Do not improve a report by patching its rendered output.
- The default report root is `final_output/`. Canonical report names are
  `analysis_ja_{security_code}_{YYYYMMDD}.md` and
  `analysis_en_{security_code}_{YYYYMMDD}.md`, using the local run date.
  Timestamped history directories preserve those filenames unchanged.
- Treat deterministic evaluation scores as diagnostics, not as the authority on
  report quality.
- Deterministic validation is intentionally non-gating. It must still run and
  persist its findings, but `valid`, `publishable`, errors, warnings, and
  quality scores must not prevent a parseable, normalizable, renderable response
  from being written to the canonical Markdown path. Do not reintroduce
  draft-only or validation-blocked publication behavior unless the user
  explicitly changes this policy.
- The same non-gating policy applies between research and synthesis. Once the
  provider returns a Pydantic-parseable research dossier, persist it, record
  deterministic findings in `validation_research.json`, and continue. Only a
  provider failure or an unparseable response may stop before synthesis.
- Keep model-profile labels and model names inspectable, but never log
  `GEMINI_API_KEY`, `GEMINI_API_KEY2`, or `OPENAI_API_KEY`. The `pro` profile
  uses the source-configured secondary Gemini model and credential for both
  stages. The `sol` profile uses OpenAI for analysis and the secondary Gemini
  profile for translation. Model names are constants in
  `tanshin_pipeline/config.py`; `.env` contains credentials only.
- A Japanese report uses two model calls: PDF-backed research followed by
  dossier-backed synthesis. Optional English translation is a third call.
  The second call receives no PDFs, and no report is rendered until it succeeds.
- Keep the first Japanese request coverage-first: it must return one coverage
  record per selected filing with explicit packets for operating results,
  financial condition, forward-looking information, strategy and plan
  progress, segment conditions, capital allocation, and material footnotes.
  Local code derives comparisons and subscores; the second request ranks those
  findings and writes the existing report structure.
- Keep research extraction compact. Array ceilings are source constants, counts
  are never quotas, and one evidence record should be reused wherever a single
  source sentence supports multiple research records.
- Compress historical financial slots into one consistent annual anchor per
  year-end filing, pairing the current actual and next original forecast where
  available. Use the saved response budget for concise qualitative discussion
  from every filing. Allocate commentary slots to a few recurring multi-period
  comparison tracks, and prioritize commitments with observable outcomes over
  pending promises.
- The default profile uses `gemini-3.6-flash` with `GEMINI_API_KEY` for research,
  synthesis, and translation. `--key2-translation` changes only translation to
  `GEMINI_API_KEY2`. When consecutive stages share a Gemini credential and their
  combined estimated input plus maximum-output allowance reaches 225,000
  tokens, the interactive runner waits 75 seconds between them, preserving
  10% headroom below the 250,000-token planning limit. The separate
  inter-company research cooldown remains in effect.
- User-facing preflight must not print a JPY/USD conversion assumption. A
  default, primary-key-only run should be described as free when
  `GEMINI_API_KEY` is eligible for Gemini's free tier; displayed yen estimates
  are conservative paid-tier upper bounds.
- Render yen-denominated financial amounts in English narrative prose using
  one-decimal billion notation at or above ¥1 billion, million notation below
  that threshold, and forms such as `¥95 per share` for per-share amounts.
  Preserve economic value within display precision, scale, sign, percentages,
  margins, and ratios; never perform foreign-exchange conversion.
- Do not render inline citations or evidence ledgers in either report.
  Preserve lightweight source provenance in structured artifacts, and preserve
  Japanese proper names whenever the source analysis does not provide an
  authoritative Latin-script form.
- When a live evaluation is needed, stop and give the human the exact command,
  expected companies/files, maximum estimated cost in yen, and expected
  artifacts.
