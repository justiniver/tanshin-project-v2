from __future__ import annotations

import unittest
from pathlib import Path

from tanshin_pipeline.management_consistency import (
    COMPONENT_WEIGHTS,
    calculate_management_consistency,
    is_management_discussion_evidence,
)
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.render import render_english, render_japanese
from tanshin_pipeline.schemas import (
    EnglishTranslation,
    JapaneseAnalysis,
    ManagementConsistencyAssessment,
    ManagementConsistencyComponent,
    ManagementConsistencyDimension,
    EvidenceRecord,
    StatementType,
)
from tanshin_pipeline.selection import select_filings


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _pending_assessment(
    evidence_ids: list[str],
) -> ManagementConsistencyAssessment:
    ratings = {
        ManagementConsistencyDimension.STRATEGIC_COHERENCE: 3,
        ManagementConsistencyDimension.EXECUTION_FOLLOW_THROUGH: 3,
        ManagementConsistencyDimension.FORECAST_TARGET_DISCIPLINE: 2,
        ManagementConsistencyDimension.ACCOUNTABILITY_TRANSPARENCY: 3,
    }
    return ManagementConsistencyAssessment(
        methodology_version="pending",
        components=[
            ManagementConsistencyComponent(
                dimension=dimension,
                rating=rating,
                normalized_score=rating / 4,
                weight=0,
                rationale_ja="複数期の経営者説明と後続実績を比較した評価です。",
                evidence_ids=evidence_ids,
            )
            for dimension, rating in ratings.items()
        ],
        overall_rationale_ja="方針は概ね継続した一方、予想達成にはばらつきがあります。",
    )


class ManagementConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = select_filings(REPOSITORY_ROOT, "1808")
        cls.real_analysis = JapaneseAnalysis.model_validate(
            read_json(FIXTURES / "real_1808_analysis_ja.json")
        )

    def test_score_uses_fixed_weights_and_management_discussion_coverage(self) -> None:
        evidence_ids = [
            "37_2017-05-12_tanshin.pdf:s0001",
            "37_2017-05-12_tanshin.pdf:s0003",
            "21_2021-05-13_tanshin.pdf:s0001",
            "21_2021-05-13_tanshin.pdf:s0002",
            "09_2024-05-10_tanshin.pdf:s0001",
            "05_2025_FY_tanshin.pdf:s0001",
            "01_2026_FY_tanshin.pdf:s0001",
            "01_2026_FY_tanshin.pdf:s0006",
        ]
        assessment, changes = calculate_management_consistency(
            _pending_assessment(evidence_ids),
            self.real_analysis.evidence,
            self.manifest,
        )
        assert assessment is not None
        self.assertEqual(
            {item.dimension: item.weight for item in assessment.components},
            COMPONENT_WEIGHTS,
        )
        self.assertEqual(assessment.raw_score, 0.6875)
        self.assertEqual(assessment.score, 0.69)
        self.assertGreaterEqual(assessment.evidence_confidence, 0.9)
        self.assertEqual(assessment.confidence_label, "high")
        self.assertEqual(assessment.evidence_count, 8)
        self.assertEqual(assessment.management_discussion_evidence_share, 1.0)
        self.assertEqual(changes[0]["type"], "management_consistency_calculated")

    def test_thin_evidence_keeps_subscores_blank_and_uses_overall_fallback(
        self,
    ) -> None:
        high = _pending_assessment(["01_2026_FY_tanshin.pdf:s0001"])
        for component in high.components:
            component.rating = 4
            component.normalized_score = 1
        assessment, _ = calculate_management_consistency(
            high,
            self.real_analysis.evidence,
            self.manifest,
        )
        assert assessment is not None
        self.assertEqual(assessment.score, 0.5)
        self.assertEqual(assessment.raw_score, 0.5)
        self.assertTrue(
            all(
                component.evidence_sufficiency == "insufficient"
                and component.rating is None
                and component.normalized_score is None
                for component in assessment.components
            )
        )
        self.assertLess(assessment.evidence_confidence, 0.5)
        self.assertEqual(assessment.confidence_label, "low")

    def test_management_discussion_classifier_includes_narrative_sections_only(self) -> None:
        def evidence(section: str) -> EvidenceRecord:
            return EvidenceRecord(
                evidence_id="01_2026_FY_tanshin.pdf:s9999",
                source_filename="01_2026_FY_tanshin.pdf",
                pdf_page=1,
                exact_quote_ja="テスト",
                period_label_ja="2026年3月期",
                period_label_en="FY2026",
                statement_type=StatementType.ACTUAL,
                source_section=section,
            )

        for section in (
            "当連結会計年度の概況",
            "建設関連事業",
            "管理運営事業",
            "サービス関連事業",
            "経営目標・株主還元方針",
        ):
            self.assertTrue(is_management_discussion_evidence(evidence(section)))
        for section in (
            "連結経営成績",
            "連結キャッシュ・フローの状況",
            "連結業績予想",
            "連結損益計算書",
        ):
            self.assertFalse(is_management_discussion_evidence(evidence(section)))

    def test_score_and_method_note_render_in_both_languages(self) -> None:
        analysis = JapaneseAnalysis.model_validate(
            read_json(FIXTURES / "fake_analysis_ja.json")
        )
        translation = EnglishTranslation.model_validate(
            read_json(FIXTURES / "fake_translation_en.json")
        )
        assessment, _ = calculate_management_consistency(
            _pending_assessment(
                [
                    "37_2017-05-12_tanshin.pdf:s0001",
                    "21_2021-05-13_tanshin.pdf:s0001",
                    "01_2026_FY_tanshin.pdf:s0001",
                    "05_2025_FY_tanshin.pdf:s0001",
                ]
            ),
            self.real_analysis.evidence,
            self.manifest,
        )
        analysis.management_consistency = assessment
        ja = render_japanese(analysis)
        en = render_english(analysis, translation)
        assert assessment is not None and assessment.score is not None
        self.assertIn(
            f"経営一貫性スコア：{assessment.score:.2f}",
            ja,
        )
        self.assertIn(
            f"Management consistency score: {assessment.score:.2f}",
            en,
        )
        self.assertNotIn("証拠信頼度", ja)
        self.assertNotIn("evidence confidence", en)
        self.assertIn(
            "内訳：戦略 0.75｜実行 0.75｜予想・目標規律 0.50｜説明責任 0.75"
            "（評価済み 4/4項目）",
            ja,
        )
        self.assertIn(
            "Breakdown: strategy 0.75｜execution 0.75｜"
            "forecast discipline 0.50｜accountability 0.75"
            " (4 of 4 dimensions assessed)",
            en,
        )
        self.assertIn(
            f"経営一貫性スコア：{assessment.score:.2f}**"
            "<sup>*</sup><br>\n内訳：",
            ja,
        )
        self.assertIn(
            f"Management consistency score: {assessment.score:.2f}**"
            "<sup>*</sup><br>\nBreakdown:",
            en,
        )
        self.assertIn("<small>&#42; 経営一貫性スコア", ja)
        self.assertIn("<small>&#42; The management consistency score", en)
        self.assertIn(
            "「戦略」は、会社の基本方針が長期的に筋の通った形で続いているか",
            ja,
        )
        self.assertIn(
            "「予想・目標規律」は、業績予想や中期目標を後年の実績と照合し",
            ja,
        )
        self.assertIn(
            "整合する材料と反する材料の両方を確認します",
            ja,
        )
        self.assertIn(
            "“Strategy” asks whether the company's central direction remains coherent",
            en,
        )
        self.assertIn(
            "“Forecast discipline” compares forecasts and medium-term targets",
            en,
        )
        self.assertIn(
            "Both supporting and contradictory evidence are considered",
            en,
        )
        self.assertIn(
            "not necessarily a better strategy, stronger business, or more attractive investment",
            en,
        )

    def test_insufficient_components_are_blank_and_excluded(self) -> None:
        analysis = JapaneseAnalysis.model_validate(
            read_json(FIXTURES / "fake_analysis_ja.json")
        )
        assessment, changes = calculate_management_consistency(
            _pending_assessment(["01_2026_FY_tanshin.pdf:s0001"]),
            self.real_analysis.evidence,
            self.manifest,
        )
        analysis.management_consistency = assessment
        rendered = render_japanese(analysis)
        self.assertIn("経営一貫性スコア：0.50", rendered)
        self.assertIn("<sup>*</sup><br>\n内訳：", rendered)
        self.assertIn("戦略 —", rendered)
        self.assertIn("（評価済み 0/4項目）", rendered)
        self.assertEqual(
            changes[0]["score_calculation_method"],
            "neutral_0.50_fallback_no_scorable_components",
        )
        self.assertEqual(changes[0]["available_component_count"], 0)

    def test_one_missing_component_is_blank_and_overall_uses_available_mean(
        self,
    ) -> None:
        evidence_ids = [
            "37_2017-05-12_tanshin.pdf:s0001",
            "21_2021-05-13_tanshin.pdf:s0001",
            "01_2026_FY_tanshin.pdf:s0001",
            "05_2025_FY_tanshin.pdf:s0001",
        ]
        pending = _pending_assessment(evidence_ids)
        forecast = next(
            component
            for component in pending.components
            if component.dimension
            == ManagementConsistencyDimension.FORECAST_TARGET_DISCIPLINE
        )
        forecast.evidence_ids = ["01_2026_FY_tanshin.pdf:s0001"]
        assessment, changes = calculate_management_consistency(
            pending,
            self.real_analysis.evidence,
            self.manifest,
        )
        forecast_result = next(
            component
            for component in assessment.components
            if component.dimension
            == ManagementConsistencyDimension.FORECAST_TARGET_DISCIPLINE
        )
        self.assertEqual(forecast_result.evidence_sufficiency, "insufficient")
        self.assertIsNone(forecast_result.rating)
        self.assertIsNone(forecast_result.normalized_score)
        self.assertEqual(assessment.score, 0.75)
        analysis = JapaneseAnalysis.model_validate(
            read_json(FIXTURES / "fake_analysis_ja.json")
        )
        analysis.management_consistency = assessment
        rendered = render_japanese(analysis)
        self.assertIn("（評価済み 3/4項目）", rendered)
        self.assertEqual(
            changes[0]["score_calculation_method"],
            "arithmetic_mean_of_available_normalized_components",
        )
        self.assertEqual(
            changes[0]["excluded_dimensions"],
            ["forecast_target_discipline"],
        )

    def test_missing_model_assessment_still_produces_complete_numeric_score(
        self,
    ) -> None:
        assessment, changes = calculate_management_consistency(
            None,
            self.real_analysis.evidence,
            self.manifest,
        )
        self.assertEqual(assessment.score, 0.5)
        self.assertEqual(len(assessment.components), 4)
        self.assertTrue(
            all(
                component.normalized_score is None
                and component.evidence_sufficiency == "insufficient"
                for component in assessment.components
            )
        )
        self.assertEqual(
            changes[0]["score_calculation_method"],
            "neutral_0.50_fallback_no_scorable_components",
        )


if __name__ == "__main__":
    unittest.main()
