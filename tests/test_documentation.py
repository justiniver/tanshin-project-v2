from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def test_readme_describes_the_current_primary_workflow(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(
            r".\scripts\run_reports.ps1 1808 6361",
            readme,
        )
        self.assertIn("-PreviewOnly", readme)
        self.assertIn("there is no later per-company", readme)
        self.assertIn("selection prompt.", readme)
        self.assertIn(
            "75-second interval between companies' PDF-backed research",
            readme,
        )
        self.assertIn("counting", readme)
        self.assertIn("synthesis and translation time toward that interval", readme)
        self.assertIn(
            "combined estimated input and maximum-output allowance",
            readme,
        )
        self.assertIn("at least 225,000 tokens", readme)
        self.assertIn("10% headroom below the 250,000-token", readme)
        self.assertIn("Companies are processed in the order supplied", readme)
        self.assertIn("1808` research, `1808` synthesis", readme)
        self.assertIn("A stage failure stops the workflow", readme)
        self.assertIn(
            "final_output/{security_code}/"
            "analysis_ja_{security_code}_{YYYYMMDD}.md",
            readme,
        )
        self.assertIn(
            "final_output/{security_code}/"
            "analysis_en_{security_code}_{YYYYMMDD}.md",
            readme,
        )
        self.assertIn("local report-generation date", readme)
        self.assertIn("filenames are preserved unchanged", readme)
        self.assertIn("does not maintain a separate draft report", readme)
        self.assertIn(
            "mode: model_rendered_english_financial_notation",
            readme,
        )
        self.assertIn("financial presentation are English", readme)
        self.assertIn("¥293.2 billion", readme)
        self.assertIn("should be free when that key is eligible", readme)
        self.assertIn("paid-tier upper-bound", readme)
        self.assertNotIn("150 per USD", readme)
        self.assertIn("Request 2 is citation-free", readme)
        self.assertIn("New runs do not create an evidence ledger", readme)
        self.assertIn("Neither language contains citation bookkeeping", readme)
        self.assertIn("otherwise the Japanese name is", readme)
        self.assertIn("retained rather than translated by meaning", readme)
        self.assertIn("uses their arithmetic mean", readme)
        self.assertIn("exactly one memo for every selected filing", readme)
        self.assertIn("Both Japanese requests receive the selected PDFs", readme)
        self.assertIn("map directs attention", readme)
        self.assertIn("is not a source boundary", readme)
        self.assertIn("forecast/actual comparisons", readme)
        self.assertIn(
            r".\scripts\run_reports.ps1 1878 --pro",
            readme,
        )
        self.assertIn(
            r".\scripts\run_reports.ps1 1878 --key2-translation",
            readme,
        )
        self.assertIn(
            r".\scripts\run_reports.ps1 1878 --pro-translation",
            readme,
        )
        self.assertIn(
            r".\scripts\run_reports.ps1 1878 --sol",
            readme,
        )
        self.assertIn("GPT-5.6 Sol", readme)
        self.assertIn(
            "Gemini Pro for optional\nEnglish translation",
            readme,
        )
        self.assertIn(
            "Model names are fixed in `tanshin_pipeline/config.py`",
            readme,
        )
        self.assertIn("`.env` stores credentials only", readme)
        self.assertIn(
            "`--pro-translation` keeps primary-key Flash research and synthesis",
            readme,
        )
        self.assertIn(
            "`gemini-3.6-flash` and `GEMINI_API_KEY` perform",
            readme,
        )
        self.assertIn(
            "A second request receives both that compact research map",
            readme,
        )
        self.assertIn("Optional English translation is a third request", readme)
        self.assertIn("request_plan_research.json", readme)
        self.assertIn("research.structured.json", readme)
        self.assertIn("detailed explanations beneath each", readme)
        self.assertNotIn("--flash-translation", readme)
        self.assertNotIn("-FlashTranslation", readme)
        self.assertNotIn("gemini-3.5-flash-lite", readme)
        self.assertNotIn("GEMINI_MODEL", readme)
        self.assertIn("[COMMANDS.md](COMMANDS.md)", readme)
        self.assertIn("company overview", readme)
        self.assertIn("through 200,000 prompt tokens", readme)
        self.assertIn(
            "deterministic validation layer is intentionally bypassed",
            readme,
        )
        self.assertIn("publication\ngate", readme)
        self.assertIn(
            "does not mean that validation is disabled",
            readme,
        )
        self.assertIn(
            "Even `publishable: false` does not prevent",
            readme,
        )

        self.assertNotIn("Review drafts are always written", readme)
        self.assertNotIn("financial notation remains exactly in", readme)
        self.assertNotIn("BLOCKED_AFTER_GEMINI_SUCCESS", readme)
        self.assertNotIn("strategic coherence (30%)", readme)
        self.assertNotIn(
            "English translation is skipped for the whole batch",
            readme,
        )

    def test_api_setup_uses_a_safe_template(self) -> None:
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        env_example = (
            REPOSITORY_ROOT / ".env.example"
        ).read_text(encoding="utf-8")

        self.assertIn("Copy-Item .env.example .env", readme)
        self.assertIn("https://ai.google.dev/gemini-api/docs/api-key", readme)
        self.assertIn("https://aistudio.google.com/apikey", readme)
        self.assertIn(
            "GEMINI_API_KEY=replace-with-your-api-key",
            env_example,
        )
        self.assertIn(
            "GEMINI_API_KEY2=replace-with-your-api-key",
            env_example,
        )
        self.assertIn(
            "OPENAI_API_KEY=replace-with-your-api-key",
            env_example,
        )
        assignments = [
            line.split("=", 1)[0]
            for line in env_example.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(
            assignments,
            [
                "GEMINI_API_KEY",
                "GEMINI_API_KEY2",
                "OPENAI_API_KEY",
            ],
        )
        self.assertNotIn("GEMINI_MODEL", env_example)
        self.assertNotIn("AIza", env_example)

    def test_ai_contributor_guide_repeats_the_hard_safety_rules(self) -> None:
        guide = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")

        for required in (
            "Never initiate a Gemini or OpenAI request",
            "Keep every implementation company-agnostic",
            "TANSHIN_OFFLINE_ONLY",
            "TANSHIN_TESTING",
            "run_reports.ps1 1808 6361 -PreviewOnly",
            "run_reports.ps1 1878 --key2-translation -PreviewOnly",
            "run_reports.ps1 1878 --pro-translation -PreviewOnly",
            "run_reports.ps1 1878 --pro -PreviewOnly",
            "run_reports.ps1 1878 --sol -PreviewOnly",
        ):
            self.assertIn(required, guide)
        self.assertIn(
            "Model names are fixed in `tanshin_pipeline/config.py`",
            guide,
        )
        self.assertIn(
            "combined estimated input plus maximum-output allowance",
            guide,
        )
        self.assertIn("two PDF-backed model calls", guide)
        self.assertIn("attention\n  guide, not a source boundary", guide)
        self.assertIn("225,000", guide)
        self.assertIn(
            "Deterministic validation is intentionally non-gating",
            guide,
        )
        self.assertIn("The default report root is `final_output/`", guide)
        self.assertIn(
            "`analysis_ja_{security_code}_{YYYYMMDD}.md`",
            guide,
        )
        self.assertIn(
            "Timestamped history directories preserve those filenames unchanged",
            guide,
        )
        self.assertIn(
            "must not prevent a parseable, normalizable, renderable response",
            guide,
        )
        self.assertNotIn("--flash-translation", guide)
        self.assertNotIn("-FlashTranslation", guide)
        self.assertNotIn("GEMINI_MODEL", guide)

    def test_concise_command_reference_lists_every_model_profile(self) -> None:
        commands = (REPOSITORY_ROOT / "COMMANDS.md").read_text(
            encoding="utf-8"
        )
        for required in (
            r".\scripts\run_reports.ps1 1808",
            "--key2-translation",
            "--pro-translation",
            "--pro",
            "--sol",
            "`gemini-3.6-flash`, primary key",
            "`gemini-3.6-flash`, secondary key",
            "`gemini-3.1-pro-preview`, secondary key",
            "`gpt-5.6-sol`, OpenAI key",
            "`GEMINI_API_KEY`, `GEMINI_API_KEY2`, and `OPENAI_API_KEY`",
            "-PreviewOnly",
            "multiple tickers",
            "225,000 tokens",
            "Gemini's free tier",
            "Deterministic validation is intentionally non-gating",
            "Final quality review is manual",
            "A Japanese report",
            "PDF-backed chronological filing map",
            "using both that map and the original PDFs",
            "`final_output/{security_code}/`",
            "`analysis_ja_{security_code}_{YYYYMMDD}.md`",
            "`analysis_en_{security_code}_{YYYYMMDD}.md`",
            "preserve these filenames unchanged",
        ):
            self.assertIn(required, commands)
        self.assertNotIn("--flash-translation", commands)
        self.assertNotIn("-FlashTranslation", commands)
        self.assertNotIn("gemini-3.5-flash-lite", commands)

    def test_output_contract_is_documented_and_ignored(self) -> None:
        project_rules = (REPOSITORY_ROOT / "PROJECT_RULES.txt").read_text(
            encoding="utf-8"
        )
        gitignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("default report root is final_output/", project_rules)
        self.assertIn("local run date as YYYYMMDD", project_rules)
        self.assertIn("preserve those dated filenames unchanged", project_rules)
        ignored_paths = {
            line.strip()
            for line in gitignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("final_output/", ignored_paths)
        self.assertIn("output/", ignored_paths)

    def test_documented_local_files_exist(self) -> None:
        for relative_path in (
            "README.md",
            "COMMANDS.md",
            "AGENTS.md",
            "PROJECT_RULES.txt",
            ".env.example",
            "requirements.txt",
            "scripts/run_reports.ps1",
            "scripts/run_gemini_stage.ps1",
            "scripts/check_gemini.py",
            "tanshin_pipeline/gemini_runtime.py",
            "tanshin_pipeline/openai_runtime.py",
            "prompt_assets/generic_report_blueprint_ja.md",
        ):
            with self.subTest(path=relative_path):
                self.assertTrue((REPOSITORY_ROOT / relative_path).is_file())


if __name__ == "__main__":
    unittest.main()
