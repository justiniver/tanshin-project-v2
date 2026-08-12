from __future__ import annotations

import io
import socket
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tanshin_pipeline.cli import main
from tanshin_pipeline.persistence import read_json
from tests.helpers import workspace_temp_directory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DryRunTests(unittest.TestCase):
    def test_default_cli_is_offline_and_writes_inspection_artifacts(self) -> None:
        real_import = __import__

        def guarded_import(name, *args, **kwargs):
            if name.endswith(("gemini_runtime", "openai_runtime")):
                raise AssertionError("Dry-run imported the live runtime.")
            return real_import(name, *args, **kwargs)

        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            stdout = io.StringIO()
            with (
                patch("builtins.__import__", side_effect=guarded_import),
                patch.object(
                    socket,
                    "socket",
                    side_effect=AssertionError("Network access attempted in dry-run."),
                ),
                redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "1808",
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                        "--output-root",
                        str(output_root),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertIn("no API request was sent", stdout.getvalue())
            self.assertIn("Prepared stage: research", stdout.getvalue())
            artifacts = output_root / "1808" / "artifacts"
            manifest = read_json(artifacts / "selection_manifest.json")
            plan = read_json(artifacts / "request_plan_research.json")
            metadata = read_json(artifacts / "run_metadata.json")
            self.assertEqual(manifest["latest_filename"], "01_2026_FY_tanshin.pdf")
            self.assertEqual(len(manifest["selected_files"]), 10)
            self.assertEqual(plan["request_count_if_executed"], 1)
            self.assertTrue(plan["makes_network_request"])
            self.assertIsNone(plan["style_blueprint_path"])
            self.assertIsNone(plan["style_blueprint_sha256"])
            self.assertIsNone(plan["exemplar_path"])
            self.assertIsNone(plan["exemplar_sha256"])
            self.assertEqual(metadata["api_requests_sent_by_this_invocation"], 0)
            cost = read_json(artifacts / "cost.json")
            self.assertEqual(cost["display_currency"], "JPY")
            self.assertEqual(cost["usd_to_jpy_rate"], 150.0)
            self.assertGreater(cost["maximum_one_pass_cost_jpy"], 0)
            self.assertIn("JPY", stdout.getvalue())
            self.assertNotIn("Yen conversion assumption", stdout.getvalue())
            self.assertIn(
                "eligible for the Gemini free tier",
                stdout.getvalue(),
            )
            prompt = (artifacts / "prompt_research.txt").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("GEMINI_API_KEY", prompt)
            self.assertNotIn("<EXEMPLAR>", prompt)

    def test_execute_flag_requires_confirmed_request(self) -> None:
        with self.assertRaises(SystemExit):
            main(
                [
                    "1808",
                    "--repository-root",
                    str(REPOSITORY_ROOT),
                    "--execute-api",
                ]
            )


if __name__ == "__main__":
    unittest.main()
