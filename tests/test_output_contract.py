from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tanshin_pipeline.cli import main
from tanshin_pipeline.config import output_paths
from tanshin_pipeline.pipeline import _retire_current_report
from tests.helpers import workspace_temp_directory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class OutputContractTests(unittest.TestCase):
    def test_report_paths_use_the_requested_local_date(self) -> None:
        root = Path("final_output")
        paths = output_paths(
            root,
            "1808",
            report_date=date(2026, 8, 10),
        )

        self.assertEqual(paths.report_date, "20260810")
        self.assertEqual(
            paths.report_ja,
            root / "1808" / "analysis_ja_1808_20260810.md",
        )
        self.assertEqual(
            paths.report_en,
            root / "1808" / "analysis_en_1808_20260810.md",
        )

    def test_invalid_report_date_is_rejected_by_the_cli(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["1808", "--report-date", "2026-08-10"])

    def test_default_cli_root_is_final_output(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as repository_root:
            stdout = io.StringIO()
            with (
                patch(
                    "tanshin_pipeline.cli.compare_existing_reports",
                    return_value={},
                ) as compare,
                redirect_stdout(stdout),
            ):
                result = main(
                    [
                        "1808",
                        "--repository-root",
                        str(repository_root),
                        "--compare-exemplar",
                        "--report-date",
                        "20260810",
                    ]
                )

        self.assertEqual(result, 0)
        self.assertEqual(
            compare.call_args.kwargs["output_root"],
            repository_root / "final_output",
        )
        self.assertEqual(compare.call_args.kwargs["report_date"], "20260810")

    def test_retirement_preserves_dated_filenames_in_history(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temporary_directory:
            output_root = temporary_directory / "final_output"
            paths = output_paths(
                output_root,
                "1808",
                report_date="20260810",
            )
            paths.output_dir.mkdir(parents=True)
            prior_names = [
                "analysis_ja_1808_20260808.md",
                "analysis_ja_1808_20260809.md",
            ]
            for name in prior_names:
                (paths.output_dir / name).write_text(name, encoding="utf-8")

            archived_to = _retire_current_report(paths, "ja")

            self.assertIsNotNone(archived_to)
            self.assertEqual(
                sorted(
                    path.name
                    for path in (paths.output_dir / "history").glob("*/*.md")
                ),
                prior_names,
            )
            self.assertEqual(Path(archived_to).name, prior_names[-1])
            for name in prior_names:
                self.assertFalse((paths.output_dir / name).exists())


if __name__ == "__main__":
    unittest.main()
