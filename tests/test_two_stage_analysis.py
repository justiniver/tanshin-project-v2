from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from tanshin_pipeline.config import RESEARCH_MAX_EVIDENCE_RECORDS
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
    ManagementConsistencyDimension,
    materialize_japanese_synthesis,
)
from tanshin_pipeline.selection import select_filings
from tests.helpers import fake_research_dossier


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

    def test_metrics_are_deterministic_and_scope_revision_counts(self) -> None:
        metrics = build_research_metrics(self.dossier, self.manifest)
        self.assertEqual(metrics["business_drivers"]["total"], 1)
        self.assertEqual(metrics["commitments"]["total"], 0)
        self.assertEqual(
            metrics["commitments"]["observed_revision_count"],
            0,
        )
        self.assertIn("selected filings", metrics["interpretation_guardrail"])
        self.assertEqual(
            metrics["filing_coverage"]["selected_filings"],
            len(self.manifest.selected_files),
        )
        self.assertEqual(metrics["management_consistency"]["score"], 0.5)
        self.assertEqual(
            len(metrics["management_consistency"]["components"]),
            4,
        )

    def test_local_metrics_compare_forecasts_and_commentary_changes(self) -> None:
        payload = self.dossier.model_dump(mode="json")
        forecast_evidence = "05_2025_FY_tanshin.pdf:s9001"
        actual_evidence = "01_2026_FY_tanshin.pdf:s9002"
        earlier_commentary = "05_2025_FY_tanshin.pdf:s9003"
        later_commentary = "01_2026_FY_tanshin.pdf:s9004"
        disclosure_evidence = "01_2026_FY_tanshin.pdf:s9005"
        payload["evidence"].extend(
            [
                {
                    "evidence_id": forecast_evidence,
                    "source_filename": "05_2025_FY_tanshin.pdf",
                    "pdf_page": 4,
                    "exact_quote_ja": "2026年3月期の連結経常利益は900億円を予想しております。",
                    "period_label_ja": "2025年3月期",
                    "period_label_en": "FY2025",
                    "statement_type": "forecast",
                    "source_section": "業績予想",
                    "tags": ["management_discussion"],
                },
                {
                    "evidence_id": actual_evidence,
                    "source_filename": "01_2026_FY_tanshin.pdf",
                    "pdf_page": 4,
                    "exact_quote_ja": "2026年3月期の連結経常利益は941億円となりました。",
                    "period_label_ja": "2026年3月期",
                    "period_label_en": "FY2026",
                    "statement_type": "actual",
                    "source_section": "経営成績",
                    "tags": ["management_discussion"],
                },
                {
                    "evidence_id": earlier_commentary,
                    "source_filename": "05_2025_FY_tanshin.pdf",
                    "pdf_page": 4,
                    "exact_quote_ja": "資材・労務費の高騰による影響を受けました。",
                    "period_label_ja": "2025年3月期",
                    "period_label_en": "FY2025",
                    "statement_type": "actual",
                    "source_section": "経営成績",
                    "tags": ["management_discussion"],
                },
                {
                    "evidence_id": later_commentary,
                    "source_filename": "01_2026_FY_tanshin.pdf",
                    "pdf_page": 4,
                    "exact_quote_ja": "資材・労務費の高騰による影響が一段と強まりました。",
                    "period_label_ja": "2026年3月期",
                    "period_label_en": "FY2026",
                    "statement_type": "actual",
                    "source_section": "経営成績",
                    "tags": ["management_discussion"],
                },
                {
                    "evidence_id": disclosure_evidence,
                    "source_filename": "01_2026_FY_tanshin.pdf",
                    "pdf_page": 7,
                    "exact_quote_ja": "減損損失を特別損失として計上しました。",
                    "period_label_ja": "2026年3月期",
                    "period_label_en": "FY2026",
                    "statement_type": "actual",
                    "source_section": "重要な後発事象等",
                    "tags": ["footnote"],
                },
            ]
        )
        payload["financial_observations"] = [
            {
                "observation_id": "financial-forecast",
                "source_filename": "05_2025_FY_tanshin.pdf",
                "metric": "ordinary_profit",
                "metric_label_ja": "連結経常利益",
                "scope": "consolidated",
                "scope_label_ja": "連結",
                "value_kind": "monetary",
                "statement_type": "forecast",
                "forecast_version": "original",
                "target_fiscal_year": 2026,
                "target_period": "FY",
                "value_surface_ja": "900億円",
                "evidence_id": forecast_evidence,
            },
            {
                "observation_id": "financial-actual",
                "source_filename": "01_2026_FY_tanshin.pdf",
                "metric": "ordinary_profit",
                "metric_label_ja": "連結経常利益",
                "scope": "consolidated",
                "scope_label_ja": "連結",
                "value_kind": "monetary",
                "statement_type": "actual",
                "forecast_version": "not_applicable",
                "target_fiscal_year": 2026,
                "target_period": "FY",
                "value_surface_ja": "941億円",
                "evidence_id": actual_evidence,
            },
        ]
        payload["commentary_observations"] = [
            {
                "observation_id": "commentary-earlier",
                "source_filename": "05_2025_FY_tanshin.pdf",
                "fiscal_year": 2025,
                "period_label_ja": "2025年3月期",
                "canonical_tag": "material_and_labor_costs",
                "label_ja": "資材・労務費",
                "tone": "negative",
                "intensity": "moderate",
                "summary_ja": "コスト高が利益率を圧迫しました。",
                "evidence_ids": [earlier_commentary],
            },
            {
                "observation_id": "commentary-later",
                "source_filename": "01_2026_FY_tanshin.pdf",
                "fiscal_year": 2026,
                "period_label_ja": "2026年3月期",
                "canonical_tag": "material_and_labor_costs",
                "label_ja": "資材・労務費",
                "tone": "negative",
                "intensity": "high",
                "summary_ja": "コスト高の影響が強まりました。",
                "evidence_ids": [later_commentary],
            },
        ]
        payload["disclosures"] = [
            {
                "disclosure_id": "disclosure-impairment",
                "source_filename": "01_2026_FY_tanshin.pdf",
                "fiscal_year": 2026,
                "category": "impairment",
                "label_ja": "減損損失",
                "summary_ja": "減損損失を特別損失として計上しました。",
                "importance": "primary",
                "evidence_ids": [disclosure_evidence],
            }
        ]
        coverage_by_name = {
            item["source_filename"]: item for item in payload["filing_coverage"]
        }
        earlier = coverage_by_name["05_2025_FY_tanshin.pdf"]
        earlier["outlook_evidence_ids"].append(forecast_evidence)
        earlier["management_discussion_evidence_ids"].append(earlier_commentary)
        earlier["financial_observation_ids"].append("financial-forecast")
        earlier["commentary_observation_ids"].append("commentary-earlier")
        latest = coverage_by_name["01_2026_FY_tanshin.pdf"]
        latest["management_discussion_evidence_ids"].extend(
            [actual_evidence, later_commentary]
        )
        latest["financial_observation_ids"].append("financial-actual")
        latest["commentary_observation_ids"].append("commentary-later")
        latest["footnote_evidence_ids"].append(disclosure_evidence)
        latest["disclosure_ids"].append("disclosure-impairment")

        dossier = JapaneseResearchDossier.model_validate(payload)
        metrics = build_research_metrics(dossier, self.manifest)
        forecast = metrics["financial_observations"]["forecast_accuracy"]
        self.assertEqual(forecast["observable_comparisons"], 1)
        self.assertEqual(
            forecast["comparisons"][0]["result"],
            "actual_above_forecast",
        )
        self.assertAlmostEqual(
            forecast["comparisons"][0]["percentage_error"],
            4.5556,
        )
        self.assertEqual(
            metrics["commentary"]["change_counts"]["intensified"],
            1,
        )
        self.assertEqual(metrics["disclosures"]["primary_ids"], [
            "disclosure-impairment"
        ])

    def test_unresolved_dossier_evidence_is_rejected_before_synthesis(self) -> None:
        broken = self.dossier.model_copy(deep=True)
        broken.business_drivers[0].evidence_ids.append("missing:evidence")
        with self.assertRaisesRegex(ValueError, "absent from its ledger"):
            validate_research_dossier(broken)

    def test_duplicate_consistency_dimension_is_rejected_before_synthesis(
        self,
    ) -> None:
        broken = self.dossier.model_copy(deep=True)
        broken.management_consistency.components[0].dimension = (
            ManagementConsistencyDimension.EXECUTION_FOLLOW_THROUGH
        )
        with self.assertRaisesRegex(
            ValueError,
            "exactly one management-consistency component",
        ):
            validate_research_dossier(broken)

    def test_missing_selected_filing_coverage_is_rejected(self) -> None:
        broken = self.dossier.model_copy(deep=True)
        broken.filing_coverage.pop()
        with self.assertRaisesRegex(
            ValueError,
            "exactly one record for every selected filing",
        ):
            validate_research_dossier(broken, self.manifest)

    def test_financial_value_surface_must_exist_in_cited_evidence(self) -> None:
        payload = self.dossier.model_dump(mode="json")
        evidence_id = payload["evidence"][0]["evidence_id"]
        filename = payload["evidence"][0]["source_filename"]
        payload["financial_observations"] = [
            {
                "observation_id": "financial-mismatch",
                "source_filename": filename,
                "metric": "ordinary_profit",
                "metric_label_ja": "経常利益",
                "scope": "consolidated",
                "scope_label_ja": "連結",
                "value_kind": "monetary",
                "statement_type": "actual",
                "forecast_version": "not_applicable",
                "target_fiscal_year": 2026,
                "target_period": "FY",
                "value_surface_ja": "999億円",
                "evidence_id": evidence_id,
            }
        ]
        coverage = next(
            item
            for item in payload["filing_coverage"]
            if item["source_filename"] == filename
        )
        coverage["financial_observation_ids"] = ["financial-mismatch"]
        broken = JapaneseResearchDossier.model_validate(payload)
        with self.assertRaisesRegex(
            ValueError,
            "value surfaces absent",
        ):
            validate_research_dossier(broken, self.manifest)

    def test_research_dossier_rejects_evidence_above_the_output_ceiling(
        self,
    ) -> None:
        payload = self.dossier.model_dump(mode="json")
        template = payload["evidence"][0]
        payload["evidence"] = [
            {
                **template,
                "evidence_id": (
                    f"{template['source_filename']}:s{index:04d}"
                ),
            }
            for index in range(1, RESEARCH_MAX_EVIDENCE_RECORDS + 2)
        ]
        with self.assertRaises(ValidationError):
            JapaneseResearchDossier.model_validate(payload)

    def test_grounding_can_return_no_business_driver(self) -> None:
        payload = self.dossier.model_dump(mode="json")
        payload["business_drivers"] = []
        dossier = JapaneseResearchDossier.model_validate(payload)
        self.assertEqual(dossier.business_drivers, [])

    def test_synthesis_uses_dossier_evidence_and_renders_score_details(self) -> None:
        evidence_ids = [item.evidence_id for item in self.dossier.evidence]
        management_sections = (
            "management.strategy",
            "management.execution",
            "management.forecast_discipline",
            "management.accountability",
        )
        claims = [
            {
                "claim_id": "driver-claim",
                "section": "latest.business_driver",
                "order": 1,
                "headline_ja": "顧客需要｜影響混在",
                "body_ja": "需要の変動が主要事業の業績に影響しています。",
                "evidence_ids": [evidence_ids[0]],
                "statement_type": "actual",
            }
        ]
        claims.extend(
            {
                "claim_id": f"management-{index}",
                "section": section,
                "order": 1,
                "headline_ja": "評価の根拠",
                "body_ja": "方針と後続結果を比較し、反証も含めて評価しました。",
                "evidence_ids": evidence_ids,
                "statement_type": "mixed",
            }
            for index, section in enumerate(management_sections, start=1)
        )
        response = JapaneseSynthesisResponse.model_validate(
            {
                "schema_version": "2.0-test",
                "identity": self.dossier.identity,
                "claims": claims,
                "model_notes": [],
            }
        )
        analysis = materialize_japanese_synthesis(self.dossier, response)
        rendered = render_japanese(analysis)
        self.assertIn("**顧客需要｜影響混在：**", rendered)
        self.assertIn("**戦略 0.50：**", rendered)
        self.assertIn("**実行 0.50：**", rendered)
        self.assertIn("**予想・目標規律 0.50：**", rendered)
        self.assertIn("**説明責任 0.50：**", rendered)


if __name__ == "__main__":
    unittest.main()
