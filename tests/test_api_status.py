from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tanshin_pipeline.gemini_runtime import ExecutionResult, GeminiResponseError
from tanshin_pipeline.persistence import read_json, write_text
from tanshin_pipeline.pipeline import (
    execute_analysis,
    prepare_analysis,
)
from tanshin_pipeline.schemas import (
    ValidationIssue,
    ValidationResult,
    parse_japanese_analysis_payload,
)
from tests.helpers import workspace_temp_directory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "real_1808_analysis_ja.json"
)


class ApiStatusTests(unittest.TestCase):
    def test_successful_gemini_response_is_not_blocked_by_validation_diagnostics(self) -> None:
        analysis = parse_japanese_analysis_payload(read_json(FIXTURE))
        result = ExecutionResult(
            structured=analysis,
            raw_response={
                "response_id": "response-123",
                "candidates": [{"finish_reason": "STOP"}],
            },
            usage={
                "prompt_token_count": 100,
                "candidates_token_count": 20,
                "thoughts_token_count": 10,
            },
            model_version="fake-version",
            response_id="response-123",
            finish_reason="STOP",
            attempts=1,
        )
        blocked = ValidationResult(
            valid=False,
            publishable=False,
            factual_integrity_passed=True,
            quality_gate_passed=False,
            blocking_error_count=1,
            warning_count=0,
            language="ja",
            issues=[
                ValidationIssue(
                    severity="error",
                    category="essential_quality",
                    code="trend_perspective_too_short",
                    message="Perspective is too short.",
                )
            ],
            statistics={},
        )
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            prepared = prepare_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            with (
                patch(
                    "tanshin_pipeline.gemini_runtime.execute_request",
                    return_value=result,
                ),
                patch(
                    "tanshin_pipeline.pipeline._process_japanese_response",
                    return_value=(analysis, blocked),
                ),
            ):
                execute_analysis(
                    REPOSITORY_ROOT,
                    "1808",
                    confirmed_request_id=prepared.plan.request_id,
                    output_root=output_root,
                )
            status = read_json(prepared.paths.analysis_api_status)
            self.assertEqual(status["state"], "SUCCESS")
            self.assertTrue(status["api_request_completed"])
            self.assertEqual(status["response_id"], "response-123")
            self.assertEqual(status["finish_reason"], "STOP")
            self.assertEqual(status["attempts"], 1)

    def test_rate_limit_is_recorded_without_exposing_a_key(self) -> None:
        class FakeRateLimitError(RuntimeError):
            status_code = 429

        error = FakeRateLimitError(
            "quota exceeded api_key=AIzaDefinitelyNotARealSecretValue"
        )
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            prepared = prepare_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            with patch(
                "tanshin_pipeline.gemini_runtime.execute_request",
                side_effect=error,
            ):
                with self.assertRaises(FakeRateLimitError):
                    execute_analysis(
                        REPOSITORY_ROOT,
                        "1808",
                        confirmed_request_id=prepared.plan.request_id,
                        output_root=output_root,
                    )
            status = read_json(prepared.paths.analysis_api_status)
            self.assertEqual(status["state"], "RATE_LIMITED")
            self.assertFalse(status["api_request_completed"])
            self.assertEqual(status["status_code"], 429)
            self.assertNotIn("AIza", status["error_summary"])
            self.assertIn("[REDACTED]", status["error_summary"])

    def test_temporary_503_is_recorded_as_manually_retryable(self) -> None:
        class FakeUnavailableError(RuntimeError):
            status_code = 503

        error = FakeUnavailableError(
            "503 UNAVAILABLE: model is currently experiencing high demand"
        )
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            prepared = prepare_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            write_text(prepared.paths.report_ja, "prior final")
            write_text(prepared.paths.report_ja_draft, "prior draft")
            with patch(
                "tanshin_pipeline.gemini_runtime.execute_request",
                side_effect=error,
            ):
                with self.assertRaises(FakeUnavailableError):
                    execute_analysis(
                        REPOSITORY_ROOT,
                        "1808",
                        confirmed_request_id=prepared.plan.request_id,
                        output_root=output_root,
                    )
            status = read_json(prepared.paths.analysis_api_status)
            self.assertEqual(status["state"], "TEMPORARILY_UNAVAILABLE")
            self.assertFalse(status["api_request_completed"])
            self.assertEqual(status["status_code"], 503)
            self.assertTrue(status["retryable"])
            self.assertIn("manually rerun", status["retry_guidance"])
            report_status = read_json(prepared.paths.report_status_ja)
            self.assertEqual(report_status["run_id"], prepared.plan.request_id)
            self.assertEqual(
                report_status["api_state"],
                "TEMPORARILY_UNAVAILABLE",
            )
            self.assertIsNone(report_status["draft_path"])
            self.assertIsNone(report_status["final_path"])
            self.assertFalse(prepared.paths.report_ja.exists())
            self.assertFalse(prepared.paths.report_ja_draft.exists())
            self.assertEqual(
                Path(
                    report_status["previous_final_archived_to"]
                ).read_text(encoding="utf-8"),
                "prior final",
            )
            self.assertEqual(
                Path(
                    report_status["previous_draft_archived_to"]
                ).read_text(encoding="utf-8"),
                "prior draft",
            )

    def test_malformed_gemini_json_preserves_raw_response(self) -> None:
        raw_response = {
            "response_id": "failed-response-123",
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": '{"claims": ['}],
                    },
                    "finish_reason": "STOP",
                }
            ],
        }
        error = GeminiResponseError(
            "Gemini response text was not valid JSON.",
            raw_response=raw_response,
        )
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            prepared = prepare_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            with patch(
                "tanshin_pipeline.gemini_runtime.execute_request",
                side_effect=error,
            ):
                with self.assertRaises(GeminiResponseError):
                    execute_analysis(
                        REPOSITORY_ROOT,
                        "1808",
                        confirmed_request_id=prepared.plan.request_id,
                        output_root=output_root,
                    )
            self.assertEqual(
                read_json(prepared.paths.analysis_raw_response),
                raw_response,
            )
            status = read_json(prepared.paths.analysis_api_status)
            self.assertEqual(status["state"], "FAILED")
            self.assertEqual(status["error_type"], "GeminiResponseError")


if __name__ == "__main__":
    unittest.main()
