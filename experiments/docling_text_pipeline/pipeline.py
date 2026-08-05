"""Isolated text-input report orchestration built on the production components."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from tanshin_pipeline.config import (
    ANALYSIS_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_API_ATTEMPTS,
    DEFAULT_MODEL_PROFILE,
    TRANSLATION_MAX_OUTPUT_TOKENS,
    USD_TO_JPY_ESTIMATE,
    model_price_for_input_tokens,
    output_paths,
)
from tanshin_pipeline.costing import estimate_text_tokens
from tanshin_pipeline.persistence import read_json, write_json
from tanshin_pipeline.pipeline import (
    PipelineConfigurationError,
    PipelineValidationError,
    PreparedRun,
    _api_failure_state,
    _execute_model_request,
    _metadata,
    _process_english_response,
    _process_japanese_response,
    _profile_configuration,
    _record_actual_cost,
    _usage_artifact,
    _write_api_failure_report_status,
    _write_api_status,
    prepare_translation,
)
from tanshin_pipeline.prompts import (
    TRANSLATION_SYSTEM_PROMPT,
    translation_prompt_template,
)
from tanshin_pipeline.request_builder import (
    RequestSpec,
    build_analysis_spec,
)
from tanshin_pipeline.schemas import (
    CostEstimate,
    CostStage,
    EnglishTranslationPatch,
    JapaneseAnalysis,
    JapaneseModelResponse,
)
from tanshin_pipeline.selection import select_filings
from tanshin_pipeline.translation_contract import materialize_english_translation

from .audit import audit_extraction
from .common import (
    DEFAULT_DOCLING_MODELS_ROOT,
    DEFAULT_EXPERIMENT_OUTPUT_ROOT,
    DEFAULT_DOCLING_VENV_ROOT,
    EXPERIMENT_SCHEMA_VERSION,
    SOURCE_REPRESENTATION_NOTICE,
    experiment_artifact_paths,
    sha256_text,
    validate_experiment_output_root,
)


ExtractorRunner = Callable[
    [Path, Path, Path, Path, Path, bool],
    None,
]

TEXT_INPUT_SYSTEM_ADDENDUM = """\
Experimental source representation: in this request, every reference in the
instructions to supplied source PDFs means the page-preserving Docling
Markdown derived locally from those PDFs. Use only that supplied text, retain
its original filenames and physical-page markers in evidence records, and do
not assume content that is absent from the extraction."""

_SAFE_EXTRACTOR_ENVIRONMENT_NAMES = (
    "SYSTEMROOT",
    "WINDIR",
    "PATH",
    "PATHEXT",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_docling_python(repository_root: Path) -> Path:
    return (
        repository_root
        / DEFAULT_DOCLING_VENV_ROOT
        / "Scripts"
        / "python.exe"
    )


def _extractor_environment() -> dict[str, str]:
    environment = {
        name: value
        for name in _SAFE_EXTRACTOR_ENVIRONMENT_NAMES
        if (value := os.environ.get(name)) is not None
    }
    environment.update(
        {
            "TANSHIN_OFFLINE_ONLY": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        }
    )
    return environment


def _default_extractor_runner(
    repository_root: Path,
    selection_manifest: Path,
    output_root: Path,
    docling_python: Path,
    models_dir: Path,
    force_reparse: bool,
) -> None:
    if not docling_python.is_file():
        raise PipelineConfigurationError(
            f"Disposable Docling environment was not found: {docling_python}. "
            r"Run .\scripts\setup_docling_experiment.ps1 first."
        )
    command = [
        str(docling_python),
        "-m",
        "experiments.docling_text_pipeline.extract_worker",
        "--repository-root",
        str(repository_root),
        "--selection-manifest",
        str(selection_manifest),
        "--output-root",
        str(output_root),
        "--models-dir",
        str(models_dir),
    ]
    if force_reparse:
        command.append("--force-reparse")
    completed = subprocess.run(
        command,
        cwd=repository_root,
        env=_extractor_environment(),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        summary = (completed.stderr or completed.stdout).strip()
        raise PipelineConfigurationError(
            "Local Docling extraction failed before any AI request. "
            f"{summary[:1000]}"
        )


def _stage_cost(
    *,
    model: str,
    input_tokens: int,
    maximum_output_tokens: int,
) -> CostStage:
    price = model_price_for_input_tokens(model, input_tokens)
    input_cost = input_tokens / 1_000_000 * price.input_per_million
    output_cost = (
        maximum_output_tokens
        / 1_000_000
        * price.output_per_million
    )
    return CostStage(
        model=model,
        estimated_input_tokens=input_tokens,
        maximum_output_tokens=maximum_output_tokens,
        input_cost_usd=round(input_cost, 6),
        maximum_output_cost_usd=round(output_cost, 6),
        maximum_stage_cost_usd=round(input_cost + output_cost, 6),
        input_cost_jpy=round(input_cost * USD_TO_JPY_ESTIMATE, 2),
        maximum_output_cost_jpy=round(
            output_cost * USD_TO_JPY_ESTIMATE,
            2,
        ),
        maximum_stage_cost_jpy=round(
            (input_cost + output_cost) * USD_TO_JPY_ESTIMATE,
            2,
        ),
    )


def _estimate_text_cost(
    *,
    spec: RequestSpec,
    translation_model: str,
    max_api_attempts: int,
) -> CostEstimate:
    if max_api_attempts < 1:
        raise ValueError("max_api_attempts must be at least one.")
    schema_text = json.dumps(
        spec.response_schema,
        ensure_ascii=False,
        sort_keys=True,
    )
    analysis_input = estimate_text_tokens(
        "\n".join(
            (
                spec.system_prompt,
                spec.prompt,
                schema_text,
            )
        )
    )
    translation_schema = json.dumps(
        EnglishTranslationPatch.model_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
    )
    translation_input = (
        ANALYSIS_MAX_OUTPUT_TOKENS
        + estimate_text_tokens(TRANSLATION_SYSTEM_PROMPT)
        + estimate_text_tokens(translation_prompt_template())
        + estimate_text_tokens(translation_schema)
    )
    analysis = _stage_cost(
        model=spec.model,
        input_tokens=analysis_input,
        maximum_output_tokens=ANALYSIS_MAX_OUTPUT_TOKENS,
    )
    translation = _stage_cost(
        model=translation_model,
        input_tokens=translation_input,
        maximum_output_tokens=TRANSLATION_MAX_OUTPUT_TOKENS,
    )
    one_pass_usd = (
        analysis.maximum_stage_cost_usd
        + translation.maximum_stage_cost_usd
    )
    return CostEstimate(
        currency="USD",
        display_currency="JPY",
        usd_to_jpy_rate=USD_TO_JPY_ESTIMATE,
        pdf_tokens_per_page=0,
        analysis=analysis,
        translation=translation,
        maximum_one_pass_cost_usd=round(one_pass_usd, 6),
        maximum_configured_cost_usd=round(
            one_pass_usd * max_api_attempts,
            6,
        ),
        maximum_one_pass_cost_jpy=round(
            one_pass_usd * USD_TO_JPY_ESTIMATE,
            2,
        ),
        maximum_configured_cost_jpy=round(
            one_pass_usd
            * max_api_attempts
            * USD_TO_JPY_ESTIMATE,
            2,
        ),
        maximum_api_attempts_per_stage=max_api_attempts,
        assumptions=[
            (
                "Analysis input is estimated from the actual page-marked "
                "Docling Markdown corpus, system prompt, task prompt, and "
                "structured-output schema."
            ),
            (
                "No PDF bytes, page images, Files API uploads, or native PDF "
                "parts are included in the experimental model request."
            ),
            (
                "The translation stage is unchanged and receives the structured "
                "Japanese analysis rather than source documents."
            ),
            (
                "Maximum cost assumes both stages consume their configured "
                "maximum output."
            ),
            (
                "User-facing yen estimates use the fixed offline planning rate "
                f"of ¥{USD_TO_JPY_ESTIMATE:g} per USD."
            ),
        ],
    )


def build_text_analysis_spec(
    repository_root: Path,
    manifest: Any,
    corpus: str,
    extraction_manifest: dict[str, Any],
    *,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> RequestSpec:
    configuration = _profile_configuration(model_profile)
    base = build_analysis_spec(
        repository_root,
        manifest,
        model=configuration.analysis.model,
        model_profile=model_profile,
        provider=configuration.analysis.provider,
        provider_profile=configuration.analysis.provider_profile,
    )
    context = "\n\n".join(
        (
            base.context_prompt or "",
            SOURCE_REPRESENTATION_NOTICE,
            corpus.rstrip(),
        )
    ).strip()
    task = base.task_prompt or base.prompt
    prompt = f"{context}\n\n{task}"
    options = dict(base.request_options)
    options.pop("pdf_detail", None)
    options.update(
        {
            "source_representation": "docling_markdown",
            "source_corpus_sha256": extraction_manifest["corpus_sha256"],
            "docling_config_sha256": extraction_manifest["config_sha256"],
            "source_pdf_count": len(manifest.selected_files),
            "physical_pdf_pages": manifest.total_selected_pages,
        }
    )
    # The empty file tuple is the critical safety property: both existing
    # provider runtimes iterate spec.files to attach bytes or input_file parts.
    return replace(
        base,
        system_prompt=(
            f"{base.system_prompt.rstrip()}\n\n"
            f"{TEXT_INPUT_SYSTEM_ADDENDUM}"
        ),
        prompt=prompt,
        context_prompt=context,
        task_prompt=task,
        files=(),
        request_options=options,
    )


def _execute_text_model_request(
    repository_root: Path,
    prepared: PreparedRun,
    *,
    confirmed_request_id: str,
    max_attempts: int,
) -> Any:
    if (
        prepared.spec.request_options.get("source_representation")
        != "docling_markdown"
    ):
        raise PipelineConfigurationError(
            "The experimental analysis request lost its text-input marker."
        )
    if prepared.spec.files:
        raise PipelineConfigurationError(
            "Refusing to dispatch a Docling text request with file "
            "attachments."
        )
    if max_attempts != 1:
        raise PipelineConfigurationError(
            "Each manually authorized experimental stage permits exactly one "
            "API attempt."
        )
    return _execute_model_request(
        repository_root,
        prepared,
        confirmed_request_id=confirmed_request_id,
        max_attempts=1,
    )


def _write_analysis_preflight(
    *,
    repository_root: Path,
    prepared: PreparedRun,
    extraction_manifest: dict[str, Any],
    extraction_audit: dict[str, Any],
    current_pdf_token_estimate: int,
) -> None:
    paths = prepared.paths
    experiment_paths = experiment_artifact_paths(
        paths.output_dir.parent,
        prepared.manifest.security_code,
    )
    write_json(paths.selection_manifest, prepared.manifest)
    write_json(paths.analysis_request_plan, prepared.plan)
    paths.analysis_system_prompt.parent.mkdir(parents=True, exist_ok=True)
    paths.analysis_system_prompt.write_text(
        prepared.spec.system_prompt,
        encoding="utf-8",
    )
    paths.analysis_prompt.write_text(prepared.spec.prompt, encoding="utf-8")
    write_json(paths.analysis_schema, prepared.spec.response_schema)
    write_json(paths.cost, prepared.cost)
    write_json(
        paths.translation_request_plan,
        {
            "status": "pending_validated_japanese_analysis",
            "stage": "translation",
            "model_profile": prepared.spec.model_profile,
            "note": (
                "Complete and validate the experimental Japanese text-input "
                "analysis before preparing translation."
            ),
        },
    )
    write_json(
        paths.run_metadata,
        _metadata(
            repository_root,
            prepared,
            mode="dry-run",
            api_requests=0,
        ),
    )
    write_json(experiment_paths.extraction_audit, extraction_audit)
    corpus_tokens = estimate_text_tokens(
        experiment_paths.text_corpus.read_text(encoding="utf-8")
    )
    write_json(
        experiment_paths.input_size_comparison,
        {
            "schema_version": "docling-input-size-comparison-v1",
            "security_code": prepared.manifest.security_code,
            "selected_pdf_count": len(prepared.manifest.selected_files),
            "selected_pdf_pages": prepared.manifest.total_selected_pages,
            "selected_pdf_bytes": prepared.manifest.total_selected_bytes,
            "docling_corpus_characters": extraction_manifest[
                "total_characters"
            ],
            "estimated_docling_corpus_tokens": corpus_tokens,
            "current_pdf_planning_tokens": current_pdf_token_estimate,
            "text_to_pdf_planning_token_ratio": (
                round(corpus_tokens / current_pdf_token_estimate, 4)
                if current_pdf_token_estimate
                else None
            ),
            "analysis_request_estimated_input_tokens": (
                prepared.cost.analysis.estimated_input_tokens
            ),
            "note": (
                "This compares local text-token estimation with the current "
                "pipeline's page-based PDF planning estimate. Provider billing "
                "may tokenize both representations differently."
            ),
        },
    )
    write_json(
        experiment_paths.experiment_metadata,
        {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "security_code": prepared.manifest.security_code,
            "input_mode": "docling_markdown",
            "source_pdf_bytes_attached": False,
            "source_pdf_files_uploaded": False,
            "normal_pipeline_files_modified": False,
            "source_manifest_id": prepared.manifest.manifest_id,
            "source_corpus_sha256": extraction_manifest["corpus_sha256"],
            "docling_version": extraction_manifest["docling_version"],
            "docling_config_sha256": extraction_manifest["config_sha256"],
            "prepared_at_utc": _utc_now(),
        },
    )


def prepare_text_analysis(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    docling_python: Path | None = None,
    models_dir: Path | None = None,
    force_reparse: bool = False,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
    extractor_runner: ExtractorRunner | None = None,
) -> PreparedRun:
    repository_root = repository_root.resolve()
    requested_output_root = (
        output_root
        if output_root is not None
        else repository_root / DEFAULT_EXPERIMENT_OUTPUT_ROOT
    )
    try:
        output_root = validate_experiment_output_root(
            repository_root,
            requested_output_root,
        )
    except ValueError as exc:
        raise PipelineConfigurationError(str(exc)) from exc
    docling_python = (
        docling_python.resolve()
        if docling_python is not None
        else _default_docling_python(repository_root).resolve()
    )
    models_dir = (
        models_dir.resolve()
        if models_dir is not None
        else (repository_root / DEFAULT_DOCLING_MODELS_ROOT).resolve()
    )
    configuration = _profile_configuration(model_profile)
    manifest = select_filings(repository_root, security_code)
    paths = output_paths(output_root, security_code)
    experiment_paths = experiment_artifact_paths(output_root, security_code)
    experiment_paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    write_json(experiment_paths.selection_manifest, manifest)

    runner = extractor_runner or _default_extractor_runner
    runner(
        repository_root,
        experiment_paths.selection_manifest,
        output_root,
        docling_python,
        models_dir,
        force_reparse,
    )
    if not experiment_paths.extraction_manifest.is_file():
        raise PipelineConfigurationError(
            "Docling extraction completed without writing its manifest."
        )
    if not experiment_paths.text_corpus.is_file():
        raise PipelineConfigurationError(
            "Docling extraction completed without writing its text corpus."
        )
    extraction_manifest = read_json(experiment_paths.extraction_manifest)
    if extraction_manifest.get("source_manifest_id") != manifest.manifest_id:
        raise PipelineConfigurationError(
            "The Docling extraction belongs to a different selection manifest."
        )
    corpus = experiment_paths.text_corpus.read_text(encoding="utf-8")
    if sha256_text(corpus) != extraction_manifest.get("corpus_sha256"):
        raise PipelineConfigurationError(
            "The Docling text corpus hash does not match its manifest."
        )
    extraction_audit = audit_extraction(
        repository_root=repository_root,
        selection_manifest=manifest,
        extraction_manifest=extraction_manifest,
    )
    spec = build_text_analysis_spec(
        repository_root,
        manifest,
        corpus,
        extraction_manifest,
        model_profile=model_profile,
    )
    if spec.files:
        raise PipelineConfigurationError(
            "Text-input request unexpectedly contains source file attachments."
        )
    plan = spec.plan()
    cost = _estimate_text_cost(
        spec=spec,
        translation_model=configuration.translation.model,
        max_api_attempts=max_api_attempts,
    )
    prepared = PreparedRun(
        manifest=manifest,
        spec=spec,
        plan=plan,
        cost=cost,
        paths=paths,
    )
    _write_analysis_preflight(
        repository_root=repository_root,
        prepared=prepared,
        extraction_manifest=extraction_manifest,
        extraction_audit=extraction_audit,
        current_pdf_token_estimate=(
            manifest.total_selected_pages
            * configuration.pdf_tokens_per_page
        ),
    )
    return prepared


def _schema_only_cost(payload: dict[str, Any]) -> CostEstimate:
    return CostEstimate.model_validate(
        {
            key: payload[key]
            for key in CostEstimate.model_fields
            if key in payload
        }
    )


def _merge_translation_cost(
    original_payload: dict[str, Any],
    translation_prepared: PreparedRun,
    *,
    max_api_attempts: int,
) -> CostEstimate:
    original = _schema_only_cost(original_payload)
    translation = translation_prepared.cost.translation
    one_pass_usd = (
        original.analysis.maximum_stage_cost_usd
        + translation.maximum_stage_cost_usd
    )
    return CostEstimate(
        currency="USD",
        display_currency="JPY",
        usd_to_jpy_rate=USD_TO_JPY_ESTIMATE,
        pdf_tokens_per_page=0,
        analysis=original.analysis,
        translation=translation,
        maximum_one_pass_cost_usd=round(one_pass_usd, 6),
        maximum_configured_cost_usd=round(
            one_pass_usd * max_api_attempts,
            6,
        ),
        maximum_one_pass_cost_jpy=round(
            one_pass_usd * USD_TO_JPY_ESTIMATE,
            2,
        ),
        maximum_configured_cost_jpy=round(
            one_pass_usd
            * max_api_attempts
            * USD_TO_JPY_ESTIMATE,
            2,
        ),
        maximum_api_attempts_per_stage=max_api_attempts,
        assumptions=list(original.assumptions),
    )


def prepare_text_translation(
    repository_root: Path,
    security_code: str,
    *,
    output_root: Path | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    repository_root = repository_root.resolve()
    requested_output_root = (
        output_root
        if output_root is not None
        else repository_root / DEFAULT_EXPERIMENT_OUTPUT_ROOT
    )
    try:
        output_root = validate_experiment_output_root(
            repository_root,
            requested_output_root,
        )
    except ValueError as exc:
        raise PipelineConfigurationError(str(exc)) from exc
    paths = output_paths(output_root, security_code)
    if not paths.cost.is_file():
        raise PipelineValidationError(
            "Experimental text-input analysis cost artifact is missing."
        )
    original_payload = read_json(paths.cost)
    prepared = prepare_translation(
        repository_root,
        security_code,
        output_root=output_root,
        max_api_attempts=max_api_attempts,
        model_profile=model_profile,
    )
    merged_cost = _merge_translation_cost(
        original_payload,
        prepared,
        max_api_attempts=max_api_attempts,
    )
    merged_payload = merged_cost.model_dump(mode="json")
    for key in (
        "actual_cost_by_stage_usd",
        "actual_cost_total_usd",
        "actual_cost_total_jpy",
    ):
        if key in original_payload:
            merged_payload[key] = original_payload[key]
    write_json(paths.cost, merged_payload)
    return replace(prepared, cost=merged_cost)


def execute_text_analysis(
    repository_root: Path,
    security_code: str,
    *,
    confirmed_request_id: str,
    output_root: Path | None = None,
    docling_python: Path | None = None,
    models_dir: Path | None = None,
    force_reparse: bool = False,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    if max_api_attempts != 1:
        raise PipelineConfigurationError(
            "Experimental live analysis permits exactly one API attempt."
        )
    prepared = prepare_text_analysis(
        repository_root,
        security_code,
        output_root=output_root,
        docling_python=docling_python,
        models_dir=models_dir,
        force_reparse=force_reparse,
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
        result = _execute_text_model_request(
            repository_root.resolve(),
            prepared,
            confirmed_request_id=confirmed_request_id,
            max_attempts=1,
        )
    except Exception as exc:
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
    if not isinstance(
        result.structured,
        (JapaneseModelResponse, JapaneseAnalysis),
    ):
        raise PipelineValidationError(
            "Text-input analysis returned the wrong structured response type."
        )
    paths = prepared.paths
    write_json(paths.analysis_raw_response, result.raw_response)
    write_json(paths.analysis_structured, result.structured)
    write_json(
        paths.token_usage,
        _usage_artifact(
            stage="analysis",
            provider=prepared.spec.provider,
            model=prepared.spec.model,
            model_version=result.model_version,
            response_id=result.response_id,
            attempts=result.attempts,
            usage=result.usage,
        ),
    )
    _record_actual_cost(
        paths,
        stage="analysis",
        model=prepared.spec.model,
        usage=result.usage,
    )
    _process_japanese_response(
        repository_root.resolve(),
        prepared,
        result.structured,
        mode="analysis",
    )
    write_json(
        paths.run_metadata,
        _metadata(
            repository_root.resolve(),
            prepared,
            mode="analysis",
            api_requests=1,
        ),
    )
    return prepared


def execute_text_translation(
    repository_root: Path,
    security_code: str,
    *,
    confirmed_request_id: str,
    output_root: Path | None = None,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    model_profile: str = DEFAULT_MODEL_PROFILE,
) -> PreparedRun:
    if max_api_attempts != 1:
        raise PipelineConfigurationError(
            "Experimental live translation permits exactly one API attempt."
        )
    prepared = prepare_text_translation(
        repository_root,
        security_code,
        output_root=output_root,
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
            max_attempts=1,
        )
    except Exception as exc:
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
    if not isinstance(result.structured, EnglishTranslationPatch):
        raise PipelineValidationError(
            "Text-input translation returned the wrong response type."
        )
    translation = materialize_english_translation(analysis, result.structured)
    paths = prepared.paths
    write_json(paths.translation_raw_response, result.raw_response)
    write_json(paths.translation_structured, translation)
    prior_usage = (
        read_json(paths.token_usage)
        if paths.token_usage.is_file()
        else None
    )
    write_json(
        paths.token_usage,
        {
            "analysis": prior_usage,
            "translation": _usage_artifact(
                stage="translation",
                provider=prepared.spec.provider,
                model=prepared.spec.model,
                model_version=result.model_version,
                response_id=result.response_id,
                attempts=result.attempts,
                usage=result.usage,
            ),
        },
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
