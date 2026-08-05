from __future__ import annotations

import unittest
from types import SimpleNamespace

from scripts.download_jpx_universe import (
    _coverage_from_discovery,
    _filter_by_recorded_available_year_counts,
)


class RecordedCoverageFilterTests(unittest.TestCase):
    def test_filter_uses_distinct_fiscal_years_not_raw_filing_count(self) -> None:
        codes = ["C8", "C9", "C10", "NONE"]
        by_code = {
            "C8": {
                "coverage": {
                    "available_fiscal_years": [
                        2019,
                        2020,
                        2021,
                        2022,
                        2023,
                        2024,
                        2025,
                        2026,
                    ],
                    "available_year_end_count": 11,
                }
            },
            "C9": {
                "coverage": {
                    "available_fiscal_years": list(range(2018, 2027)),
                    "available_year_end_count": 10,
                }
            },
            "C10": {
                "coverage": {
                    "available_fiscal_years": list(range(2017, 2027)),
                    "available_year_end_count": 10,
                }
            },
            "NONE": {"coverage": {}},
        }

        selected = _filter_by_recorded_available_year_counts(
            codes,
            by_code,
            {8, 9},
        )

        self.assertEqual(selected, ["C8", "C9"])

    def test_duplicate_recorded_years_do_not_inflate_count(self) -> None:
        codes = ["DUP"]
        by_code = {
            "DUP": {
                "coverage": {
                    "available_fiscal_years": [
                        2019,
                        2019,
                        2020,
                        2021,
                        2022,
                        2023,
                        2024,
                        2025,
                        2026,
                    ]
                }
            }
        }

        selected = _filter_by_recorded_available_year_counts(
            codes,
            by_code,
            {8},
        )

        self.assertEqual(selected, ["DUP"])


class CoverageMetadataTests(unittest.TestCase):
    def test_short_window_records_target_minimum_and_selected_count(self) -> None:
        disclosures = [
            SimpleNamespace(period="FY", fiscal_year=year)
            for year in range(2019, 2027)
        ]
        latest = SimpleNamespace(
            disclosure_id="latest",
            disclosure_date="2026-05-01",
            fiscal_year=2026,
            period="FY",
        )
        selection = SimpleNamespace(
            latest=latest,
            trend_fiscal_years=tuple(range(2019, 2027)),
            requested_trend_year_count=10,
            minimum_trend_year_count=8,
        )

        coverage = _coverage_from_discovery(
            disclosures,
            selection,
            requested_trend_years=10,
            minimum_trend_years=8,
        )

        self.assertEqual(coverage["available_distinct_fiscal_year_count"], 8)
        self.assertEqual(coverage["selected_trend_year_count"], 8)
        self.assertEqual(coverage["requested_trend_year_count"], 10)
        self.assertEqual(coverage["minimum_trend_year_count"], 8)
        self.assertTrue(coverage["minimum_year_requirement_met"])
        self.assertFalse(coverage["ten_year_requirement_met"])
        self.assertTrue(coverage["short_window_accepted"])

    def test_full_window_is_not_marked_as_short(self) -> None:
        disclosures = [
            SimpleNamespace(period="FY", fiscal_year=year)
            for year in range(2017, 2027)
        ]
        latest = SimpleNamespace(
            disclosure_id="latest",
            disclosure_date="2026-05-01",
            fiscal_year=2026,
            period="FY",
        )
        selection = SimpleNamespace(
            latest=latest,
            trend_fiscal_years=tuple(range(2017, 2027)),
            requested_trend_year_count=10,
            minimum_trend_year_count=8,
        )

        coverage = _coverage_from_discovery(
            disclosures,
            selection,
            requested_trend_years=10,
            minimum_trend_years=8,
        )

        self.assertTrue(coverage["ten_year_requirement_met"])
        self.assertFalse(coverage["short_window_accepted"])


if __name__ == "__main__":
    unittest.main()
