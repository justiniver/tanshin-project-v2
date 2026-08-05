from __future__ import annotations

import unittest
from pathlib import Path

from tanshin_pipeline.evaluation import (
    compare_reports,
    essential_quality_issues,
    report_metrics,
)
from tanshin_pipeline.normalization import numeric_surfaces
from tanshin_pipeline.persistence import read_json
from tanshin_pipeline.render import (
    bilingual_evidence_ledger,
    render_english,
    render_japanese,
    render_japanese_draft,
)
from tanshin_pipeline.schemas import (
    EnglishTranslation,
    JapaneseAnalysis,
    SectionKey,
)
from tanshin_pipeline.selection import select_filings
from tanshin_pipeline.validation import (
    ValidationPolicy,
    validate_english,
    validate_japanese,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class ValidationRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = select_filings(REPOSITORY_ROOT, "1808")
        self.analysis = JapaneseAnalysis.model_validate(
            read_json(FIXTURES / "fake_analysis_ja.json")
        )
        self.translation = EnglishTranslation.model_validate(
            read_json(FIXTURES / "fake_translation_en.json")
        )

    def test_stored_fake_responses_validate_with_relaxed_quality_policy(self) -> None:
        ja = validate_japanese(
            self.analysis,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=False,
                verify_quote_on_page=False,
            ),
        )
        en = validate_english(self.translation, self.analysis, self.manifest)
        self.assertTrue(ja.valid, ja.model_dump())
        self.assertTrue(en.valid, en.model_dump())

    def test_numeric_span_bookkeeping_is_advisory_by_default(self) -> None:
        changed = self.analysis.model_copy(deep=True)
        changed.claims[0].body_ja += " 売上高は100億円です。"
        default_result = validate_japanese(
            changed,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=False,
                verify_quote_on_page=False,
            ),
        )
        self.assertTrue(default_result.valid)
        self.assertNotIn(
            "unsupported_numeric_surface",
            {issue.code for issue in default_result.issues},
        )

        audit_result = validate_japanese(
            changed,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=False,
                verify_quote_on_page=False,
                manual_review_publication=False,
                emit_low_value_diagnostics=True,
            ),
        )
        self.assertFalse(audit_result.valid)
        self.assertIn(
            "unsupported_numeric_surface",
            {issue.code for issue in audit_result.issues},
        )

    def test_invalid_page_is_rejected(self) -> None:
        changed = self.analysis.model_copy(deep=True)
        changed.evidence[0].pdf_page = 9999
        result = validate_japanese(
            changed,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=False,
                verify_quote_on_page=False,
            ),
        )
        self.assertIn("invalid_pdf_page", {issue.code for issue in result.issues})

    def test_unselected_period_source_is_rejected(self) -> None:
        changed = self.analysis.model_copy(deep=True)
        changed.evidence[1].source_filename = "04_2026_Q1_tanshin.pdf"
        changed.evidence[1].evidence_id = "04_2026_Q1_tanshin.pdf:s0001"
        for claim in changed.claims:
            claim.evidence_ids = [
                "04_2026_Q1_tanshin.pdf:s0001"
                if value == "05_2025_FY_tanshin.pdf:s0001"
                else value
                for value in claim.evidence_ids
            ]
        result = validate_japanese(
            changed,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=False,
                verify_quote_on_page=False,
            ),
        )
        self.assertIn("unselected_source", {issue.code for issue in result.issues})

    def test_quote_must_exist_on_cited_page_when_enabled(self) -> None:
        result = validate_japanese(
            self.analysis,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=False,
                verify_quote_on_page=True,
                emit_low_value_diagnostics=True,
            ),
            repository_root=REPOSITORY_ROOT,
        )
        self.assertIn(
            "quote_not_found_on_page",
            {issue.code for issue in result.issues},
        )

    def test_cross_language_evidence_change_is_rejected(self) -> None:
        changed = self.translation.model_copy(deep=True)
        changed.claims[0].evidence_ids = ["05_2025_FY_tanshin.pdf:s0001"]
        result = validate_english(changed, self.analysis, self.manifest)
        self.assertFalse(result.valid)
        self.assertIn(
            "cross_language_evidence_ids",
            {issue.code for issue in result.issues},
        )

    def test_markdown_rendering_from_stored_fake_responses(self) -> None:
        ja = render_japanese(self.analysis)
        en = render_english(self.analysis, self.translation)
        self.assertIn("## 1. エグゼクティブサマリー", ja)
        self.assertIn("### 資本配分の変化", ja)
        self.assertIn("01_2026_FY_tanshin.pdf:s0001", ja)
        self.assertIn("## 1. Executive summary", en)
        self.assertIn("### Capital-allocation developments", en)
        self.assertIn("01_2026_FY_tanshin.pdf:s0001", en)
        self.assertIn(self.analysis.evidence[0].exact_quote_ja, en)
        self.assertNotIn(
            self.translation.evidence_translations[0].quote_en,
            en,
        )

    def test_company_overview_replaces_top_metadata_bullets(self) -> None:
        analysis = self.analysis.model_copy(deep=True)
        translation = self.translation.model_copy(deep=True)
        analysis.claims.append(
            analysis.claims[0].model_copy(
                update={
                    "claim_id": "company_overview",
                    "section": SectionKey.COMPANY_OVERVIEW,
                    "order": 1,
                    "headline_ja": "企業概要",
                    "body_ja": (
                        "法人顧客向けに設備と保守サービスを提供し、"
                        "販売後の継続サービスからも収益を得ています。"
                    ),
                }
            )
        )
        translation.claims.append(
            translation.claims[0].model_copy(
                update={
                    "claim_id": "company_overview",
                    "section": SectionKey.COMPANY_OVERVIEW,
                    "order": 1,
                    "headline_en": "Company overview",
                    "body_en": (
                        "The company supplies equipment and maintenance services "
                        "to corporate customers, with recurring revenue from "
                        "post-sale support."
                    ),
                }
            )
        )

        ja = render_japanese(analysis)
        en = render_english(analysis, translation)

        self.assertTrue(
            ja.startswith(
                f"# {analysis.identity.company_name_ja}"
                f"（{analysis.identity.security_code}）"
            )
        )
        self.assertTrue(
            en.startswith(
                f"# {translation.identity.company_name_en} "
                f"({translation.identity.security_code})"
            )
        )
        self.assertIn("## 企業概要", ja)
        self.assertIn("## Company overview", en)
        self.assertLess(
            ja.index("## 企業概要"),
            ja.index("## 1. エグゼクティブサマリー"),
        )
        self.assertLess(
            en.index("## Company overview"),
            en.index("## 1. Executive summary"),
        )
        self.assertIn("法人顧客向けに設備と保守サービス", ja)
        self.assertIn("supplies equipment and maintenance services", en)
        self.assertNotIn("- 証券コード：", ja)
        self.assertNotIn("- 最新の決算短信：", ja)
        self.assertNotIn("- Security code:", en)
        self.assertNotIn("- Latest filing:", en)

    def test_trend_theme_headlines_precede_bodies_without_blank_line(self) -> None:
        ja = render_japanese(self.analysis)
        en = render_english(self.analysis, self.translation)

        self.assertIn(
            "**継続する基盤**<br>\n事業基盤の重視は継続しています。",
            ja,
        )
        self.assertNotIn(
            "**継続する基盤**<br>\n\n事業基盤の重視は継続しています。",
            ja,
        )
        self.assertIn(
            "**Persistent foundation**<br>\n"
            "Management continued to emphasize the business foundation.",
            en,
        )
        self.assertNotIn(
            "**Persistent foundation**<br>\n\n"
            "Management continued to emphasize the business foundation.",
            en,
        )

    def test_evidence_translations_are_optional_and_ignored(self) -> None:
        changed = self.translation.model_copy(deep=True)
        changed.evidence_translations = []
        rendered = render_english(self.analysis, changed)
        self.assertIn(self.analysis.evidence[0].exact_quote_ja, rendered)
        self.assertNotIn("[English translation unavailable]", rendered)
        validation = validate_english(changed, self.analysis, self.manifest)
        self.assertTrue(validation.publishable)

        ledger = bilingual_evidence_ledger(self.analysis, changed)
        self.assertIsNone(ledger[0]["quote_en"])
        self.assertEqual(ledger[0]["rendered_quote_language"], "ja")
        self.assertEqual(
            ledger[0]["rendered_quote"],
            self.analysis.evidence[0].exact_quote_ja,
        )

    def test_fiscal_period_is_one_numeric_surface(self) -> None:
        self.assertEqual(
            numeric_surfaces("2026年3月期、総還元性向50％、6期合計"),
            ["2026年3月期", "50%", "6期"],
        )

    def test_warning_only_draft_is_clean_markdown(self) -> None:
        warning_only = validate_japanese(
            self.analysis,
            self.manifest,
            policy=ValidationPolicy(
                strict_quality=False,
                verify_quote_on_page=True,
            ),
            repository_root=REPOSITORY_ROOT,
        )
        self.assertTrue(warning_only.publishable)
        self.assertGreater(warning_only.warning_count, 0)
        draft = render_japanese_draft(self.analysis, warning_only)
        self.assertNotIn("[!WARNING]", draft)
        self.assertIn("01_2026_FY_tanshin.pdf:s0001", draft)

    def test_offline_comparison_rubric(self) -> None:
        generated = render_english(self.analysis, self.translation)
        exemplar = (
            REPOSITORY_ROOT
            / "exemplar_output"
            / "1808"
            / "analysis_en_1808.md"
        ).read_text(encoding="utf-8")
        result = compare_reports(generated, exemplar)
        self.assertEqual(
            set(result["scores_0_to_5"]),
            {
                "structure",
                "section_coverage",
                "executive_breadth",
                "analytical_depth",
                "trend_specificity",
                "evidence_density",
                "tone",
                "repetition",
                "readability",
                "approximate_length",
            },
        )

    def test_quality_metrics_reward_breadth_and_restrained_tone(self) -> None:
        narrow = """\
# Report
## 1. エグゼクティブサマリー
### 経営者コメントの要点
- 売上高が増加しました。
- 営業利益が増加しました。
- 純利益が増加しました。
## 2. トレンド分析
### 10年間を通じた見方
業績は推移しました。
### 変わらなかったこと
**圧倒的な強み** 圧倒的な競争力で大台を突破し、正常化しました。
## 根拠一覧
"""
        broad = """\
# Report
## 1. エグゼクティブサマリー
### 経営者コメントの要点
- 売上高と営業利益が増加しました。
- 営業キャッシュ・フローと純資産が増加しました。
- 配当方針を変更し、次期予想を公表しました。
- 原材料費上昇を主要リスクとして示しました。
## 2. トレンド分析
### 10年間を通じた見方
能力は維持された一方、利益率は循環しました。
### 変わらなかったこと
**能力と収益化を区別** 能力は続いたものの、利益は需要に左右されました。
## 根拠一覧
"""
        narrow_metrics = report_metrics(narrow)
        broad_metrics = report_metrics(broad)
        self.assertGreater(
            broad_metrics["key_takeaway_category_count"],
            narrow_metrics["key_takeaway_category_count"],
        )
        self.assertGreater(
            narrow_metrics["promotional_terms"],
            broad_metrics["promotional_terms"],
        )
        result = compare_reports(broad, narrow)
        self.assertGreater(result["scores_0_to_5"]["executive_breadth"], 0)

    def test_metrics_ignore_years_in_evidence_ids_and_japanese_substrings(self) -> None:
        report = """\
# Report
## 1. エグゼクティブサマリー
### 経営者コメントの要点
- 配当方針を変更しました。 <sup>[2026年3月期 p.8/r0008-2010a067]</sup>
## 2. トレンド分析
### 10年間を通じた見方
2026年3月期までの推移です。
## 根拠一覧
"""
        metrics = report_metrics(report, anchor_fiscal_year=2026)
        self.assertEqual(metrics["unique_years"], [2026])
        self.assertEqual(metrics["first_person_terms"], 0)

    def test_trend_length_is_independent_of_executive_summary_length(self) -> None:
        def report(trend_characters: int) -> str:
            return (
                "# Report\n\n"
                "## 1. エグゼクティブサマリー\n\n"
                + ("要" * 4000)
                + "\n\n## 2. トレンド分析\n\n"
                + ("変" * trend_characters)
                + "\n\n## 根拠一覧\n"
            )

        exemplar = report(1000)
        short = report(1490)
        adequate = report(1500)
        short_codes = {
            code
            for code, _ in essential_quality_issues(
                short,
                exemplar,
                language="ja",
            )
        }
        adequate_codes = {
            code
            for code, _ in essential_quality_issues(
                adequate,
                exemplar,
                language="ja",
            )
        }
        self.assertIn("trend_analysis_too_short", short_codes)
        self.assertNotIn("report_severely_short", short_codes)
        self.assertNotIn("trend_analysis_too_short", adequate_codes)
        self.assertEqual(
            report_metrics(short)["trend_narrative_characters"],
            1490,
        )

    def test_decade_perspective_has_its_own_length_gate(self) -> None:
        def report(perspective_characters: int) -> str:
            return (
                "# Report\n\n"
                "## 1. エグゼクティブサマリー\n\n"
                + ("要" * 3000)
                + "\n\n## 2. トレンド分析\n\n"
                "### 10年間を通じた見方\n\n"
                + ("観" * perspective_characters)
                + "\n\n### 変わらなかったこと\n\n"
                + ("継" * 1300)
                + "\n\n## 根拠一覧\n"
            )

        exemplar = report(300)
        short = report(340)
        adequate = report(350)
        short_codes = {
            code
            for code, _ in essential_quality_issues(
                short,
                exemplar,
                language="ja",
            )
        }
        adequate_codes = {
            code
            for code, _ in essential_quality_issues(
                adequate,
                exemplar,
                language="ja",
            )
        }
        self.assertIn("trend_perspective_too_short", short_codes)
        self.assertNotIn("trend_perspective_too_short", adequate_codes)
        self.assertEqual(
            report_metrics(short)[
                "trend_perspective_narrative_characters"
            ],
            340,
        )


if __name__ == "__main__":
    unittest.main()
