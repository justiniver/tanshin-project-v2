from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from scripts.download_jpx_universe import _initial_status, _load_status
from scripts.download_jpx_tanshin import (
    Disclosure,
    JpxCoverageError,
    JpxDownloadError,
    _filenames,
    _is_primary_tanshin,
    _metadata_from_pdf,
    _non_primary_reason,
    _parse_explicit_annual_period_end,
    _parse_fiscal_month,
    _parse_fiscal_year,
    _preview_payload,
    download_company,
    select_required_disclosures,
)
from tests.helpers import workspace_temp_directory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class UniverseStatusTests(unittest.TestCase):
    def test_new_universe_code_is_added_without_resetting_existing_status(
        self,
    ) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as temp:
            status_path = temp / "universe_status.json"
            status = _initial_status(
                universe_path=Path("universe.txt"),
                universe_sha256="old-hash",
                codes=["1111", "3333"],
            )
            status["companies"][0]["status"] = "complete"
            status_path.write_text(
                json.dumps(status, ensure_ascii=False),
                encoding="utf-8",
            )

            loaded = _load_status(
                status_path,
                universe_path=Path("universe.txt"),
                universe_sha256="new-hash",
                codes=["1111", "2222", "3333"],
            )

        companies = loaded["companies"]
        self.assertEqual(
            [item["security_code"] for item in companies],
            ["1111", "2222", "3333"],
        )
        self.assertEqual(companies[0]["status"], "complete")
        self.assertEqual(companies[1]["status"], "pending")
        self.assertEqual(loaded["requested_count"], 3)
        self.assertEqual(loaded["universe_sha256"], "new-hash")
        self.assertEqual(
            loaded["universe_updates"][-1]["added_codes"],
            ["2222"],
        )


def _disclosure(
    fiscal_year: int,
    *,
    period: str = "FY",
    fiscal_month: int | None = 3,
    disclosure_date: str | None = None,
    disclosure_id: str | None = None,
    title: str | None = None,
    security_code: str = "9989",
) -> Disclosure:
    date = disclosure_date or f"{fiscal_year:04d}-05-10"
    identifier = disclosure_id or f"140120{date.replace('-', '')}{period}"
    return Disclosure(
        security_code=security_code,
        manager_code=f"{security_code}0",
        company_name="Test Company",
        disclosure_date=date,
        title=title or f"{fiscal_year}年{fiscal_month or 3}月期 決算短信（連結）",
        url=f"https://example.invalid/{identifier}.pdf",
        disclosure_id=identifier,
        fiscal_year=fiscal_year,
        fiscal_month=fiscal_month,
        period=period,
    )


def _ten_year_ends() -> list[Disclosure]:
    return [_disclosure(year) for year in range(2017, 2027)]


class _TextPage:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_text(self, mode: str) -> str:
        if mode != "text":
            raise AssertionError(f"Unexpected extraction mode: {mode}")
        return self.text


class _TextDocument:
    def __init__(self, text: str) -> None:
        self.page_count = 1
        self.page = _TextPage(text)

    def __enter__(self) -> "_TextDocument":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def __getitem__(self, index: int) -> _TextPage:
        if index != 0:
            raise IndexError(index)
        return self.page


def _metadata_from_text(
    text: str,
    *,
    period_hint: str | None = "FY",
) -> tuple[int, int | None, str]:
    with (
        patch(
            "scripts.download_jpx_tanshin._download_bytes",
            return_value=b"%PDF-fake",
        ),
        patch(
            "scripts.download_jpx_tanshin.fitz.open",
            return_value=_TextDocument(text),
        ),
    ):
        return _metadata_from_pdf(
            "https://example.invalid/metadata.pdf",
            timeout=1,
            period_hint=period_hint,
        )


