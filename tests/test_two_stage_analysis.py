from __future__ import annotations

import json
import unittest
from pathlib import Path

from tanshin_pipeline.config import output_paths
from tanshin_pipeline.persistence import read_json, write_json
from tanshin_pipeline.pipeline import reprocess_stored_research
from tanshin_pipeline.render import render_japanese
from tanshin_pipeline.request_builder import (
    build_analysis_spec,
    build_research_spec,
)
from tanshin_pipeline.research import (
    build_research_metrics,
    validate_research_dossier,
)
from tanshin_pipeline.schemas import (
    JapaneseResearchDossier,
    materialize_japanese_synthesis,
)
from tanshin_pipeline.selection import select_filings
from tests.helpers import (
    fake_research_dossier,
    fake_synthesis_response,
    workspace_temp_directory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class TwoStageAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = select_filings(REPOSITORY_ROOT, "1808")
        self.dossier = fake_research_dossier(REPOSITORY_ROOT)

    def test_both_japanese_requests_receive_the_selected_pdfs(self) -> None:
        research = build_research_spec(REPOSITORY_ROOT, self.manifest)
        synthesis = build_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            self.dossier,
        )
        expected = [item.filename for item in self.manifest.selected_files]
        self.assertEqual([item.filename for item in research.files], expected)
        self.assertEqual([item.filename for item in synthesis.files], expected)
        self.assertIn("<research_map>", synthesis.prompt)
        self.assertIn("<research_metrics>", synthesis.prompt)
        self.assertIn("attention guide", synthesis.prompt)
        self.assertIn("authoritative", synthesis.prompt)
        self.assertIn("trend.capital_value_creation", synthesis.prompt)
        self.assertIn('answer the question "Did capital allocation', synthesis.prompt)
        self.assertIn("actions, not outcomes by themselves", synthesis.prompt)
        self.assertNotIn("WACC", synthesis.prompt)

    def test_research_schema_is_a_direct_chronological_map(self) -> None:
        schema = build_research_spec(
            REPOSITORY_ROOT,
            self.manifest,
        ).response_schema
        properties = schema["properties"]
        self.assertEqual(
            set(properties),
            {"schema_version", "identity", "filings", "research_notes"},
        )
        filing = schema["$defs"]["ResearchFilingMemo"]["properties"]
        self.assertIn("items", filing)
        self.assertIn("annual_financial_anchor", filing)
        self.assertIn("unavailable_categories", filing)
        for obsolete in (
            "source_records",
            "filing_coverage",
            "commentary_observations",
            "commitments",
            "management_consistency",
        ):
            self.assertNotIn(obsolete, properties)

    def test_synthesis_schema_uses_small_pdf_source_references(self) -> None:
        spec = build_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            self.dossier,
        )
        claim = spec.response_schema["$defs"]["SynthesisAnalysisClaim"][
            "properties"
        ]
        self.assertIn("sources", claim)
        self.assertNotIn("source_record_ids", claim)
        self.assertNotIn("evidence_ids", claim)
        reference = spec.response_schema["$defs"]["SynthesisSourceReference"][
            "properties"
        ]
        self.assertEqual(
            set(reference),
            {
                "source_filename",
                "pdf_page",
                "source_section",
                "statement_type",
                "support_summary_ja",
            },
        )

    def test_metrics_compare_original_forecasts_with_later_actuals(self) -> None:
        payload = self.dossier.model_dump(mode="json")
        year_ends = sorted(
            (
                item
                for item in self.manifest.selected_files
                if "trend_year_end" in item.roles
            ),
            key=lambda item: item.fiscal_year,
        )
        earlier, later = year_ends[-2:]
        memos = {
            item["source_filename"]: item for item in payload["filings"]
        }
        memos[earlier.filename]["items"].append(
            {
                "category": "forward_looking_information",
                "pdf_page": 1,
                "statement_type": "forecast",
                "summary_ja": "翌年度の経常利益を900億円と予想した。",
            }
        )
        memos[earlier.filename]["annual_financial_anchor"] = {
            "metric": "ordinary_profit",
            "metric_label_ja": "経常利益",
            "scope": "consolidated",
            "scope_label_ja": "連結",
            "value_kind": "monetary",
            "actual": None,
            "next_original_forecast": {
                "target_fiscal_year": later.fiscal_year,
                "target_period": "FY",
                "value_surface_ja": "900億円",
                "pdf_page": 1,
            },
        }
        memos[later.filename]["items"].append(
            {
                "category": "operating_results",
                "pdf_page": 1,
                "statement_type": "actual",
                "summary_ja": "経常利益は941億円となった。",
            }
        )
        memos[later.filename]["annual_financial_anchor"] = {
            "metric": "ordinary_profit",
            "metric_label_ja": "経常利益",
            "scope": "consolidated",
            "scope_label_ja": "連結",
            "value_kind": "monetary",
            "actual": {
                "target_fiscal_year": later.fiscal_year,
                "target_period": "FY",
                "value_surface_ja": "941億円",
                "pdf_page": 1,
            },
            "next_original_forecast": None,
        }
        dossier = JapaneseResearchDossier.model_validate(payload)
        validate_research_dossier(dossier, self.manifest)
        metrics = build_research_metrics(dossier, self.manifest)
        forecast = metrics["financial_observations"]["forecast_accuracy"]
        self.assertEqual(forecast["observable_comparisons"], 1)
        self.assertEqual(
            forecast["comparisons"][0]["result"],
            "actual_above_forecast",
        )

    def test_research_validation_catches_missing_memo_and_invalid_page(self) -> None:
        missing = self.dossier.model_copy(deep=True)
        missing.filings.pop()
        with self.assertRaisesRegex(ValueError, "missing selected filings"):
            validate_research_dossier(missing, self.manifest)

        invalid = self.dossier.model_copy(deep=True)
        invalid.filings[0].items[0].pdf_page = (
            invalid.filings[0].pdf_page_count + 1
        )
        with self.assertRaisesRegex(ValueError, "invalid page"):
            validate_research_dossier(invalid, self.manifest)

    def test_synthesis_can_use_a_valid_pdf_passage_omitted_from_the_map(
        self,
    ) -> None:
        synthesis = fake_synthesis_response(REPOSITORY_ROOT)
        source = synthesis.claims[0].sources[0]
        source.source_filename = self.manifest.selected_files[-1].filename
        source.pdf_page = 1
        source.support_summary_ja = "研究マップにないがPDFで確認した情報。"
        analysis = materialize_japanese_synthesis(self.dossier, synthesis)
        report = render_japanese(analysis)
        self.assertTrue(analysis.claims[0].evidence_ids[0].startswith(
            f"{source.source_filename}:r0001-"
        ))
        self.assertNotIn(analysis.claims[0].evidence_ids[0], report)

    def test_unselected_pdf_reference_is_rejected(self) -> None:
        synthesis = fake_synthesis_response(REPOSITORY_ROOT)
        synthesis.claims[0].sources[0].source_filename = "not-selected.pdf"
        with self.assertRaisesRegex(ValueError, "unselected PDFs"):
            materialize_japanese_synthesis(self.dossier, synthesis)

    def test_stored_raw_research_can_be_reprocessed_without_network(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            paths = output_paths(temp / "output", "1808")
            write_json(
                paths.research_raw_response,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": json.dumps(
                                            self.dossier.model_dump(mode="json"),
                                            ensure_ascii=False,
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                },
            )
            result = reprocess_stored_research(
                REPOSITORY_ROOT,
                "1808",
                output_root=temp / "output",
            )
            self.assertEqual(result["api_requests"], 0)
            self.assertEqual(
                read_json(paths.research_structured),
                self.dossier.model_dump(mode="json"),
            )


if __name__ == "__main__":
    unittest.main()
