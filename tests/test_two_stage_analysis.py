from __future__ import annotations

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

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
    JapaneseSynthesisResponse,
    SectionKey,
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
        self.assertIn(
            "reported capital stocks and operating assets as primary evidence",
            " ".join(synthesis.prompt.split()),
        )
        self.assertIn(
            "did it flow toward the destinations earning the strongest",
            " ".join(synthesis.prompt.split()),
        )
        self.assertIn("disclosed ROIC, ROA", synthesis.prompt)
        self.assertIn("never manufacture ROIC or ROA", synthesis.prompt)
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
                "capital_allocation_tracks",
                "research_notes",
            },
        )
        self.assertIn(
            "capital_allocation_tracks",
            schema["required"],
        )
        filing = schema["$defs"]["ResearchFilingMemo"]["properties"]
        self.assertIn("items", filing)
        self.assertIn("annual_financial_anchor", filing)
        self.assertIn("unavailable_categories", filing)
        track = schema["$defs"]["ResearchCapitalAllocationTrack"][
            "properties"
        ]
        self.assertIn("stated_rationale_ja", track)
        self.assertIn("capital_destination_ja", track)
        self.assertIn("capital_inputs", track)
        self.assertIn("immediate_effects", track)
        self.assertIn("subsequent_returns", track)
        self.assertIn("adverse_evidence_ja", track)
        self.assertIn("record_maturity", track)
        return_record = schema["$defs"]["ResearchCapitalReturn"][
            "properties"
        ]
        self.assertIn("return_type", return_record)
        self.assertIn("attribution", return_record)
        self.assertIn("signal", return_record)
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
        input_source, return_source = year_ends[-2:]
        payload["capital_allocation_tracks"] = [
            {
                "track_label_ja": "Growth segment investment",
                "track_type": "acquisition",
                "capital_destination_ja": "Growth segment",
                "start_fiscal_year": input_source.fiscal_year,
                "end_fiscal_year": return_source.fiscal_year,
                "stated_rationale_ja": "Management sought to expand the business.",
                "capital_inputs": [
                    {
                        "source_filename": input_source.filename,
                        "fiscal_year": input_source.fiscal_year,
                        "period_label_ja": f"FY{input_source.fiscal_year}",
                        "input_type": "acquisition_or_investment_spend",
                        "amount_or_scale_ja": "100 億円",
                        "input_ja": "A subsidiary was acquired.",
                        "relative_priority_ja": (
                            "Capital priority shifted toward the growth segment."
                        ),
                    }
                ],
                "immediate_effects": [
                    {
                        "source_filename": input_source.filename,
                        "fiscal_year": input_source.fiscal_year,
                        "period_label_ja": f"FY{input_source.fiscal_year}",
                        "effect_type": "goodwill_or_negative_goodwill",
                        "effect_ja": "Negative goodwill was recorded at acquisition.",
                    }
                ],
                "subsequent_returns": [
                    {
                        "source_filename": return_source.filename,
                        "fiscal_year": return_source.fiscal_year,
                        "period_label_ja": f"FY{return_source.fiscal_year}",
                        "return_type": "profit_or_loss",
                        "return_ja": "Only aggregate segment profit was disclosed.",
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
        self.assertEqual(capital["track_records"], 1)
        self.assertEqual(capital["tracks_with_subsequent_returns"], 1)
        self.assertEqual(capital["capital_input_records"], 1)
        self.assertEqual(capital["immediate_effect_records"], 1)
        self.assertEqual(capital["subsequent_return_records"], 1)
        self.assertEqual(capital["by_track_type"], {"acquisition": 1})
        self.assertEqual(
            capital["by_record_maturity"],
            {"partial_record": 1},
        )
        self.assertEqual(
            capital["by_return_attribution"],
            {"aggregate_only": 1},
        )
        self.assertEqual(
            capital["records"][0]["capital_destination_ja"],
            "Growth segment",
        )
        self.assertEqual(
            capital["records"][0]["capital_inputs_with_relative_priority"],
            1,
        )
        self.assertEqual(capital["records"][0]["immediate_effect_count"], 1)
        self.assertTrue(capital["records"][0]["has_disclosure_limit"])

    def test_capital_allocation_validation_rejects_unselected_sources(
        self,
    ) -> None:
        payload = self.dossier.model_dump(mode="json")
        payload["capital_allocation_tracks"] = [
            {
                "track_label_ja": "Growth segment investment",
                "track_type": "acquisition",
                "capital_destination_ja": "Growth segment",
                "start_fiscal_year": 2025,
                "end_fiscal_year": 2025,
                "stated_rationale_ja": "Management sought business expansion.",
                "capital_inputs": [
                    {
                        "source_filename": "not-selected.pdf",
                        "fiscal_year": 2025,
                        "period_label_ja": "FY2025",
                        "input_type": "acquisition_or_investment_spend",
                        "amount_or_scale_ja": None,
                        "input_ja": "A subsidiary was acquired.",
                        "relative_priority_ja": None,
                    }
                ],
                "immediate_effects": [],
                "subsequent_returns": [],
                "adverse_evidence_ja": [],
                "record_maturity": "not_observable",
                "disclosure_limit_ja": None,
            }
        ]
        dossier = JapaneseResearchDossier.model_validate(payload)
        with self.assertRaisesRegex(ValueError, "unselected filing"):
            validate_research_dossier(dossier, self.manifest)

    def test_haseko_style_tracks_separate_capital_stock_from_returns(self) -> None:
        payload = self.dossier.model_dump(mode="json")
        year_ends = sorted(
            (
                item
                for item in self.manifest.selected_files
                if "trend_year_end" in item.roles
            ),
            key=lambda item: item.fiscal_year,
        )
        early, recent = year_ends[0], year_ends[-1]
        payload["capital_allocation_tracks"] = [
            {
                "track_label_ja": "国内中核事業への資本蓄積",
                "track_type": "organic_accumulation",
                "capital_destination_ja": "国内中核事業",
                "start_fiscal_year": early.fiscal_year,
                "end_fiscal_year": recent.fiscal_year,
                "stated_rationale_ja": None,
                "capital_inputs": [
                    {
                        "source_filename": early.filename,
                        "fiscal_year": early.fiscal_year,
                        "period_label_ja": f"FY{early.fiscal_year}",
                        "input_type": "segment_or_operating_assets",
                        "amount_or_scale_ja": "1,000 億円",
                        "input_ja": "中核事業のセグメント資産を開示した。",
                        "relative_priority_ja": None,
                    },
                    {
                        "source_filename": recent.filename,
                        "fiscal_year": recent.fiscal_year,
                        "period_label_ja": f"FY{recent.fiscal_year}",
                        "input_type": "segment_or_operating_assets",
                        "amount_or_scale_ja": "1,600 億円",
                        "input_ja": "中核事業のセグメント資産が増加した。",
                        "relative_priority_ja": (
                            "他事業より大きい資産増加が確認された。"
                        ),
                    },
                ],
                "immediate_effects": [],
                "subsequent_returns": [
                    {
                        "source_filename": recent.filename,
                        "fiscal_year": recent.fiscal_year,
                        "period_label_ja": f"FY{recent.fiscal_year}",
                        "return_type": "profit_or_loss",
                        "return_ja": "同事業のセグメント利益が増加した。",
                        "attribution": "direct",
                        "signal": "positive",
                    },
                    {
                        "source_filename": recent.filename,
                        "fiscal_year": recent.fiscal_year,
                        "period_label_ja": f"FY{recent.fiscal_year}",
                        "return_type": "return_on_capital_or_assets",
                        "return_ja": "同事業の事業資産利益率は8.0%と開示された。",
                        "attribution": "direct",
                        "signal": "positive",
                    }
                ],
                "adverse_evidence_ja": [],
                "record_maturity": "mature_record",
                "disclosure_limit_ja": None,
            },
            {
                "track_label_ja": "株主還元",
                "track_type": "shareholder_return",
                "capital_destination_ja": "株主",
                "start_fiscal_year": recent.fiscal_year,
                "end_fiscal_year": recent.fiscal_year,
                "stated_rationale_ja": "利益還元を強化する方針。",
                "capital_inputs": [
                    {
                        "source_filename": recent.filename,
                        "fiscal_year": recent.fiscal_year,
                        "period_label_ja": f"FY{recent.fiscal_year}",
                        "input_type": "shareholder_distribution",
                        "amount_or_scale_ja": "200 億円",
                        "input_ja": "自己株式取得に資本を配分した。",
                        "relative_priority_ja": None,
                    }
                ],
                "immediate_effects": [
                    {
                        "source_filename": recent.filename,
                        "fiscal_year": recent.fiscal_year,
                        "period_label_ja": f"FY{recent.fiscal_year}",
                        "effect_type": "distribution_execution",
                        "effect_ja": "自己株式取得を実行した。",
                    }
                ],
                "subsequent_returns": [],
                "adverse_evidence_ja": [],
                "record_maturity": "too_recent",
                "disclosure_limit_ja": "実行後の経済的成果は未確立。",
            },
        ]
        dossier = JapaneseResearchDossier.model_validate(payload)
        validate_research_dossier(dossier, self.manifest)
        capital = build_research_metrics(
            dossier,
            self.manifest,
        )["capital_allocation"]
        self.assertEqual(capital["track_records"], 2)
        self.assertEqual(
            capital["by_capital_input_type"],
            {
                "segment_or_operating_assets": 2,
                "shareholder_distribution": 1,
            },
        )
        self.assertEqual(
            capital["by_immediate_effect_type"],
            {"distribution_execution": 1},
        )
        self.assertEqual(
            capital["by_return_type"],
            {
                "profit_or_loss": 1,
                "return_on_capital_or_assets": 1,
            },
        )
        self.assertEqual(capital["tracks_with_direct_return_evidence"], 1)
        self.assertEqual(
            capital["reported_return_on_capital_or_assets_records"],
            1,
        )
        self.assertEqual(
            capital["tracks_with_reported_return_on_capital_or_assets"],
            1,
        )
        self.assertEqual(
            capital["tracks_with_capital_base_and_direct_return_evidence"],
            1,
        )
        self.assertEqual(
            capital["tracks_with_only_management_or_aggregate_returns"],
            0,
        )
        domestic, distribution = capital["records"]
        self.assertTrue(domestic["has_destination_level_return_evidence"])
        self.assertEqual(domestic["direct_return_count"], 2)
        self.assertTrue(domestic["has_reported_return_on_capital_or_assets"])
        self.assertTrue(domestic["has_capital_base_observation"])
        self.assertTrue(
            domestic["has_capital_base_and_direct_return_evidence"]
        )
        self.assertFalse(distribution["has_destination_level_return_evidence"])
        self.assertFalse(
            distribution["has_reported_return_on_capital_or_assets"]
        )
        self.assertEqual(distribution["subsequent_return_count"], 0)
        self.assertIn(
            "shareholder distribution cannot establish",
            capital["interpretation_guardrail"],
        )

    def test_aggregate_or_management_linked_returns_are_not_direct_evidence(
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
        early, recent = year_ends[-2:]
        payload["capital_allocation_tracks"] = [
            {
                "track_label_ja": "買収先",
                "track_type": "acquisition",
                "capital_destination_ja": "買収先事業",
                "start_fiscal_year": early.fiscal_year,
                "end_fiscal_year": recent.fiscal_year,
                "stated_rationale_ja": "シナジー創出を目指す。",
                "capital_inputs": [
                    {
                        "source_filename": early.filename,
                        "fiscal_year": early.fiscal_year,
                        "period_label_ja": f"FY{early.fiscal_year}",
                        "input_type": "acquisition_or_investment_spend",
                        "amount_or_scale_ja": "20 億円",
                        "input_ja": "買収を実施した。",
                        "relative_priority_ja": None,
                    }
                ],
                "immediate_effects": [],
                "subsequent_returns": [
                    {
                        "source_filename": recent.filename,
                        "fiscal_year": recent.fiscal_year,
                        "period_label_ja": f"FY{recent.fiscal_year}",
                        "return_type": "profit_or_loss",
                        "return_ja": "経営陣は買収がセグメント増益に寄与したと説明した。",
                        "attribution": "management_linked",
                        "signal": "positive",
                    },
                    {
                        "source_filename": recent.filename,
                        "fiscal_year": recent.fiscal_year,
                        "period_label_ja": f"FY{recent.fiscal_year}",
                        "return_type": "profit_or_loss",
                        "return_ja": "セグメント全体の利益が増加した。",
                        "attribution": "aggregate_only",
                        "signal": "positive",
                    },
                ],
                "adverse_evidence_ja": [],
                "record_maturity": "partial_record",
                "disclosure_limit_ja": "買収先単体の損益は非開示。",
            }
        ]
        dossier = JapaneseResearchDossier.model_validate(payload)
        capital = build_research_metrics(
            dossier,
            self.manifest,
        )["capital_allocation"]
        record = capital["records"][0]
        self.assertEqual(capital["tracks_with_direct_return_evidence"], 0)
        self.assertEqual(
            capital["tracks_with_only_management_or_aggregate_returns"],
            1,
        )
        self.assertEqual(record["management_linked_return_count"], 1)
        self.assertEqual(record["non_attributable_return_count"], 1)
        self.assertFalse(record["has_destination_level_return_evidence"])
        self.assertTrue(record["has_capital_base_observation"])
        self.assertFalse(
            record["has_capital_base_and_direct_return_evidence"]
        )

    def test_transaction_accounting_effect_cannot_be_a_return_type(self) -> None:
        payload = self.dossier.model_dump(mode="json")
        source = next(
            item
            for item in self.manifest.selected_files
            if "trend_year_end" in item.roles
        )
        payload["capital_allocation_tracks"] = [
            {
                "track_label_ja": "買収",
                "track_type": "acquisition",
                "capital_destination_ja": "買収事業",
                "start_fiscal_year": source.fiscal_year,
                "end_fiscal_year": source.fiscal_year,
                "stated_rationale_ja": "事業拡大",
                "capital_inputs": [
                    {
                        "source_filename": source.filename,
                        "fiscal_year": source.fiscal_year,
                        "period_label_ja": f"FY{source.fiscal_year}",
                        "input_type": "acquisition_or_investment_spend",
                        "amount_or_scale_ja": "20 億円",
                        "input_ja": "株式を取得した。",
                        "relative_priority_ja": None,
                    }
                ],
                "immediate_effects": [],
                "subsequent_returns": [
                    {
                        "source_filename": source.filename,
                        "fiscal_year": source.fiscal_year,
                        "period_label_ja": f"FY{source.fiscal_year}",
                        "return_type": "goodwill_or_negative_goodwill",
                        "return_ja": "負ののれん発生益を計上した。",
                        "attribution": "direct",
                        "signal": "positive",
                    }
                ],
                "adverse_evidence_ja": [],
                "record_maturity": "partial_record",
                "disclosure_limit_ja": None,
            }
        ]
        with self.assertRaises(ValidationError):
            JapaneseResearchDossier.model_validate(payload)

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

    def test_management_rationales_are_derived_from_report_claims(self) -> None:
        payload = fake_synthesis_response(REPOSITORY_ROOT).model_dump(mode="json")
        sections = (
            SectionKey.MANAGEMENT_STRATEGY,
            SectionKey.MANAGEMENT_EXECUTION,
            SectionKey.MANAGEMENT_FORECAST_DISCIPLINE,
            SectionKey.MANAGEMENT_ACCOUNTABILITY,
        )
        for index, section in enumerate(sections, start=1):
            payload["claims"].append(
                {
                    "claim_id": f"management-{index}",
                    "section": section.value,
                    "order": 1,
                    "headline_ja": f"management headline {index}",
                    "body_ja": f"management rationale {index}",
                    "statement_type": "inference",
                    "is_inference": True,
                    "causal": False,
                }
            )
        synthesis = JapaneseSynthesisResponse.model_validate(payload)
        analysis = materialize_japanese_synthesis(self.dossier, synthesis)
        rationales = [
            component.rationale_ja
            for component in analysis.management_consistency.components
        ]
        self.assertEqual(
            rationales,
            [f"management rationale {index}" for index in range(1, 5)],
        )

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
