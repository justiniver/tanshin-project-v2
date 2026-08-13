from __future__ import annotations

import json
import unittest
from pathlib import Path

from tanshin_pipeline.config import RESEARCH_MAX_SOURCE_RECORDS, output_paths
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

    def test_only_research_request_contains_pdfs(self) -> None:
        research = build_research_spec(REPOSITORY_ROOT, self.manifest)
        synthesis = build_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            self.dossier,
        )
        self.assertEqual(research.stage, "research")
        self.assertGreater(len(research.files), 0)
        self.assertEqual(synthesis.stage, "analysis")
        self.assertEqual(synthesis.files, ())
        self.assertIn("<research_dossier>", synthesis.prompt)
        self.assertIn("<research_metrics>", synthesis.prompt)

    def test_research_schema_is_extraction_only(self) -> None:
        schema = build_research_spec(
            REPOSITORY_ROOT,
            self.manifest,
        ).response_schema
        properties = schema["properties"]
        self.assertIn("source_records", properties)
        self.assertIn("filing_coverage", properties)
        self.assertIn("annual_financial_anchors", properties)
        self.assertIn("financial_observations", properties)
        self.assertIn("commentary_observations", properties)
        self.assertNotIn("evidence", properties)
        self.assertNotIn("business_drivers", properties)
        self.assertNotIn("management_themes", properties)
        self.assertNotIn("management_consistency", properties)

    def test_synthesis_schema_uses_internal_provenance_not_citations(self) -> None:
        spec = build_analysis_spec(
            REPOSITORY_ROOT,
            self.manifest,
            self.dossier,
        )
        claim = spec.response_schema["$defs"]["SynthesisAnalysisClaim"][
            "properties"
        ]
        self.assertIn("source_record_ids", claim)
        self.assertNotIn("evidence_ids", claim)
        self.assertNotIn("figures", claim)
        self.assertNotIn("dates", claim)
        self.assertIn("management_consistency", spec.response_schema["properties"])

    def test_metrics_are_deterministic_and_leave_synthesis_to_request_two(
        self,
    ) -> None:
        metrics = build_research_metrics(self.dossier, self.manifest)
        self.assertEqual(
            metrics["filing_coverage"]["selected_filings"],
            len(self.manifest.selected_files),
        )
        self.assertEqual(
            metrics["coverage"]["source_records"],
            len(self.dossier.source_records),
        )
        self.assertNotIn("business_drivers", metrics)
        self.assertNotIn("management_consistency", metrics)
        self.assertIn("financial_observations", metrics)
        self.assertIn("commentary", metrics)

    def test_local_metrics_compare_forecasts_and_commentary_changes(self) -> None:
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
        forecast_id = f"{earlier.filename}:r9001"
        actual_id = f"{later.filename}:r9002"
        earlier_commentary_id = f"{earlier.filename}:r9003"
        later_commentary_id = f"{later.filename}:r9004"
        payload["source_records"].extend(
            [
                {
                    "record_id": forecast_id,
                    "source_filename": earlier.filename,
                    "pdf_page": 1,
                    "period_label_ja": f"FY{earlier.fiscal_year}",
                    "period_label_en": f"FY{earlier.fiscal_year}",
                    "statement_type": "forecast",
                    "source_section": "業績予想",
                    "summary_ja": "翌年度の経常利益は900億円を予想。",
                    "tags": ["management_discussion"],
                },
                {
                    "record_id": actual_id,
                    "source_filename": later.filename,
                    "pdf_page": 1,
                    "period_label_ja": f"FY{later.fiscal_year}",
                    "period_label_en": f"FY{later.fiscal_year}",
                    "statement_type": "actual",
                    "source_section": "経営成績",
                    "summary_ja": "経常利益は941億円となった。",
                    "tags": ["management_discussion"],
                },
                {
                    "record_id": earlier_commentary_id,
                    "source_filename": earlier.filename,
                    "pdf_page": 2,
                    "period_label_ja": f"FY{earlier.fiscal_year}",
                    "period_label_en": f"FY{earlier.fiscal_year}",
                    "statement_type": "actual",
                    "source_section": "経営成績",
                    "summary_ja": "原材料費の上昇が利益を圧迫した。",
                    "tags": ["management_discussion"],
                },
                {
                    "record_id": later_commentary_id,
                    "source_filename": later.filename,
                    "pdf_page": 2,
                    "period_label_ja": f"FY{later.fiscal_year}",
                    "period_label_en": f"FY{later.fiscal_year}",
                    "statement_type": "actual",
                    "source_section": "経営成績",
                    "summary_ja": "原材料費の上昇による利益圧迫が一段と強まった。",
                    "tags": ["management_discussion"],
                },
            ]
        )
        payload["financial_observations"] = [
            {
                "observation_id": "forecast",
                "source_filename": earlier.filename,
                "metric": "ordinary_profit",
                "metric_label_ja": "経常利益",
                "scope": "consolidated",
                "scope_label_ja": "連結",
                "value_kind": "monetary",
                "statement_type": "forecast",
                "forecast_version": "original",
                "target_fiscal_year": later.fiscal_year,
                "target_period": "FY",
                "value_surface_ja": "900億円",
                "source_record_id": forecast_id,
            },
            {
                "observation_id": "actual",
                "source_filename": later.filename,
                "metric": "ordinary_profit",
                "metric_label_ja": "経常利益",
                "scope": "consolidated",
                "scope_label_ja": "連結",
                "value_kind": "monetary",
                "statement_type": "actual",
                "forecast_version": "not_applicable",
                "target_fiscal_year": later.fiscal_year,
                "target_period": "FY",
                "value_surface_ja": "941億円",
                "source_record_id": actual_id,
            },
        ]
        payload["commentary_observations"] = [
            {
                "observation_id": "commentary-earlier",
                "source_filename": earlier.filename,
                "fiscal_year": earlier.fiscal_year,
                "period_label_ja": f"FY{earlier.fiscal_year}",
                "canonical_tag": "material_costs",
                "label_ja": "原材料費",
                "tone": "negative",
                "intensity": "moderate",
                "summary_ja": "原材料費の上昇が利益を圧迫した。",
                "source_record_ids": [earlier_commentary_id],
            },
            {
                "observation_id": "commentary-later",
                "source_filename": later.filename,
                "fiscal_year": later.fiscal_year,
                "period_label_ja": f"FY{later.fiscal_year}",
                "canonical_tag": "material_costs",
                "label_ja": "原材料費",
                "tone": "negative",
                "intensity": "high",
                "summary_ja": "原材料費の上昇による利益圧迫が一段と強まった。",
                "source_record_ids": [later_commentary_id],
            },
        ]
        coverage = {
            item["source_filename"]: item
            for item in payload["filing_coverage"]
        }
        earlier_outlook = coverage[earlier.filename][
            "forward_looking_information"
        ]
        earlier_outlook.update(
            {
                "status": "extracted",
                "source_record_ids": [forecast_id],
                "coverage_note": None,
            }
        )
        coverage[earlier.filename]["operating_results"][
            "source_record_ids"
        ].append(earlier_commentary_id)
        coverage[earlier.filename]["financial_observation_ids"].append(
            "forecast"
        )
        coverage[earlier.filename]["commentary_observation_ids"].append(
            "commentary-earlier"
        )
        coverage[later.filename]["operating_results"][
            "source_record_ids"
        ].extend([actual_id, later_commentary_id])
        coverage[later.filename]["financial_observation_ids"].append("actual")
        coverage[later.filename]["commentary_observation_ids"].append(
            "commentary-later"
        )

        dossier = JapaneseResearchDossier.model_validate(payload)
        validate_research_dossier(dossier, self.manifest)
        metrics = build_research_metrics(dossier, self.manifest)
        comparison = metrics["financial_observations"]["forecast_accuracy"]
        self.assertEqual(comparison["observable_comparisons"], 1)
        self.assertEqual(
            comparison["comparisons"][0]["result"],
            "actual_above_forecast",
        )
        self.assertEqual(
            metrics["commentary"]["change_counts"]["intensified"],
            1,
        )

    def test_compact_annual_anchors_feed_forecast_metrics(self) -> None:
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
        forecast_record_id = f"{earlier.filename}:r9101"
        actual_record_id = f"{later.filename}:r9102"
        payload["source_records"].extend(
            [
                {
                    "record_id": forecast_record_id,
                    "source_filename": earlier.filename,
                    "pdf_page": 1,
                    "period_label_ja": f"FY{earlier.fiscal_year}",
                    "period_label_en": f"FY{earlier.fiscal_year}",
                    "statement_type": "forecast",
                    "source_section": "業績予想",
                    "summary_ja": "翌年度の経常利益は900億円を予想した。",
                    "tags": ["outlook"],
                },
                {
                    "record_id": actual_record_id,
                    "source_filename": later.filename,
                    "pdf_page": 1,
                    "period_label_ja": f"FY{later.fiscal_year}",
                    "period_label_en": f"FY{later.fiscal_year}",
                    "statement_type": "actual",
                    "source_section": "経営成績",
                    "summary_ja": "経常利益は941億円となった。",
                    "tags": ["operating_results"],
                },
            ]
        )
        payload["annual_financial_anchors"] = [
            {
                "anchor_id": "anchor-earlier",
                "source_filename": earlier.filename,
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
                    "source_record_id": forecast_record_id,
                },
            },
            {
                "anchor_id": "anchor-later",
                "source_filename": later.filename,
                "metric": "ordinary_profit",
                "metric_label_ja": "経常利益",
                "scope": "consolidated",
                "scope_label_ja": "連結",
                "value_kind": "monetary",
                "actual": {
                    "target_fiscal_year": later.fiscal_year,
                    "target_period": "FY",
                    "value_surface_ja": "941億円",
                    "source_record_id": actual_record_id,
                },
                "next_original_forecast": None,
            },
        ]
        coverage = {
            item["source_filename"]: item
            for item in payload["filing_coverage"]
        }
        coverage[earlier.filename]["forward_looking_information"] = {
            "status": "extracted",
            "source_record_ids": [forecast_record_id],
            "coverage_note": None,
        }
        coverage[earlier.filename]["annual_financial_anchor_ids"].append(
            "anchor-earlier"
        )
        coverage[later.filename]["operating_results"][
            "source_record_ids"
        ].append(actual_record_id)
        coverage[later.filename]["annual_financial_anchor_ids"].append(
            "anchor-later"
        )

        dossier = JapaneseResearchDossier.model_validate(payload)
        validate_research_dossier(dossier, self.manifest)
        metrics = build_research_metrics(dossier, self.manifest)
        financial = metrics["financial_observations"]
        self.assertEqual(financial["annual_anchor_count"], 2)
        self.assertEqual(financial["annual_anchor_value_count"], 2)
        self.assertEqual(
            financial["forecast_accuracy"]["observable_comparisons"],
            1,
        )
        self.assertEqual(
            financial["forecast_accuracy"]["comparisons"][0]["result"],
            "actual_above_forecast",
        )

    def test_unresolved_source_record_is_rejected(self) -> None:
        broken = self.dossier.model_copy(deep=True)
        section = broken.filing_coverage[0].operating_results
        section.source_record_ids.append("missing:record")
        with self.assertRaisesRegex(ValueError, "absent from its provenance"):
            validate_research_dossier(broken)

    def test_missing_selected_filing_coverage_is_rejected(self) -> None:
        broken = self.dossier.model_copy(deep=True)
        broken.filing_coverage.pop()
        with self.assertRaisesRegex(ValueError, "exactly one record"):
            validate_research_dossier(broken, self.manifest)

    def test_financial_value_surface_must_exist_in_source_summary(self) -> None:
        payload = self.dossier.model_dump(mode="json")
        source = payload["source_records"][0]
        payload["financial_observations"] = [
            {
                "observation_id": "unsupported-value",
                "source_filename": source["source_filename"],
                "metric": "revenue",
                "metric_label_ja": "売上高",
                "scope": "consolidated",
                "scope_label_ja": "連結",
                "value_kind": "monetary",
                "statement_type": "actual",
                "forecast_version": "not_applicable",
                "target_fiscal_year": 2026,
                "target_period": "FY",
                "value_surface_ja": "999億円",
                "source_record_id": source["record_id"],
            }
        ]
        coverage = next(
            item
            for item in payload["filing_coverage"]
            if item["source_filename"] == source["source_filename"]
        )
        coverage["financial_observation_ids"].append("unsupported-value")
        broken = JapaneseResearchDossier.model_validate(payload)
        with self.assertRaisesRegex(ValueError, "absent from their supporting"):
            validate_research_dossier(broken, self.manifest)

    def test_source_record_ceiling_is_prompt_guidance_not_local_failure(
        self,
    ) -> None:
        payload = self.dossier.model_dump(mode="json")
        template = payload["source_records"][0]
        payload["source_records"] = [
            {
                **template,
                "record_id": f"{template['source_filename']}:r{index:04d}",
            }
            for index in range(1, RESEARCH_MAX_SOURCE_RECORDS + 2)
        ]
        dossier = JapaneseResearchDossier.model_validate(payload)
        self.assertEqual(
            len(dossier.source_records),
            RESEARCH_MAX_SOURCE_RECORDS + 1,
        )

    def test_synthesis_materializes_internal_provenance_but_not_report_citations(
        self,
    ) -> None:
        synthesis = fake_synthesis_response(REPOSITORY_ROOT)
        analysis = materialize_japanese_synthesis(self.dossier, synthesis)
        report = render_japanese(analysis)
        self.assertEqual(
            analysis.claims[0].evidence_ids,
            synthesis.claims[0].source_record_ids,
        )
        for source_record in self.dossier.source_records:
            self.assertNotIn(source_record.record_id, report)
        self.assertNotIn("## 根拠一覧", report)

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
