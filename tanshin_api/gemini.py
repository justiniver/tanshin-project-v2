"""Minimal Gemini API configuration.

Importing this module never sends an API request. Call ``get_gemini_client()``
when a future pipeline stage is ready to use Gemini.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from google import genai

from tanshin_pipeline.config import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_MODEL_PROFILE,
    DEFAULT_TRANSLATION_MODEL,
    KEY2_TRANSLATION_MODEL_PROFILE,
    PRO_GEMINI_MODEL,
    PRO_MODEL_PROFILE,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GeminiProfile = Literal["default", "key2-translation", "pro"]
GeminiStage = Literal["analysis", "translation"]


def load_repository_environment() -> None:
    """Load local development settings without overriding shell variables."""

    if os.getenv("TANSHIN_TESTING") == "1":
        return
    load_dotenv(REPOSITORY_ROOT / ".env", override=False)


def _validate_profile(profile: str) -> GeminiProfile:
    if profile not in {
        DEFAULT_MODEL_PROFILE,
        KEY2_TRANSLATION_MODEL_PROFILE,
        PRO_MODEL_PROFILE,
    }:
        raise ValueError(f"Unknown Gemini model profile: {profile!r}.")
    return profile


def get_gemini_model(
    profile: GeminiProfile = DEFAULT_MODEL_PROFILE,
    stage: GeminiStage = "analysis",
) -> str:
    """Return the fixed repository model for one profile and pipeline stage."""

    selected_profile = _validate_profile(profile)
    if selected_profile == PRO_MODEL_PROFILE:
        return PRO_GEMINI_MODEL
    if selected_profile == KEY2_TRANSLATION_MODEL_PROFILE:
        return DEFAULT_ANALYSIS_MODEL
    if stage == "translation":
        return DEFAULT_TRANSLATION_MODEL
    return DEFAULT_ANALYSIS_MODEL


def get_gemini_client(
    profile: GeminiProfile = DEFAULT_MODEL_PROFILE,
) -> genai.Client:
    """Create a Gemini client using the selected non-secret profile."""

    load_repository_environment()
    selected_profile = _validate_profile(profile)
    key_name = (
        "GEMINI_API_KEY2"
        if selected_profile
        in {KEY2_TRANSLATION_MODEL_PROFILE, PRO_MODEL_PROFILE}
        else "GEMINI_API_KEY"
    )
    api_key = os.getenv(key_name, "").strip()
    if not api_key or api_key == "replace-with-your-api-key":
        raise RuntimeError(
            f"{key_name} is not configured. Copy .env.example to .env "
            "and add a Google AI Studio API key for this profile."
        )
    return genai.Client(api_key=api_key)
