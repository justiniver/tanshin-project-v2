"""Offline preparation and explicitly gated single-request execution stages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_MAX_API_ATTEMPTS,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_TRANSLATION_MODEL,
    KEY2_TRANSLATION_MODEL_PROFILE,
    OPENAI_MAX_INLINE_PDF_BYTES,
    OPENAI_PDF_TOKENS_PER_PAGE,
    OPENAI_SOL_MODEL,
    PDF_TOKENS_PER_PAGE,
    PRO_GEMINI_MODEL,
    PRO_MODEL_PROFILE,
    PRO_TRANSLATION_MODEL_PROFILE,
    SCHEMA_VERSION,
    SOL_MODEL_PROFILE,
    USD_TO_JPY_ESTIMATE,
    OutputPaths,
    model_price_for_input_tokens,
    output_paths,
)
from .costing import estimate_cost
from .english_financials import preserve_english_translation
from .evaluation import compare_files, compare_reports
from .persistence import ensure_directory, read_json, write_json, write_text
from .normalization import normalize_japanese_analysis
from .prompts import (
    ANALYSIS_SYSTEM_PROMPT,
    RESEARCH_SYSTEM_PROMPT,
    TRANSLATION_SYSTEM_PROMPT,
    analysis_prompt_template,
    translation_prompt_template,
)
from .research import build_research_metrics, validate_research_dossier
from .render import (
    bilingual_evidence_ledger,
    render_english,
    render_japanese,
)
from .request_builder import (
    RequestSpec,
    build_analysis_spec,
    build_research_spec,
    build_translation_spec,
    response_schema_for,
)
from .schemas import (
    CostEstimate,
    EnglishTranslation,
    EnglishTranslationPatch,
    JapaneseAnalysis,
    JapaneseModelResponse,
    JapaneseResearchDossier,
    JapaneseSynthesisResponse,
    RequestPlan,
    RunMetadata,
    SelectionManifest,
    ValidationResult,
    materialize_japanese_analysis,
    materialize_japanese_synthesis,
    parse_japanese_analysis_payload,
)
from .selection import select_filings
from .translation_contract import materialize_english_translation
from .validation import validate_english, validate_japanese


class PipelineValidationError(RuntimeError):
    """Raised when processing cannot safely produce a rendered report."""


class PipelineConfigurationError(RuntimeError):
    """Raised when a selected model profile is missing or unsupported."""


@dataclass(frozen=True)
class PreparedRun:
    manifest: SelectionManifest
    spec: RequestSpec
    plan: RequestPlan
    cost: CostEstimate
    paths: OutputPaths
    research_source: JapaneseResearchDossier | None = None
    translation_source: JapaneseAnalysis | None = None


@dataclass(frozen=True)
class StageRoute:
    provider: str
    model: str
    provider_profile: str | None


@dataclass(frozen=True)
class ProfileConfiguration:
    analysis: StageRoute
    translation: StageRoute
    pdf_tokens_per_page: int
    pdf_token_assumption: str


def _profile_configuration(model_profile: str) -> ProfileConfiguration:
    if model_profile == DEFAULT_MODEL_PROFILE:
        return ProfileConfiguration(
            analysis=StageRoute(
                provider="gemini",
                model=DEFAULT_ANALYSIS_MODEL,
                provider_profile=DEFAULT_MODEL_PROFILE,
            ),
            translation=StageRoute(
                provider="gemini",
                model=DEFAULT_TRANSLATION_MODEL,
                provider_profile=DEFAULT_MODEL_PROFILE,
            ),
            pdf_tokens_per_page=PDF_TOKENS_PER_PAGE,
            pdf_token_assumption=(
                "Each PDF page is estimated at 540 Gemini visual tokens, "
                "rounded above observed production usage."
            ),
        )
    if model_profile == PRO_MODEL_PROFILE:
        return ProfileConfiguration(
            analysis=StageRoute(
                provider="gemini",
                model=PRO_GEMINI_MODEL,
                provider_profile=PRO_MODEL_PROFILE,
            ),
            translation=StageRoute(
                provider="gemini",
                model=PRO_GEMINI_MODEL,
                provider_profile=PRO_MODEL_PROFILE,
            ),
            pdf_tokens_per_page=PDF_TOKENS_PER_PAGE,
            pdf_token_assumption=(
                "Each PDF page is estimated at 540 Gemini visual tokens, "
                "rounded above observed production usage."
            ),
        )
    if model_profile == PRO_TRANSLATION_MODEL_PROFILE:
        return ProfileConfiguration(
            analysis=StageRoute(
                provider="gemini",
                model=DEFAULT_ANALYSIS_MODEL,
                provider_profile=DEFAULT_MODEL_PROFILE,
            ),
            translation=StageRoute(
                provider="gemini",
                model=PRO_GEMINI_MODEL,
                provider_profile=PRO_MODEL_PROFILE,
            ),
            pdf_tokens_per_page=PDF_TOKENS_PER_PAGE,
            pdf_token_assumption=(
                "Each PDF page is estimated at 540 Gemini visual tokens, "
                "rounded above observed production usage."
            ),
        )
    if model_profile == KEY2_TRANSLATION_MODEL_PROFILE:
        return ProfileConfiguration(
            analysis=StageRoute(
                provider="gemini",
                model=DEFAULT_ANALYSIS_MODEL,
                provider_profile=DEFAULT_MODEL_PROFILE,
            ),
            translation=StageRoute(
                provider="gemini",
                model=DEFAULT_ANALYSIS_MODEL,
                provider_profile=KEY2_TRANSLATION_MODEL_PROFILE,
            ),
            pdf_tokens_per_page=PDF_TOKENS_PER_PAGE,
            pdf_token_assumption=(
                "Each PDF page is estimated at 540 Gemini visual tokens, "
                "rounded above observed production usage."
            ),
        )
    if model_profile == SOL_MODEL_PROFILE:
        return ProfileConfiguration(
            analysis=StageRoute(
                provider="openai",
                model=OPENAI_SOL_MODEL,
                provider_profile=None,
            ),
            translation=StageRoute(
                provider="gemini",
                model=PRO_GEMINI_MODEL,
                provider_profile=PRO_MODEL_PROFILE,
            ),
            pdf_tokens_per_page=OPENAI_PDF_TOKENS_PER_PAGE,
            pdf_token_assumption=(
                "OpenAI PDF input includes extracted text and page images. "
                f"The offline budget uses a conservative "
                f"{OPENAI_PDF_TOKENS_PER_PAGE}-token estimate per page without "
                "contacting an API tokenizer."
            ),
        )
    raise PipelineConfigurationError(
        f"Unknown model profile: {model_profile!r}."
    )


def _profile_models(model_profile: str) -> tuple[str, str]:
    """Backward-compatible model-only view of the provider routing profile."""

    configuration = _profile_configuration(model_profile)
    return configuration.analysis.model, configuration.translation.model


def _validate_inline_pdf_limits(
    manifest: SelectionManifest,
    route: StageRoute,
) -> None:
    if route.provider != "openai":
        return
    oversized = [
        item.filename
        for item in manifest.selected_files
        if item.byte_size >= OPENAI_MAX_INLINE_PDF_BYTES
    ]
    if oversized:
        raise PipelineConfigurationError(
            "OpenAI inline PDF input requires every file to be under 50 MB. "
            f"Oversized selected files: {', '.join(oversized)}."
        )
    if manifest.total_selected_bytes >= OPENAI_MAX_INLINE_PDF_BYTES:
        raise PipelineConfigurationError(
            "OpenAI inline PDF input requires the combined selected PDF payload "
            "to be under 50 MB. Reduce the selected corpus before using --sol."
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata(
    repository_root: Path,
    prepared: PreparedRun,
    *,
    mode: str,
    api_requests: int,
) -> RunMetadata:
    return RunMetadata(
        schema_version=SCHEMA_VERSION,
        security_code=prepared.manifest.security_code,
        mode=mode,
        model_profile=prepared.spec.model_profile,
        prepared_at_utc=_utc_now(),
        repository_root=str(repository_root),
        output_directory=str(prepared.paths.output_dir),
        manifest_id=prepared.manifest.manifest_id,
        analysis_model=prepared.cost.research.model,
        translation_model=prepared.cost.translation.model,
        analysis_provider=(
            prepared.spec.provider
            if prepared.spec.stage in {"research", "analysis"}
            else _profile_configuration(prepared.spec.model_profile).analysis.provider
        ),
        translation_provider=(
            prepared.spec.provider
            if prepared.spec.stage == "translation"
            else _profile_configuration(
                prepared.spec.model_profile
            ).translation.provider
        ),
        api_requests_sent_by_this_invocation=api_requests,
        environment_key_logged=False,
    )


def _pending_stage_plan(
    *,
    stage: str,
    model_profile: str,
    route: StageRoute,
    model: str,
    dependency: str,
) -> dict[str, Any]:
    return {
        "status": f"pending_{dependency}",
        "stage": stage,
        "model_profile": model_profile,
        "provider": route.provider,
        "provider_profile": route.provider_profile,
        "model": model,
        "makes_network_request_when_executed": True,
        "request_count_if_executed": 1,
        "note": (
            f"Complete and persist {dependency.replace('_', ' ')} before "
            f"preparing the {stage} request ID."
        ),
    }


def _persist_research_preflight(
    prepared: PreparedRun,
    repository_root: Path,
) -> None:
    paths = prepared.paths
    write_json(paths.selection_manifest, prepared.manifest)
    write_json(paths.research_request_plan, prepared.plan)
    write_text(paths.research_system_prompt, prepared.spec.system_prompt)
    write_text(paths.research_prompt, prepared.spec.prompt)
    write_json(paths.research_schema, prepared.spec.response_schema)
    write_json(paths.cost, prepared.cost)
    configuration = _profile_configuration(prepared.spec.model_profile)
    write_json(
        paths.analysis_request_plan,
        _pending_stage_plan(
            stage="analysis",
            model_profile=prepared.spec.model_profile,
            route=configuration.analysis,
            model=prepared.cost.analysis.model,
            dependency="research_dossier",
        ),
    )
    write_json(
        paths.translation_request_plan,
        _pending_stage_plan(
            stage="translation",
            model_profile=prepared.spec.model_profile,
            route=configuration.translation,
            model=prepared.cost.translation.model,
            dependency="japanese_analysis",
        ),
    )
    write_json(
        paths.run_metadata,
        _metadata(repository_root, prepared, mode="dry-run", api_requests=0),
    )


def _write_cost_preserving_actuals(
    path: Path,
    cost: CostEstimate,
) -> None:
    """Refresh estimates without discarding completed-stage usage."""

    payload = cost.model_dump(mode="json")
    if path.is_file():
        existing = read_json(path)
        for key in (
            "actual_cost_by_stage_usd",
            "actual_cost_total_usd",
            "actual_cost_total_jpy",
        ):
            if key in existing:
                payload[key] = existing[key]
    write_json(path, payload)


def prepare_research(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    repository_root = repository_root.resolve()
    output_root = (
        output_root or repository_root / DEFAULT_OUTPUT_DIRECTORY
    ).resolve()
    configuration = _profile_configuration(model_profile)
    manifest = select_filings(repository_root, security_code)
    _validate_inline_pdf_limits(manifest, configuration.analysis)
    spec = build_research_spec(
        repository_root,
        manifest,
        model=configuration.analysis.model,
        model_profile=model_profile,
        provider=configuration.analysis.provider,
        provider_profile=configuration.analysis.provider_profile,
    )
    plan = spec.plan()
    cost = estimate_cost(
        manifest,
        research_system_prompt=spec.system_prompt,
        research_prompt=spec.prompt,
        research_response_schema=spec.response_schema,
        analysis_system_prompt=ANALYSIS_SYSTEM_PROMPT,
        analysis_prompt=analysis_prompt_template(),
        analysis_response_schema=response_schema_for(
            JapaneseSynthesisResponse,
            configuration.analysis.provider,
        ),
        translation_system_prompt=TRANSLATION_SYSTEM_PROMPT,
        translation_prompt_template=translation_prompt_template(),
        translation_response_schema=response_schema_for(
            EnglishTranslationPatch,
            configuration.translation.provider,
        ),
        analysis_model=configuration.analysis.model,
        translation_model=configuration.translation.model,
        max_api_attempts=max_api_attempts,
        pdf_tokens_per_page=configuration.pdf_tokens_per_page,
        pdf_token_assumption=configuration.pdf_token_assumption,
    )
    prepared = PreparedRun(
        manifest=manifest,
        spec=spec,
        plan=plan,
        cost=cost,
        paths=output_paths(output_root, security_code, report_date=report_date),
    )
    _persist_research_preflight(prepared, repository_root)
    return prepared


def _load_research(
    paths: OutputPaths,
    manifest: SelectionManifest,
) -> JapaneseResearchDossier:
    if not paths.research_structured.is_file():
        raise PipelineValidationError(
            f"Stored research dossier is missing: {paths.research_structured}"
        )
    dossier = JapaneseResearchDossier.model_validate(
        read_json(paths.research_structured)
    )
    try:
        validate_research_dossier(dossier, manifest)
    except ValueError as exc:
        raise PipelineValidationError(str(exc)) from exc
    return dossier


def prepare_analysis(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    repository_root = repository_root.resolve()
    output_root = (
        output_root or repository_root / DEFAULT_OUTPUT_DIRECTORY
    ).resolve()
    configuration = _profile_configuration(model_profile)
    manifest = select_filings(repository_root, security_code)
    paths = output_paths(output_root, security_code, report_date=report_date)
    dossier = _load_research(paths, manifest)
    if dossier.identity.security_code != security_code:
        raise PipelineValidationError(
            "Stored research dossier security code does not match the request."
        )
    if dossier.identity.latest_filename != manifest.latest_filename:
        raise PipelineValidationError(
            "Stored research dossier latest filing does not match the current selection."
        )
    spec = build_analysis_spec(
        repository_root,
        manifest,
        dossier,
        model=configuration.analysis.model,
        model_profile=model_profile,
        provider=configuration.analysis.provider,
        provider_profile=configuration.analysis.provider_profile,
    )
    research_spec = build_research_spec(
        repository_root,
        manifest,
        model=configuration.analysis.model,
        model_profile=model_profile,
        provider=configuration.analysis.provider,
        provider_profile=configuration.analysis.provider_profile,
    )
    cost = estimate_cost(
        manifest,
        research_system_prompt=research_spec.system_prompt,
        research_prompt=research_spec.prompt,
        research_response_schema=research_spec.response_schema,
        analysis_system_prompt=spec.system_prompt,
        analysis_prompt=spec.prompt,
        analysis_response_schema=spec.response_schema,
        translation_system_prompt=TRANSLATION_SYSTEM_PROMPT,
        translation_prompt_template=translation_prompt_template(),
        translation_response_schema=response_schema_for(
            EnglishTranslationPatch,
            configuration.translation.provider,
        ),
        analysis_model=configuration.analysis.model,
        translation_model=configuration.translation.model,
        max_api_attempts=max_api_attempts,
        pdf_tokens_per_page=configuration.pdf_tokens_per_page,
        pdf_token_assumption=configuration.pdf_token_assumption,
        analysis_prompt_includes_source=True,
    )
    prepared = PreparedRun(
        manifest=manifest,
        spec=spec,
        plan=spec.plan(),
        cost=cost,
        paths=paths,
        research_source=dossier,
    )
    write_json(paths.selection_manifest, manifest)
    write_json(paths.analysis_request_plan, prepared.plan)
    write_text(paths.analysis_system_prompt, spec.system_prompt)
    write_text(paths.analysis_prompt, spec.prompt)
    write_json(paths.analysis_schema, spec.response_schema)
    write_json(
        paths.research_metrics,
        build_research_metrics(dossier, manifest),
    )
    write_json(
        paths.translation_request_plan,
        _pending_stage_plan(
            stage="translation",
            model_profile=model_profile,
            route=configuration.translation,
            model=cost.translation.model,
            dependency="japanese_analysis",
        ),
    )
    _write_cost_preserving_actuals(paths.cost, cost)
    write_json(
        paths.run_metadata,
        _metadata(repository_root, prepared, mode="dry-run", api_requests=0),
    )
    return prepared


def _load_analysis(paths: OutputPaths) -> JapaneseAnalysis:
    source = (
        paths.analysis_normalized
        if paths.analysis_normalized.is_file()
        else paths.analysis_structured
    )
    if not source.is_file():
        raise PipelineValidationError(
            f"Validated Japanese analysis artifact is missing: {paths.analysis_structured}"
        )
    payload = read_json(source)
    return materialize_japanese_analysis(parse_japanese_analysis_payload(payload))


def _retire_report_paths(
    paths: OutputPaths,
    current_paths: list[Path],
) -> list[str]:
    existing = [path for path in current_paths if path.is_file()]
    if not existing:
        return []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    retired_dir = paths.output_dir / "history" / stamp
    ensure_directory(retired_dir)
    destinations: list[str] = []
    for current in existing:
        destination = retired_dir / current.name
        current.replace(destination)
        destinations.append(str(destination))
    return destinations


def _dated_report_paths(
    paths: OutputPaths,
    language: str,
    *,
    draft: bool,
) -> list[Path]:
    marker = "_draft_" if draft else "_"
    pattern = re.compile(
        rf"analysis_{re.escape(language)}_{re.escape(paths.security_code)}"
        rf"{marker}\d{{8}}\.md"
    )
    return sorted(
        path
        for path in paths.output_dir.glob(
            f"analysis_{language}_{paths.security_code}_*.md"
        )
        if path.is_file() and pattern.fullmatch(path.name)
    )


def _retire_current_report(paths: OutputPaths, language: str) -> str | None:
    retired = _retire_report_paths(
        paths,
        _dated_report_paths(paths, language, draft=False),
    )
    return retired[-1] if retired else None


def _retire_current_draft(paths: OutputPaths, language: str) -> str | None:
    retired = _retire_report_paths(
        paths,
        _dated_report_paths(paths, language, draft=True),
    )
    return retired[-1] if retired else None


def _discard_report_path(path: Path) -> None:
    if path.is_file():
        path.unlink()


def _invalidate_dependent_english_report(
    prepared: PreparedRun,
    *,
    mode: str,
) -> None:
    paths = prepared.paths
    retired_final = _retire_current_report(paths, "en")
    retired_draft = _retire_current_draft(paths, "en")
    if (
        retired_final is None
        and retired_draft is None
        and not paths.report_status_en.is_file()
    ):
        return
    write_json(
        paths.report_status_en,
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": prepared.plan.request_id,
            "mode": mode,
            "language": "en",
            "publishable": False,
            "factual_integrity_passed": False,
            "quality_gate_passed": False,
            "blocking_error_count": 0,
            "warning_count": 0,
            "draft_path": None,
            "final_path": None,
            "previous_final_archived_to": retired_final,
            "previous_draft_archived_to": retired_draft,
            "validation_path": None,
            "invalidated_by_analysis_run_id": prepared.plan.request_id,
            "reason": (
                "A new Japanese analysis was processed; the prior English "
                "report no longer represents the current Japanese source."
            ),
            "generated_at_utc": _utc_now(),
        },
    )


def _write_report_status(
    prepared: PreparedRun,
    validation: ValidationResult,
    *,
    language: str,
    mode: str,
    previous_final_archived_to: str | None,
) -> None:
    paths = prepared.paths
    status_path = (
        paths.report_status_ja if language == "ja" else paths.report_status_en
    )
    final_path = paths.report_ja if language == "ja" else paths.report_en
    write_json(
        status_path,
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": prepared.plan.request_id,
            "mode": mode,
            "language": language,
            "publishable": validation.publishable,
            "factual_integrity_passed": validation.factual_integrity_passed,
            "quality_gate_passed": validation.quality_gate_passed,
            "blocking_error_count": validation.blocking_error_count,
            "warning_count": validation.warning_count,
            "report_generated": True,
            "requires_review": not validation.publishable,
            "publication_state": (
                "generated"
                if validation.publishable
                else "generated_with_diagnostics"
            ),
            "management_consistency_score": validation.statistics.get(
                "management_consistency_score"
            ),
            "management_consistency_evidence_confidence": (
                validation.statistics.get(
                    "management_consistency_evidence_confidence"
                )
            ),
            "draft_path": None,
            "final_path": str(final_path),
            "previous_final_archived_to": previous_final_archived_to,
            "validation_path": str(
                paths.analysis_validation
                if language == "ja"
                else paths.translation_validation
            ),
            "generated_at_utc": _utc_now(),
        },
    )


def _write_api_failure_report_status(
    prepared: PreparedRun,
    *,
    language: str,
    mode: str,
    api_state: str,
) -> None:
    paths = prepared.paths
    status_path = (
        paths.report_status_ja if language == "ja" else paths.report_status_en
    )
    retired_final = _retire_current_report(paths, language)
    retired_draft = _retire_current_draft(paths, language)
    write_json(
        status_path,
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": prepared.plan.request_id,
            "mode": mode,
            "language": language,
            "publishable": False,
            "factual_integrity_passed": False,
            "quality_gate_passed": False,
            "blocking_error_count": 0,
            "warning_count": 0,
            "report_generated": False,
            "requires_review": False,
            "publication_state": "not_generated_api_failure",
            "draft_path": None,
            "final_path": None,
            "previous_final_archived_to": retired_final,
            "previous_draft_archived_to": retired_draft,
            "validation_path": None,
            "api_state": api_state,
            "reason": (
                "The model API request did not return a schema-valid response, so "
                "this run generated no report. Any prior report was retired to "
                "prevent it from appearing current."
            ),
            "generated_at_utc": _utc_now(),
        },
    )


def _write_processing_failure_report_status(
    prepared: PreparedRun,
    *,
    language: str,
    mode: str,
    reason: str,
) -> None:
    """Record a local failure after the provider completed successfully."""

    paths = prepared.paths
    status_path = (
        paths.report_status_ja if language == "ja" else paths.report_status_en
    )
    retired_final = _retire_current_report(paths, language)
    retired_draft = _retire_current_draft(paths, language)
    write_json(
        status_path,
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": prepared.plan.request_id,
            "mode": mode,
            "language": language,
            "publishable": False,
            "factual_integrity_passed": False,
            "quality_gate_passed": False,
            "blocking_error_count": 0,
            "warning_count": 0,
            "report_generated": False,
            "requires_review": True,
            "publication_state": "not_generated_processing_failure",
            "draft_path": None,
            "final_path": None,
            "previous_final_archived_to": retired_final,
            "previous_draft_archived_to": retired_draft,
            "validation_path": None,
            "api_state": "SUCCESS",
            "reason": reason,
            "generated_at_utc": _utc_now(),
        },
    )


def _process_japanese_response(
    repository_root: Path,
    prepared: PreparedRun,
    analysis: JapaneseModelResponse | JapaneseAnalysis,
    *,
    mode: str,
) -> tuple[JapaneseAnalysis, ValidationResult]:
    paths = prepared.paths
    _invalidate_dependent_english_report(prepared, mode=mode)
    normalized = normalize_japanese_analysis(
        analysis, prepared.manifest, repository_root.resolve()
    )
    write_json(paths.analysis_normalized, normalized.analysis)
    write_json(
        paths.analysis_normalization,
        {
            "change_count": len(normalized.changes),
            "changes": normalized.changes,
        },
    )
    write_json(
        paths.management_consistency,
        (
            normalized.analysis.management_consistency
            if normalized.analysis.management_consistency is not None
            else {
                "status": "not_available_in_stored_response",
                "note": (
                    "New analysis responses include model-rated components and "
                    "a locally calculated score. This stored response predates "
                    "that schema."
                ),
            }
        ),
    )
    clean_report = render_japanese(normalized.analysis)
    exemplar_path = (
        repository_root
        / "exemplar_output"
        / prepared.manifest.security_code
        / f"analysis_ja_{prepared.manifest.security_code}.md"
    )
    exemplar_text = (
        exemplar_path.read_text(encoding="utf-8")
        if exemplar_path.is_file()
        else None
    )
    validation = validate_japanese(
        normalized.analysis,
        prepared.manifest,
        repository_root=repository_root.resolve(),
        generated_report=clean_report,
        exemplar_text=exemplar_text,
    )
    write_json(paths.analysis_validation, validation)
    retired = _retire_current_report(paths, "ja")
    write_json(paths.evidence_ledger, bilingual_evidence_ledger(normalized.analysis))
    write_json(
        paths.evaluation_ja,
        compare_reports(
            clean_report,
            exemplar_text,
            anchor_fiscal_year=prepared.manifest.window.anchor_fiscal_year,
        ),
    )
    write_text(paths.report_ja, clean_report)
    _discard_report_path(paths.report_ja_draft)
    _write_report_status(
        prepared,
        validation,
        language="ja",
        mode=mode,
        previous_final_archived_to=retired,
    )
    return normalized.analysis, validation


def prepare_translation(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    repository_root = repository_root.resolve()
    output_root = (
        output_root or repository_root / DEFAULT_OUTPUT_DIRECTORY
    ).resolve()
    configuration = _profile_configuration(model_profile)
    paths = output_paths(output_root, security_code, report_date=report_date)
    manifest = select_filings(repository_root, security_code)
    dossier = _load_research(paths, manifest)
    analysis = _load_analysis(paths)
    clean_report = render_japanese(analysis)
    exemplar_path = (
        repository_root
        / "exemplar_output"
        / security_code
        / f"analysis_ja_{security_code}.md"
    )
    ja_validation = validate_japanese(
        analysis,
        manifest,
        repository_root=repository_root,
        generated_report=clean_report,
        exemplar_text=(
            exemplar_path.read_text(encoding="utf-8")
            if exemplar_path.is_file()
            else None
        ),
    )
    write_json(paths.analysis_validation, ja_validation)
    spec = build_translation_spec(
        manifest,
        analysis,
        model=configuration.translation.model,
        model_profile=model_profile,
        provider=configuration.translation.provider,
        provider_profile=configuration.translation.provider_profile,
    )
    plan = spec.plan()
    analysis_spec = build_analysis_spec(
        repository_root,
        manifest,
        dossier,
        model=configuration.analysis.model,
        model_profile=model_profile,
        provider=configuration.analysis.provider,
        provider_profile=configuration.analysis.provider_profile,
    )
    research_spec = build_research_spec(
        repository_root,
        manifest,
        model=configuration.analysis.model,
        model_profile=model_profile,
        provider=configuration.analysis.provider,
        provider_profile=configuration.analysis.provider_profile,
    )
    cost = estimate_cost(
        manifest,
        research_system_prompt=research_spec.system_prompt,
        research_prompt=research_spec.prompt,
        research_response_schema=research_spec.response_schema,
        analysis_system_prompt=analysis_spec.system_prompt,
        analysis_prompt=analysis_spec.prompt,
        analysis_response_schema=analysis_spec.response_schema,
        translation_system_prompt=spec.system_prompt,
        translation_prompt_template=spec.prompt,
        translation_response_schema=spec.response_schema,
        analysis_model=configuration.analysis.model,
        translation_model=configuration.translation.model,
        max_api_attempts=max_api_attempts,
        pdf_tokens_per_page=configuration.pdf_tokens_per_page,
        pdf_token_assumption=configuration.pdf_token_assumption,
        analysis_prompt_includes_source=True,
        translation_prompt_includes_source=True,
    )
    prepared = PreparedRun(
        manifest=manifest,
        spec=spec,
        plan=plan,
        cost=cost,
        paths=paths,
        research_source=dossier,
        translation_source=analysis,
    )
    write_json(paths.selection_manifest, manifest)
    write_json(paths.translation_request_plan, plan)
    write_text(paths.translation_system_prompt, spec.system_prompt)
    write_text(paths.translation_prompt, spec.prompt)
    write_json(paths.translation_schema, spec.response_schema)
    _write_cost_preserving_actuals(paths.cost, cost)
    write_json(
        paths.run_metadata,
        _metadata(repository_root, prepared, mode="dry-run", api_requests=0),
    )
    return prepared


def _usage_artifact(
    *,
    stage: str,
    provider: str,
    model: str,
    model_version: str | None,
    response_id: str | None,
    attempts: int,
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "provider": provider,
        "model": model,
        "model_version": model_version,
        "response_id": response_id,
        "attempts": attempts,
        "usage_metadata": usage,
    }


def _execute_model_request(
    repository_root: Path,
    prepared: PreparedRun,
    *,
    confirmed_request_id: str,
    max_attempts: int,
) -> Any:
    """Dispatch only at the explicitly authorized live boundary."""

    if prepared.spec.provider == "openai":
        from .openai_runtime import execute_request
    elif prepared.spec.provider == "gemini":
        from .gemini_runtime import execute_request
    else:
        raise PipelineConfigurationError(
            f"Unsupported API provider: {prepared.spec.provider!r}."
        )
    return execute_request(
        repository_root,
        prepared.spec,
        confirmed_request_id=confirmed_request_id,
        max_attempts=max_attempts,
    )


def _api_status_path(paths: OutputPaths, stage: str) -> Path:
    if stage == "research":
        return paths.research_api_status
    if stage == "analysis":
        return paths.analysis_api_status
    return paths.translation_api_status


def _error_status_code(exc: Exception) -> int | str | None:
    for source in (exc, getattr(exc, "response", None)):
        if source is None:
            continue
        for attribute in ("status_code", "code"):
            value = getattr(source, attribute, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    value = None
            if value is not None:
                return getattr(value, "value", value)
    return None


def _safe_error_summary(exc: Exception) -> str:
    summary = re.sub(r"\s+", " ", str(exc)).strip()
    summary = re.sub(
        r"(?i)(api[_-]?key|key)(\s*[=:]\s*)[^&\s,]+",
        r"\1\2[REDACTED]",
        summary,
    )
    summary = re.sub(
        r"AIza[0-9A-Za-z_-]{20,}",
        "[REDACTED_API_KEY]",
        summary,
    )
    summary = re.sub(
        r"sk-[0-9A-Za-z_-]{16,}",
        "[REDACTED_API_KEY]",
        summary,
    )
    return summary[:500]


def _api_failure_state(exc: Exception) -> str:
    status_code = _error_status_code(exc)
    text = f"{type(exc).__name__} {exc}".lower()
    if status_code == 429 or any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "rate_limit",
            "resource_exhausted",
            "quota exceeded",
            "quota_exceeded",
        )
    ):
        return "RATE_LIMITED"
    if status_code == 503 or any(
        marker in text
        for marker in (
            "503",
            "temporarily unavailable",
            "high demand",
            "service unavailable",
            "unavailable",
        )
    ):
        return "TEMPORARILY_UNAVAILABLE"
    return "FAILED"


def _write_api_status(
    prepared: PreparedRun,
    *,
    stage: str,
    state: str,
    started_at_utc: str,
    result: object | None = None,
    error: Exception | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": prepared.plan.request_id,
        "stage": stage,
        "model_profile": prepared.spec.model_profile,
        "provider": prepared.spec.provider,
        "provider_profile": prepared.spec.provider_profile,
        "model": prepared.spec.model,
        "state": state,
        "api_request_completed": state == "SUCCESS",
        "started_at_utc": started_at_utc,
        "updated_at_utc": _utc_now(),
    }
    if result is not None:
        payload.update(
            {
                "response_id": getattr(result, "response_id", None),
                "model_version": getattr(result, "model_version", None),
                "finish_reason": getattr(result, "finish_reason", None),
                "attempts": getattr(result, "attempts", None),
                "usage_metadata": getattr(result, "usage", {}),
            }
        )
    if error is not None:
        status_code = _error_status_code(error)
        retryable = state in {"RATE_LIMITED", "TEMPORARILY_UNAVAILABLE"}
        payload.update(
            {
                "error_type": type(error).__name__,
                "status_code": status_code,
                "error_summary": _safe_error_summary(error),
                "retryable": retryable,
            }
        )
        if state == "TEMPORARILY_UNAVAILABLE":
            payload["retry_guidance"] = (
                "The model provider returned a temporary service-capacity error. Wait and "
                "manually rerun the same one-line stage command; no automatic "
                "retry was attempted."
            )
        elif state == "RATE_LIMITED":
            payload["retry_guidance"] = (
                "Wait for quota or rate capacity to recover, then manually "
                "rerun the same one-line stage command; no automatic retry "
                "was attempted."
            )
    write_json(_api_status_path(prepared.paths, stage), payload)


def _record_actual_cost(
    paths: OutputPaths,
    *,
    stage: str,
    model: str,
    usage: dict[str, Any],
) -> None:
    payload = read_json(paths.cost) if paths.cost.is_file() else {}
    input_tokens = int(usage.get("prompt_token_count") or 0)
    output_tokens = int(usage.get("candidates_token_count") or 0) + int(
        usage.get("thoughts_token_count") or 0
    )
    price = model_price_for_input_tokens(model, input_tokens)
    stage_cost = (
        input_tokens / 1_000_000 * price.input_per_million
        + output_tokens / 1_000_000 * price.output_per_million
    )
    actual = payload.setdefault("actual_cost_by_stage_usd", {})
    actual[stage] = {
        "model": model,
        "input_tokens": input_tokens,
        "billed_output_and_thinking_tokens": output_tokens,
        "cost_usd": round(stage_cost, 6),
        "cost_jpy": round(stage_cost * USD_TO_JPY_ESTIMATE, 2),
    }
    payload["actual_cost_total_usd"] = round(
        sum(item["cost_usd"] for item in actual.values()),
        6,
    )
    payload["actual_cost_total_jpy"] = round(
        sum(item["cost_jpy"] for item in actual.values()),
        2,
    )
    write_json(paths.cost, payload)


def _record_stage_usage(
    paths: OutputPaths,
    *,
    stage: str,
    prepared: PreparedRun,
    result: object,
) -> None:
    payload = read_json(paths.token_usage) if paths.token_usage.is_file() else {}
    if not isinstance(payload, dict):
        payload = {}
    payload[stage] = _usage_artifact(
        stage=stage,
        provider=prepared.spec.provider,
        model=prepared.spec.model,
        model_version=getattr(result, "model_version", None),
        response_id=getattr(result, "response_id", None),
        attempts=getattr(result, "attempts", 1),
        usage=getattr(result, "usage", {}),
    )
    write_json(paths.token_usage, payload)


def execute_research(
    repository_root: Path,
    security_code: str,
    *,
    confirmed_request_id: str,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    """Send exactly one PDF-backed Japanese research request."""

    prepared = prepare_research(
        repository_root,
        security_code,
        output_root=output_root,
        report_date=report_date,
        max_api_attempts=max_api_attempts,
        model_profile=model_profile,
    )
    started_at_utc = _utc_now()
    _write_api_status(
        prepared,
        stage="research",
        state="REQUESTING",
        started_at_utc=started_at_utc,
    )
    try:
        result = _execute_model_request(
            repository_root.resolve(),
            prepared,
            confirmed_request_id=confirmed_request_id,
            max_attempts=max_api_attempts,
        )
    except Exception as exc:
        raw_response = getattr(exc, "raw_response", None)
        if isinstance(raw_response, dict):
            write_json(prepared.paths.research_raw_response, raw_response)
        failure_state = _api_failure_state(exc)
        _write_api_status(
            prepared,
            stage="research",
            state=failure_state,
            started_at_utc=started_at_utc,
            error=exc,
        )
        _write_api_failure_report_status(
            prepared,
            language="ja",
            mode="research",
            api_state=failure_state,
        )
        raise
    assert isinstance(result.structured, JapaneseResearchDossier)
    paths = prepared.paths
    write_json(paths.research_raw_response, result.raw_response)
    _write_api_status(
        prepared,
        stage="research",
        state="SUCCESS",
        started_at_utc=started_at_utc,
        result=result,
    )
    try:
        validate_research_dossier(result.structured, prepared.manifest)
    except ValueError as exc:
        wrapped = PipelineValidationError(str(exc))
        _write_processing_failure_report_status(
            prepared,
            language="ja",
            mode="research",
            reason=(
                "The provider completed the research request, but the returned "
                f"dossier could not be used for synthesis: {wrapped}"
            ),
        )
        raise wrapped from exc
    write_json(paths.research_structured, result.structured)
    write_json(
        paths.research_metrics,
        build_research_metrics(result.structured, prepared.manifest),
    )
    _record_stage_usage(
        paths,
        stage="research",
        prepared=prepared,
        result=result,
    )
    _record_actual_cost(
        paths,
        stage="research",
        model=prepared.spec.model,
        usage=result.usage,
    )
    write_json(
        paths.run_metadata,
        _metadata(
            repository_root.resolve(),
            prepared,
            mode="research",
            api_requests=1,
        ),
    )
    return prepared


def execute_analysis(
    repository_root: Path,
    security_code: str,
    *,
    confirmed_request_id: str,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    """Send exactly one Japanese analysis request under an inspected profile."""

    prepared = prepare_analysis(
        repository_root,
        security_code,
        output_root=output_root,
        report_date=report_date,
        max_api_attempts=max_api_attempts,
        model_profile=model_profile,
    )
    started_at_utc = _utc_now()
    _write_api_status(
        prepared,
        stage="analysis",
        state="REQUESTING",
        started_at_utc=started_at_utc,
    )
    try:
        result = _execute_model_request(
            repository_root.resolve(),
            prepared,
            confirmed_request_id=confirmed_request_id,
            max_attempts=max_api_attempts,
        )
    except Exception as exc:
        raw_response = getattr(exc, "raw_response", None)
        if isinstance(raw_response, dict):
            write_json(prepared.paths.analysis_raw_response, raw_response)
        failure_state = _api_failure_state(exc)
        _write_api_status(
            prepared,
            stage="analysis",
            state=failure_state,
            started_at_utc=started_at_utc,
            error=exc,
        )
        _write_api_failure_report_status(
            prepared,
            language="ja",
            mode="analysis",
            api_state=failure_state,
        )
        raise
    _write_api_status(
        prepared,
        stage="analysis",
        state="SUCCESS",
        started_at_utc=started_at_utc,
        result=result,
    )
    assert isinstance(result.structured, JapaneseSynthesisResponse)
    dossier = prepared.research_source
    if dossier is None:
        raise PipelineValidationError(
            "Prepared analysis is missing its source research dossier."
        )
    try:
        analysis = materialize_japanese_synthesis(dossier, result.structured)
    except ValueError as exc:
        raise PipelineValidationError(str(exc)) from exc
    paths = prepared.paths
    write_json(paths.analysis_raw_response, result.raw_response)
    write_json(paths.analysis_structured, result.structured)
    _record_stage_usage(
        paths,
        stage="analysis",
        prepared=prepared,
        result=result,
    )
    _record_actual_cost(
        paths,
        stage="analysis",
        model=prepared.spec.model,
        usage=result.usage,
    )
    _process_japanese_response(
        repository_root, prepared, analysis, mode="analysis"
    )
    write_json(
        paths.run_metadata,
        _metadata(repository_root.resolve(), prepared, mode="analysis", api_requests=1),
    )
    return prepared


def execute_translation(
    repository_root: Path,
    security_code: str,
    *,
    confirmed_request_id: str,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    """Send exactly one translation request under an inspected profile."""

    prepared = prepare_translation(
        repository_root,
        security_code,
        output_root=output_root,
        report_date=report_date,
        max_api_attempts=max_api_attempts,
        model_profile=model_profile,
    )
    analysis = prepared.translation_source
    if analysis is None:
        raise PipelineValidationError(
            "Prepared translation is missing its source Japanese analysis."
        )
    started_at_utc = _utc_now()
    _write_api_status(
        prepared,
        stage="translation",
        state="REQUESTING",
        started_at_utc=started_at_utc,
    )
    try:
        result = _execute_model_request(
            repository_root.resolve(),
            prepared,
            confirmed_request_id=confirmed_request_id,
            max_attempts=max_api_attempts,
        )
    except Exception as exc:
        raw_response = getattr(exc, "raw_response", None)
        if isinstance(raw_response, dict):
            write_json(prepared.paths.translation_raw_response, raw_response)
        failure_state = _api_failure_state(exc)
        _write_api_status(
            prepared,
            stage="translation",
            state=failure_state,
            started_at_utc=started_at_utc,
            error=exc,
        )
        _write_api_failure_report_status(
            prepared,
            language="en",
            mode="translation",
            api_state=failure_state,
        )
        raise
    _write_api_status(
        prepared,
        stage="translation",
        state="SUCCESS",
        started_at_utc=started_at_utc,
        result=result,
    )
    assert isinstance(result.structured, EnglishTranslationPatch)
    translation = materialize_english_translation(analysis, result.structured)
    paths = prepared.paths
    write_json(paths.translation_raw_response, result.raw_response)
    write_json(paths.translation_structured, translation)
    _record_stage_usage(
        paths,
        stage="translation",
        prepared=prepared,
        result=result,
    )
    _record_actual_cost(
        paths,
        stage="translation",
        model=prepared.spec.model,
        usage=result.usage,
    )
    _process_english_response(
        repository_root.resolve(),
        prepared,
        analysis,
        translation,
        mode="translation",
    )
    write_json(
        paths.run_metadata,
        _metadata(
            repository_root.resolve(),
            prepared,
            mode="translation",
            api_requests=1,
        ),
    )
    return prepared


def _process_english_response(
    repository_root: Path,
    prepared: PreparedRun,
    analysis: JapaneseAnalysis,
    translation: EnglishTranslation,
    *,
    mode: str,
) -> tuple[EnglishTranslation, ValidationResult]:
    paths = prepared.paths
    preserved = preserve_english_translation(translation)
    write_json(paths.translation_normalized, preserved.translation)
    write_json(
        paths.translation_normalization,
        {
            "mode": "model_rendered_english_financial_notation",
            "financial_text_modified": False,
            "change_count": 0,
            "unresolved_count": 0,
            "changes": [],
            "unresolved": [],
            "note": (
                "The translation model renders yen-denominated values in English "
                "investor notation "
                "without foreign-exchange conversion. Local prose rewriting remains "
                "disabled; Python validates economic equivalence."
            ),
        },
    )
    clean_report = render_english(analysis, preserved.translation)
    exemplar_path = (
        repository_root
        / "exemplar_output"
        / prepared.manifest.security_code
        / f"analysis_en_{prepared.manifest.security_code}.md"
    )
    exemplar_text = (
        exemplar_path.read_text(encoding="utf-8")
        if exemplar_path.is_file()
        else None
    )
    validation = validate_english(
        preserved.translation,
        analysis,
        prepared.manifest,
        generated_report=clean_report,
        exemplar_text=exemplar_text,
    )
    write_json(paths.translation_validation, validation)
    retired = _retire_current_report(paths, "en")
    write_text(paths.report_en, clean_report)
    _discard_report_path(paths.report_en_draft)
    _write_report_status(
        prepared,
        validation,
        language="en",
        mode=mode,
        previous_final_archived_to=retired,
    )
    write_json(
        paths.evidence_ledger,
        bilingual_evidence_ledger(analysis, preserved.translation),
    )
    write_json(
        paths.evaluation_en,
        compare_reports(
            clean_report,
            exemplar_text,
            anchor_fiscal_year=prepared.manifest.window.anchor_fiscal_year,
        ),
    )
    return preserved.translation, validation


def reprocess_stored_analysis(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> dict[str, Any]:
    """Normalize, validate, and render an existing response without networking."""

    prepared = prepare_analysis(
        repository_root,
        security_code,
        output_root=output_root,
        report_date=report_date,
        model_profile=model_profile,
    )
    if not prepared.paths.analysis_structured.is_file():
        raise PipelineValidationError(
            f"Stored response is missing: {prepared.paths.analysis_structured}"
        )
    dossier = prepared.research_source
    if dossier is None:
        raise PipelineValidationError(
            "Stored analysis reprocessing is missing its research dossier."
        )
    payload = read_json(prepared.paths.analysis_structured)
    try:
        synthesis = JapaneseSynthesisResponse.model_validate(payload)
        analysis = materialize_japanese_synthesis(dossier, synthesis)
    except ValueError as exc:
        raise PipelineValidationError(
            f"Stored synthesis response is invalid: {exc}"
        ) from exc
    _, validation = _process_japanese_response(
        repository_root.resolve(), prepared, analysis, mode="reprocess"
    )
    write_json(
        prepared.paths.run_metadata,
        _metadata(
            repository_root.resolve(),
            prepared,
            mode="reprocess",
            api_requests=0,
        ),
    )
    return {
        "valid": validation.publishable,
        "publishable": validation.publishable,
        "factual_integrity_passed": validation.factual_integrity_passed,
        "quality_gate_passed": validation.quality_gate_passed,
        "errors": validation.blocking_error_count,
        "warnings": validation.warning_count,
        "report_generated": True,
        "requires_review": not validation.publishable,
        "draft_report": None,
        "final_report": str(prepared.paths.report_ja),
        "validation": str(prepared.paths.analysis_validation),
        "normalization": str(prepared.paths.analysis_normalization),
    }


def reprocess_stored_translation(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> dict[str, Any]:
    """Normalize, validate, and render a stored translation without networking."""

    prepared = prepare_translation(
        repository_root,
        security_code,
        output_root=output_root,
        report_date=report_date,
        model_profile=model_profile,
    )
    if not prepared.paths.translation_structured.is_file():
        raise PipelineValidationError(
            f"Stored response is missing: {prepared.paths.translation_structured}"
        )
    analysis = _load_analysis(prepared.paths)
    translation = EnglishTranslation.model_validate(
        read_json(prepared.paths.translation_structured)
    )
    _, validation = _process_english_response(
        repository_root.resolve(),
        prepared,
        analysis,
        translation,
        mode="reprocess",
    )
    write_json(
        prepared.paths.run_metadata,
        _metadata(
            repository_root.resolve(),
            prepared,
            mode="reprocess",
            api_requests=0,
        ),
    )
    return {
        "valid": validation.publishable,
        "publishable": validation.publishable,
        "factual_integrity_passed": validation.factual_integrity_passed,
        "quality_gate_passed": validation.quality_gate_passed,
        "errors": validation.blocking_error_count,
        "warnings": validation.warning_count,
        "report_generated": True,
        "requires_review": not validation.publishable,
        "draft_report": None,
        "final_report": str(prepared.paths.report_en),
        "validation": str(prepared.paths.translation_validation),
        "normalization": str(prepared.paths.translation_normalization),
    }


def compare_existing_reports(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    report_date: date | datetime | str | None = None,
) -> dict[str, Any]:
    output_root = (
        output_root or repository_root / DEFAULT_OUTPUT_DIRECTORY
    ).resolve()
    paths = output_paths(output_root, security_code, report_date=report_date)
    result: dict[str, Any] = {}
    for language, generated, evaluation in (
        ("ja", paths.report_ja, paths.evaluation_ja),
        ("en", paths.report_en, paths.evaluation_en),
    ):
        available_reports = _dated_report_paths(
            paths,
            language,
            draft=False,
        )
        current_report = (
            generated
            if generated.is_file()
            else (available_reports[-1] if available_reports else generated)
        )
        configured_draft = (
            paths.report_ja_draft if language == "ja" else paths.report_en_draft
        )
        available_drafts = _dated_report_paths(paths, language, draft=True)
        draft = (
            configured_draft
            if configured_draft.is_file()
            else (available_drafts[-1] if available_drafts else configured_draft)
        )
        selected_report = current_report if current_report.is_file() else draft
        if not selected_report.is_file():
            result[language] = {"status": "missing", "path": str(generated)}
            continue
        exemplar = (
            repository_root
            / "exemplar_output"
            / security_code
            / f"analysis_{language}_{security_code}.md"
        )
        manifest = select_filings(repository_root, security_code)
        comparison = compare_files(
            selected_report,
            exemplar if exemplar.is_file() else None,
            anchor_fiscal_year=manifest.window.anchor_fiscal_year,
        )
        comparison["report_kind"] = (
            "final" if current_report.is_file() else "draft"
        )
        write_json(evaluation, comparison)
        result[language] = comparison
    return result
