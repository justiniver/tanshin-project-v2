# Docling text-input experiment

This disposable sidecar compares the normal native-PDF analysis workflow with
an alternative that:

1. selects the same Tanshin filings with the production selector;
2. converts each physical PDF page to Markdown with Docling;
3. supplies only the page-marked Markdown to the analysis model;
4. runs the normal structured-response parsing, PDF-backed local validation,
   management-consistency calculation, and Markdown rendering;
5. writes everything under `output/experiments/docling_text/`.

No production Python or PowerShell file is changed by this experiment.

## One-time setup

From the repository root:

```powershell
.\scripts\setup_docling_experiment.ps1
```

This creates a separate parser environment, installs the pinned Docling
package, records its complete package set, and downloads the layout and table
models. All of these disposable dependencies stay under:

```text
output/experiments/docling_venv/
output/experiments/docling_models/
output/experiments/docling_environment.freeze.txt
```

The ordinary `.venv` and `requirements.txt` are not modified.

With the pinned package set used in this experiment, the parser environment
and local models occupy about 1.9 GB combined. The first parse can take roughly
one minute per filing; unchanged filings are reused from the extraction cache.

## Offline preview

```powershell
.\scripts\run_docling_reports.ps1 6777 -PreviewOnly
```

The preview performs local extraction and reports:

- selected PDFs and physical pages;
- Docling cache status;
- extracted Markdown character and token estimates;
- the current page-based PDF token estimate;
- the text/PDF planning-token ratio;
- numeric-token extraction diagnostics;
- models, maximum cost in yen, request ID, and intended report paths.

No AI request is made in preview mode.

Do not assume that Markdown will be smaller than native PDF input. Docling adds
table and document structure that can expand token count. The reported
text/PDF ratio is the experiment's decision metric: values below `1.0x` suggest
an input reduction under the local planning assumptions, while values above
`1.0x` indicate that the parsed representation is estimated to be larger.

Force a fresh local parse:

```powershell
.\scripts\run_docling_reports.ps1 6777 -PreviewOnly -ForceReparse
```

## Interactive report run

```powershell
.\scripts\run_docling_reports.ps1 6777
```

The runner shows the complete preflight, asks whether to proceed, then asks
whether an English report should also be generated. The English stage is the
same structured-analysis translation used by the normal pipeline; source PDFs
are not part of translation in either workflow.

Multiple companies and the existing model profiles are supported:

```powershell
.\scripts\run_docling_reports.ps1 1808 6361
.\scripts\run_docling_reports.ps1 1878 -Pro
.\scripts\run_docling_reports.ps1 1878 -Sol
.\scripts\run_docling_reports.ps1 1878 -Key2Translation
.\scripts\run_docling_reports.ps1 1878 -ProTranslation
```

The default profile uses `gemini-3.6-flash` and `GEMINI_API_KEY` for both
stages. `-Key2Translation` keeps Flash for both stages but moves translation to
`GEMINI_API_KEY2`. The experimental runner follows the production runner's
token-aware 75-second same-credential cooldown and its separate inter-company
analysis cooldown. A default run should be free when the primary key is
eligible for Gemini's free tier; displayed yen estimates remain conservative
paid-tier upper bounds.

## Outputs

For ticker `6777`:

```text
output/experiments/docling_text/6777/analysis_ja_6777.md
output/experiments/docling_text/6777/analysis_en_6777.md
```

Important experimental artifacts include:

```text
artifacts/docling_extraction_manifest.json
artifacts/docling_extraction_audit.json
artifacts/docling_text_corpus.md
artifacts/input_size_comparison.json
artifacts/text_input_experiment.json
artifacts/parsed_sources/*.docling.md
artifacts/parsed_sources/*.docling.json
```

The normal reports under `output/6777/` are not touched.

The experiment inherits the production pipeline's intentional non-gating
validation policy. Deterministic checks still run and are retained as
diagnostics, but they do not suppress a parseable and renderable experimental
Markdown report.

## Safety

- Local extraction never imports or invokes an AI client.
- The parser subprocess receives a small allowlist of ordinary Windows
  environment variables; provider API keys and other credentials are not
  inherited.
- Hugging Face and transformer offline modes are forced during report
  preparation, and incomplete or partial Docling conversions are rejected.
- Analysis requests contain an empty attachment list, so the provider runtimes
  cannot add PDF bytes or file parts.
- Report artifacts are fenced under `output/experiments/`; the CLI rejects the
  normal `output/` tree, `exemplar_output/`, and external paths.
- Dry run is the default.
- Live execution still requires the explicit live flag, exact inspected request
  ID, and `TANSHIN_LIVE_API=MANUAL_USER_RUN`.
- Tests inject fake model clients and remain completely offline.
- Returned evidence is still validated against the original selected PDFs.

## Removal

The experiment is isolated to these additions:

```text
experiments/docling_text_pipeline/
scripts/setup_docling_experiment.ps1
scripts/run_docling_reports.ps1
DOCLING_TEXT_EXPERIMENT.md
tests/test_docling_text_experiment.py
output/experiments/docling_venv/
output/experiments/docling_environment.freeze.txt
output/experiments/docling_text/
output/experiments/docling_models/
```

Deleting those paths removes the experiment without reverting any production
pipeline file.
