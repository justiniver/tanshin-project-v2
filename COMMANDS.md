# Report commands

All commands first show the selected PDFs, models, and maximum estimated cost,
then ask whether to proceed and whether to generate English. A Japanese report
uses two requests: a PDF-backed per-filing extraction dossier followed by a
dossier-backed analytical synthesis. The first request does not draft
conclusions or score management, and the rendered reports do not include inline
citations or an evidence ledger. English translation is an optional third
request.

Model names are fixed in `tanshin_pipeline/config.py`. `.env` contains only
`GEMINI_API_KEY`, `GEMINI_API_KEY2`, and `OPENAI_API_KEY`.

| Command | Japanese research + synthesis | English translation |
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

Reports are written under `final_output/{security_code}/`. Canonical Markdown
filenames include the local report-generation date:
`analysis_ja_{security_code}_{YYYYMMDD}.md` and
`analysis_en_{security_code}_{YYYYMMDD}.md`. Timestamped history directories
preserve these filenames unchanged.

PowerShell spellings `-Key2Translation`, `-ProTranslation`, `-Pro`, and `-Sol`
are equivalent to their double-hyphen forms. Only one model option may be used
at a time.

Between any two consecutive Gemini stages using the same credential, the runner
inserts a 75-second cooldown if their combined estimated input plus maximum
output allowance reaches 225,000 tokens. This preserves 10% headroom below the
250,000-token planning limit. A separate 75-second interval protects consecutive
companies' PDF-backed research requests.

The default profile uses only `GEMINI_API_KEY` and should be free when that key
is eligible for Gemini's free tier. Displayed yen figures are conservative
paid-tier upper-bound estimates rather than an expected charge.

Deterministic validation is intentionally non-gating. It still runs and writes
diagnostic artifacts, but its `publishable`, error, and warning results do not
prevent a parseable and renderable response from being written to the canonical
Markdown report. Final quality review is manual.
