"""Shared API clients for the Tanshin report pipeline."""

from .gemini import get_gemini_client, get_gemini_model

__all__ = ["get_gemini_client", "get_gemini_model"]

