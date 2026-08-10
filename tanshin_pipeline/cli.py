"""Command-line interface. Offline dry-run is the default."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import DEFAULT_OUTPUT_DIRECTORY, normalize_report_date
from .pipeline import (
    PipelineConfigurationError,
    PipelineValidationError,
    PreparedRun,
    compare_existing_reports,
    execute_analysis,
    execute_translation,
    prepare_analysis,
    prepare_translation,
    reprocess_stored_analysis,
    reprocess_stored_translation,
)


def _parse_report_date(value: str) -> str:
    try:
        return normalize_report_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Select Japanese Tanshin PDFs and prepare an evidence-validated "
            "Japanese/English report pipeline. Default mode is offline dry-run."
        )
    )
    parser.add_argument("security_code", help="Security code, for example 1808.")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to the current directory.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Output root. Defaults to <repository-root>/final_output.",
    )
    parser.add_argument(
        "--report-date",
        type=_parse_report_date,
        help=(
            "Local report date in YYYYMMDD form. Defaults to today's local "
            "date; runners pin this value for a complete invocation."
        ),
    )
    parser.add_argument(
        "--stage",
        choices=("analysis", "translation"),
        default="analysis",
        help="Prepare or execute exactly one stage.",
    )
    parser.add_argument(
        "--model-profile",
        "--gemini-profile",
        dest="model_profile",
        choices=(
            "default",
            "key2-translation",
            "pro-translation",
            "pro",
            "sol",
        ),
        default="default",
        help=(
            "Select default Flash/Flash, Flash analysis with secondary-key "
            "Flash or Pro translation, Pro/Pro, or hybrid Sol/Pro."
        ),
    )
    parser.add_argument(
        "--execute-api",
        action="store_true",
        help="Send exactly one stage request. Never implied by any other option.",
    )
    parser.add_argument(
        "--confirm-request",
        help="Request ID printed by a prior offline dry-run.",
    )
    parser.add_argument(
        "--max-api-attempts",
        type=int,
        default=1,
        help=(
            "Maximum requests for this stage. Defaults to 1; values above 1 "
            "explicitly authorize automatic retries."
        ),
    )
    parser.add_argument(
        "--compare-exemplar",
        action="store_true",
        help="Offline-only comparison of existing Markdown against any exemplar.",
    )
    parser.add_argument(
        "--reprocess-stored",
        action="store_true",
        help=(
            "Offline-only normalization, validation, and rendering of the stored "
            "response for --stage."
        ),
    )
    return parser


def _format_bytes(value: int) -> str:
    return f"{value / (1024 * 1024):.2f} MiB"


def _print_prepared(prepared: PreparedRun) -> None:
    manifest = prepared.manifest
    print("OFFLINE DRY RUN - no API request was sent.")
    print(f"Security code: {manifest.security_code}")
    print(f"Latest filing: {manifest.latest_filename}")
    print("Selected PDFs:")
    for item in manifest.selected_files:
        roles = ", ".join(item.roles)
        inference = " (inferred year-end)" if item.year_end_inferred else ""
        print(
            f"  - {item.filename}: {item.page_count} pages, "
            f"{_format_bytes(item.byte_size)}, {roles}{inference}"
        )
    print(
        f"Totals: {manifest.total_selected_pages} pages, "
        f"{_format_bytes(manifest.total_selected_bytes)}"
    )
    print(f"Prepared stage: {prepared.plan.stage}")
    print(f"Model profile: {prepared.plan.model_profile}")
    print(f"API provider: {prepared.plan.provider}")
    print(f"Model: {prepared.plan.model}")
    if prepared.plan.provider == "openai":
        pdf_detail = prepared.plan.request_options.get("pdf_detail")
        if pdf_detail is not None:
            print(f"PDF detail: {pdf_detail}")
    print(f"Request ID: {prepared.plan.request_id}")
    if prepared.plan.style_blueprint_path is not None:
        print(
            "Fact-free style blueprint: "
            f"{prepared.plan.style_blueprint_path} "
            f"(sha256 {prepared.plan.style_blueprint_sha256})"
        )
    print(
        "Estimated maximum one-pass cost for analysis + translation: "
        f"JPY {prepared.cost.maximum_one_pass_cost_jpy:,.0f}"
    )
    print(
        "Estimated maximum configured cost: "
        f"JPY {prepared.cost.maximum_configured_cost_jpy:,.0f}"
    )
    if prepared.plan.model_profile == "default":
        print(
            "Billing note: This profile uses only GEMINI_API_KEY and should be "
            "free when that key's project is eligible for the Gemini free tier; "
            "the JPY estimate is a paid-tier upper bound."
        )
    print("Intended report paths:")
    print(f"  - {prepared.paths.report_ja}")
    print(f"  - {prepared.paths.report_en}")
    print(
        "Schema-valid responses render to the canonical Markdown paths; "
        "validation findings remain in JSON diagnostics."
    )
    print(f"Selection manifest: {prepared.paths.selection_manifest}")
    print(f"Inspectable request plan: {prepared.paths.analysis_request_plan if prepared.plan.stage == 'analysis' else prepared.paths.translation_request_plan}")
    print("Live execution remains blocked unless --execute-api, the matching")
    print("--confirm-request value, and TANSHIN_LIVE_API=MANUAL_USER_RUN are all set.")


def _print_validation_failure(
    error: PipelineValidationError,
    output_root: Path,
    security_code: str,
    stage: str,
) -> int:
    print(f"PIPELINE BLOCKED: {error}", file=sys.stderr)
    artifact = (
        output_root
        / security_code
        / "artifacts"
        / ("validation_ja.json" if stage == "analysis" else "validation_en.json")
    )
    if artifact.is_file():
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        print(
            "Validation summary: "
            f"{payload.get('blocking_error_count', payload.get('statistics', {}).get('errors', 0))} "
            "blocking errors, "
            f"{payload.get('warning_count', payload.get('statistics', {}).get('warnings', 0))} "
            "warnings.",
            file=sys.stderr,
        )
        counts: dict[str, int] = {}
        for issue in payload.get("issues", []):
            if issue.get("severity") == "error":
                code = issue.get("code", "unknown")
                counts[code] = counts.get(code, 0) + 1
        for code, count in sorted(counts.items()):
            print(f"  - {code}: {count}", file=sys.stderr)
        print(f"Details: {artifact}", file=sys.stderr)
    return 2


def _print_api_status(
    output_root: Path,
    security_code: str,
    stage: str,
    *,
    file: object = sys.stderr,
) -> bool:
    artifact = (
        output_root
        / security_code
        / "artifacts"
        / (
            "api_status_analysis.json"
            if stage == "analysis"
            else "api_status_translation.json"
        )
    )
    if not artifact.is_file():
        print("API STATE: UNKNOWN (no status artifact)", file=file)
        return False
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    print(f"API STATE: {payload.get('state', 'UNKNOWN')}", file=file)
    if payload.get("provider"):
        print(f"Provider: {payload['provider']}", file=file)
    if payload.get("response_id"):
        print(f"Response ID: {payload['response_id']}", file=file)
    if payload.get("finish_reason"):
        print(f"Finish reason: {payload['finish_reason']}", file=file)
    if payload.get("state") in {
        "RATE_LIMITED",
        "TEMPORARILY_UNAVAILABLE",
        "FAILED",
    }:
        if payload.get("status_code") is not None:
            print(f"Status code: {payload['status_code']}", file=file)
        if payload.get("error_summary"):
            print(f"Summary: {payload['error_summary']}", file=file)
        if payload.get("retry_guidance"):
            print(f"Retry guidance: {payload['retry_guidance']}", file=file)
    print(f"API status artifact: {artifact}", file=file)
    return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root = args.repository_root.resolve()
    output_root = (
        args.output_root.resolve()
        if args.output_root is not None
        else repository_root / DEFAULT_OUTPUT_DIRECTORY
    )
    if args.max_api_attempts < 1:
        raise SystemExit("--max-api-attempts must be at least 1.")
    if args.compare_exemplar:
        if args.execute_api:
            raise SystemExit("--compare-exemplar cannot be combined with --execute-api.")
        result = compare_existing_reports(
            repository_root,
            args.security_code,
            output_root=output_root,
            report_date=args.report_date,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.reprocess_stored:
        if args.execute_api:
            raise SystemExit("--reprocess-stored cannot be combined with --execute-api.")
        try:
            result = (
                reprocess_stored_analysis(
                    repository_root,
                    args.security_code,
                    output_root=output_root,
                    report_date=args.report_date,
                    model_profile=args.model_profile,
                )
                if args.stage == "analysis"
                else reprocess_stored_translation(
                    repository_root,
                    args.security_code,
                    output_root=output_root,
                    report_date=args.report_date,
                    model_profile=args.model_profile,
                )
            )
        except PipelineValidationError as exc:
            return _print_validation_failure(
                exc, output_root, args.security_code, args.stage
            )
        except PipelineConfigurationError as exc:
            print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if not args.execute_api:
        try:
            prepared = (
                prepare_analysis(
                    repository_root,
                    args.security_code,
                    output_root=output_root,
                    report_date=args.report_date,
                    max_api_attempts=args.max_api_attempts,
                    model_profile=args.model_profile,
                )
                if args.stage == "analysis"
                else prepare_translation(
                    repository_root,
                    args.security_code,
                    output_root=output_root,
                    report_date=args.report_date,
                    max_api_attempts=args.max_api_attempts,
                    model_profile=args.model_profile,
                )
            )
        except PipelineValidationError as exc:
            return _print_validation_failure(
                exc, output_root, args.security_code, args.stage
            )
        except PipelineConfigurationError as exc:
            print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
            return 2
        _print_prepared(prepared)
        return 0

    if not args.confirm_request:
        raise SystemExit(
            "--execute-api requires --confirm-request from a prior dry-run."
        )
    try:
        if args.stage == "analysis":
            execute_analysis(
                repository_root,
                args.security_code,
                confirmed_request_id=args.confirm_request,
                output_root=output_root,
                report_date=args.report_date,
                max_api_attempts=args.max_api_attempts,
                model_profile=args.model_profile,
            )
        else:
            execute_translation(
                repository_root,
                args.security_code,
                confirmed_request_id=args.confirm_request,
                output_root=output_root,
                report_date=args.report_date,
                max_api_attempts=args.max_api_attempts,
                model_profile=args.model_profile,
            )
    except PipelineValidationError as exc:
        code = _print_validation_failure(
            exc, output_root, args.security_code, args.stage
        )
        _print_api_status(output_root, args.security_code, args.stage)
        return code
    except PipelineConfigurationError as exc:
        print(f"CONFIGURATION ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception:
        _print_api_status(output_root, args.security_code, args.stage)
        return 3
    _print_api_status(
        output_root,
        args.security_code,
        args.stage,
        file=sys.stdout,
    )
    print(f"Completed one manually authorized {args.stage} API stage.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
