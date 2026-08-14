"""Build inspectable request specifications without importing the API client."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from .config import (
    ANALYSIS_MAX_OUTPUT_TOKENS,
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TRANSLATION_MODEL,
    OPENAI_PDF_DETAIL,
    RESEARCH_MAX_OUTPUT_TOKENS,
    SCHEMA_VERSION,
    TRANSLATION_MAX_OUTPUT_TOKENS,
)
from .prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    TRANSLATION_SYSTEM_PROMPT,
    build_analysis_prompt,
    build_research_prompt,
    build_translation_prompt,
    load_generic_blueprint,
)
from .research import build_research_metrics
from .schemas import (
    ApiProvider,
    EnglishTranslationPatch,
    JapaneseAnalysis,
    JapaneseResearchDossier,
    JapaneseSynthesisResponse,
    ModelProfile,
    ProviderProfile,
    RequestFileDescriptor,
    RequestPlan,
    SelectionManifest,
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def response_schema_for(
    model: type[BaseModel],
    provider: ApiProvider,
) -> dict[str, Any]:
    """Build the provider-compatible native structured-output schema."""

    if provider == "openai":
        # This is the same strict-schema converter used by the pinned official
        # OpenAI SDK when responses.parse receives a Pydantic model.
        from openai.lib._pydantic import to_strict_json_schema

        return to_strict_json_schema(model)
    schema = copy.deepcopy(model.model_json_schema())

    def simplify_gemini_schema(value: Any) -> None:
        if isinstance(value, dict):
            # Gemini can reject a large schema when many optional array-size
            # constraints are combined. Keep exact-cardinality arrays, such as
            # the four management-consistency components, but enforce all other
            # research ceilings in the prompt and local Pydantic validation.
            if (
                "maxItems" in value
                and value.get("minItems") != value["maxItems"]
            ):
                value.pop("maxItems")
            for child in value.values():
                simplify_gemini_schema(child)
        elif isinstance(value, list):
            for child in value:
                simplify_gemini_schema(child)

    simplify_gemini_schema(schema)
    return schema


@dataclass(frozen=True)
class RequestSpec:
    stage: Literal["research", "analysis", "translation"]
    security_code: str
    model_profile: ModelProfile
    provider: ApiProvider
    provider_profile: ProviderProfile | None
    model: str
    manifest_id: str
    system_prompt: str
    prompt: str
    response_schema: dict[str, Any]
    max_output_tokens: int
    files: tuple[RequestFileDescriptor, ...]
    request_options: dict[str, str | int | float | bool] = field(
        default_factory=dict
    )
    style_blueprint_path: str | None = None
    style_blueprint_sha256: str | None = None
    exemplar_path: str | None = None
    exemplar_sha256: str | None = None
    context_prompt: str | None = None
    task_prompt: str | None = None

    def plan(self) -> RequestPlan:
        schema_hash = sha256_json(self.response_schema)
        request_payload = {
            "stage": self.stage,
            "security_code": self.security_code,
            "model_profile": self.model_profile,
            "provider": self.provider,
            "provider_profile": self.provider_profile,
            "model": self.model,
            "request_options": self.request_options,
            "manifest_id": self.manifest_id,
            "system_prompt_sha256": sha256_text(self.system_prompt),
            "prompt_sha256": sha256_text(self.prompt),
            "response_schema_sha256": schema_hash,
            "files": [item.model_dump(mode="json") for item in self.files],
            "max_output_tokens": self.max_output_tokens,
            "style_blueprint_sha256": self.style_blueprint_sha256,
            "exemplar_sha256": self.exemplar_sha256,
        }
        return RequestPlan(
            schema_version=SCHEMA_VERSION,
            stage=self.stage,
            security_code=self.security_code,
            model_profile=self.model_profile,
            provider=self.provider,
            provider_profile=self.provider_profile,
            model=self.model,
            request_options=self.request_options,
            request_id=sha256_json(request_payload)[:16],
            manifest_id=self.manifest_id,
            system_prompt_sha256=request_payload["system_prompt_sha256"],
            prompt_sha256=request_payload["prompt_sha256"],
            response_schema_sha256=schema_hash,
            files=list(self.files),
            style_blueprint_path=self.style_blueprint_path,
            style_blueprint_sha256=self.style_blueprint_sha256,
            exemplar_path=self.exemplar_path,
            exemplar_sha256=self.exemplar_sha256,
            max_output_tokens=self.max_output_tokens,
            makes_network_request=True,
            request_count_if_executed=1,
        )


def _request_files(
    manifest: SelectionManifest,
) -> tuple[RequestFileDescriptor, ...]:
    return tuple(
        RequestFileDescriptor(
            filename=item.filename,
            relative_path=item.relative_path,
            mime_type="application/pdf",
            page_count=item.page_count,
            byte_size=item.byte_size,
            sha256=item.sha256,
        )
        for item in manifest.selected_files
    )


def build_research_spec(
    repository_root: Path,
    manifest: SelectionManifest,
    *,
    model: str = DEFAULT_ANALYSIS_MODEL,
    model_profile: ModelProfile = DEFAULT_MODEL_PROFILE,
    provider: ApiProvider = "gemini",
    provider_profile: ProviderProfile | None = DEFAULT_MODEL_PROFILE,
) -> RequestSpec:
    del repository_root
    prompt = build_research_prompt(manifest)
    task_marker = "<research_task>"
    task_index = prompt.index(task_marker)
    context_prompt = prompt[:task_index].rstrip()
    task_prompt = prompt[task_index:].lstrip()
    return RequestSpec(
        stage="research",
        security_code=manifest.security_code,
        model_profile=model_profile,
        provider=provider,
        provider_profile=provider_profile,
        model=model,
        manifest_id=manifest.manifest_id,
        system_prompt=RESEARCH_SYSTEM_PROMPT,
        prompt=prompt,
        response_schema=response_schema_for(JapaneseResearchDossier, provider),
        max_output_tokens=RESEARCH_MAX_OUTPUT_TOKENS,
        files=_request_files(manifest),
        request_options=(
            {
                "reasoning_effort": "medium",
                "text_verbosity": "high",
                "pdf_detail": OPENAI_PDF_DETAIL,
                "store": False,
            }
            if provider == "openai"
            else {}
        ),
        context_prompt=context_prompt,
        task_prompt=task_prompt,
    )


def build_analysis_spec(
    repository_root: Path,
    manifest: SelectionManifest,
    dossier: JapaneseResearchDossier,
    *,
    model: str = DEFAULT_ANALYSIS_MODEL,
    model_profile: ModelProfile = DEFAULT_MODEL_PROFILE,
    provider: ApiProvider = "gemini",
    provider_profile: ProviderProfile | None = DEFAULT_MODEL_PROFILE,
) -> RequestSpec:
    blueprint = load_generic_blueprint(repository_root)
    metrics = build_research_metrics(dossier, manifest)
    prompt = build_analysis_prompt(manifest, blueprint, dossier, metrics)
    task_marker = "<analysis_task>"
    task_index = prompt.index(task_marker)
    blueprint_path = blueprint.path.relative_to(repository_root).as_posix()
    return RequestSpec(
        stage="analysis",
        security_code=manifest.security_code,
        model_profile=model_profile,
        provider=provider,
        provider_profile=provider_profile,
        model=model,
        manifest_id=manifest.manifest_id,
        system_prompt=ANALYSIS_SYSTEM_PROMPT,
        prompt=prompt,
        response_schema=response_schema_for(JapaneseSynthesisResponse, provider),
        max_output_tokens=ANALYSIS_MAX_OUTPUT_TOKENS,
        files=_request_files(manifest),
        request_options=(
            {
                "reasoning_effort": "medium",
                "text_verbosity": "high",
                "pdf_detail": OPENAI_PDF_DETAIL,
                "store": False,
            }
            if provider == "openai"
            else {}
        ),
        style_blueprint_path=blueprint_path,
        style_blueprint_sha256=sha256_text(blueprint.text),
        context_prompt=prompt[:task_index].rstrip(),
        task_prompt=prompt[task_index:].lstrip(),
    )


def build_translation_spec(
    manifest: SelectionManifest,
    analysis: JapaneseAnalysis,
    *,
    model: str = DEFAULT_TRANSLATION_MODEL,
    model_profile: ModelProfile = DEFAULT_MODEL_PROFILE,
    provider: ApiProvider = "gemini",
    provider_profile: ProviderProfile | None = DEFAULT_MODEL_PROFILE,
) -> RequestSpec:
    prompt = build_translation_prompt(analysis)
    task_marker = "<translation_task>"
    task_index = prompt.index(task_marker)
    return RequestSpec(
        stage="translation",
        security_code=manifest.security_code,
        model_profile=model_profile,
        provider=provider,
        provider_profile=provider_profile,
        model=model,
        manifest_id=manifest.manifest_id,
        system_prompt=TRANSLATION_SYSTEM_PROMPT,
        prompt=prompt,
        response_schema=response_schema_for(EnglishTranslationPatch, provider),
        max_output_tokens=TRANSLATION_MAX_OUTPUT_TOKENS,
        files=(),
        context_prompt=prompt[:task_index].rstrip(),
        task_prompt=prompt[task_index:].lstrip(),
    )
