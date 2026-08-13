from __future__ import annotations

import io
import json
import logging
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tanshin_pipeline.cli import main
from tanshin_pipeline.config import output_paths
from tanshin_pipeline.evaluation import report_metrics
from tanshin_pipeline.normalization import (
    _date_source_surface,
    _rounded_threshold_source,
    normalize_japanese_analysis,
)
from tanshin_pipeline.pdf_text import PdfTextIndex
from tanshin_pipeline.persistence import read_json, write_json, write_text
from tanshin_pipeline.pipeline import reprocess_stored_analysis
from tanshin_pipeline.render import render_japanese
from tanshin_pipeline.schemas import (
    EvidenceRecord,
    JapaneseModelResponse,
    SectionKey,
    StatementType,
    materialize_japanese_analysis,
    parse_japanese_analysis_payload,
)
from tanshin_pipeline.selection import select_filings
from tanshin_pipeline.validation import ValidationPolicy, validate_japanese
from tests.helpers import (
    persist_research_for_payload,
    synthesis_from_analysis_payload,
    workspace_temp_directory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "real_1808_analysis_ja.json"


def _payload() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _quality_ready_payload() -> dict[str, object]:
    payload = _payload()
    claims = payload["claims"]
    assert isinstance(claims, list)
    extensions = {
        "claim_trend_pers_01": (
            " 長期評価では、単年度の利益水準だけでなく、需要変動を受けた"
            "収益の振幅、投資規律、株主還元の連動性を一体で確認する必要がある。"
            "最新期の改善が構造的なものか、循環回復に依存するものかが次の"
            "検証点となる。"
        ),
        "claim_trend_cons_01": (
            " 継続性の評価では、事業基盤の強さと利益の安定性を分けて"
            "捉えることが重要であり、両者の結び付きが確認点となる。"
        ),
        "claim_trend_cons_02": (
            " この持続性は規模だけでなく案件選別や採算管理にも表れ、"
            "環境変化への対応力を見極める材料となる。"
        ),
        "claim_trend_change_01": (
            " 変化の持続性を判断するには、受注環境から利益とキャッシュへの"
            "移行が同じ方向を保つかを追う必要がある。"
        ),
        "claim_trend_change_02": (
            " 戦略の評価軸は投資額の大きさではなく、既存基盤との補完性と"
            "収益規律が開示上も一貫しているかにある。"
        ),
        "claim_trend_cap_01": (
            " 還元と成長投資の優先順位がキャッシュ創出力に沿って運用されるかが、"
            "資本配分の実効性を測る焦点となる。"
        ),
        "claim_trend_imp_01": (
            " 投資家にとっては、改善を示す先行指標が実績へ連鎖する速度と、"
            "その過程で財務規律が維持されるかが重要である。"
        ),
    }
    for claim in claims:
        claim_id = claim["claim_id"]
        if claim_id in extensions:
            claim["body_ja"] += extensions[claim_id]
    return payload


class QualityFirstPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = select_filings(REPOSITORY_ROOT, "1808")
        parsed = parse_japanese_analysis_payload(_payload())
        cls.normalized = normalize_japanese_analysis(
            parsed,
            cls.manifest,
            REPOSITORY_ROOT,
        )

    def test_model_response_schema_omits_support_span_bookkeeping(self) -> None:
        schema = JapaneseModelResponse.model_json_schema()
        claim_schema = schema["$defs"]["ModelAnalysisClaim"]["properties"]
        self.assertNotIn("figures", claim_schema)
        self.assertNotIn("dates", claim_schema)
        self.assertNotIn("qualifiers", claim_schema)

        payload = _payload()
        claims = payload["claims"]
        assert isinstance(claims, list)
        for claim in claims:
            claim.pop("figures", None)
            claim.pop("dates", None)
            claim.pop("qualifiers", None)
        payload["management_consistency"] = {
            "components": [
                {
                    "dimension": dimension,
                    "rating": 2,
                    "evidence_sufficiency": "sufficient",
                    "rationale_ja": "複数期の経営者説明を比較した中立的な評価です。",
                    "evidence_ids": ["02_2026_FY_tanshin.pdf:s0001"],
                }
                for dimension in (
                    "strategic_coherence",
                    "execution_follow_through",
                    "forecast_target_discipline",
                    "accountability_transparency",
                )
            ],
            "overall_rationale_ja": "複数期を通じた評価には強弱があります。",
        }
        materialized = materialize_japanese_analysis(
            JapaneseModelResponse.model_validate(payload)
        )
        self.assertTrue(all(not claim.figures for claim in materialized.claims))

    def test_1808_regression_recovers_required_same_page_evidence(self) -> None:
        analysis = self.normalized.analysis
        derived = [
            evidence
            for evidence in analysis.evidence
            if re.fullmatch(
                re.escape(evidence.source_filename) + r":r\d{4}-[0-9a-f]{8}",
                evidence.evidence_id,
            )
        ]
        self.assertTrue(
            any("542億円" in evidence.exact_quote_ja for evidence in derived)
        )
        self.assertTrue(
            any(
                "受注時採算の改善" in evidence.exact_quote_ja
                and len(evidence.exact_quote_ja) < 130
                for evidence in derived
            )
        )
        actual_95 = next(
            evidence for evidence in derived if "年95円" in evidence.exact_quote_ja
        )
        forecast_100 = next(
            evidence
            for evidence in analysis.evidence
            if "100円" in evidence.exact_quote_ja
            and "予定" in evidence.exact_quote_ja
        )
        self.assertEqual(actual_95.statement_type, StatementType.ACTUAL)
        self.assertEqual(forecast_100.statement_type, StatementType.FORECAST)
        self.assertNotEqual(actual_95.evidence_id, forecast_100.evidence_id)

        capital_claim = next(
            claim
            for claim in analysis.claims
            if claim.section == SectionKey.TREND_CAPITAL_ALLOCATION
        )
        self.assertEqual(capital_claim.statement_type, StatementType.MIXED)
        self.assertIn(actual_95.evidence_id, capital_claim.evidence_ids)
        self.assertIn(forecast_100.evidence_id, capital_claim.evidence_ids)
        self.assertIn("超過して", next(
            claim.body_ja
            for claim in analysis.claims
            if claim.claim_id == "claim_latest_ctx_01"
        ))

        change_types = {change["type"] for change in self.normalized.changes}
        self.assertIn("derived_evidence_added", change_types)
        self.assertIn("model_support_spans_rebuilt", change_types)
        self.assertIn("safe_text_typo_corrected", change_types)

    def test_prior_1808_response_publishes_under_manual_review_policy(self) -> None:
        analysis = self.normalized.analysis
        report = render_japanese(analysis)
        exemplar = (
            REPOSITORY_ROOT
            / "exemplar_output"
            / "1808"
            / "analysis_ja_1808.md"
        ).read_text(encoding="utf-8")
        validation = validate_japanese(
            analysis,
            self.manifest,
            repository_root=REPOSITORY_ROOT,
            generated_report=report,
            exemplar_text=exemplar,
        )
        self.assertTrue(validation.publishable, validation.model_dump())
        self.assertTrue(validation.factual_integrity_passed)
        self.assertTrue(validation.quality_gate_passed)
        self.assertEqual(validation.blocking_error_count, 0)

        strict_audit = validate_japanese(
            analysis,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=True,
                verify_quote_on_page=False,
                manual_review_publication=False,
                emit_low_value_diagnostics=True,
            ),
            repository_root=REPOSITORY_ROOT,
            generated_report=report,
            exemplar_text=exemplar,
        )
        self.assertFalse(strict_audit.publishable)
        self.assertIn(
            "trend_perspective_too_short",
            {
                issue.code
                for issue in strict_audit.issues
                if issue.severity == "error"
            },
        )

        metrics = report_metrics(
            report,
            anchor_fiscal_year=self.manifest.window.anchor_fiscal_year,
        )
        self.assertNotIn(2027, metrics["unique_years"])
        self.assertNotIn(2031, metrics["unique_years"])
        self.assertEqual(
            metrics["future_years_excluded_from_trend_score"],
            [],
        )

    def test_publishable_run_writes_only_clean_final(self) -> None:
        payload = _quality_ready_payload()
        evidence = payload["evidence"]
        claims = payload["claims"]
        assert isinstance(evidence, list)
        assert isinstance(claims, list)
        old_id = evidence[0]["evidence_id"]
        new_id = "legacy-evidence-id"
        evidence[0]["evidence_id"] = new_id
        for claim in claims:
            claim["evidence_ids"] = [
                new_id if evidence_id == old_id else evidence_id
                for evidence_id in claim["evidence_ids"]
            ]

        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            paths = output_paths(output_root, "1808")
            persist_research_for_payload(paths, payload)
            write_json(
                paths.analysis_structured,
                synthesis_from_analysis_payload(payload),
            )
            legacy_ledger = paths.artifacts_dir / "evidence_ledger.json"
            write_json(legacy_ledger, [{"legacy": True}])
            write_text(paths.report_ja_draft, "stale draft")
            result = reprocess_stored_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            self.assertTrue(result["publishable"])
            status = read_json(paths.report_status_ja)
            self.assertGreaterEqual(status["warning_count"], 0)
            self.assertTrue(paths.report_ja.is_file())
            self.assertFalse(paths.report_ja_draft.exists())
            self.assertIsNone(status["draft_path"])
            self.assertTrue(status["report_generated"])
            self.assertFalse(status["requires_review"])
            self.assertEqual(status["publication_state"], "generated")
            validation = read_json(paths.analysis_validation)
            self.assertEqual(
                validation["statistics"]["citation_mode"],
                "disabled",
            )
            self.assertFalse(legacy_ledger.exists())
            final = paths.report_ja.read_text(encoding="utf-8")
            self.assertNotIn("[!WARNING]", final)

    def test_diagnostic_failure_still_writes_canonical_report(self) -> None:
        payload = _payload()
        claims = payload["claims"]
        assert isinstance(claims, list)
        payload["claims"] = [
            claim
            for claim in claims
            if claim["section"] != "latest.key_takeaway"
        ][:1] + [
            claim
            for claim in claims
            if claim["section"] != "latest.key_takeaway"
        ][1:]

        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            output_root = temp / "output"
            paths = output_paths(output_root, "1808")
            persist_research_for_payload(paths, payload)
            write_json(
                paths.analysis_structured,
                synthesis_from_analysis_payload(payload),
            )
            write_text(paths.report_ja, "stale canonical report")
            write_text(paths.report_en, "stale English final")
            write_text(paths.report_en_draft, "stale English draft")
            result = reprocess_stored_analysis(
                REPOSITORY_ROOT,
                "1808",
                output_root=output_root,
            )
            self.assertTrue(result["report_generated"])
            self.assertIsNone(result["draft_report"])
            self.assertTrue(paths.report_ja.is_file())
            self.assertFalse(paths.report_ja_draft.exists())
            status = read_json(paths.report_status_ja)
            self.assertIsNotNone(status["previous_final_archived_to"])
            self.assertTrue(status["report_generated"])
            self.assertIn(
                status["publication_state"],
                {"generated", "generated_with_diagnostics"},
            )
            self.assertEqual(status["final_path"], str(paths.report_ja))
            self.assertIsNone(status["draft_path"])
            retired = Path(status["previous_final_archived_to"])
            self.assertEqual(
                retired.read_text(encoding="utf-8"),
                "stale canonical report",
            )
            self.assertFalse(paths.report_en.exists())
            self.assertFalse(paths.report_en_draft.exists())
            english_status = read_json(paths.report_status_en)
            self.assertFalse(english_status["publishable"])
            self.assertEqual(
                english_status["invalidated_by_analysis_run_id"],
                status["run_id"],
            )
            self.assertEqual(
                Path(
                    english_status["previous_final_archived_to"]
                ).read_text(encoding="utf-8"),
                "stale English final",
            )
            self.assertEqual(
                Path(
                    english_status["previous_draft_archived_to"]
                ).read_text(encoding="utf-8"),
                "stale English draft",
            )
            self.assertNotIn("[!WARNING]", paths.report_ja.read_text(encoding="utf-8"))

            stderr = io.StringIO()
            stdout = io.StringIO()
            with redirect_stderr(stderr), redirect_stdout(stdout):
                code = main(
                    [
                        "1808",
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                        "--output-root",
                        str(output_root),
                        "--stage",
                        "analysis",
                        "--reprocess-stored",
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn('"report_generated": true', stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_pdf_text_falls_back_when_primary_extraction_is_empty(self) -> None:
        index = PdfTextIndex(REPOSITORY_ROOT, self.manifest)
        try:
            with (
                patch.object(index, "_pypdf_text", return_value=""),
                patch.object(index, "_fitz_text", return_value="fallback text"),
            ):
                self.assertEqual(
                    index.page_text("02_2026_FY_tanshin.pdf", 1),
                    "fallback text",
                )
        finally:
            index.close()
        self.assertGreaterEqual(
            logging.getLogger("pypdf").level,
            logging.CRITICAL,
        )

    def test_period_aliases_and_rounded_thresholds_are_supported(self) -> None:
        latest = EvidenceRecord(
            evidence_id="02_2026_FY_tanshin.pdf:s9998",
            source_filename="02_2026_FY_tanshin.pdf",
            pdf_page=8,
            exact_quote_ja="2026年度の新規供給戸数は増加する見込みです。",
            period_label_ja="2026年3月期",
            period_label_en="FY2026",
            statement_type=StatementType.FORECAST,
            source_section="outlook",
        )
        historical = EvidenceRecord(
            evidence_id="22_2021-05-13_tanshin.pdf:s9999",
            source_filename="22_2021-05-13_tanshin.pdf",
            pdf_page=5,
            exact_quote_ja="完成工事総利益率が低下しました。",
            period_label_ja="2021年3月期",
            period_label_en="FY2021",
            statement_type=StatementType.ACTUAL,
            source_section="results",
        )
        self.assertEqual(
            _date_source_surface("2027年3月期", latest, self.manifest),
            "2026年度",
        )
        self.assertEqual(
            _date_source_surface("2021年", historical, self.manifest),
            "2021年3月期",
        )
        self.assertEqual(
            _rounded_threshold_source(
                "営業活動によるキャッシュ・フローは1,535億円増加し、"
                "1,574億円の収入超過となりました。",
                "1,500億円",
                "1,500億円超の営業キャッシュ・フロー回復",
            ),
            "1,535億円",
        )


if __name__ == "__main__":
    unittest.main()
