from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from google.genai import types

from tanshin_pipeline.config import PRO_GEMINI_MODEL
from tanshin_pipeline.gemini_runtime import (
    GeminiResponseError,
    LiveApiSafetyError,
    call_with_retries,
    execute_request,
)
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.request_builder import (
    build_analysis_spec,
    build_translation_spec,
)
from tanshin_pipeline.schemas import (
    EnglishTranslationPatch,
    JapaneseAnalysis,
    materialize_japanese_analysis,
    parse_japanese_analysis_payload,
)
from tanshin_pipeline.selection import select_filings


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _translation_patch_payload(payload: dict) -> dict:
    return {
        "claims": [
            {
                "claim_id": claim["claim_id"],
                "headline_en": claim["headline_en"],
                "body_en": claim["body_en"],
                "figures": [
                    {
                        "value_id": span["value_id"],
                        "claim_surface_en": span["claim_surface_en"],
                    }
                    for span in claim.get("figures", [])
                ],
                "dates": [
                    {
                        "value_id": span["value_id"],
                        "claim_surface_en": span["claim_surface_en"],
                    }
                    for span in claim.get("dates", [])
                ],
                "qualifiers": [
                    {
                        "value_id": span["value_id"],
                        "claim_surface_en": span["claim_surface_en"],
                    }
                    for span in claim.get("qualifiers", [])
                ],
            }
            for claim in payload["claims"]
        ],
        "model_notes": payload.get("model_notes", []),
    }


class _Dumpable:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **_kwargs):
        return self.payload


class _FakeResponse:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = json.dumps(parsed, ensure_ascii=False)
        self.candidates = [
            type("Candidate", (), {"finish_reason": "STOP"})()
        ]
        self.usage_metadata = _Dumpable(
            {"prompt_token_count": 123, "candidates_token_count": 45}
        )
        self.model_version = "fake-model-version"
        self.response_id = "fake-response-id"

    def model_dump(self, **_kwargs):
        return {
            "model_version": self.model_version,
            "response_id": self.response_id,
            "usage_metadata": self.usage_metadata.model_dump(),
        }


class _FakeModels:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)
        self.closed = False

    def close(self):
        self.closed = True


class RuntimeMockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = select_filings(REPOSITORY_ROOT, "1808")
        self.spec = build_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
        )
        self.analysis_payload = read_json(FIXTURES / "fake_analysis_ja.json")
        self.translation_payload = _translation_patch_payload(
            read_json(FIXTURES / "fake_translation_en.json")
        )

    def test_request_construction_and_response_parsing_with_fake_client(self) -> None:
        fake = _FakeClient(_FakeResponse(self.analysis_payload))
        request_id = self.spec.plan().request_id
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            result = execute_request(
                REPOSITORY_ROOT,
                self.spec,
                confirmed_request_id=request_id,
                client_factory=lambda: fake,
                configured_model_getter=lambda: self.spec.model,
            )
        self.assertIsInstance(result.structured, JapaneseAnalysis)
        self.assertEqual(len(fake.models.calls), 1)
        call = fake.models.calls[0]
        self.assertEqual(call["model"], "gemini-3.6-flash")
        self.assertEqual(len(call["contents"][0].parts), len(self.spec.files) * 2 + 2)
        context = call["contents"][0].parts[0].text
        self.assertIn("<document_manifest>", context)
        self.assertIn("<report_blueprint>", context)
        first_metadata = call["contents"][0].parts[1].text
        self.assertIn("<DOCUMENT_METADATA>", first_metadata)
        self.assertIn(
            f"<source_filename>{self.spec.files[0].filename}</source_filename>",
            first_metadata,
        )
        self.assertIn("<physical_pdf_pages>", first_metadata)
        self.assertEqual(
            call["config"].response_mime_type,
            "application/json",
        )
        self.assertEqual(
            call["config"].response_json_schema,
            self.spec.response_schema,
        )
        self.assertTrue(
            call["contents"][0].parts[-1].text.rstrip().endswith(
                "</analysis_task>"
            )
        )
        self.assertTrue(fake.closed)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.finish_reason, "STOP")

    def test_retry_helper(self) -> None:
        state = {"calls": 0}

        def operation():
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("transient")
            return "ok"

        value, attempts = call_with_retries(operation, max_attempts=2)
        self.assertEqual(value, "ok")
        self.assertEqual(attempts, 2)

    def test_retry_failure_is_propagated(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "failed"):
            call_with_retries(
                lambda: (_ for _ in ()).throw(RuntimeError("failed")),
                max_attempts=2,
            )

    def test_safety_guard_rejects_wrong_request_id(self) -> None:
        fake = _FakeClient(_FakeResponse(self.analysis_payload))
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            with self.assertRaises(LiveApiSafetyError):
                execute_request(
                    REPOSITORY_ROOT,
                    self.spec,
                    confirmed_request_id="wrong",
                    client_factory=lambda: fake,
                    configured_model_getter=lambda: self.spec.model,
                )
        self.assertEqual(len(fake.models.calls), 0)

    def test_tests_cannot_use_default_live_client(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(LiveApiSafetyError, "fake client"):
                execute_request(
                    REPOSITORY_ROOT,
                    self.spec,
                    confirmed_request_id=self.spec.plan().request_id,
                    configured_model_getter=lambda: self.spec.model,
                )

    def test_invalid_response_fails_without_retrying_by_default(self) -> None:
        class EmptyResponse:
            parsed = None
            text = None
            usage_metadata = None
            model_version = None
            response_id = None

            def model_dump(self, **_kwargs):
                return {}

        fake = _FakeClient(EmptyResponse())
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            with self.assertRaises(GeminiResponseError):
                execute_request(
                    REPOSITORY_ROOT,
                    self.spec,
                    confirmed_request_id=self.spec.plan().request_id,
                    client_factory=lambda: fake,
                    configured_model_getter=lambda: self.spec.model,
                )
        self.assertEqual(len(fake.models.calls), 1)

    def test_invalid_json_error_retains_raw_provider_response(self) -> None:
        class InvalidJsonResponse:
            parsed = None
            text = '{"claims": ['
            usage_metadata = None
            model_version = "fake-model-version"
            response_id = "failed-response-id"

            def model_dump(self, **_kwargs):
                return {
                    "response_id": self.response_id,
                    "model_version": self.model_version,
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [{"text": self.text}],
                            },
                            "finish_reason": "STOP",
                        }
                    ],
                }

        fake = _FakeClient(InvalidJsonResponse())
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            with self.assertRaises(GeminiResponseError) as raised:
                execute_request(
                    REPOSITORY_ROOT,
                    self.spec,
                    confirmed_request_id=self.spec.plan().request_id,
                    client_factory=lambda: fake,
                    configured_model_getter=lambda: self.spec.model,
                )
        self.assertEqual(
            raised.exception.raw_response["response_id"],
            "failed-response-id",
        )
        self.assertEqual(
            raised.exception.raw_response["candidates"][0]["content"]["parts"][0][
                "text"
            ],
            '{"claims": [',
        )

    def test_pro_translation_uses_low_thinking_and_selected_model(self) -> None:
        analysis = materialize_japanese_analysis(
            parse_japanese_analysis_payload(self.analysis_payload)
        )
        spec = build_translation_spec(
            self.manifest,
            analysis,
            model=PRO_GEMINI_MODEL,
            model_profile="pro",
            provider_profile="pro",
        )
        fake = _FakeClient(_FakeResponse(self.translation_payload))
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            result = execute_request(
                REPOSITORY_ROOT,
                spec,
                confirmed_request_id=spec.plan().request_id,
                client_factory=lambda: fake,
                configured_model_getter=lambda: PRO_GEMINI_MODEL,
            )
        self.assertIsInstance(result.structured, EnglishTranslationPatch)
        self.assertEqual(len(fake.models.calls), 1)
        call = fake.models.calls[0]
        self.assertEqual(call["model"], PRO_GEMINI_MODEL)
        claim_schema = call["config"].response_json_schema["$defs"][
            "TranslatedClaimPatch"
        ]["properties"]
        self.assertNotIn("section", claim_schema)
        self.assertNotIn("evidence_ids", claim_schema)
        self.assertNotIn("statement_type", claim_schema)
        self.assertEqual(
            call["config"].thinking_config.thinking_level,
            types.ThinkingLevel.LOW,
        )

    def test_translation_model_mismatch_is_blocked_before_request(self) -> None:
        analysis = materialize_japanese_analysis(
            parse_japanese_analysis_payload(self.analysis_payload)
        )
        spec = build_translation_spec(
            self.manifest,
            analysis,
            model=PRO_GEMINI_MODEL,
            model_profile="pro",
            provider_profile="pro",
        )
        fake = _FakeClient(_FakeResponse(self.translation_payload))
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                LiveApiSafetyError,
                "Configured translation model",
            ):
                execute_request(
                    REPOSITORY_ROOT,
                    spec,
                    confirmed_request_id=spec.plan().request_id,
                    client_factory=lambda: fake,
                    configured_model_getter=lambda: "wrong-model",
                )
        self.assertEqual(len(fake.models.calls), 0)


if __name__ == "__main__":
    unittest.main()
