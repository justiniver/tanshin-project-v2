"""The only module allowed to materialize and send Gemini requests.

Dry-run code never imports this module. Each public execution function performs
exactly one model request unless the caller explicitly configures more attempts.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable, TypeVar

from google.genai import types
from pydantic import BaseModel

from tanshin_api.gemini import get_gemini_client, get_gemini_model

from .config import LIVE_CONFIRMATION_VALUE, PRO_GEMINI_MODEL
from .request_builder import RequestSpec
from .schemas import (
    EnglishTranslationPatch,
    JapaneseResearchDossier,
    JapaneseSynthesisResponse,
)


class LiveApiSafetyError(RuntimeError):
    """Raised when a live request is not explicitly authorized."""


class GeminiResponseError(RuntimeError):
    """Raised when a model response cannot be parsed."""

    def __init__(
        self,
        message: str,
        *,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.raw_response = raw_response


T = TypeVar("T")


@dataclass(frozen=True)
class ExecutionResult:
    structured: (
        JapaneseResearchDossier
        | JapaneseSynthesisResponse
        | EnglishTranslationPatch
    )
    raw_response: dict[str, Any]
    usage: dict[str, Any]
    model_version: str | None
    response_id: str | None
    finish_reason: str | None
    attempts: int


def assert_live_execution_authorized(
    *,
    expected_request_id: str,
    confirmed_request_id: str | None,
) -> None:
    if os.getenv("TANSHIN_OFFLINE_ONLY") == "1":
        raise LiveApiSafetyError("TANSHIN_OFFLINE_ONLY blocks all live API execution.")
    if os.getenv("TANSHIN_LIVE_API") != LIVE_CONFIRMATION_VALUE:
        raise LiveApiSafetyError(
            "Set TANSHIN_LIVE_API=MANUAL_USER_RUN in the command process."
        )
    if confirmed_request_id != expected_request_id:
        raise LiveApiSafetyError(
            "The --confirm-request value does not match the inspected request plan."
        )


def call_with_retries(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    retry_delay_seconds: float = 0,
) -> tuple[T, int]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one.")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation(), attempt
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
    assert last_error is not None
    raise last_error


def _response_payload(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=True)
    if isinstance(response, dict):
        return response
    return {"repr": repr(response)}


def _structured_payload(response: Any) -> dict[str, Any]:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, BaseModel):
        return parsed.model_dump(mode="json")
    if isinstance(parsed, dict):
        return parsed
    text = getattr(response, "text", None)
    if not text:
        raise GeminiResponseError(
            "Gemini returned no parsed object or response text.",
            raw_response=_response_payload(response),
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        message = (
            "Gemini response reached its output-token limit before completing "
            "valid JSON."
            if _finish_reason(response) == "MAX_TOKENS"
            else "Gemini response text was not valid JSON."
        )
        raise GeminiResponseError(
            message,
            raw_response=_response_payload(response),
        ) from exc
    if not isinstance(payload, dict):
        raise GeminiResponseError(
            "Gemini JSON response must be an object.",
            raw_response=_response_payload(response),
        )
    return payload


def _finish_reason(response: Any) -> str | None:
    candidates = getattr(response, "candidates", None)
    if not candidates:
        return None
    value = getattr(candidates[0], "finish_reason", None)
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    result = str(value)
    return result.rsplit(".", 1)[-1]


def execute_request(
    repository_root: Path,
    spec: RequestSpec,
    *,
    confirmed_request_id: str,
    max_attempts: int = 1,
    client_factory: Callable[[], Any] | None = None,
    configured_model_getter: Callable[[], str] | None = None,
) -> ExecutionResult:
    """Execute one analysis or translation request after all safety checks."""

    if spec.provider != "gemini":
        raise LiveApiSafetyError(
            "The Gemini runtime accepts only Gemini request plans."
        )
    plan = spec.plan()
    assert_live_execution_authorized(
        expected_request_id=plan.request_id,
        confirmed_request_id=confirmed_request_id,
    )
    using_configured_client = client_factory is None
    if os.getenv("TANSHIN_TESTING") == "1" and using_configured_client:
        raise LiveApiSafetyError(
            "Tests must inject a fake client; the configured live client is blocked."
        )
    model_getter = configured_model_getter or (
        lambda: get_gemini_model(
            spec.provider_profile or spec.model_profile,
            spec.stage,
        )
    )
    configured = model_getter()
    if configured != spec.model:
        raise LiveApiSafetyError(
            f"Configured {spec.stage} model {configured!r} differs from inspected "
            f"request model {spec.model!r}."
        )

    parts: list[types.Part] = []
    if spec.context_prompt:
        parts.append(types.Part.from_text(text=spec.context_prompt))
    for file in spec.files:
        path = repository_root / file.relative_path
        data = path.read_bytes()
        parts.append(
            types.Part.from_text(
                text=(
                    "<DOCUMENT_METADATA>\n"
                    f"<source_filename>{escape(file.filename)}</source_filename>\n"
                    f"<physical_pdf_pages>{file.page_count}</physical_pdf_pages>\n"
                    "<content>The immediately following part is this PDF.</content>\n"
                    "</DOCUMENT_METADATA>"
                )
            )
        )
        parts.append(types.Part.from_bytes(data=data, mime_type=file.mime_type))
    parts.append(
        types.Part.from_text(text=spec.task_prompt or spec.prompt)
    )
    contents = [types.Content(role="user", parts=parts)]
    if spec.stage == "research":
        thinking_level = types.ThinkingLevel.LOW
    elif spec.stage == "analysis":
        thinking_level = types.ThinkingLevel.MEDIUM
    elif spec.model == PRO_GEMINI_MODEL:
        thinking_level = types.ThinkingLevel.LOW
    else:
        thinking_level = types.ThinkingLevel.MINIMAL
    config = types.GenerateContentConfig(
        system_instruction=spec.system_prompt,
        response_mime_type="application/json",
        response_json_schema=spec.response_schema,
        max_output_tokens=spec.max_output_tokens,
        thinking_config=types.ThinkingConfig(
            thinking_level=thinking_level,
            include_thoughts=False,
        ),
    )

    client = (
        client_factory()
        if client_factory is not None
        else get_gemini_client(spec.provider_profile or spec.model_profile)
    )
    try:
        response, attempts = call_with_retries(
            lambda: client.models.generate_content(
                model=spec.model,
                contents=contents,
                config=config,
            ),
            max_attempts=max_attempts,
        )
    finally:
        client.close()

    payload = _structured_payload(response)
    if spec.stage == "research":
        structured = JapaneseResearchDossier.model_validate(payload)
    elif spec.stage == "analysis":
        structured = JapaneseSynthesisResponse.model_validate(payload)
    else:
        structured = EnglishTranslationPatch.model_validate(payload)
    usage_obj = getattr(response, "usage_metadata", None)
    usage = (
        usage_obj.model_dump(mode="json", exclude_none=True)
        if hasattr(usage_obj, "model_dump")
        else (usage_obj if isinstance(usage_obj, dict) else {})
    )
    return ExecutionResult(
        structured=structured,
        raw_response=_response_payload(response),
        usage=usage,
        model_version=getattr(response, "model_version", None),
        response_id=getattr(response, "response_id", None),
        finish_reason=_finish_reason(response),
        attempts=attempts,
    )
