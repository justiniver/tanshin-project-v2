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
            {
                "schema_version",
                "identity",
                "filings",
                "capital_allocation_decisions",
                "research_notes",
            },
        )
        self.assertIn(
            "capital_allocation_decisions",
            schema["required"],
        )
        filing = schema["$defs"]["ResearchFilingMemo"]["properties"]
        self.assertIn("items", filing)
        self.assertIn("annual_financial_anchor", filing)
        self.assertIn("unavailable_categories", filing)
        decision = schema["$defs"]["ResearchCapitalAllocationDecision"][
            "properties"
        ]
        self.assertIn("stated_rationale_ja", decision)
        self.assertIn("subsequent_outcomes", decision)
        self.assertIn("adverse_evidence_ja", decision)
        self.assertIn("record_maturity", decision)
        outcome = schema["$defs"]["ResearchCapitalAllocationOutcome"][
            "properties"
        ]
        self.assertIn("attribution", outcome)
        self.assertIn("signal", outcome)
        for obsolete in (
            "source_records",
            "filing_coverage",
            "commentary_observations",
            "commitments",
            "management_consistency",
        ):
            self.assertNotIn(obsolete, properties)

    def test_synthesis_schema_is_citation_free(self) -> None:
        spec = build_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            self.dossier,
        )
        claim = spec.response_schema["$defs"]["SynthesisAnalysisClaim"][
            "properties"
        ]
        self.assertNotIn("sources", claim)
        self.assertNotIn("source_record_ids", claim)
        self.assertNotIn("evidence_ids", claim)
        self.assertNotIn("SynthesisSourceReference", spec.response_schema["$defs"])

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
                "summary_ja": "翌年度の経常利益を900 億円と予想した。",
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
                "value_surface_ja": "900 億円",
                "pdf_page": 1,
            },
        }
        memos[later.filename]["items"].append(
            {
                "category": "operating_results",
                "pdf_page": 1,
                "statement_type": "actual",
                "summary_ja": "経常利益は941 億円となった。",
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
                "value_surface_ja": "941 億円",
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

    def test_capital_allocation_metrics_preserve_attribution_and_maturity(
        self,
    ) -> None:
        payload = self.dossier.model_dump(mode="json")
        year_ends = sorted(
            (
                item
                for item in self.manifest.selected_files
                if "trend_year_end" in item.roles
            ),
            key=lambda item: item.fiscal_year,
        )
        decision_source, outcome_source = year_ends[-2:]
        payload["capital_allocation_decisions"] = [
            {
                "decision_label_ja": "Material acquisition",
                "decision_type": "acquisition",
                "decision_source_filename": decision_source.filename,
                "decision_fiscal_year": decision_source.fiscal_year,
                "decision_period_ja": f"FY{decision_source.fiscal_year}",
                "amount_or_scale_ja": "100 億円",
                "decision_ja": "A subsidiary was acquired.",
                "stated_rationale_ja": "Management sought to expand the business.",
                "funding_or_tradeoff_ja": None,
                "subsequent_outcomes": [
                    {
                        "source_filename": outcome_source.filename,
                        "fiscal_year": outcome_source.fiscal_year,
                        "period_label_ja": f"FY{outcome_source.fiscal_year}",
                        "outcome_ja": (
                            "Only aggregate segment growth was disclosed."
                        ),
                        "attribution": "aggregate_only",
                        "signal": "positive",
                    }
                ],
                "adverse_evidence_ja": [],
                "record_maturity": "partial_record",
                "disclosure_limit_ja": (
                    "The acquired company's standalone contribution is unavailable."
                ),
            }
        ]
        dossier = JapaneseResearchDossier.model_validate(payload)
        validate_research_dossier(dossier, self.manifest)
        capital = build_research_metrics(
            dossier,
            self.manifest,
        )["capital_allocation"]
        self.assertEqual(capital["decision_records"], 1)
        self.assertEqual(capital["decisions_with_later_outcomes"], 1)
        self.assertEqual(capital["by_decision_type"], {"acquisition": 1})
        self.assertEqual(
            capital["by_record_maturity"],
            {"partial_record": 1},
        )
        self.assertEqual(
            capital["by_outcome_attribution"],
            {"aggregate_only": 1},
        )
        self.assertTrue(capital["records"][0]["has_disclosure_limit"])

    def test_capital_allocation_validation_rejects_unselected_sources(
        self,
    ) -> None:
        payload = self.dossier.model_dump(mode="json")
        payload["capital_allocation_decisions"] = [
            {
                "decision_label_ja": "Material acquisition",
                "decision_type": "acquisition",
                "decision_source_filename": "not-selected.pdf",
                "decision_fiscal_year": 2025,
                "decision_period_ja": "FY2025",
                "amount_or_scale_ja": None,
                "decision_ja": "A subsidiary was acquired.",
                "stated_rationale_ja": "Management sought business expansion.",
                "funding_or_tradeoff_ja": None,
                "subsequent_outcomes": [],
                "adverse_evidence_ja": [],
                "record_maturity": "not_observable",
                "disclosure_limit_ja": None,
            }
        ]
        dossier = JapaneseResearchDossier.model_validate(payload)
        with self.assertRaisesRegex(ValueError, "unselected filing"):
            validate_research_dossier(dossier, self.manifest)

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

    def test_synthesis_materialization_creates_no_citation_graph(self) -> None:
        synthesis = fake_synthesis_response(REPOSITORY_ROOT)
        analysis = materialize_japanese_synthesis(self.dossier, synthesis)
        report = render_japanese(analysis)
        self.assertEqual(analysis.evidence, [])
        self.assertTrue(all(not claim.evidence_ids for claim in analysis.claims))
        self.assertTrue(
            all(
                not component.evidence_ids
                for component in analysis.management_consistency.components
            )
        )
        self.assertNotIn("evidence_id", report)

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
