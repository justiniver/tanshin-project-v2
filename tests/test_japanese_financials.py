from __future__ import annotations

import unittest

from tanshin_pipeline.japanese_financials import (
    _normalize_statement_amounts,
    _repair_malformed_surfaces,
    normalize_japanese_financials,
)
from tanshin_pipeline.normalization import _source_numeric_surface
from tanshin_pipeline.schemas import (
    AnalysisClaim,
    CompanyIdentity,
    EvidenceRecord,
    JapaneseAnalysis,
    SectionKey,
    StatementType,
)


class JapaneseFinancialNormalizationTests(unittest.TestCase):
    def test_repairs_concatenated_and_ungrouped_million_yen_values(self) -> None:
        normalized, changes = _repair_malformed_surfaces(
            "売上高1兆2,73136百万円、営業利益98743百万円、"
            "営業CF3916百万円"
        )
        self.assertEqual(
            normalized,
            "売上高1,273,136百万円、営業利益98,743百万円、"
            "営業CF3,916百万円",
        )
        self.assertEqual(len(changes), 3)

    def test_normalization_does_not_change_unrelated_japanese_punctuation(self) -> None:
        normalized, _ = _repair_malformed_surfaces(
            "利益は98743百万円（前期比16.6％増）：順調です。"
        )
        self.assertEqual(
            normalized,
            "利益は98,743百万円（前期比16.6％増）：順調です。",
        )

    def test_prefers_uniquely_matching_narrative_units(self) -> None:
        normalized, changes = _normalize_statement_amounts(
            "売上高1,273,136百万円、営業利益98,743百万円、"
            "経常利益94,051百万円、純利益54,839百万円",
            [
                "売上高は1兆2,731億円、営業利益は987億円、"
                "経常利益は941億円、純利益は548億円となりました。"
            ],
        )
        self.assertEqual(
            normalized,
            "売上高1兆2,731億円、営業利益987億円、"
            "経常利益941億円、純利益548億円",
        )
        self.assertTrue(
            all(change["reason"] == "source_narrative_unit" for change in changes)
        )

    def test_exact_forecast_values_convert_without_rounding(self) -> None:
        normalized, _ = _normalize_statement_amounts(
            "売上高1,380,000百万円、営業利益110,000百万円、"
            "純利益66,000百万円",
            [],
        )
        self.assertEqual(
            normalized,
            "売上高1兆3,800億円、営業利益1,100億円、純利益660億円",
        )

    def test_ambiguous_narrative_mapping_is_not_guessed(self) -> None:
        normalized, changes = _normalize_statement_amounts(
            "営業利益98,743百万円",
            ["営業利益は987億円。別指標は988億円。"],
        )
        self.assertEqual(normalized, "営業利益98,743百万円")
        self.assertEqual(changes, [])

    def test_equivalent_units_resolve_to_source_surface(self) -> None:
        self.assertEqual(
            _source_numeric_surface(
                "通期売上高は1,380,000百万円を予想しています。",
                "1兆3,800億円",
            ),
            "1,380,000百万円",
        )

    def test_cited_page_narrative_can_supply_readable_unit(self) -> None:
        analysis = JapaneseAnalysis(
            schema_version="test",
            identity=CompanyIdentity(
                security_code="0000",
                company_name_ja="テスト株式会社",
                company_name_en="Test Co.",
                latest_filename="latest.pdf",
                latest_period_ja="2026年3月期",
                latest_period_en="FY2026",
            ),
            claims=[
                AnalysisClaim(
                    claim_id="claim_01",
                    section=SectionKey.LATEST_KEY_TAKEAWAY,
                    order=1,
                    headline_ja="業績",
                    body_ja="売上高は1兆2,73136百万円となりました。",
                    evidence_ids=["latest.pdf:s0001"],
                    statement_type=StatementType.ACTUAL,
                )
            ],
            evidence=[
                EvidenceRecord(
                    evidence_id="latest.pdf:s0001",
                    source_filename="latest.pdf",
                    pdf_page=4,
                    exact_quote_ja="売上高 1,273,136百万円",
                    period_label_ja="2026年3月期",
                    period_label_en="FY2026",
                    statement_type=StatementType.ACTUAL,
                    source_section="経営成績等の概況",
                )
            ],
        )

        class FakePageIndex:
            def sentences(
                self,
                filename: str,
                page: int,
                *,
                include_fallback: bool,
            ) -> list[str]:
                self.last_call = (filename, page, include_fallback)
                return ["売上高は1兆2,731億円となりました。"]

        page_index = FakePageIndex()
        changes = normalize_japanese_financials(analysis, page_index)
        self.assertEqual(
            analysis.claims[0].body_ja,
            "売上高は1兆2,731億円となりました。",
        )
        self.assertEqual(page_index.last_call, ("latest.pdf", 4, True))
        self.assertTrue(
            any(change["reason"] == "source_narrative_unit" for change in changes)
        )


if __name__ == "__main__":
    unittest.main()
