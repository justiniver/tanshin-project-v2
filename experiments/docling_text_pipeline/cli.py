"""Offline-by-default CLI for the removable Docling text-input experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tanshin_pipeline.pipeline import (
    PipelineConfigurationError,
    PipelineValidationError,
    PreparedRun,
)

from .common import (
    DEFAULT_DOCLING_MODELS_ROOT,
    DEFAULT_EXPERIMENT_OUTPUT_ROOT,
    experiment_artifact_paths,
)
from .pipeline import (
    execute_text_analysis,
    execute_text_translation,
    prepare_text_analysis,
    prepare_text_translation,
)


MODEL_PROFILES = (
    "default",
    "flash-translation",
    "pro-translation",
    "pro",
    "sol",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Experimental Tanshin pipeline that parses selected PDFs locally "
            "with Docling and supplies page-marked Markdown to the analysis "
            "model. Default mode is an offline dry run."
        )
    )
    parser.add_argument("security_code")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help=(
            "Experiment output root. Defaults to "
            "output/experiments/docling_text."
        ),
    )
    parser.add_argument(
        "--docling-python",
        type=Path,
        help=(
            "Python executable in the disposable parser environment under "
            "output/experiments/docling_venv."
        ),
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        help=(
            "Prefetched local Docling model directory. Defaults to "
            "output/experiments/docling_models."
        ),
    )
    parser.add_argument(
        "--stage",
        choices=("analysis", "translation"),
        default="analysis",
    )
    parser.add_argument(
        "--model-profile",
        choices=MODEL_PROFILES,
        default="default",
    )
    parser.add_argument(
        "--force-reparse",
        action="store_true",
        help="Ignore the Docling extraction cache for the analysis stage.",
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help=(
            "Complete local extraction, audit, and request preparation, then "
            "stop. This is offline and is valid only for analysis."
        ),
    )
    parser.add_argument(
        "--execute-api",
        action="store_true",
        help=(
            "Send exactly one manually authorized stage request. Never implied "
            "by extraction, preparation, or another option."
        ),
    )
    parser.add_argument("--confirm-request")
    parser.add_argument(
        "--max-api-attempts",
        type=int,
        default=1,
    )
    return parser


def _resolved_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    repository_root = args.repository_root.resolve()
    output_root = (
        args.output_root.resolve()
        if args.output_root is not None
        else (repository_root / DEFAULT_EXPERIMENT_OUTPUT_ROOT).resolve()
    )
    models_dir = (
        args.models_dir.resolve()
        if args.models_dir is not None
        else (repository_root / DEFAULT_DOCLING_MODELS_ROOT).resolve()
    )
    return repository_root, output_root, models_dir


def _print_prepared(prepared: PreparedRun, *, extract_only: bool) -> None:
    manifest = prepared.manifest
    output_root = prepared.paths.output_dir.parent
    experimental = experiment_artifact_paths(
        output_root,
        manifest.security_code,
    )
    comparison = json.loads(
        experimental.input_size_comparison.read_text(encoding="utf-8")
    )
    extraction = json.loads(
        experimental.extraction_manifest.read_text(encoding="utf-8")
    )
    audit = json.loads(
        experimental.extraction_audit.read_text(encoding="utf-8")
    )
    print("EXPERIMENTAL OFFLINE DRY RUN - no API request was sent.")
    print("Input mode: page-marked Docling Markdown")
    print("PDF bytes attached to model request: 0")
    print(f"Security code: {manifest.security_code}")
    print(f"Latest filing: {manifest.latest_filename}")
    print("Selected PDFs parsed locally:")
    extraction_by_name = {
        item["filename"]: item
        for item in extraction["files"]
    }
    for selected in manifest.selected_files:
        parsed = extraction_by_name[selected.filename]
        print(
            f"  - {selected.filename}: {selected.page_count} pages, "
            f"{parsed['cache_status']}"
        )
    print(
        f"Totals: {manifest.total_selected_pages} physical pages, "
        f"{comparison['docling_corpus_characters']:,} Markdown characters"
    )
    print(
        "Estimated Docling corpus tokens: "
        f"{comparison['estimated_docling_corpus_tokens']:,}"
    )
    print(
        "Current PDF planning estimate: "
        f"{comparison['current_pdf_planning_tokens']:,} tokens"
    )
    print(
        "Text/PDF planning-token ratio: "
        f"{comparison['text_to_pdf_planning_token_ratio']:.2f}x"
    )
    print(
        f"Extraction audit warnings: {audit['warning_count']} "
        "(diagnostic only)"
    )
    print(f"Prepared stage: {prepared.plan.stage}")
    print(f"Model profile: {prepared.plan.model_profile}")
    print(f"Provider: {prepared.plan.provider}")
    print(f"Model: {prepared.plan.model}")
    print(f"Request ID: {prepared.plan.request_id}")
    print(
        "Estimated maximum analysis cost: "
        f"JPY {prepared.cost.analysis.maximum_stage_cost_jpy:,.0f}"
    )
    print(
        "Estimated maximum optional translation cost: "
        f"JPY {prepared.cost.translation.maximum_stage_cost_jpy:,.0f}"
    )
    print(
        "Yen conversion assumption: "
        f"JPY {prepared.cost.usd_to_jpy_rate:g} per USD"
    )
    print(f"Text corpus: {experimental.text_corpus}")
    print(f"Extraction manifest: {experimental.extraction_manifest}")
    print(f"Extraction audit: {experimental.extraction_audit}")
    print(f"Input-size comparison: {experimental.input_size_comparison}")
    print(f"Inspectable request plan: {prepared.paths.analysis_request_plan}")
    print(f"Experimental Japanese report: {prepared.paths.report_ja}")
    print(f"Optional English report: {prepared.paths.report_en}")
    if extract_only:
        print("EXTRACT ONLY: local preparation is complete.")
    else:
        print(
            "Live execution remains blocked unless --execute-api, the exact "
            "--confirm-request value, and TANSHIN_LIVE_API=MANUAL_USER_RUN "
            "are all supplied."
        )


def _print_translation_prepared(prepared: PreparedRun) -> None:
    print("EXPERIMENTAL TRANSLATION DRY RUN - no API request was sent.")
    print("Translation input: stored structured Japanese analysis")
    print("Source PDFs submitted: none")
    print(f"Security code: {prepared.manifest.security_code}")
    print(f"Model profile: {prepared.plan.model_profile}")
    print(f"Provider: {prepared.plan.provider}")
    print(f"Model: {prepared.plan.model}")
    print(f"Request ID: {prepared.plan.request_id}")
    print(
        "Estimated maximum translation cost: "
        f"JPY {prepared.cost.translation.maximum_stage_cost_jpy:,.0f}"
    )
    print(f"Expected English report: {prepared.paths.report_en}")


def _print_api_status(
    output_root: Path,
    security_code: str,
    stage: str,
) -> None:
    name = (
        "api_status_analysis.json"
        if stage == "analysis"
        else "api_status_translation.json"
    )
    path = output_root / security_code / "artifacts" / name
    if not path.is_file():
        print("API STATE: UNKNOWN (no status artifact)")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"API STATE: {payload.get('state', 'UNKNOWN')}")
    print(f"Provider: {payload.get('provider', 'unknown')}")
    if payload.get("status_code") is not None:
        print(f"Status code: {payload['status_code']}")
    if payload.get("error_summary"):
        print(f"Summary: {payload['error_summary']}")
    if payload.get("response_id"):
        print(f"Response ID: {payload['response_id']}")
    print(f"API status artifact: {path}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root, output_root, models_dir = _resolved_paths(args)
    if args.max_api_attempts < 1:
        raise SystemExit("--max-api-attempts must be at least 1.")
    if args.execute_api and args.max_api_attempts != 1:
        raise SystemExit(
            "Experimental live execution permits exactly one API attempt."
        )
    if args.extract_only and args.stage != "analysis":
        raise SystemExit("--extract-only is valid only for analysis.")
    if args.extract_only and args.execute_api:
        raise SystemExit("--extract-only cannot be combined with --execute-api.")

    prepare_kwargs = {
        "output_root": output_root,
        "max_api_attempts": args.max_api_attempts,
        "model_profile": args.model_profile,
    }
    if not args.execute_api:
        try:
            if args.stage == "analysis":
                prepared = prepare_text_analysis(
                    repository_root,
                    args.security_code,
                    docling_python=args.docling_python,
                    models_dir=models_dir,
                    force_reparse=args.force_reparse,
                    **prepare_kwargs,
                )
                _print_prepared(prepared, extract_only=args.extract_only)
            else:
                prepared = prepare_text_translation(
                    repository_root,
                    args.security_code,
                    **prepare_kwargs,
                )
                _print_translation_prepared(prepared)
        except (PipelineConfigurationError, PipelineValidationError) as exc:
            print(f"EXPERIMENT BLOCKED: {exc}", file=sys.stderr)
            return 2
        return 0

    if not args.confirm_request:
        raise SystemExit(
            "--execute-api requires --confirm-request from a prior dry run."
        )
    try:
        if args.stage == "analysis":
            execute_text_analysis(
                repository_root,
                args.security_code,
                confirmed_request_id=args.confirm_request,
                docling_python=args.docling_python,
                models_dir=models_dir,
                force_reparse=args.force_reparse,
                **prepare_kwargs,
            )
        else:
            execute_text_translation(
                repository_root,
                args.security_code,
                confirmed_request_id=args.confirm_request,
                **prepare_kwargs,
            )
    except (PipelineConfigurationError, PipelineValidationError) as exc:
        print(f"EXPERIMENT BLOCKED: {exc}", file=sys.stderr)
        _print_api_status(output_root, args.security_code, args.stage)
        return 2
    except Exception:
        _print_api_status(output_root, args.security_code, args.stage)
        return 3
    _print_api_status(output_root, args.security_code, args.stage)
    print(f"Completed one manually authorized {args.stage} API stage.")
    return 0