class RequiredFilingSelectionTests(unittest.TestCase):
    def test_latest_year_end_is_deduplicated_into_ten_files(self) -> None:
        selection = select_required_disclosures(_ten_year_ends())

        self.assertEqual(selection.latest.fiscal_year, 2026)
        self.assertEqual(selection.latest.period, "FY")
        self.assertEqual(selection.trend_fiscal_years, tuple(range(2017, 2027)))
        self.assertEqual(len(selection.selected), 10)
        self.assertEqual(
            len({item.url for item in selection.selected}),
            len(selection.selected),
        )

    def test_latest_interim_is_added_to_ten_year_ends(self) -> None:
        latest_interim = _disclosure(
            2027,
            period="Q1",
            disclosure_date="2026-08-07",
            disclosure_id="140120260807000001",
            title="2027年3月期 第1四半期決算短信（連結）",
        )

        selection = select_required_disclosures(
            _ten_year_ends() + [latest_interim]
        )

        self.assertEqual(selection.latest, latest_interim)
        self.assertEqual(selection.trend_fiscal_years, tuple(range(2017, 2027)))
        self.assertEqual(len(selection.selected), 11)
        self.assertIn(latest_interim, selection.selected)

    def test_insufficient_year_end_history_has_specific_reason(self) -> None:
        with self.assertRaises(JpxCoverageError) as captured:
            select_required_disclosures(_ten_year_ends()[1:])

        self.assertEqual(
            captured.exception.reason_code,
            "insufficient_year_end_history",
        )
        self.assertEqual(
            captured.exception.details["available_fiscal_years"],
            list(range(2018, 2027)),
        )
        self.assertEqual(captured.exception.details["required_years"], 10)

    def test_explicit_minimum_accepts_eight_consecutive_years(self) -> None:
        filings = _ten_year_ends()[2:]

        selection = select_required_disclosures(
            filings,
            minimum_trend_years=8,
        )

        self.assertEqual(selection.trend_fiscal_years, tuple(range(2019, 2027)))
        self.assertEqual(selection.requested_trend_year_count, 10)
        self.assertEqual(selection.minimum_trend_year_count, 8)
        self.assertEqual(selection.selected_trend_year_count, 8)
        self.assertEqual(len(selection.selected), 8)

    def test_explicit_minimum_accepts_nine_and_keeps_all_nine_years(self) -> None:
        filings = _ten_year_ends()[1:]

        selection = select_required_disclosures(
            filings,
            minimum_trend_years=9,
        )

        self.assertEqual(selection.trend_fiscal_years, tuple(range(2018, 2027)))
        self.assertEqual(selection.requested_trend_year_count, 10)
        self.assertEqual(selection.minimum_trend_year_count, 9)
        self.assertEqual(selection.selected_trend_year_count, 9)
        self.assertEqual(len(selection.selected), 9)

    def test_explicit_eight_year_minimum_rejects_seven_years(self) -> None:
        with self.assertRaises(JpxCoverageError) as captured:
            select_required_disclosures(
                _ten_year_ends()[3:],
                minimum_trend_years=8,
            )

        self.assertEqual(
            captured.exception.reason_code,
            "insufficient_year_end_history",
        )
        self.assertEqual(captured.exception.details["required_years"], 8)
        self.assertEqual(captured.exception.details["requested_years"], 10)
        self.assertEqual(
            captured.exception.details["available_fiscal_years"],
            list(range(2020, 2027)),
        )

    def test_short_history_selection_metadata_is_explicit(self) -> None:
        selection = select_required_disclosures(
            _ten_year_ends()[2:],
            minimum_trend_years=8,
        )

        preview = _preview_payload(
            "9989",
            list(selection.selected),
            [],
            selection=selection,
        )

        self.assertEqual(
            preview["selection"],
            {
                "mode": "latest_plus_consecutive_year_ends",
                "latest_disclosure_id": selection.latest.disclosure_id,
                "trend_fiscal_years": list(range(2019, 2027)),
                "requested_trend_year_count": 10,
                "minimum_trend_year_count": 8,
                "selected_trend_year_count": 8,
                "full_requested_window_selected": False,
                "superseded_primary_disclosure_count": 0,
            },
        )

    def test_nonconsecutive_years_are_not_backfilled_with_older_history(self) -> None:
        filings = [
            _disclosure(year)
            for year in range(2016, 2027)
            if year != 2021
        ]

        with self.assertRaises(JpxCoverageError) as captured:
            select_required_disclosures(filings)

        self.assertEqual(
            captured.exception.reason_code,
            "nonconsecutive_year_end_history",
        )
        self.assertEqual(
            captured.exception.details["missing_fiscal_years"],
            [2021],
        )

    def test_later_revision_supersedes_same_period_without_duplicate(self) -> None:
        original = _disclosure(
            2022,
            disclosure_date="2022-05-10",
            disclosure_id="140120220510000001",
        )
        revision = _disclosure(
            2022,
            disclosure_date="2022-05-20",
            disclosure_id="140120220520000002",
        )
        filings = [
            item for item in _ten_year_ends() if item.fiscal_year != 2022
        ] + [original, revision]

        selection = select_required_disclosures(filings)

        selected_urls = {item.url for item in selection.selected}
        self.assertIn(revision.url, selected_urls)
        self.assertNotIn(original.url, selected_urls)
        self.assertEqual(len(selection.selected), 10)
        self.assertEqual(
            selection.superseded_primary_disclosures,
            (
                {
                    "disclosure_date": original.disclosure_date,
                    "title": original.title,
                    "url": original.url,
                    "reason": "superseded_primary_filing",
                    "superseded_by": revision.url,
                },
            ),
        )

    def test_true_transition_year_keeps_distinct_fiscal_months(self) -> None:
        transition = _disclosure(
            2020,
            fiscal_month=12,
            disclosure_date="2021-02-12",
            disclosure_id="140120210212000001",
            title="2020年12月期 決算短信（連結）",
        )

        selection = select_required_disclosures(_ten_year_ends() + [transition])
        transition_filings = [
            item for item in selection.selected if item.fiscal_year == 2020
        ]
        names = _filenames(list(selection.selected))

        self.assertEqual(len(transition_filings), 2)
        self.assertEqual(
            {item.fiscal_month for item in transition_filings},
            {3, 12},
        )
        self.assertEqual(len(selection.selected), 11)
        self.assertTrue(
            any("_2020-03_FY_" in names[item.url] for item in transition_filings)
        )
        self.assertTrue(
            any("_2020-12_FY_" in names[item.url] for item in transition_filings)
        )


