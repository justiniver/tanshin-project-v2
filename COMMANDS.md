# Report commands

All commands first show the selected PDFs, models, and maximum estimated cost,
then ask whether to proceed and whether to generate English.

Model names are fixed in `tanshin_pipeline/config.py`. `.env` contains only
`GEMINI_API_KEY`, `GEMINI_API_KEY2`, and `OPENAI_API_KEY`.

| Command | Japanese analysis | English translation |
| --- | --- | --- |
| `.\scripts\run_reports.ps1 1808` | `gemini-3.6-flash`, primary key | `gemini-3.6-flash`, primary key |
| `.\scripts\run_reports.ps1 1808 --key2-translation` | `gemini-3.6-flash`, primary key | `gemini-3.6-flash`, secondary key |
| `.\scripts\run_reports.ps1 1808 --pro-translation` | `gemini-3.6-flash`, primary key | `gemini-3.1-pro-preview`, secondary key |
| `.\scripts\run_reports.ps1 1808 --pro` | `gemini-3.1-pro-preview`, secondary key | `gemini-3.1-pro-preview`, secondary key |
| `.\scripts\run_reports.ps1 1808 --sol` | `gpt-5.6-sol`, OpenAI key | `gemini-3.1-pro-preview`, secondary key |

Use multiple tickers in one sequential batch:

```powershell
.\scripts\run_reports.ps1 1808 3923 6361 --pro-translation
```

Preview any command without sending an API request:

```powershell
.\scripts\run_reports.ps1 1808 --pro-translation -PreviewOnly
```

PowerShell spellings `-Key2Translation`, `-ProTranslation`, `-Pro`, and `-Sol`
are equivalent to their double-hyphen forms. Only one model option may be used
at a time.

When analysis and translation share one Gemini credential, the runner inserts
a 75-second inter-stage cooldown if their combined estimated input plus maximum
output allowance reaches 225,000 tokens. This preserves 10% headroom below the
250,000-token planning limit. The separate 75-second cooldown between company
analysis requests remains in place.

The default profile uses only `GEMINI_API_KEY` and should be free when that key
is eligible for Gemini's free tier. Displayed yen figures are conservative
paid-tier upper-bound estimates rather than an expected charge.

## Docling text-input experiment

The disposable Docling sidecar uses the same model profiles but supplies
page-marked Markdown instead of PDF attachments:

```powershell
.\scripts\run_docling_reports.ps1 6777 -PreviewOnly
.\scripts\run_docling_reports.ps1 6777
```

See [DOCLING_TEXT_EXPERIMENT.md](DOCLING_TEXT_EXPERIMENT.md) for setup,
artifacts, safety boundaries, and removal instructions.
