from __future__ import annotations

import io
import os
import socket
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tanshin_api.gemini import get_gemini_client, get_gemini_model
from tanshin_pipeline.cli import main
from tanshin_pipeline.config import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_TRANSLATION_MODEL,
    OPENAI_PDF_TOKENS_PER_PAGE,
    OPENAI_SOL_MODEL,
    PRO_GEMINI_MODEL,
    model_price_for_input_tokens,
)
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.persistence import write_json
from tanshin_pipeline.pipeline import (
    PipelineConfigurationError,
    StageRoute,
    prepare_analysis,
    prepare_translation,
    _validate_inline_pdf_limits,
    _write_translation_cost,
)
from tanshin_pipeline.request_builder import build_analysis_spec
from tanshin_pipeline.schemas import (
    CostEstimate,
    CostStage,
    materialize_japanese_analysis,
    parse_japanese_analysis_payload,
)
from tanshin_pipeline.selection import select_filings
from tests.helpers import workspace_temp_directory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ModelProfileTests(unittest.TestCase):
    def test_model_names_are_fixed_and_ignore_legacy_environment_values(
        self,
    ) -> None:
        with (
            patch(
                "tanshin_api.gemini.load_repository_environment",
                side_effect=AssertionError(
                    "Model selection must not read .env."
                ),
            ),
            patch.dict(
                os.environ,
                {
                    "GEMINI_MODEL": "legacy-analysis-override",
                    "GEMINI_MODEL2": "legacy-pro-override",
                },
                clear=False,
            ),
        ):
            self.assertEqual(
                get_gemini_model("default", "analysis"),
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                get_gemini_model("default", "translation"),
                DEFAULT_TRANSLATION_MODEL,
            )
            self.assertEqual(
                get_gemini_model("pro", "analysis"),
                PRO_GEMINI_MODEL,
            )
            self.assertEqual(
                get_gemini_model("pro", "translation"),
                PRO_GEMINI_MODEL,
            )
            self.assertEqual(
                get_gemini_model("key2-translation", "translation"),
                DEFAULT_ANALYSIS_MODEL,
            )

    def test_default_dry_run_uses_primary_flash_for_both_stages(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            stdout = io.StringIO()
            with (
                patch.object(
                    socket,
                    "socket",
                    side_effect=AssertionError(
                        "Network access attempted in default dry-run."
                    ),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "1808",
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                        "--output-root",
                        str(output_root),
                    ]
                )
            self.assertEqual(exit_code, 0)
            artifacts = output_root / "1808" / "artifacts"
            analysis_plan = read_json(
                artifacts / "request_plan_analysis.json"
            )
            translation_plan = read_json(
                artifacts / "request_plan_translation.json"
            )
            metadata = read_json(artifacts / "run_metadata.json")
            self.assertEqual(analysis_plan["model_profile"], "default")
            self.assertEqual(analysis_plan["provider_profile"], "default")
            self.assertEqual(analysis_plan["model"], DEFAULT_ANALYSIS_MODEL)
            self.assertEqual(translation_plan["model_profile"], "default")
            self.assertEqual(translation_plan["provider_profile"], "default")
            self.assertEqual(
                translation_plan["model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                metadata["translation_model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertIn(
                "eligible for the Gemini free tier",
                stdout.getvalue(),
            )

    def test_default_client_uses_primary_key_for_both_stages(self) -> None:
        fake_client = object()
        with (
            patch("tanshin_api.gemini.load_repository_environment"),
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "offline-test-primary-key",
                    "GEMINI_API_KEY2": "must-not-be-selected",
                },
                clear=False,
            ),
            patch(
                "tanshin_api.gemini.genai.Client",
                return_value=fake_client,
            ) as client_constructor,
        ):
            client = get_gemini_client("default")
        self.assertIs(client, fake_client)
        client_constructor.assert_called_once_with(
            api_key="offline-test-primary-key"
        )

    def test_pro_client_uses_secondary_key_without_logging_it(self) -> None:
        fake_client = object()
        with (
            patch("tanshin_api.gemini.load_repository_environment"),
            patch.dict(
                os.environ,
                {"GEMINI_API_KEY2": "offline-test-secondary-key"},
                clear=False,
            ),
            patch(
                "tanshin_api.gemini.genai.Client",
                return_value=fake_client,
            ) as client_constructor,
        ):
            client = get_gemini_client("pro")
        self.assertIs(client, fake_client)
        client_constructor.assert_called_once_with(
            api_key="offline-test-secondary-key"
        )

    def test_key2_translation_client_uses_secondary_key(self) -> None:
        fake_client = object()
        with (
            patch("tanshin_api.gemini.load_repository_environment"),
            patch.dict(
                os.environ,
                {"GEMINI_API_KEY2": "offline-test-secondary-flash-key"},
                clear=False,
            ),
            patch(
                "tanshin_api.gemini.genai.Client",
                return_value=fake_client,
            ) as client_constructor,
        ):
            client = get_gemini_client("key2-translation")
        self.assertIs(client, fake_client)
        client_constructor.assert_called_once_with(
            api_key="offline-test-secondary-flash-key"
        )

    def test_pro_dry_run_uses_pro_for_both_stages_without_network(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            stdout = io.StringIO()
            with (
                patch.dict(
                    os.environ,
                    {
                        "GEMINI_MODEL": "legacy-analysis-override",
                        "GEMINI_MODEL2": "legacy-pro-override",
                    },
                    clear=False,
                ),
                patch.object(
                    socket,
                    "socket",
                    side_effect=AssertionError(
                        "Network access attempted in Pro dry-run."
                    ),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "1808",
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                        "--output-root",
                        str(output_root),
                        "--gemini-profile",
                        "pro",
                    ]
                )
            self.assertEqual(exit_code, 0)
            artifacts = output_root / "1808" / "artifacts"
            analysis_plan = read_json(
                artifacts / "request_plan_analysis.json"
            )
            translation_plan = read_json(
                artifacts / "request_plan_translation.json"
            )
            cost = read_json(artifacts / "cost.json")
            metadata = read_json(artifacts / "run_metadata.json")
            self.assertEqual(analysis_plan["model_profile"], "pro")
            self.assertEqual(analysis_plan["provider"], "gemini")
            self.assertEqual(analysis_plan["provider_profile"], "pro")
            self.assertEqual(analysis_plan["model"], PRO_GEMINI_MODEL)
            self.assertEqual(translation_plan["model_profile"], "pro")
            self.assertEqual(translation_plan["model"], PRO_GEMINI_MODEL)
            self.assertEqual(cost["analysis"]["model"], PRO_GEMINI_MODEL)
            self.assertEqual(cost["translation"]["model"], PRO_GEMINI_MODEL)
            self.assertEqual(metadata["model_profile"], "pro")
            self.assertEqual(metadata["analysis_model"], PRO_GEMINI_MODEL)
            self.assertEqual(metadata["translation_model"], PRO_GEMINI_MODEL)
            self.assertIn("Model profile: pro", stdout.getvalue())
            self.assertIn("API provider: gemini", stdout.getvalue())
            self.assertIn("no API request was sent", stdout.getvalue())

    def test_pro_translation_uses_flash_analysis_and_pro_translation_offline(
        self,
    ) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            stdout = io.StringIO()
            with (
                patch.object(
                    socket,
                    "socket",
                    side_effect=AssertionError(
                        "Network access attempted in Pro-translation dry-run."
                    ),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "1808",
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                        "--output-root",
                        str(output_root),
                        "--model-profile",
                        "pro-translation",
                    ]
                )
            self.assertEqual(exit_code, 0)
            artifacts = output_root / "1808" / "artifacts"
            analysis_plan = read_json(
                artifacts / "request_plan_analysis.json"
            )
            translation_plan = read_json(
                artifacts / "request_plan_translation.json"
            )
            cost = read_json(artifacts / "cost.json")
            metadata = read_json(artifacts / "run_metadata.json")
            self.assertEqual(
                analysis_plan["model_profile"],
                "pro-translation",
            )
            self.assertEqual(analysis_plan["provider"], "gemini")
            self.assertEqual(analysis_plan["provider_profile"], "default")
            self.assertEqual(
                analysis_plan["model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                translation_plan["model_profile"],
                "pro-translation",
            )
            self.assertEqual(translation_plan["provider"], "gemini")
            self.assertEqual(translation_plan["provider_profile"], "pro")
            self.assertEqual(
                translation_plan["model"],
                PRO_GEMINI_MODEL,
            )
            self.assertEqual(
                cost["analysis"]["model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                cost["translation"]["model"],
                PRO_GEMINI_MODEL,
            )
            self.assertEqual(
                metadata["model_profile"],
                "pro-translation",
            )
            self.assertEqual(
                metadata["analysis_model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                metadata["translation_model"],
                PRO_GEMINI_MODEL,
            )
            self.assertIn(
                "Model profile: pro-translation",
                stdout.getvalue(),
            )
            self.assertIn("no API request was sent", stdout.getvalue())

    def test_key2_translation_uses_primary_flash_then_secondary_flash_offline(
        self,
    ) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            stdout = io.StringIO()
            with (
                patch.object(
                    socket,
                    "socket",
                    side_effect=AssertionError(
                        "Network access attempted in key2-translation dry-run."
                    ),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "1808",
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                        "--output-root",
                        str(output_root),
                        "--model-profile",
                        "key2-translation",
                    ]
                )
            self.assertEqual(exit_code, 0)
            artifacts = output_root / "1808" / "artifacts"
            analysis_plan = read_json(
                artifacts / "request_plan_analysis.json"
            )
            translation_plan = read_json(
                artifacts / "request_plan_translation.json"
            )
            cost = read_json(artifacts / "cost.json")
            metadata = read_json(artifacts / "run_metadata.json")
            self.assertEqual(
                analysis_plan["model_profile"],
                "key2-translation",
            )
            self.assertEqual(analysis_plan["provider_profile"], "default")
            self.assertEqual(
                analysis_plan["model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                translation_plan["model_profile"],
                "key2-translation",
            )
            self.assertEqual(
                translation_plan["provider_profile"],
                "key2-translation",
            )
            self.assertEqual(
                translation_plan["model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                cost["analysis"]["model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                cost["translation"]["model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                metadata["model_profile"],
                "key2-translation",
            )
            self.assertEqual(
                metadata["analysis_model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertEqual(
                metadata["translation_model"],
                DEFAULT_ANALYSIS_MODEL,
            )
            self.assertIn(
                "Model profile: key2-translation",
                stdout.getvalue(),
            )
            self.assertIn("no API request was sent", stdout.getvalue())

    def test_sol_dry_run_routes_analysis_to_openai_and_translation_to_pro(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            stdout = io.StringIO()
            with (
                patch.object(
                    socket,
                    "socket",
                    side_effect=AssertionError(
                        "Network access attempted in Sol dry-run."
                    ),
                ),
                redirect_stdout(stdout),
            ):
                exit_code = main(
                    [
                        "1878",
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                        "--output-root",
                        str(output_root),
                        "--model-profile",
                        "sol",
                    ]
                )
            self.assertEqual(exit_code, 0)
            artifacts = output_root / "1878" / "artifacts"
            analysis_plan = read_json(
                artifacts / "request_plan_analysis.json"
            )
            translation_plan = read_json(
                artifacts / "request_plan_translation.json"
            )
            cost = read_json(artifacts / "cost.json")
            metadata = read_json(artifacts / "run_metadata.json")
            self.assertEqual(analysis_plan["model_profile"], "sol")
            self.assertEqual(analysis_plan["provider"], "openai")
            self.assertIsNone(analysis_plan["provider_profile"])
            self.assertEqual(analysis_plan["model"], OPENAI_SOL_MODEL)
            self.assertEqual(
                analysis_plan["request_options"]["pdf_detail"],
                "low",
            )
            self.assertEqual(translation_plan["provider"], "gemini")
            self.assertEqual(translation_plan["provider_profile"], "pro")
            self.assertEqual(translation_plan["model"], PRO_GEMINI_MODEL)
            self.assertEqual(cost["analysis"]["model"], OPENAI_SOL_MODEL)
            self.assertEqual(cost["translation"]["model"], PRO_GEMINI_MODEL)
            self.assertEqual(
                cost["pdf_tokens_per_page"],
                OPENAI_PDF_TOKENS_PER_PAGE,
            )
            self.assertEqual(metadata["model_profile"], "sol")
            self.assertEqual(metadata["analysis_provider"], "openai")
            self.assertEqual(metadata["translation_provider"], "gemini")
            self.assertNotIn("OPENAI_API_KEY", stdout.getvalue())
            self.assertIn("API provider: openai", stdout.getvalue())
            self.assertIn("PDF detail: low", stdout.getvalue())

    def test_pro_pricing_switches_above_200k_prompt_tokens(self) -> None:
        standard = model_price_for_input_tokens(PRO_GEMINI_MODEL, 200_000)
        long_context = model_price_for_input_tokens(
            PRO_GEMINI_MODEL,
            200_001,
        )
        self.assertEqual(standard.input_per_million, 2.0)
        self.assertEqual(standard.output_per_million, 12.0)
        self.assertEqual(long_context.input_per_million, 4.0)
        self.assertEqual(long_context.output_per_million, 18.0)

    def test_sol_pricing_switches_above_272k_input_tokens(self) -> None:
        standard = model_price_for_input_tokens(OPENAI_SOL_MODEL, 272_000)
        long_context = model_price_for_input_tokens(
            OPENAI_SOL_MODEL,
            272_001,
        )
        self.assertEqual(standard.input_per_million, 5.0)
        self.assertEqual(standard.output_per_million, 30.0)
        self.assertEqual(long_context.input_per_million, 10.0)
        self.assertEqual(long_context.output_per_million, 45.0)

    def test_pro_profile_has_a_distinct_inspected_request_id(self) -> None:
        manifest = select_filings(REPOSITORY_ROOT, "1808")
        default_spec = build_analysis_spec(REPOSITORY_ROOT, manifest)
        pro_spec = build_analysis_spec(
            REPOSITORY_ROOT,
            manifest,
            model=PRO_GEMINI_MODEL,
            model_profile="pro",
            provider_profile="pro",
        )
        self.assertNotEqual(
            default_spec.plan().request_id,
            pro_spec.plan().request_id,
        )

    def test_sol_provider_has_a_distinct_inspected_request_id(self) -> None:
        manifest = select_filings(REPOSITORY_ROOT, "1808")
        default_spec = build_analysis_spec(REPOSITORY_ROOT, manifest)
        sol_spec = build_analysis_spec(
            REPOSITORY_ROOT,
            manifest,
            model=OPENAI_SOL_MODEL,
            model_profile="sol",
            provider="openai",
            provider_profile=None,
        )
        self.assertNotEqual(
            default_spec.plan().request_id,
            sol_spec.plan().request_id,
        )

    def test_sol_rejects_combined_inline_pdfs_at_50_mib(self) -> None:
        manifest = select_filings(REPOSITORY_ROOT, "1808").model_copy(
            update={"total_selected_bytes": 50_000_000}
        )
        with self.assertRaisesRegex(
            PipelineConfigurationError,
            "combined selected PDF payload",
        ):
            _validate_inline_pdf_limits(
                manifest,
                StageRoute(
                    provider="openai",
                    model=OPENAI_SOL_MODEL,
                    provider_profile=None,
                ),
            )

    def test_sol_translation_request_uses_gemini_pro(self) -> None:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "real_1808_analysis_ja.json"
        )
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            analysis_run = prepare_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
                model_profile="sol",
            )
            analysis = materialize_japanese_analysis(
                parse_japanese_analysis_payload(read_json(fixture))
            )
            write_json(analysis_run.paths.analysis_normalized, analysis)
            translation_run = prepare_translation(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
                model_profile="sol",
            )
        self.assertEqual(translation_run.plan.model_profile, "sol")
        self.assertEqual(translation_run.plan.provider, "gemini")
        self.assertEqual(translation_run.plan.provider_profile, "pro")
        self.assertEqual(translation_run.plan.model, PRO_GEMINI_MODEL)

    def test_translation_preparation_preserves_actual_analysis_cost(self) -> None:
        stage = CostStage(
            model=PRO_GEMINI_MODEL,
            estimated_input_tokens=100,
            maximum_output_tokens=10,
            input_cost_usd=0.01,
            maximum_output_cost_usd=0.02,
            maximum_stage_cost_usd=0.03,
            input_cost_jpy=1.5,
            maximum_output_cost_jpy=3.0,
            maximum_stage_cost_jpy=4.5,
        )
        estimate = CostEstimate(
            currency="USD",
            display_currency="JPY",
            usd_to_jpy_rate=150,
            pdf_tokens_per_page=540,
            analysis=stage,
            translation=stage,
            maximum_one_pass_cost_usd=0.06,
            maximum_configured_cost_usd=0.06,
            maximum_one_pass_cost_jpy=9.0,
            maximum_configured_cost_jpy=9.0,
            maximum_api_attempts_per_stage=1,
            assumptions=[],
        )
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            path = temp / "cost.json"
            write_json(
                path,
                {
                    "actual_cost_by_stage_usd": {
                        "analysis": {"cost_usd": 0.123, "cost_jpy": 18.45}
                    },
                    "actual_cost_total_usd": 0.123,
                    "actual_cost_total_jpy": 18.45,
                },
            )
            _write_translation_cost(path, estimate)
            payload = read_json(path)
        self.assertIn("analysis", payload["actual_cost_by_stage_usd"])
        self.assertEqual(payload["actual_cost_total_usd"], 0.123)
        self.assertEqual(payload["actual_cost_total_jpy"], 18.45)


if __name__ == "__main__":
    unittest.main()
