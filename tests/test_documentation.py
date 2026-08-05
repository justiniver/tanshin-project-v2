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
        self.assertIn("75-second cooldown between analysis requests", readme)
        self.assertIn("counting any time", readme)
        self.assertIn("spent translating toward that interval", readme)
        self.assertIn("Companies are processed in the order supplied", readme)
        self.assertIn("1808` analysis, `1808` translation", readme)
        self.assertIn("A stage failure stops the workflow", readme)
        self.assertIn("analysis_ja_{security_code}.md", readme)
        self.assertIn("analysis_en_{security_code}.md", readme)
        self.assertIn("does not maintain a separate draft report", readme)
        self.assertIn(
            "mode: model_rendered_english_financial_notation",
            readme,
        )
        self.assertIn("financial presentation are English", readme)
        self.assertIn("¥293.2 billion", readme)
        self.assertIn("¥150 per USD", readme)
        self.assertIn("original Japanese", readme)
        self.assertIn("quotations. Gemini is not asked", readme)
        self.assertIn("otherwise the Japanese name is", readme)
        self.assertIn("retained rather than translated by meaning", readme)
        self.assertIn("arithmetic mean of the available subscores", readme)
        self.assertIn(
            r".\scripts\run_reports.ps1 1878 --pro",
            readme,
        )
        self.assertIn(
            r".\scripts\run_reports.ps1 1878 --flash-translation",
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
        self.assertIn("Gemini Pro for optional English", readme)
        self.assertIn(
            "Model names are fixed in `tanshin_pipeline/config.py`",
            readme,
        )
        self.assertIn("`.env` stores credentials only", readme)
        self.assertIn(
            "`--pro-translation` keeps primary-key Flash analysis",
            readme,
        )
        self.assertNotIn("GEMINI_MODEL", readme)
        self.assertIn("[COMMANDS.md](COMMANDS.md)", readme)
        self.assertIn("company overview", readme)
        self.assertIn("through 200,000 prompt tokens", readme)

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
            "Never read, display, log, copy, or modify `.env`",
            "Never manually edit Markdown under `output/` or `exemplar_output/`",
            "Keep every implementation company-agnostic",
            "TANSHIN_OFFLINE_ONLY",
            "TANSHIN_TESTING",
            "run_reports.ps1 1808 6361 -PreviewOnly",
            "run_reports.ps1 1878 --flash-translation -PreviewOnly",
            "run_reports.ps1 1878 --pro-translation -PreviewOnly",
            "run_reports.ps1 1878 --pro -PreviewOnly",
            "run_reports.ps1 1878 --sol -PreviewOnly",
        ):
            self.assertIn(required, guide)
        self.assertIn(
            "Model names are fixed in `tanshin_pipeline/config.py`",
            guide,
        )
        self.assertNotIn("GEMINI_MODEL", guide)

    def test_concise_command_reference_lists_every_model_profile(self) -> None:
        commands = (REPOSITORY_ROOT / "COMMANDS.md").read_text(
            encoding="utf-8"
        )
        for required in (
            r".\scripts\run_reports.ps1 1808",
            "--flash-translation",
            "--pro-translation",
            "--pro",
            "--sol",
            "`gemini-3.6-flash`, primary key",
            "`gemini-3.5-flash-lite`, primary key",
            "`gemini-3.6-flash`, secondary key",
            "`gemini-3.1-pro-preview`, secondary key",
            "`gpt-5.6-sol`, OpenAI key",
            "`GEMINI_API_KEY`, `GEMINI_API_KEY2`, and `OPENAI_API_KEY`",
            "-PreviewOnly",
            "multiple tickers",
        ):
            self.assertIn(required, commands)

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
