"""Minimal OpenAI API configuration for the optional Sol analysis profile.

Importing this module never sends an API request. The environment and client
are materialized only from the explicitly authorized live runtime.
"""

from __future__ import annotations

import os

from openai import OpenAI

from tanshin_api.gemini import load_repository_environment
from tanshin_pipeline.config import OPENAI_SOL_MODEL


def get_openai_model() -> str:
    """Return the single OpenAI model supported by this pipeline profile."""

    return OPENAI_SOL_MODEL


def get_openai_client() -> OpenAI:
    """Create an OpenAI client without exposing the configured credential."""

    load_repository_environment()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "replace-with-your-api-key":
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. Copy .env.example to .env "
            "and add an OpenAI API key for the Sol analysis profile."
        )
    return OpenAI(api_key=api_key)
