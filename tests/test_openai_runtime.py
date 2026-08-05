from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from openai import BadRequestError, OpenAI
from pydantic import BaseModel

from tanshin_api.openai_client import get_openai_client
from tanshin_pipeline.config import OPENAI_SOL_MODEL
from tanshin_pipeline.gemini_runtime import LiveApiSafetyError
from tanshin_pipeline.openai_runtime import OpenAIResponseError, execute_request
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.request_builder import build_analysis_spec, sha256_json
from tanshin_pipeline.schemas import JapaneseAnalysis, JapaneseModelResponse
from tanshin_pipeline.selection import select_filings


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "real_1808_analysis_ja.json"
)


class _Dumpable:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, **_kwargs):
        return self.payload


class _FakeResponse:
    def __init__(self, parsed, *, status: str = "completed"):
        self.output_parsed = parsed
        self.status = status
        self.incomplete_details = None
        self.usage = _Dumpable(
            {
                "input_tokens": 1_000,
                "output_tokens": 300,
                "output_tokens_details": {"reasoning_tokens": 80},
            }
        )
        self.model = OPENAI_SOL_MODEL
        self.id = "resp_offline_fake"

    def model_dump(self, **_kwargs):
        return {
            "id": self.id,
            "model": self.model,
            "status": self.status,
            "usage": self.usage.model_dump(),
        }


class _FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.responses = _FakeResponses(response)
        self.closed = False

    def close(self):
        self.closed = True


class OpenAIRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        manifest = select_filings(REPOSITORY_ROOT, "1808")
        self.spec = build_analysis_spec(
            REPOSITORY_ROOT,
            manifest,
            model=OPENAI_SOL_MODEL,
            model_profile="sol",
            provider="openai",
            provider_profile=None,
        )
        payload = read_json(FIXTURE)
        try:
            self.parsed = JapaneseModelResponse.model_validate(payload)
        except Exception:
            self.parsed = JapaneseAnalysis.model_validate(payload)

    def test_request_uses_inline_pdfs_and_native_pydantic_parsing(self) -> None:
        fake = _FakeClient(_FakeResponse(self.parsed))
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
                confirmed_request_id=self.spec.plan().request_id,
                client_factory=lambda: fake,
                configured_model_getter=lambda: OPENAI_SOL_MODEL,
            )

        self.assertIsInstance(result.structured, BaseModel)
        self.assertEqual(len(fake.responses.calls), 1)
        call = fake.responses.calls[0]
        self.assertEqual(call["model"], OPENAI_SOL_MODEL)
        self.assertEqual(call["instructions"], self.spec.system_prompt)
        self.assertIs(call["text_format"], JapaneseModelResponse)
        self.assertEqual(call["reasoning"], {"effort": "medium"})
        self.assertEqual(call["text"], {"verbosity": "high"})
        self.assertNotIn("verbosity", call)
        self.assertFalse(call["store"])
        content = call["input"][0]["content"]
        self.assertEqual(
            len(content),
            len(self.spec.files) * 2 + 2,
        )
        self.assertEqual(content[0]["type"], "input_text")
        self.assertEqual(content[1]["type"], "input_text")
        self.assertEqual(content[2]["type"], "input_file")
        self.assertEqual(
            content[2]["filename"],
            self.spec.files[0].filename,
        )
        self.assertEqual(content[2]["detail"], "low")
        self.assertTrue(
            content[2]["file_data"].startswith(
                "data:application/pdf;base64,"
            )
        )
        self.assertNotIn("file_id", content[2])
        self.assertTrue(fake.closed)
        self.assertEqual(result.usage["prompt_token_count"], 1_000)
        self.assertEqual(result.usage["candidates_token_count"], 220)
        self.assertEqual(result.usage["thoughts_token_count"], 80)

    def test_official_sdk_serializes_verbosity_inside_text(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                400,
                request=request,
                json={
                    "error": {
                        "message": "offline serialization stop",
                        "type": "invalid_request_error",
                        "param": None,
                        "code": "offline_test",
                    }
                },
            )

        client = OpenAI(
            api_key="offline-test-key",
            base_url="https://offline.invalid/v1",
            max_retries=0,
            http_client=httpx.Client(
                transport=httpx.MockTransport(handler)
            ),
        )
        spec = replace(self.spec, files=())
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            with self.assertRaises(BadRequestError):
                execute_request(
                    REPOSITORY_ROOT,
                    spec,
                    confirmed_request_id=spec.plan().request_id,
                    client_factory=lambda: client,
                    configured_model_getter=lambda: OPENAI_SOL_MODEL,
                )

        body = captured["body"]
        self.assertIsInstance(body, dict)
        assert isinstance(body, dict)
        self.assertNotIn("verbosity", body)
        self.assertEqual(body["text"]["verbosity"], "high")
        self.assertEqual(body["text"]["format"]["type"], "json_schema")

    def test_strict_schema_is_the_inspected_schema(self) -> None:
        schema = self.spec.response_schema
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("model_notes", schema["required"])
        self.assertEqual(
            self.spec.plan().request_options,
            {
                "reasoning_effort": "medium",
                "text_verbosity": "high",
                "pdf_detail": "low",
                "store": False,
            },
        )
        self.assertEqual(
            self.spec.plan().response_schema_sha256,
            sha256_json(schema),
        )

    def test_wrong_request_id_blocks_before_client_call(self) -> None:
        fake = _FakeClient(_FakeResponse(self.parsed))
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
                    configured_model_getter=lambda: OPENAI_SOL_MODEL,
                )
        self.assertEqual(fake.responses.calls, [])

    def test_tests_cannot_use_configured_openai_client(self) -> None:
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
                    configured_model_getter=lambda: OPENAI_SOL_MODEL,
                )

    def test_incomplete_response_is_rejected(self) -> None:
        fake = _FakeClient(_FakeResponse(self.parsed, status="incomplete"))
        with patch.dict(
            os.environ,
            {
                "TANSHIN_LIVE_API": "MANUAL_USER_RUN",
                "TANSHIN_TESTING": "1",
                "TANSHIN_OFFLINE_ONLY": "0",
            },
            clear=False,
        ):
            with self.assertRaises(OpenAIResponseError):
                execute_request(
                    REPOSITORY_ROOT,
                    self.spec,
                    confirmed_request_id=self.spec.plan().request_id,
                    client_factory=lambda: fake,
                    configured_model_getter=lambda: OPENAI_SOL_MODEL,
                )
        self.assertTrue(fake.closed)

    def test_openai_client_uses_key_without_logging_it(self) -> None:
        fake_client = object()
        with (
            patch("tanshin_api.openai_client.load_repository_environment"),
            patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "offline-test-openai-key"},
                clear=False,
            ),
            patch(
                "tanshin_api.openai_client.OpenAI",
                return_value=fake_client,
            ) as constructor,
        ):
            client = get_openai_client()
        self.assertIs(client, fake_client)
        constructor.assert_called_once_with(
            api_key="offline-test-openai-key"
        )


if __name__ == "__main__":
    unittest.main()
