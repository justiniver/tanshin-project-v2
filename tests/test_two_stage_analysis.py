from __future__ import annotations

import unittest
from pathlib import Path

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
        metrics = build_research_metrics(self.dossier)
        self.assertEqual(metrics["business_drivers"]["total"], 1)
        self.assertEqual(metrics["commitments"]["total"], 0)
        self.assertEqual(
            metrics["commitments"]["observed_revision_count"],
            0,
        )
        self.assertIn("selected filings", metrics["interpretation_guardrail"])

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