class DisclosureFilteringTests(unittest.TestCase):
    def test_primary_tanshin_is_kept_and_related_material_is_classified(self) -> None:
        self.assertTrue(
            _is_primary_tanshin("2026年3月期 決算短信〔日本基準〕（連結）")
        )
        cases = {
            "（訂正）2026年3月期 決算短信": "correction_notice",
            "2026年3月期 決算短信 補足資料": "supplemental_material",
            "2026年3月期 決算短信 説明資料": "supplemental_material",
            "2026年3月期 決算短信についてのお知らせ": "related_notice",
            "（追加）「2025年3月期 決算短信」の一部追加について": (
                "addition_notice"
            ),
            "数値データ追加 2025年3月期 決算短信 XBRL": "addition_notice",
        }
        for title, expected_reason in cases.items():
            with self.subTest(title=title):
                self.assertFalse(_is_primary_tanshin(title))
                self.assertEqual(_non_primary_reason(title), expected_reason)

    def test_fiscal_year_parser_accepts_spacing_and_jpx_month_typo(self) -> None:
        self.assertEqual(
            _parse_fiscal_year("2026 年3月期 決算短信"),
            2026,
        )
        self.assertEqual(
            _parse_fiscal_year("2026月3月期 第1四半期決算短信"),
            2026,
        )
        self.assertEqual(
            _parse_fiscal_month("2026月3月期 第1四半期決算短信"),
            3,
        )


class PdfMetadataTests(unittest.TestCase):
    def test_annual_period_range_uses_its_end_year_and_month(self) -> None:
        text = (
            "2017年度 決算短信\n"
            "1．2017年度の連結業績"
            "（2017年4月1日～2018年3月31日）"
        )

        self.assertEqual(
            _metadata_from_text(text),
            (2018, 3, "FY"),
        )

    def test_ntt_style_filing_remains_distinct_from_2017_march_filing(
        self,
    ) -> None:
        fiscal_year, fiscal_month, period = _metadata_from_text(
            "2017年度 決算短信\n"
            "1．2017年度の連結業績"
            "（2017年4月1日～2018年3月31日）"
        )
        current = _disclosure(
            fiscal_year,
            fiscal_month=fiscal_month,
            period=period,
            disclosure_date="2018-05-10",
            disclosure_id="140120180510000001",
            title="2017年度 決算短信",
        )
        prior = _disclosure(
            2017,
            fiscal_month=3,
            disclosure_date="2017-05-10",
            disclosure_id="140120170510000001",
        )
        older = [_disclosure(year) for year in range(2009, 2017)]

        selection = select_required_disclosures(older + [prior, current])

        self.assertEqual(selection.trend_fiscal_years, tuple(range(2009, 2019)))
        self.assertIn(current, selection.selected)
        self.assertIn(prior, selection.selected)
        self.assertEqual(len(selection.selected), 10)
        self.assertEqual(
            {item.fiscal_year for item in selection.selected},
            set(range(2009, 2019)),
        )

    def test_japanese_era_annual_period_range_is_supported(self) -> None:
        self.assertEqual(
            _parse_explicit_annual_period_end(
                "平成29年度の連結業績"
                "（平成29年4月1日～平成30年3月31日）"
            ),
            (2018, 3),
        )

    def test_metadata_without_date_range_keeps_existing_title_fallback(
        self,
    ) -> None:
        self.assertEqual(
            _metadata_from_text("2026年3月期 決算短信"),
            (2026, 3, "FY"),
        )


