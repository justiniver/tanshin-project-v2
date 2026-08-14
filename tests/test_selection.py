from __future__ import annotations

import unittest
from pathlib import Path

from pypdf import PdfWriter

from tanshin_pipeline.selection import SelectionError, select_filings
from tests.helpers import workspace_temp_directory


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class SelectionTests(unittest.TestCase):
    def test_all_company_directories(self) -> None:
        expected = {
            "1808": [
                "01_2027_Q1_tanshin.pdf",
                "02_2026_FY_tanshin.pdf",
                "06_2025_FY_tanshin.pdf",
                "10_2024-05-10_tanshin.pdf",
                "14_2023-05-11_tanshin.pdf",
                "18_2022-05-12_tanshin.pdf",
                "22_2021-05-13_tanshin.pdf",
                "26_2020-05-14_tanshin.pdf",
                "30_2019-05-10_tanshin.pdf",
                "34_2018-05-11_tanshin.pdf",
                "38_2017-05-12_tanshin.pdf",
            ],
            "3923": [
                "01_2026_FY_rakus_tanshin.pdf",
                "05_2025_FY_rakus_tanshin.pdf",
                "09_2024_FY_rakus_tanshin.pdf",
                "13_2023_FY_rakus_tanshin.pdf",
                "17_2022_FY_rakus_tanshin.pdf",
                "21_2021_FY_rakus_tanshin.pdf",
                "25_2020_FY_rakus_tanshin.pdf",
                "29_2019_FY_rakus_tanshin.pdf",
                "33_2018_FY_rakus_tanshin.pdf",
                "37_2017_FY_rakus_tanshin.pdf",
                "41_2016_Q4_FY_rakus_tanshin.pdf",
            ],
            "6361": [
                "01_2026_Q1_ebara_tanshin.pdf",
                "02_2025_FY_ebara_tanshin.pdf",
                "06_2024_FY_ebara_tanshin.pdf",
                "10_2023_FY_ebara_tanshin.pdf",
                "14_2022_FY_ebara_tanshin.pdf",
                "18_2021_FY_ebara_tanshin.pdf",
                "22_2020_FY_ebara_tanshin.pdf",
                "26_2019_FY_ebara_tanshin.pdf",
                "30_2018_FY_ebara_tanshin.pdf",
                "34_2017-12_FY_ebara_tanshin.pdf",
                "37_2017-03_FY_ebara_tanshin.pdf",
                "41_2016-03_FY_ebara_tanshin.pdf",
            ],
        }
        for code, filenames in expected.items():
            with self.subTest(code=code):
                manifest = select_filings(REPOSITORY_ROOT, code)
                self.assertEqual(
                    [item.filename for item in manifest.selected_files],
                    filenames,
                )

    def test_latest_year_end_is_deduplicated(self) -> None:
        manifest = select_filings(REPOSITORY_ROOT, "3923")
        latest = [
            item
            for item in manifest.selected_files
            if item.filename == manifest.latest_filename
        ]
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0].roles, ["latest", "trend_year_end"])

    def test_legacy_year_ends_are_inferred_from_cadence(self) -> None:
        manifest = select_filings(REPOSITORY_ROOT, "1808")
        inferred = [item for item in manifest.selected_files if item.year_end_inferred]
        self.assertEqual(len(inferred), 8)
        self.assertTrue(all("cadence" in item.classification_reason for item in inferred))

    def _write_pdf(self, path: Path) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with path.open("wb") as stream:
            writer.write(stream)

    def _write_year_end_history(
        self,
        company: Path,
        *,
        year_count: int,
        latest_year: int = 2026,
    ) -> None:
        for ordinal, year in enumerate(
            range(latest_year, latest_year - year_count, -1),
            start=1,
        ):
            self._write_pdf(company / f"{ordinal:02d}_{year}_FY_test.pdf")

    def test_eight_and_nine_year_histories_are_accepted(self) -> None:
        for year_count in (8, 9):
            with self.subTest(year_count=year_count):
                with workspace_temp_directory(REPOSITORY_ROOT) as root:
                    company = root / "data" / "9999"
                    company.mkdir(parents=True)
                    self._write_year_end_history(
                        company,
                        year_count=year_count,
                    )

                    manifest = select_filings(root, "9999")

                    self.assertEqual(len(manifest.window.unique_years), year_count)
                    self.assertEqual(
                        manifest.window.unique_years,
                        list(range(2027 - year_count, 2027)),
                    )
                    self.assertTrue(
                        any(
                            f"contains {year_count} distinct" in note
                            for note in manifest.selection_notes
                        )
                    )

    def test_seven_year_history_is_rejected(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            company = root / "data" / "9999"
            company.mkdir(parents=True)
            self._write_year_end_history(company, year_count=7)

            with self.assertRaisesRegex(
                SelectionError,
                "identified 7 distinct fiscal years; at least 8 are required",
            ):
                select_filings(root, "9999")

    def test_missing_year_end_coverage_is_rejected(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            company = root / "data" / "9999"
            company.mkdir(parents=True)
            years = [2026, 2025, 2024, 2022, 2021, 2020, 2019, 2018, 2017, 2016]
            for index, year in enumerate(years, start=1):
                self._write_pdf(company / f"{index:02d}_{year}_FY_test.pdf")
            with self.assertRaisesRegex(SelectionError, "incomplete"):
                select_filings(root, "9999")

    def test_ambiguous_date_only_cadence_is_rejected(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            company = root / "data" / "9999"
            company.mkdir(parents=True)
            names = [
                "01_2026_FY_test.pdf",
                "05_2025_FY_test.pdf",
                "10_2024_FY_test.pdf",
                "14_2023_FY_test.pdf",
                "18_2022-05-01_test.pdf",
            ]
            for name in names:
                self._write_pdf(company / name)
            with self.assertRaisesRegex(SelectionError, "ambiguous"):
                select_filings(root, "9999")

    def test_alphanumeric_security_code_is_supported(self) -> None:
        with workspace_temp_directory(REPOSITORY_ROOT) as root:
            company = root / "data" / "141A"
            company.mkdir(parents=True)
            for ordinal, year in enumerate(range(2026, 2016, -1), start=1):
                self._write_pdf(
                    company / f"{ordinal:02d}_{year}_FY_test.pdf"
                )

            manifest = select_filings(root, "141A")

            self.assertEqual(manifest.security_code, "141A")
            self.assertEqual(manifest.latest_filename, "01_2026_FY_test.pdf")
            self.assertEqual(len(manifest.selected_files), 10)


if __name__ == "__main__":
    unittest.main()
