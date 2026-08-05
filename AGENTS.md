# Instructions for AI contributors

Read `PROJECT_RULES.txt` and `README.md` before changing this repository.

## Hard safety boundaries

- Never initiate a Gemini or OpenAI request. Do not run commands containing
  `--execute-api`, `-Execute`, or `scripts/check_gemini.py --live`.
- Never read, display, log, copy, or modify `.env` or any API key.
- Never upload PDFs through a provider Files API. The authorized runtimes send
  selected PDFs inline only when a human explicitly runs the live workflow.
- Never manually edit Markdown under `output/` or `exemplar_output/`.
  Fix prompts, schemas, normalization, validation, or rendering instead.
- Source documentation such as `README.md` and `AGENTS.md` may be edited when
  documentation is the task.
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
.\scripts\run_reports.ps1 1878 --flash-translation -PreviewOnly
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
- `tanshin_pipeline/prompts.py`: analysis and translation prompts.
- `tanshin_pipeline/schemas.py`: model-facing and normalized data contracts.
- `tanshin_pipeline/request_builder.py`: inspectable request specifications.
- `tanshin_pipeline/gemini_runtime.py`: gated Gemini request boundary.
- `tanshin_pipeline/openai_runtime.py`: gated OpenAI analysis request boundary.
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
- Treat deterministic evaluation scores as diagnostics, not as the authority on
  report quality.
- Keep model-profile labels and model names inspectable, but never log
  `GEMINI_API_KEY`, `GEMINI_API_KEY2`, or `OPENAI_API_KEY`. The `pro` profile
  uses the source-configured secondary Gemini model and credential for both
  stages. The `sol` profile uses OpenAI for analysis and the secondary Gemini
  profile for translation. Model names are constants in
  `tanshin_pipeline/config.py`; `.env` contains credentials only.
- Render yen-denominated financial amounts in English narrative prose using
  one-decimal billion notation at or above ¥1 billion, million notation below
  that threshold, and forms such as `¥95 per share` for per-share amounts.
  Preserve economic value within display precision, scale, sign, percentages,
  margins, and ratios; never perform foreign-exchange conversion.
- Render English-report evidence quotations from the original Japanese, and
  preserve Japanese proper names whenever the source analysis does not provide
  an authoritative Latin-script form.
- When a live evaluation is needed, stop and give the human the exact command,
  expected companies/files, maximum estimated cost in yen, and expected
  artifacts.