class AtomicDownloadTests(unittest.TestCase):
    def test_filename_ambiguity_leaves_no_staging_directory(self) -> None:
        ambiguous = [
            _disclosure(
                2026,
                fiscal_month=None,
                disclosure_id="140120260510000001",
            ),
            _disclosure(
                2026,
                fiscal_month=3,
                disclosure_date="2026-05-11",
                disclosure_id="140120260511000002",
            ),
        ]

        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            output_root = root / "data"
            with patch(
                "scripts.download_jpx_tanshin._download_bytes",
                side_effect=AssertionError("download must not be attempted"),
            ):
                with self.assertRaises(JpxCoverageError):
                    download_company(
                        "9989",
                        ambiguous,
                        [],
                        output_root=output_root,
                        timeout=1,
                        delay_seconds=0,
                    )

            self.assertFalse((output_root / "9989").exists())
            self.assertEqual(list(output_root.glob(".jpx_9989_*")), [])

    def test_source_manifest_records_short_history_selection_counts(self) -> None:
        filings = _ten_year_ends()[2:]
        selection = select_required_disclosures(
            filings,
            minimum_trend_years=8,
        )
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Test Company 9989")
        pdf_bytes = document.tobytes()
        document.close()

        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            output_root = root / "data"
            with patch(
                "scripts.download_jpx_tanshin._download_bytes",
                return_value=pdf_bytes,
            ):
                destination = download_company(
                    "9989",
                    list(selection.selected),
                    [],
                    output_root=output_root,
                    timeout=1,
                    delay_seconds=0,
                    selection=selection,
                    available_disclosures=filings,
                )

            manifest = json.loads(
                (destination / "source_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                manifest["selection"]["mode"],
                "latest_plus_consecutive_year_ends",
            )
            self.assertEqual(
                manifest["selection"]["requested_trend_year_count"],
                10,
            )
            self.assertEqual(
                manifest["selection"]["minimum_trend_year_count"],
                8,
            )
            self.assertEqual(
                manifest["selection"]["selected_trend_year_count"],
                8,
            )
            self.assertFalse(
                manifest["selection"]["full_requested_window_selected"]
            )

    def test_existing_destination_is_never_overwritten_or_downloaded(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            output_root = root / "data"
            destination = output_root / "9989"
            destination.mkdir(parents=True)
            sentinel = destination / "keep.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            with patch(
                "scripts.download_jpx_tanshin._download_bytes",
                side_effect=AssertionError("download must not be attempted"),
            ):
                with self.assertRaisesRegex(
                    JpxDownloadError,
                    "refusing to overwrite",
                ):
                    download_company(
                        "9989",
                        [_disclosure(2026)],
                        [],
                        output_root=output_root,
                        timeout=1,
                        delay_seconds=0,
                    )

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(
                sorted(path.name for path in destination.iterdir()),
                ["keep.txt"],
            )

    def test_failed_download_leaves_no_partial_company_or_staging_directory(
        self,
    ) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            output_root = root / "data"
            with patch(
                "scripts.download_jpx_tanshin._download_bytes",
                side_effect=JpxDownloadError("simulated failure"),
            ):
                with self.assertRaisesRegex(JpxDownloadError, "simulated"):
                    download_company(
                        "9989",
                        [_disclosure(2026)],
                        [],
                        output_root=output_root,
                        timeout=1,
                        delay_seconds=0,
                    )

            self.assertFalse((output_root / "9989").exists())
            self.assertEqual(list(output_root.glob(".jpx_9989_*")), [])


if __name__ == "__main__":
    unittest.main()
