from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tanshin_pipeline.cli import main
from tanshin_pipeline.config import output_paths
from tanshin_pipeline.english_financials import (
    extract_english_financial_amounts,
    extract_japanese_financial_amounts,
    normalize_english_financials,
    preserve_english_translation,
)
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.pipeline import (
    _process_english_response,
)
from tanshin_pipeline.render import render_english
from tanshin_pipeline.schemas import (
    EnglishTranslation,
    JapaneseAnalysis,
    SupportedSpan,
    TranslatedSpan,
    ValidationResult,
)
from tanshin_pipeline.selection import select_filings
from tanshin_pipeline.validation import validate_english


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _stored_pair() -> tuple[JapaneseAnalysis, EnglishTranslation]:
    analysis = JapaneseAnalysis.model_validate(
        read_json(FIXTURES / "fake_analysis_ja.json")
    )
    translation = EnglishTranslation.model_validate(
        read_json(FIXTURES / "fake_translation_en.json")
    )
    return analysis, translation


def _add_compound_financial_example(
    analysis: JapaneseAnalysis,
    translation: EnglishTranslation,
    *,
    render_in_english: bool = True,
) -> None:
    evidence_id = analysis.claims[0].evidence_ids[0]
    value_id = f"{analysis.claims[0].claim_id}:figure:01"
    source_surface = "3,249億51百万円"
    rendered_surface = (
        "¥324.951 billion"
        if render_in_english
        else source_surface
    )

    analysis.claims[0].body_ja += f" 売上高は{source_surface}でした。"
    analysis.claims[0].figures = [
        SupportedSpan(
            value_id=value_id,
            claim_surface_ja=source_surface,
            source_surface_ja=source_surface,
            evidence_id=evidence_id,
        )
    ]
    analysis.evidence[0].exact_quote_ja += f" 売上高は{source_surface}でした。"

    translation.claims[0].body_en += (
        f" Revenue was {rendered_surface}."
    )
    translation.claims[0].figures = [
        TranslatedSpan(
            value_id=value_id,
            claim_surface_en=rendered_surface,
            source_surface_ja=source_surface,
            evidence_id=evidence_id,
        )
    ]


def _set_financial_example(
    analysis: JapaneseAnalysis,
    translation: EnglishTranslation,
    *,
    expected_surface: str,
    rendered_surface: str,
) -> None:
    evidence_id = analysis.claims[0].evidence_ids[0]
    value_id = f"{analysis.claims[0].claim_id}:figure:01"
    analysis.claims[0].body_ja = f"売上高は{expected_surface}でした。"
    analysis.claims[0].figures = [
        SupportedSpan(
            value_id=value_id,
            claim_surface_ja=expected_surface,
            source_surface_ja=expected_surface,
            evidence_id=evidence_id,
        )
    ]
    analysis.evidence[0].exact_quote_ja = (
        f"売上高は{expected_surface}でした。"
    )
    translation.claims[0].body_en = f"Revenue was {rendered_surface}."
    translation.claims[0].figures = [
        TranslatedSpan(
            value_id=value_id,
            claim_surface_en=rendered_surface,
            source_surface_ja=expected_surface,
            evidence_id=evidence_id,
        )
    ]


