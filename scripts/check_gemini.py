"""Verify local Gemini configuration, with an optional live API request."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from tanshin_api.gemini import (  # noqa: E402
    get_gemini_client,
    get_gemini_model,
    load_repository_environment,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that the repository can use the Gemini API."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send one very small request to Gemini. Without this flag, no API call is made.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_repository_environment()

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key == "replace-with-your-api-key":
        print(
            "Gemini SDK is installed, but GEMINI_API_KEY is not configured.\n"
            "Copy .env.example to .env and add your Google AI Studio API key."
        )
        return 1

    model = get_gemini_model()
    if not args.live:
        print(
            f"Gemini is configured for model '{model}'. "
            "No API request was sent."
        )
        return 0

    client = get_gemini_client()
    try:
        interaction = client.interactions.create(
            model=model,
            input="Reply with exactly: GEMINI_OK",
        )
        response_text = interaction.output_text.strip()
    finally:
        client.close()

    if response_text != "GEMINI_OK":
        print(f"Gemini responded, but returned an unexpected value: {response_text!r}")
        return 2

    print(f"Live Gemini check succeeded with model '{model}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

