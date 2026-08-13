"""Shared configuration that is safe to import in offline mode."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


SCHEMA_VERSION = "2.1"
DEFAULT_OUTPUT_DIRECTORY = "final_output"
DEFAULT_MODEL_PROFILE = "default"
KEY2_TRANSLATION_MODEL_PROFILE = "key2-translation"
PRO_TRANSLATION_MODEL_PROFILE = "pro-translation"
PRO_MODEL_PROFILE = "pro"
SOL_MODEL_PROFILE = "sol"
DEFAULT_ANALYSIS_MODEL = "gemini-3.6-flash"
DEFAULT_TRANSLATION_MODEL = DEFAULT_ANALYSIS_MODEL
PRO_GEMINI_MODEL = "gemini-3.1-pro-preview"
OPENAI_SOL_MODEL = "gpt-5.6-sol"
OPENAI_PDF_DETAIL = "low"
# Calibrated from the first 1808 production response: Gemini reported roughly
# 532 image tokens per physical PDF page. Round upward for safer dry-run costs.
PDF_TOKENS_PER_PAGE = 540
# OpenAI PDF input includes extracted text and page images. The API does not
# publish one universal page-token count. Sol uses low-detail page images while
# retaining extracted PDF text, so keep a conservative corpus-level planning
# estimate without calling an API tokenizer.
OPENAI_PDF_TOKENS_PER_PAGE = 1_500
OPENAI_MAX_INLINE_PDF_BYTES = 50_000_000
RESEARCH_MAX_OUTPUT_TOKENS = 32_768
ANALYSIS_MAX_OUTPUT_TOKENS = 32_768
TRANSLATION_MAX_OUTPUT_TOKENS = 24_576
# Keep the PDF-backed research response comfortably below the model's output
# ceiling. These are extraction ceilings, not minimum coverage requirements.
RESEARCH_MAX_SOURCE_RECORDS = 48
RESEARCH_MAX_FINANCIAL_OBSERVATIONS = 28
RESEARCH_MAX_COMMENTARY_OBSERVATIONS = 20
RESEARCH_MAX_DISCLOSURES = 8
RESEARCH_MAX_COMMITMENTS = 8
RESEARCH_MAX_NOTES = 6
DEFAULT_MAX_API_ATTEMPTS = 1
LIVE_CONFIRMATION_VALUE = "MANUAL_USER_RUN"
# Cost preparation remains fully offline. Model list prices are denominated in
# USD, so user-facing yen estimates use this transparent planning rate.
USD_TO_JPY_ESTIMATE = 150.0


@dataclass(frozen=True)
class Price:
    input_per_million: float
    output_per_million: float
    long_context_threshold: int | None = None
    long_context_input_per_million: float | None = None
    long_context_output_per_million: float | None = None


MODEL_PRICES_USD = {
    DEFAULT_ANALYSIS_MODEL: Price(input_per_million=1.50, output_per_million=7.50),
    PRO_GEMINI_MODEL: Price(
        input_per_million=2.00,
        output_per_million=12.00,
        long_context_threshold=200_000,
        long_context_input_per_million=4.00,
        long_context_output_per_million=18.00,
    ),
    OPENAI_SOL_MODEL: Price(
        input_per_million=5.00,
        output_per_million=30.00,
        long_context_threshold=272_000,
        long_context_input_per_million=10.00,
        long_context_output_per_million=45.00,
    ),
}


def model_price_for_input_tokens(model: str, input_tokens: int) -> Price:
    """Return the applicable standard API price tier for a model request."""

    price = MODEL_PRICES_USD[model]
    if (
        price.long_context_threshold is not None
        and input_tokens > price.long_context_threshold
    ):
        assert price.long_context_input_per_million is not None
        assert price.long_context_output_per_million is not None
        return Price(
            input_per_million=price.long_context_input_per_million,
            output_per_million=price.long_context_output_per_million,
        )
    return Price(
        input_per_million=price.input_per_million,
        output_per_million=price.output_per_million,
    )


@dataclass(frozen=True)
class OutputPaths:
    security_code: str
    report_date: str
    output_dir: Path
    artifacts_dir: Path
    report_ja: Path
    report_en: Path
    report_ja_draft: Path
    report_en_draft: Path
    report_status_ja: Path
    report_status_en: Path
    selection_manifest: Path
    run_metadata: Path
    research_request_plan: Path
    research_system_prompt: Path
    research_prompt: Path
    research_schema: Path
    research_raw_response: Path
    research_api_status: Path
    research_structured: Path
    research_metrics: Path
    research_validation: Path
    analysis_request_plan: Path
    analysis_system_prompt: Path
    analysis_prompt: Path
    analysis_schema: Path
    analysis_raw_response: Path
    analysis_api_status: Path
    analysis_structured: Path
    analysis_normalized: Path
    analysis_normalization: Path
    analysis_validation: Path
    management_consistency: Path
    translation_request_plan: Path
    translation_system_prompt: Path
    translation_prompt: Path
    translation_schema: Path
    translation_raw_response: Path
    translation_api_status: Path
    translation_structured: Path
    translation_normalized: Path
    translation_normalization: Path
    translation_validation: Path
    evidence_ledger: Path
    token_usage: Path
    cost: Path
    evaluation_ja: Path
    evaluation_en: Path


def normalize_report_date(value: date | datetime | str | None = None) -> str:
    """Return the local report date in the filename-safe YYYYMMDD form."""

    if value is None:
        value = date.today()
    if isinstance(value, str):
        try:
            parsed = datetime.strptime(value, "%Y%m%d").date()
        except ValueError as exc:
            raise ValueError(
                f"Report date must use YYYYMMDD format, received {value!r}."
            ) from exc
        return parsed.strftime("%Y%m%d")
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        raise TypeError(
            "Report date must be a date, datetime, YYYYMMDD string, or None."
        )
    return value.strftime("%Y%m%d")


def output_paths(
    output_root: Path,
    security_code: str,
    *,
    report_date: date | datetime | str | None = None,
) -> OutputPaths:
    date_stamp = normalize_report_date(report_date)
    output_dir = output_root / security_code
    artifacts = output_dir / "artifacts"
    return OutputPaths(
        security_code=security_code,
        report_date=date_stamp,
        output_dir=output_dir,
        artifacts_dir=artifacts,
        report_ja=output_dir / f"analysis_ja_{security_code}_{date_stamp}.md",
        report_en=output_dir / f"analysis_en_{security_code}_{date_stamp}.md",
        report_ja_draft=(
            output_dir / f"analysis_ja_{security_code}_draft_{date_stamp}.md"
        ),
        report_en_draft=(
            output_dir / f"analysis_en_{security_code}_draft_{date_stamp}.md"
        ),
        report_status_ja=artifacts / "report_status_ja.json",
        report_status_en=artifacts / "report_status_en.json",
        selection_manifest=artifacts / "selection_manifest.json",
        run_metadata=artifacts / "run_metadata.json",
        research_request_plan=artifacts / "request_plan_research.json",
        research_system_prompt=artifacts / "system_prompt_research.txt",
        research_prompt=artifacts / "prompt_research.txt",
        research_schema=artifacts / "schema_research.json",
        research_raw_response=artifacts / "model_response_research.raw.json",
        research_api_status=artifacts / "api_status_research.json",
        research_structured=artifacts / "research.structured.json",
        research_metrics=artifacts / "research_metrics.json",
        research_validation=artifacts / "validation_research.json",
        analysis_request_plan=artifacts / "request_plan_analysis.json",
        analysis_system_prompt=artifacts / "system_prompt_analysis.txt",
        analysis_prompt=artifacts / "prompt_analysis.txt",
        analysis_schema=artifacts / "schema_analysis.json",
        analysis_raw_response=artifacts / "model_response_ja.raw.json",
        analysis_api_status=artifacts / "api_status_analysis.json",
        analysis_structured=artifacts / "analysis_ja.structured.json",
        analysis_normalized=artifacts / "analysis_ja.normalized.json",
        analysis_normalization=artifacts / "normalization_ja.json",
        analysis_validation=artifacts / "validation_ja.json",
        management_consistency=artifacts / "management_consistency.json",
        translation_request_plan=artifacts / "request_plan_translation.json",
        translation_system_prompt=artifacts / "system_prompt_translation.txt",
        translation_prompt=artifacts / "prompt_translation.txt",
        translation_schema=artifacts / "schema_translation.json",
        translation_raw_response=artifacts / "model_response_en.raw.json",
        translation_api_status=artifacts / "api_status_translation.json",
        translation_structured=artifacts / "analysis_en.structured.json",
        translation_normalized=artifacts / "analysis_en.normalized.json",
        translation_normalization=artifacts / "normalization_en.json",
        translation_validation=artifacts / "validation_en.json",
        evidence_ledger=artifacts / "evidence_ledger.json",
        token_usage=artifacts / "token_usage.json",
        cost=artifacts / "cost.json",
        evaluation_ja=artifacts / "exemplar_comparison_ja.json",
        evaluation_en=artifacts / "exemplar_comparison_en.json",
    )
