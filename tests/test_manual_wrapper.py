from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ManualWrapperTests(unittest.TestCase):
    def test_wrapper_keeps_live_execution_explicit_and_single_attempt(self) -> None:
        script = (
            REPOSITORY_ROOT / "scripts" / "run_gemini_stage.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("[switch]$Execute", script)
        self.assertIn("[switch]$Pro", script)
        self.assertIn("[switch]$Key2Translation", script)
        self.assertIn("[switch]$ProTranslation", script)
        self.assertIn("'key2-translation'", script)
        self.assertIn("'pro-translation'", script)
        self.assertIn("--model-profile $modelProfile", script)
        self.assertIn("Read-Host", script)
        self.assertIn("--confirm-request $plan.request_id", script)
        self.assertIn("--max-api-attempts 1", script)
        self.assertIn("Archived existing output to:", script)
        self.assertIn("$reportDate = Get-Date -Format 'yyyyMMdd'", script)
        self.assertEqual(script.count("--report-date $reportDate"), 2)
        self.assertIn(
            '"final_output\\$SecurityCode\\artifacts"',
            script,
        )
        self.assertIn("'ja|en'", script)
        self.assertIn("'en'", script)
        self.assertIn("Copy-Item `", script)
        self.assertIn("Remove-Item -LiteralPath $_.FullName", script)
        self.assertIn(
            "analysis_ja_${SecurityCode}_$reportDate.md",
            script,
        )
        self.assertIn(
            "analysis_en_${SecurityCode}_$reportDate.md",
            script,
        )
        self.assertNotIn('"output\\$SecurityCode', script)
        self.assertIn("Fact-free style blueprint:", script)
        self.assertIn("Estimated maximum stage cost: JPY {0:N0}", script)
        self.assertIn(
            "Billing note: This profile uses only GEMINI_API_KEY",
            script,
        )
        self.assertNotIn("Yen conversion assumption:", script)
        self.assertNotIn("FlashTranslation", script)
        self.assertNotIn("flash-translation", script)
        self.assertIn("Write-ApiStatusSummary", script)
        helper = (
            REPOSITORY_ROOT / "scripts" / "api_status_helpers.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Get-OptionalJsonProperty", helper)
        self.assertIn("TEMPORARILY_UNAVAILABLE", helper)
        self.assertIn("PROCESSING_FAILED_AFTER_API_SUCCESS", helper)
        self.assertIn("REPORT PIPELINE STATE: NOT_COMPLETED", helper)
        self.assertNotIn("$ApiStatus.response_id", helper)
        self.assertNotIn("check_gemini.py", script)
        self.assertNotIn("$env:GEMINI_API_KEY", script)

    def test_batch_wrapper_is_sequential_and_keeps_live_guards(self) -> None:
        script = (
            REPOSITORY_ROOT / "scripts" / "run_reports.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("ValueFromRemainingArguments = $true", script)
        self.assertIn("[switch]$PreviewOnly", script)
        self.assertIn("[switch]$Key2Translation", script)
        self.assertIn("[switch]$ProTranslation", script)
        self.assertIn("[switch]$Pro", script)
        self.assertIn("[switch]$Sol", script)
        self.assertIn("$_ -ieq '--key2-translation'", script)
        self.assertIn("$_ -ieq '--pro-translation'", script)
        self.assertIn("$_ -ieq '--pro'", script)
        self.assertIn("$_ -ieq '--sol'", script)
        self.assertIn(
            "Choose only one of --key2-translation, --pro-translation, ",
            script,
        )
        self.assertIn("--pro, or --sol.", script)
        self.assertIn("'key2-translation'", script)
        self.assertIn("'pro-translation'", script)
        self.assertIn("'sol'", script)
        self.assertIn("--model-profile $ModelProfile", script)
        self.assertIn("Resolve-SecurityCodes", script)
        self.assertIn("Duplicate security codes are not allowed", script)
        self.assertIn("Proceed with two-stage Japanese analysis for all", script)
        self.assertIn(
            "Generate an English report immediately after each Japanese report",
            script,
        )
        self.assertIn(
            "Companies run sequentially: PDF research, Japanese synthesis, ",
            script,
        )
        self.assertIn("optional English translation, then the next company.", script)
        self.assertIn("$cooldownSeconds = 75", script)
        self.assertIn("Wait-BetweenStagesIfNeeded", script)
        self.assertIn("Wait-BeforeNextCompany", script)
        self.assertIn("Start-Sleep -Seconds $remaining", script)
        self.assertIn("$cooldownTokenThreshold = 225000", script)
        self.assertIn("Get-StageBudget", script)
        self.assertIn("$Previous.Plan.provider_profile -ne", script)
        self.assertIn("$Next.Plan.provider_profile", script)
        self.assertIn("Research succeeded for $($research.Code)", script)
        self.assertIn("--execute-api", script)
        self.assertIn("--confirm-request $requestId", script)
        self.assertIn("--max-api-attempts 1", script)
        self.assertIn("TANSHIN_TESTING=1 blocks live execution", script)
        self.assertIn("Write-ApiStatusSummary", script)
        self.assertIn("MODEL RUN STATE: RUNNING", script)
        self.assertIn("RESEARCH RECOVERY STATE: SUCCESS", script)
        self.assertIn("--reprocess-stored", script)
        self.assertIn("Research provider/model:", script)
        self.assertIn("PDFs submitted in research request:", script)
        self.assertIn("PREVIEW ONLY: no API request was sent.", script)
        self.assertIn("$reportDate = Get-Date -Format 'yyyyMMdd'", script)
        self.assertEqual(script.count("--report-date $reportDate"), 3)
        self.assertIn(
            '$currentOutput = Join-Path $repositoryRoot "final_output\\$Code"',
            script,
        )
        self.assertIn(
            "$canonicalRoot = Join-Path $repositoryRoot 'final_output'",
            script,
        )
        self.assertIn("analysis_ja_${code}_$reportDate.md", script)
        self.assertIn("analysis_en_${code}_$reportDate.md", script)
        self.assertIn("'^analysis_(ja|en)_' +", script)
        self.assertIn(
            "Copy-Item -LiteralPath $_.FullName -Destination $archive -Recurse",
            script,
        )
        self.assertIn("Remove-Item -LiteralPath $_.FullName", script)
        self.assertNotIn('"output\\$Code', script)
        self.assertNotIn("'output'", script)
        self.assertIn("Estimated maximum research cost: JPY {0:N0}", script)
        self.assertIn(
            "Maximum Japanese report cost (research + synthesis): JPY {0:N0}",
            script,
        )
        self.assertIn(
            "Billing note: This profile uses only GEMINI_API_KEY",
            script,
        )
        self.assertNotIn("Yen conversion assumption:", script)
        self.assertNotIn("FlashTranslation", script)
        self.assertNotIn("flash-translation", script)
        live_stage = script[
            script.index("function Invoke-LiveStage") :
            script.index("function Write-ResearchPreparation")
        ]
        self.assertNotIn("2>&1", live_stage)
        self.assertLess(
            script.index("-Stage research"),
            script.index("Proceed with two-stage Japanese analysis for all"),
        )
        self.assertNotIn(
            "only if every Japanese analysis succeeds.",
            script,
        )
        self.assertNotIn("$env:GEMINI_API_KEY", script)
        self.assertNotIn("OPENAI_API_KEY", script)
        self.assertNotIn("check_gemini.py", script)

if __name__ == "__main__":
    unittest.main()