class EnglishFinancialNotationTests(unittest.TestCase):
    def test_extracts_economically_equivalent_japanese_units(self) -> None:
        cases = (
            ("252億円", "25,200百万円", Decimal("25200000000")),
            ("252 億円", "25,200 百万円", Decimal("25200000000")),
            ("55.59億円", "5,559百万円", Decimal("5559000000")),
            (
                "1兆2,731億円",
                "1,273,100百万円",
                Decimal("1273100000000"),
            ),
            (
                "1兆 2,731 億円",
                "1,273,100 百万円",
                Decimal("1273100000000"),
            ),
            (
                "3,249億51百万円",
                "324,951百万円",
                Decimal("324951000000"),
            ),
            ("15,387,883千円", "15,387.883百万円", Decimal("15387883000")),
        )

        for left, right, expected in cases:
            with self.subTest(left=left, right=right):
                left_amounts = extract_japanese_financial_amounts(left)
                right_amounts = extract_japanese_financial_amounts(right)
                self.assertEqual(len(left_amounts), 1)
                self.assertEqual(len(right_amounts), 1)
                self.assertEqual(left_amounts[0].yen_value, expected)
                self.assertEqual(right_amounts[0].yen_value, expected)

    def test_extracts_english_yen_amounts_without_fx_conversion(self) -> None:
        cases = (
            (
                "¥83.4 billion",
                Decimal("83400000000"),
                Decimal("50000000"),
            ),
            (
                "¥1,273.1 billion",
                Decimal("1273100000000"),
                Decimal("50000000"),
            ),
            ("JPY 324,951 million", Decimal("324951000000"), Decimal("0")),
            (
                "25.2 billion yen",
                Decimal("25200000000"),
                Decimal("50000000"),
            ),
            ("¥95 per share", Decimal("95"), Decimal("0")),
        )

        for surface, expected, tolerance in cases:
            with self.subTest(surface=surface):
                amounts = extract_english_financial_amounts(surface)
                self.assertEqual(len(amounts), 1)
                self.assertEqual(amounts[0].yen_value, expected)
                self.assertEqual(
                    amounts[0].rounding_tolerance_yen,
                    tolerance,
                )

    def test_preserves_model_rendered_english_financial_notation(self) -> None:
        analysis, translation = _stored_pair()
        _add_compound_financial_example(analysis, translation)
        original = translation.model_dump(mode="json")

        result = preserve_english_translation(translation)

        self.assertEqual(result.translation.model_dump(mode="json"), original)
        self.assertEqual(result.changes, [])
        self.assertEqual(result.unresolved, [])
        self.assertIsNot(result.translation, translation)

    def test_normalizer_entry_point_is_a_semantic_passthrough(self) -> None:
        analysis, translation = _stored_pair()
        _add_compound_financial_example(analysis, translation)

        result = normalize_english_financials(analysis, translation)

        self.assertIn(
            "¥324.951 billion",
            result.translation.claims[0].body_en,
        )
        self.assertFalse(result.changes)
        self.assertFalse(result.unresolved)

    def test_validation_accepts_economically_equivalent_english_notation(
        self,
    ) -> None:
        analysis, translation = _stored_pair()
        _add_compound_financial_example(analysis, translation)

        result = validate_english(
            translation,
            analysis,
            select_filings(REPOSITORY_ROOT, "1808"),
        )

        issue_codes = {issue.code for issue in result.issues}
        self.assertTrue(result.publishable, result.model_dump())
        self.assertNotIn("english_financial_value_mismatch", issue_codes)

    def test_validation_and_rendering_accept_english_financial_notation(
        self,
    ) -> None:
        analysis, translation = _stored_pair()
        _add_compound_financial_example(analysis, translation)

        result = validate_english(
            translation,
            analysis,
            select_filings(REPOSITORY_ROOT, "1808"),
        )
        rendered = render_english(analysis, translation)

        self.assertTrue(result.publishable, result.model_dump())
        self.assertIn("Revenue was ¥324.951 billion.", rendered)

    def test_validation_accepts_equivalent_english_units(
        self,
    ) -> None:
        analysis, translation = _stored_pair()
        _set_financial_example(
            analysis,
            translation,
            expected_surface="55.59億円",
            rendered_surface="¥5.559 billion",
        )

        result = validate_english(
            translation,
            analysis,
            select_filings(REPOSITORY_ROOT, "1808"),
        )

        issue_codes = {issue.code for issue in result.issues}
        self.assertTrue(result.publishable, result.model_dump())
        self.assertNotIn("translated_figure_surface_missing", issue_codes)
        self.assertNotIn("english_financial_value_mismatch", issue_codes)

    def test_validation_accepts_one_decimal_billion_display_rounding(
        self,
    ) -> None:
        analysis, translation = _stored_pair()
        _set_financial_example(
            analysis,
            translation,
            expected_surface="60,286百万円",
            rendered_surface="¥60.3 billion",
        )

        result = validate_english(
            translation,
            analysis,
            select_filings(REPOSITORY_ROOT, "1808"),
        )

        issue_codes = {issue.code for issue in result.issues}
        self.assertTrue(result.publishable, result.model_dump())
        self.assertNotIn("english_financial_value_mismatch", issue_codes)

    def test_validation_rejects_value_outside_display_rounding_tolerance(
        self,
    ) -> None:
        analysis, translation = _stored_pair()
        _set_financial_example(
            analysis,
            translation,
            expected_surface="60,286百万円",
            rendered_surface="¥60.2 billion",
        )

        result = validate_english(
            translation,
            analysis,
            select_filings(REPOSITORY_ROOT, "1808"),
        )

        issue_codes = {issue.code for issue in result.issues}
        self.assertFalse(result.publishable)
        self.assertIn("english_financial_value_mismatch", issue_codes)

    def test_validation_rejects_financial_scale_mismatch_in_claim_prose(
        self,
    ) -> None:
        analysis, translation = _stored_pair()
        _set_financial_example(
            analysis,
            translation,
            expected_surface="252億円",
            rendered_surface="¥2.52 billion",
        )

        result = validate_english(
            translation,
            analysis,
            select_filings(REPOSITORY_ROOT, "1808"),
        )

        issues = {
            issue.code: issue
            for issue in result.issues
        }
        self.assertFalse(result.publishable)
        self.assertIn("english_financial_value_mismatch", issues)
        self.assertEqual(
            issues["english_financial_value_mismatch"].severity,
            "error",
        )
        self.assertEqual(
            issues["english_financial_value_mismatch"].category,
            "factual_integrity",
        )
        self.assertNotIn("translated_figure_surface_missing", issues)

    def test_pipeline_writes_model_rendered_financial_artifacts(self) -> None:
        analysis, translation = _stored_pair()
        _add_compound_financial_example(analysis, translation)
        manifest = select_filings(REPOSITORY_ROOT, "1808")
        prepared = SimpleNamespace(
            manifest=manifest,
            paths=output_paths(REPOSITORY_ROOT / "unused_test_output", "1808"),
        )
        validation = ValidationResult(
            valid=True,
            publishable=True,
            factual_integrity_passed=True,
            quality_gate_passed=True,
            blocking_error_count=0,
            warning_count=0,
            language="en",
            issues=[],
            statistics={},
        )

        with (
            patch("tanshin_pipeline.pipeline.write_json") as write_json,
            patch("tanshin_pipeline.pipeline.write_text"),
            patch(
                "tanshin_pipeline.pipeline.render_english",
                return_value="# Test report",
            ) as render,
            patch(
                "tanshin_pipeline.pipeline.validate_english",
                return_value=validation,
            ),
            patch(
                "tanshin_pipeline.pipeline.compare_reports",
                return_value={},
            ),
            patch(
                "tanshin_pipeline.pipeline._retire_current_report",
                return_value=None,
            ),
            patch(
                "tanshin_pipeline.pipeline._discard_report_path"
            ) as discard_path,
            patch("tanshin_pipeline.pipeline._write_report_status"),
        ):
            processed, _ = _process_english_response(
                REPOSITORY_ROOT,
                prepared,
                analysis,
                translation,
                mode="test",
            )

        written_payloads = {
            call.args[0]: call.args[1] for call in write_json.call_args_list
        }
        normalization = written_payloads[
            prepared.paths.translation_normalization
        ]
        normalized_artifact = written_payloads[
            prepared.paths.translation_normalized
        ]
        rendered_translation = render.call_args.args[1]

        self.assertEqual(
            normalization["mode"],
            "model_rendered_english_financial_notation",
        )
        self.assertFalse(normalization["financial_text_modified"])
        self.assertEqual(normalization["change_count"], 0)
        self.assertEqual(
            normalized_artifact.model_dump(mode="json"),
            translation.model_dump(mode="json"),
        )
        discard_path.assert_any_call(
            prepared.paths.artifacts_dir / "evidence_ledger.json"
        )
        self.assertEqual(
            processed.model_dump(mode="json"),
            translation.model_dump(mode="json"),
        )
        self.assertEqual(
            rendered_translation.model_dump(mode="json"),
            translation.model_dump(mode="json"),
        )
        self.assertIn(
            "¥324.951 billion",
            rendered_translation.claims[0].body_en,
        )

    def test_translation_reprocess_dispatches_without_networking(self) -> None:
        result = {
            "publishable": True,
            "valid": True,
            "draft_report": None,
        }
        stdout = io.StringIO()
        with (
            patch(
                "tanshin_pipeline.cli.reprocess_stored_translation",
                return_value=result,
            ) as reprocess,
            redirect_stdout(stdout),
        ):
            code = main(
                [
                    "1808",
                    "--repository-root",
                    str(REPOSITORY_ROOT),
                    "--stage",
                    "translation",
                    "--reprocess-stored",
                ]
            )
        self.assertEqual(code, 0)
        reprocess.assert_called_once()


if __name__ == "__main__":
    unittest.main()
