"""Explicitly gated OpenAI Responses API runtime for Japanese analysis.

Dry-run code never imports this module. PDFs are sent inline in the single
authorized request; the OpenAI Files API is not used.
"""

from __future__ import annotations

import base64
import os
from html import escape
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

from tanshin_api.openai_client import get_openai_client, get_openai_model

from .config import OPENAI_PDF_DETAIL
from .gemini_runtime import (
    ExecutionResult,
    LiveApiSafetyError,
    assert_live_execution_authorized,
    call_with_retries,
)
from .request_builder import RequestSpec
from .schemas import JapaneseResearchDossier, JapaneseSynthesisResponse


class OpenAIResponseError(RuntimeError):
    """Raised when an OpenAI response cannot be materialized safely."""


def _response_payload(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json", exclude_none=True)
    if isinstance(response, dict):
        return response
    return {"repr": repr(response)}


def _usage_payload(response: Any) -> dict[str, Any]:
    usage_obj = getattr(response, "usage", None)
    if hasattr(usage_obj, "model_dump"):
        provider_usage = usage_obj.model_dump(mode="json", exclude_none=True)
    elif isinstance(usage_obj, dict):
        provider_usage = usage_obj
    else:
        provider_usage = {}

    input_tokens = int(provider_usage.get("input_tokens") or 0)
    output_tokens = int(provider_usage.get("output_tokens") or 0)
    output_details = provider_usage.get("output_tokens_details")
    reasoning_tokens = (
        int(output_details.get("reasoning_tokens") or 0)
        if isinstance(output_details, dict)
        else 0
    )
    return {
        "prompt_token_count": input_tokens,
        "candidates_token_count": max(0, output_tokens - reasoning_tokens),
        "thoughts_token_count": reasoning_tokens,
        "provider_usage": provider_usage,
    }


def _structured_payload(response: Any) -> dict[str, Any]:
    status = getattr(response, "status", None)
    if status not in {None, "completed"}:
        details = getattr(response, "incomplete_details", None)
        raise OpenAIResponseError(
            f"OpenAI response did not complete (status={status!r}, "
            f"details={details!r})."
        )
    parsed = getattr(response, "output_parsed", None)
    if isinstance(parsed, BaseModel):
        return parsed.model_dump(mode="json")
    if isinstance(parsed, dict):
        return parsed
    raise OpenAIResponseError(
        "OpenAI returned no parsed structured analysis response."
    )


def execute_request(
    repository_root: Path,
    spec: RequestSpec,
    *,
    confirmed_request_id: str,
    max_attempts: int = 1,
    client_factory: Callable[[], Any] | None = None,
    configured_model_getter: Callable[[], str] | None = None,
) -> ExecutionResult:
    """Execute one GPT-5.6 Sol analysis after all safety checks."""

    if spec.provider != "openai" or spec.stage not in {"research", "analysis"}:
        raise LiveApiSafetyError(
            "The OpenAI runtime accepts only OpenAI research or analysis request plans."
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
    model_getter = configured_model_getter or get_openai_model
    configured = model_getter()
    if configured != spec.model:
        raise LiveApiSafetyError(
            f"Configured analysis model {configured!r} differs from inspected "
            f"request model {spec.model!r}."
        )

    content: list[dict[str, Any]] = []
    pdf_detail = str(
        spec.request_options.get("pdf_detail", OPENAI_PDF_DETAIL)
    )
    if spec.context_prompt:
        content.append({"type": "input_text", "text": spec.context_prompt})
    for file in spec.files:
        path = repository_root / file.relative_path
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.extend(
            [
                {
                    "type": "input_text",
                    "text": (
                        "<DOCUMENT_METADATA>\n"
                        f"<source_filename>{escape(file.filename)}</source_filename>\n"
                        f"<physical_pdf_pages>{file.page_count}</physical_pdf_pages>\n"
                        "<content>The immediately following part is this PDF.</content>\n"
                        "</DOCUMENT_METADATA>"
                    ),
                },
                {
                    "type": "input_file",
                    "filename": file.filename,
                    "file_data": f"data:{file.mime_type};base64,{encoded}",
                    "detail": pdf_detail,
                },
            ]
        )
    content.append(
        {"type": "input_text", "text": spec.task_prompt or spec.prompt}
    )

    client = client_factory() if client_factory is not None else get_openai_client()
    try:
        response, attempts = call_with_retries(
            lambda: client.responses.parse(
                model=spec.model,
                instructions=spec.system_prompt,
                input=[{"role": "user", "content": content}],
                text_format=(
                    JapaneseResearchDossier
                    if spec.stage == "research"
                    else JapaneseSynthesisResponse
                ),
                reasoning={
                    "effort": str(
                        spec.request_options.get(
                            "reasoning_effort",
                            "medium",
                        )
                    )
                },
                text={
                    "verbosity": str(
                        spec.request_options.get(
                            "text_verbosity",
                            "high",
                        )
                    )
                },
                max_output_tokens=spec.max_output_tokens,
                store=bool(spec.request_options.get("store", False)),
            ),
            max_attempts=max_attempts,
        )
    finally:
        client.close()

    structured_payload = _structured_payload(response)
    structured = (
        JapaneseResearchDossier.model_validate(structured_payload)
        if spec.stage == "research"
        else JapaneseSynthesisResponse.model_validate(structured_payload)
    )
    return ExecutionResult(
        structured=structured,
        raw_response=_response_payload(response),
        usage=_usage_payload(response),
        model_version=getattr(response, "model", None),
        response_id=getattr(response, "id", None),
        finish_reason=(
            str(getattr(response, "status", "")).upper() or None
        ),
        attempts=attempts,
    )
