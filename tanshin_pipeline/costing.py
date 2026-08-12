"""Conservative offline token and cost estimation."""

from __future__ import annotations

import json
import math
from typing import Any

from .config import (
    ANALYSIS_MAX_OUTPUT_TOKENS,
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_MAX_API_ATTEMPTS,
    DEFAULT_TRANSLATION_MODEL,
    MODEL_PRICES_USD,
    PDF_TOKENS_PER_PAGE,
    RESEARCH_MAX_OUTPUT_TOKENS,
    TRANSLATION_MAX_OUTPUT_TOKENS,
    USD_TO_JPY_ESTIMATE,
    model_price_for_input_tokens,
)
from .schemas import CostEstimate, CostStage, SelectionManifest


def estimate_text_tokens(text: str) -> int:
    """Conservatively estimate multilingual prompt tokens without an API call."""

    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return math.ceil(ascii_chars / 4) + non_ascii_chars


def _stage(
    model: str,
    input_tokens: int,
    maximum_output_tokens: int,
) -> CostStage:
    price = model_price_for_input_tokens(model, input_tokens)
    input_cost = input_tokens / 1_000_000 * price.input_per_million
    output_cost = maximum_output_tokens / 1_000_000 * price.output_per_million
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


def estimate_cost(
    manifest: SelectionManifest,
    *,
    research_system_prompt: str,
    research_prompt: str,
    research_response_schema: dict[str, Any],
    analysis_system_prompt: str,
    analysis_prompt: str,
    analysis_response_schema: dict[str, Any],
    translation_system_prompt: str,
    translation_prompt_template: str,
    translation_response_schema: dict[str, Any],
    analysis_model: str = DEFAULT_ANALYSIS_MODEL,
    translation_model: str = DEFAULT_TRANSLATION_MODEL,
    max_api_attempts: int = DEFAULT_MAX_API_ATTEMPTS,
    pdf_tokens_per_page: int = PDF_TOKENS_PER_PAGE,
    pdf_token_assumption: str | None = None,
    analysis_prompt_includes_source: bool = False,
    translation_prompt_includes_source: bool = False,
) -> CostEstimate:
    if analysis_model not in MODEL_PRICES_USD:
        raise ValueError(f"No offline price configured for {analysis_model}.")
    if translation_model not in MODEL_PRICES_USD:
        raise ValueError(f"No offline price configured for {translation_model}.")
    if max_api_attempts < 1:
        raise ValueError("max_api_attempts must be at least one.")
    if pdf_tokens_per_page < 1:
        raise ValueError("pdf_tokens_per_page must be at least one.")

    pdf_tokens = manifest.total_selected_pages * pdf_tokens_per_page
    document_metadata = "\n".join(
        (
            "<DOCUMENT_METADATA>"
            f"<source_filename>{item.filename}</source_filename>"
            f"<physical_pdf_pages>{item.page_count}</physical_pdf_pages>"
            "</DOCUMENT_METADATA>"
        )
        for item in manifest.selected_files
    )
    research_schema_text = json.dumps(
        research_response_schema,
        ensure_ascii=False,
        sort_keys=True,
    )
    analysis_schema_text = json.dumps(
        analysis_response_schema,
        ensure_ascii=False,
        sort_keys=True,
    )
    translation_schema_text = json.dumps(
        translation_response_schema,
        ensure_ascii=False,
        sort_keys=True,
    )
    research_text = "\n".join(
        (
            research_system_prompt,
            document_metadata,
            research_prompt,
            research_schema_text,
        )
    )
    research_input = pdf_tokens + estimate_text_tokens(research_text)
    analysis_input = (
        (0 if analysis_prompt_includes_source else RESEARCH_MAX_OUTPUT_TOKENS)
        + estimate_text_tokens(analysis_system_prompt)
        + estimate_text_tokens(analysis_prompt)
        + estimate_text_tokens(analysis_schema_text)
    )
    translation_input = (
        (0 if translation_prompt_includes_source else ANALYSIS_MAX_OUTPUT_TOKENS)
        + estimate_text_tokens(translation_system_prompt)
        + estimate_text_tokens(translation_prompt_template)
        + estimate_text_tokens(translation_schema_text)
    )
    research = _stage(
        analysis_model,
        research_input,
        RESEARCH_MAX_OUTPUT_TOKENS,
    )
    analysis = _stage(
        analysis_model,
        analysis_input,
        ANALYSIS_MAX_OUTPUT_TOKENS,
    )
    translation = _stage(
        translation_model,
        translation_input,
        TRANSLATION_MAX_OUTPUT_TOKENS,
    )
    one_pass = (
        research.maximum_stage_cost_usd
        + analysis.maximum_stage_cost_usd
        + translation.maximum_stage_cost_usd
    )
    return CostEstimate(
        currency="USD",
        display_currency="JPY",
        usd_to_jpy_rate=USD_TO_JPY_ESTIMATE,
        pdf_tokens_per_page=pdf_tokens_per_page,
        research=research,
        analysis=analysis,
        translation=translation,
        maximum_one_pass_cost_usd=round(one_pass, 6),
        maximum_configured_cost_usd=round(one_pass * max_api_attempts, 6),
        maximum_one_pass_cost_jpy=round(
            one_pass * USD_TO_JPY_ESTIMATE,
            2,
        ),
        maximum_configured_cost_jpy=round(
            one_pass * max_api_attempts * USD_TO_JPY_ESTIMATE,
            2,
        ),
        maximum_api_attempts_per_stage=max_api_attempts,
        assumptions=[
            (
                pdf_token_assumption
                or (
                    f"Each PDF page is estimated at {pdf_tokens_per_page} "
                    "billable visual tokens, rounded above observed Gemini usage."
                )
            ),
            (
                "No separate native-PDF text increment is added beyond the "
                "selected per-page planning estimate."
            ),
            (
                "System instructions, document metadata, prompts, and response "
                "schemas use a conservative multilingual character-based estimate."
            ),
            (
                "The materialized analysis prompt already contains its research "
                "dossier, so no separate research-output allowance is added."
                if analysis_prompt_includes_source
                else (
                    "Analysis planning reserves the configured maximum research "
                    "output because the dossier is not materialized yet."
                )
            ),
            (
                "The materialized translation prompt already contains its source "
                "analysis, so no separate analysis-output allowance is added."
                if translation_prompt_includes_source
                else (
                    "Translation planning reserves the configured maximum analysis "
                    "output because the source analysis is not materialized yet."
                )
            ),
            (
                "Maximum cost assumes research, analysis, and translation consume "
                "their configured maximum output."
            ),
            "No caching, batch, flex, priority, grounding, or free-tier discount is assumed.",
            (
                "Models with long-context pricing use the higher standard tier "
                "when the estimated prompt exceeds the published threshold."
            ),
            (
                "User-facing yen estimates convert USD model prices at the fixed "
                f"offline planning rate of ¥{USD_TO_JPY_ESTIMATE:g} per USD; "
                "the final card or account conversion may differ."
            ),
        ],
    )
