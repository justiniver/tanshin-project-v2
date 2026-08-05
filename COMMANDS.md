# Report commands

All commands first show the selected PDFs, models, and maximum estimated cost,
then ask whether to proceed and whether to generate English.

| Command | Japanese analysis | English translation |
| --- | --- | --- |
| `.\scripts\run_reports.ps1 1808` | Gemini Flash, primary key | Gemini Flash Lite, primary key |
| `.\scripts\run_reports.ps1 1808 --flash-translation` | Gemini Flash, primary key | Gemini Flash, secondary key |
| `.\scripts\run_reports.ps1 1808 --pro-translation` | Gemini Flash, primary key | Gemini Pro, secondary key |
| `.\scripts\run_reports.ps1 1808 --pro` | Gemini Pro, secondary key | Gemini Pro, secondary key |
| `.\scripts\run_reports.ps1 1808 --sol` | GPT-5.6 Sol, OpenAI key | Gemini Pro, secondary key |

Use multiple tickers in one sequential batch:

```powershell
.\scripts\run_reports.ps1 1808 3923 6361 --pro-translation
```

Preview any command without sending an API request:

```powershell
.\scripts\run_reports.ps1 1808 --pro-translation -PreviewOnly
```

PowerShell spellings `-FlashTranslation`, `-ProTranslation`, `-Pro`, and `-Sol`
are equivalent to their double-hyphen forms. Only one model option may be used
at a time.
