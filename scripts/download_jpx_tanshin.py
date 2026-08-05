"""Discover and download a company's rolling JPX Tanshin PDF history.

The script uses the public JPX Listed Company Search. Discovery is always
performed first. PDFs are written only when --download is supplied, and the
company directory is published atomically after every file validates.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import http.cookiejar
import json
import re
import shutil
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import fitz


SEARCH_URL = "https://www2.jpx.co.jp/tseHpFront/StockSearch.do"
JPX_ORIGIN = "https://www2.jpx.co.jp"
USER_AGENT = "tanshin-project-data-acquisition/1.0"


class JpxDownloadError(RuntimeError):
    """Raised when JPX discovery or PDF validation is incomplete."""


class JpxCoverageError(JpxDownloadError):
    """Raised when a company does not have a usable requested filing window."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details = details or {}


class JpxNoPrimaryTanshinError(JpxDownloadError):
    """Raised when JPX identifies a company but has no primary Tanshin PDF."""

    def __init__(
        self,
        security_code: str,
        company_name: str,
        manager_code: str,
    ) -> None:
        super().__init__(
            f"JPX returned no primary Tanshin PDFs for {security_code}."
        )
        self.security_code = security_code
        self.company_name = company_name
        self.manager_code = manager_code


@dataclass(frozen=True)
class Disclosure:
    security_code: str
    manager_code: str
    company_name: str
    disclosure_date: str
    title: str
    url: str
    disclosure_id: str
    fiscal_year: int
    fiscal_month: int | None
    period: str
    metadata_source: str = "title"


@dataclass(frozen=True)
class RequiredFilingSelection:
    selected: tuple[Disclosure, ...]
    latest: Disclosure
    trend_fiscal_years: tuple[int, ...]
    superseded_primary_disclosures: tuple[dict[str, str], ...]
    requested_trend_year_count: int
    minimum_trend_year_count: int

    @property
    def selected_trend_year_count(self) -> int:
        return len(self.trend_fiscal_years)


def _attributes(tag: str) -> dict[str, str]:
    return {
        name.lower(): html.unescape(value)
        for name, _, value in re.findall(
            r"""([:\w.\[\]-]+)\s*=\s*(["'])(.*?)\2""",
            tag,
            flags=re.IGNORECASE | re.DOTALL,
        )
    }


def _plain_text(fragment: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _decode_response(response: urllib.response.addinfourl, content: bytes) -> str:
    charset = response.headers.get_content_charset()
    for encoding in (charset, "utf-8", "cp932"):
        if not encoding:
            continue
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise JpxDownloadError("JPX returned HTML with an unsupported character encoding.")


def _open_text(
    opener: urllib.request.OpenerDirector,
    request: urllib.request.Request,
    timeout: float,
) -> tuple[str, str]:
    with opener.open(request, timeout=timeout) as response:
        content = response.read()
        return _decode_response(response, content), response.geturl()


def _search_form_payload(
    page: str,
    security_code: str,
) -> tuple[str, dict[str, str], str]:
    form_match = re.search(
        r"""<form\b[^>]*name=["']JJK010030Form["'][^>]*>.*?</form>""",
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not form_match:
        raise JpxDownloadError(f"JPX search form was not found for {security_code}.")

    form = form_match.group(0)
    opening_tag = re.match(r"<form\b[^>]*>", form, flags=re.IGNORECASE | re.DOTALL)
    if not opening_tag:
        raise JpxDownloadError(f"JPX search form action was not found for {security_code}.")
    action = _attributes(opening_tag.group(0)).get("action")
    if not action:
        raise JpxDownloadError(f"JPX search form action was empty for {security_code}.")

    payload: dict[str, str] = {}
    for input_tag in re.findall(r"<input\b[^>]*>", form, flags=re.IGNORECASE | re.DOTALL):
        attrs = _attributes(input_tag)
        name = attrs.get("name")
        if name and attrs.get("type", "").lower() == "hidden":
            payload[name] = attrs.get("value", "")
    for inactive_action in ("Transition", "Show", "Return", "Sort"):
        payload.pop(inactive_action, None)

    manager_codes = re.findall(
        r"""name=["']ccJjCrpSelKekkLst_st\[\d+\]\.eqMgrCd["']\s+value=["']([0-9A-Z]{5,7})["']""",
        form,
        flags=re.IGNORECASE,
    )
    exact_codes = [
        code.upper()
        for code in manager_codes
        if code.upper().startswith(security_code.upper())
    ]
    if len(set(exact_codes)) != 1:
        raise JpxDownloadError(
            "JPX did not return one unique listed-company result for "
            f"{security_code}: {sorted(set(exact_codes))}"
        )
    manager_code = exact_codes[0]

    navigation = re.search(
        rf"""gotoBaseJh\(["']{re.escape(manager_code)}["']\s*,\s*["']([^"']+)["']\)""",
        form,
        flags=re.IGNORECASE,
    )
    if not navigation:
        raise JpxDownloadError(
            f"JPX company-detail navigation was not found for {manager_code}."
        )

    payload["BaseJh"] = "BaseJh"
    payload["mgrCd"] = manager_code
    payload["jjHisiFlg"] = navigation.group(1)
    return action, payload, manager_code


def _parse_fiscal_year(title: str) -> int:
    normalized = unicodedata.normalize("NFKC", title)
    western = re.search(r"(20\d{2})\s*年", normalized)
    if western:
        return int(western.group(1))
    # A small number of JPX disclosure titles contain a literal typo such as
    # "2026月3月期". The two month tokens make the intended fiscal-year field
    # unambiguous.
    western_month_typo = re.search(
        r"(20\d{2})\s*月\s*\d{1,2}\s*月期",
        normalized,
    )
    if western_month_typo:
        return int(western_month_typo.group(1))

    heisei = re.search(r"平成\s*(\d{1,2})\s*年", normalized)
    if heisei:
        return 1988 + int(heisei.group(1))

    reiwa = re.search(r"令和\s*(元|\d{1,2})\s*年", normalized)
    if reiwa:
        year_number = 1 if reiwa.group(1) == "元" else int(reiwa.group(1))
        return 2018 + year_number

    raise JpxDownloadError(f"Could not parse a fiscal year from title: {title}")


def _parse_fiscal_month(title: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", title)
    match = re.search(
        r"(?:年|20\d{2}\s*月)\s*(\d{1,2})\s*月期",
        normalized,
    )
    if not match:
        return None
    month = int(match.group(1))
    return month if 1 <= month <= 12 else None


def _parse_period(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title)
    for quarter in (1, 2, 3):
        if re.search(rf"第\s*{quarter}\s*四半期", normalized):
            return f"Q{quarter}"
    if "中間期" in normalized:
        return "Q2"
    return "FY"


def _is_primary_tanshin(title: str) -> bool:
    normalized = unicodedata.normalize("NFKC", title)
    if "決算短信" not in normalized or "訂正" in normalized:
        return False
    if "お知らせ" in normalized:
        return False
    if normalized.startswith(("（追加）", "(追加)", "数値データ追加")):
        return False
    if "XBRL" in normalized.upper() and "追加" in normalized:
        return False

    # Accompanying investor materials sometimes retain "決算短信" in their
    # title. They are useful supplements, but they are not the primary Tanshin
    # PDF that this downloader is intended to reproduce.
    supplemental_markers = ("補足資料", "説明資料", "参考資料")
    return not any(marker in normalized for marker in supplemental_markers)


def _non_primary_reason(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title)
    if "訂正" in normalized:
        return "correction_notice"
    if "お知らせ" in normalized:
        return "related_notice"
    if (
        normalized.startswith(("（追加）", "(追加)", "数値データ追加"))
        or ("XBRL" in normalized.upper() and "追加" in normalized)
    ):
        return "addition_notice"
    if any(
        marker in normalized
        for marker in ("補足資料", "説明資料", "参考資料")
    ):
        return "supplemental_material"
    return "other_non_primary_disclosure"


def _statement_scope(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title)
    if "非連結" in normalized or "個別" in normalized:
        return "standalone"
    if "連結" in normalized:
        return "consolidated"
    return "unspecified"


def _japanese_calendar_year(value: str) -> int:
    match = re.fullmatch(r"(令和|平成)\s*(元|\d{1,2})", value)
    if not match:
        return int(value)
    era, year_text = match.groups()
    era_year = 1 if year_text == "元" else int(year_text)
    return (2018 if era == "令和" else 1988) + era_year


def _parse_explicit_annual_period_end(
    text: str,
) -> tuple[int, int] | None:
    """Return the end year/month from an explicit annual date range.

    Some issuers label results by the fiscal year in which the period starts,
    such as ``2017年度``, while the corresponding Tanshin covers the fiscal
    year ending March 2018. The full performance-period range printed on the
    first page is authoritative for that ambiguity.
    """

    normalized = unicodedata.normalize("NFKC", text)
    year_token = r"(?:20\d{2}|(?:令和|平成)\s*(?:元|\d{1,2}))"
    date_range = re.compile(
        rf"(?P<start_year>{year_token})\s*年\s*"
        r"(?P<start_month>\d{1,2})\s*月\s*"
        r"(?P<start_day>\d{1,2})\s*日\s*"
        r"(?:[~～〜－ー-]|から)\s*"
        rf"(?P<end_year>{year_token})\s*年\s*"
        r"(?P<end_month>\d{1,2})\s*月\s*"
        r"(?P<end_day>\d{1,2})\s*日"
    )
    for match in date_range.finditer(normalized):
        try:
            start = datetime(
                _japanese_calendar_year(match.group("start_year")),
                int(match.group("start_month")),
                int(match.group("start_day")),
            )
            end = datetime(
                _japanese_calendar_year(match.group("end_year")),
                int(match.group("end_month")),
                int(match.group("end_day")),
            )
        except ValueError:
            continue

        # This deliberately excludes quarter-to-date ranges while permitting
        # ordinary annual periods and reasonable fiscal-year transitions.
        duration_days = (end - start).days
        if 180 <= duration_days <= 550:
            return end.year, end.month
    return None


def _metadata_from_pdf(
    url: str,
    *,
    timeout: float,
    period_hint: str | None = None,
) -> tuple[int, int | None, str]:
    content = _download_bytes(
        url,
        timeout,
        attempts=2,
        retry_delay_seconds=1.0,
    )
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            page_texts = [
                document[index].get_text("text")
                for index in range(min(3, document.page_count))
            ]
            text = "\n".join(page_texts)
    except fitz.FileDataError as exc:
        raise JpxDownloadError(
            f"Could not inspect PDF metadata fallback: {url}"
        ) from exc
    period = period_hint or _parse_period(text)
    explicit_period_end = (
        _parse_explicit_annual_period_end(page_texts[0])
        if period == "FY" and page_texts
        else None
    )
    if explicit_period_end is not None:
        fiscal_year, fiscal_month = explicit_period_end
    else:
        fiscal_year = _parse_fiscal_year(text)
        fiscal_month = _parse_fiscal_month(text)
    return fiscal_year, fiscal_month, period


def _deduplicate_primary_disclosures(
    disclosures: list[Disclosure],
) -> tuple[list[Disclosure], list[dict[str, str]]]:
    grouped: dict[tuple[int, int | None, str], list[Disclosure]] = {}
    for disclosure in disclosures:
        key = (
            disclosure.fiscal_year,
            disclosure.fiscal_month,
            disclosure.period,
        )
        grouped.setdefault(key, []).append(disclosure)

    selected: list[Disclosure] = []
    superseded: list[dict[str, str]] = []
    scope_rank = {"consolidated": 2, "unspecified": 1, "standalone": 0}
    for group in grouped.values():
        available_scopes = {_statement_scope(item.title) for item in group}
        preferred_scope = max(available_scopes, key=lambda item: scope_rank[item])
        preferred = [
            item for item in group if _statement_scope(item.title) == preferred_scope
        ]
        preferred.sort(
            key=lambda item: (item.disclosure_date, item.disclosure_id),
            reverse=True,
        )
        winner = preferred[0]
        selected.append(winner)
        for item in group:
            if item.url == winner.url:
                continue
            reason = (
                "alternate_statement_scope"
                if _statement_scope(item.title) != preferred_scope
                else "superseded_primary_filing"
            )
            superseded.append(
                {
                    "disclosure_date": item.disclosure_date,
                    "title": item.title,
                    "url": item.url,
                    "reason": reason,
                    "superseded_by": winner.url,
                }
            )

    selected.sort(
        key=lambda item: (item.disclosure_date, item.disclosure_id),
        reverse=True,
    )
    superseded.sort(
        key=lambda item: (item["disclosure_date"], item["url"]),
        reverse=True,
    )
    return selected, superseded


def select_required_disclosures(
    disclosures: list[Disclosure],
    *,
    trend_years: int = 10,
    minimum_trend_years: int | None = None,
) -> RequiredFilingSelection:
    """Select the latest filing plus up to ``trend_years`` consecutive FYs.

    By default, the complete requested window is required. Callers may
    explicitly accept a shorter history by setting ``minimum_trend_years``;
    the selector still keeps as many of the latest fiscal years as are
    available, up to ``trend_years``.
    """

    if trend_years < 1:
        raise ValueError("trend_years must be positive.")
    minimum_years = (
        trend_years
        if minimum_trend_years is None
        else minimum_trend_years
    )
    if minimum_years < 1:
        raise ValueError("minimum_trend_years must be positive.")
    if minimum_years > trend_years:
        raise ValueError(
            "minimum_trend_years cannot exceed trend_years."
        )
    deduplicated, superseded = _deduplicate_primary_disclosures(disclosures)
    if not deduplicated:
        raise JpxCoverageError(
            "no_primary_tanshin",
            "No primary Tanshin filings remained after revision deduplication.",
        )

    latest = deduplicated[0]
    year_ends = [item for item in deduplicated if item.period == "FY"]
    unique_years_desc = sorted(
        {item.fiscal_year for item in year_ends},
        reverse=True,
    )
    if len(unique_years_desc) < minimum_years:
        raise JpxCoverageError(
            "insufficient_year_end_history",
            (
                f"Only {len(unique_years_desc)} distinct year-end fiscal years "
                f"were available; at least {minimum_years} are required for "
                f"the requested window of up to {trend_years} years."
            ),
            details={
                "available_fiscal_years": sorted(unique_years_desc),
                "required_years": minimum_years,
                "requested_years": trend_years,
                "minimum_required_years": minimum_years,
            },
        )

    selected_year_count = min(trend_years, len(unique_years_desc))
    trend_fiscal_years = tuple(
        sorted(unique_years_desc[:selected_year_count])
    )
    expected_years = tuple(
        range(trend_fiscal_years[0], trend_fiscal_years[-1] + 1)
    )
    if trend_fiscal_years != expected_years:
        missing = sorted(set(expected_years) - set(trend_fiscal_years))
        raise JpxCoverageError(
            "nonconsecutive_year_end_history",
            (
                "The selected most recent year-end fiscal years are not "
                "consecutive; "
                f"missing: {missing}."
            ),
            details={
                "available_fiscal_years": sorted(unique_years_desc),
                "selected_fiscal_years": list(trend_fiscal_years),
                "missing_fiscal_years": missing,
                "requested_years": trend_years,
                "minimum_required_years": minimum_years,
            },
        )

    selected_by_url = {
        item.url: item
        for item in year_ends
        if item.fiscal_year in trend_fiscal_years
    }
    selected_by_url[latest.url] = latest
    selected = tuple(
        sorted(
            selected_by_url.values(),
            key=lambda item: (item.disclosure_date, item.disclosure_id),
            reverse=True,
        )
    )
    return RequiredFilingSelection(
        selected=selected,
        latest=latest,
        trend_fiscal_years=trend_fiscal_years,
        superseded_primary_disclosures=tuple(superseded),
        requested_trend_year_count=trend_years,
        minimum_trend_year_count=minimum_years,
    )


def discover_tanshin(
    security_code: str,
    *,
    timeout: float = 30.0,
) -> tuple[list[Disclosure], list[dict[str, str]]]:
    code = security_code.strip().upper()
    if not re.fullmatch(r"[0-9A-Z]{4,6}", code):
        raise JpxDownloadError(
            f"Security code must contain 4-6 uppercase letters or digits: {code!r}"
        )

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookie_jar)
    )
    opener.addheaders = [("User-Agent", USER_AGENT)]

    search_query = urllib.parse.urlencode({"topSearchStr": code})
    search_request = urllib.request.Request(f"{SEARCH_URL}?{search_query}")
    search_page, search_url = _open_text(opener, search_request, timeout)
    action, payload, manager_code = _search_form_payload(search_page, code)

    # This legacy Struts form expects Shift-JIS/CP932 form values.
    post_body = urllib.parse.urlencode(payload, encoding="cp932").encode("ascii")
    detail_request = urllib.request.Request(
        urllib.parse.urljoin(search_url, action),
        data=post_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    detail_page, _ = _open_text(opener, detail_request, timeout)

    company_match = re.search(
        r"""<div\b[^>]*class=["'][^"']*boxOptListed05[^"']*["'][^>]*>.*?<h3[^>]*>(.*?)</h3>""",
        detail_page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not company_match:
        raise JpxDownloadError(f"JPX company name was not found for {code}.")
    company_name = _plain_text(company_match.group(1))

    primary: dict[str, Disclosure] = {}
    excluded: dict[str, dict[str, str]] = {}
    for row in re.findall(
        r"<tr\b[^>]*>.*?</tr>",
        detail_page,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        if "決算短信" not in row:
            continue
        anchor = re.search(
            r"""<a\b[^>]*href=["']([^"']*/disc/([0-9A-Z]{5,7})/(14\d+\.pdf))["'][^>]*>(.*?)</a>""",
            row,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not anchor:
            continue
        if anchor.group(2).upper() != manager_code:
            continue
        date_match = re.search(r"(20\d{2}/\d{2}/\d{2})", row)
        if not date_match:
            continue

        title = _plain_text(anchor.group(4))
        url = urllib.parse.urljoin(JPX_ORIGIN, anchor.group(1))
        if not _is_primary_tanshin(title):
            excluded[url] = {
                "disclosure_date": date_match.group(1).replace("/", "-"),
                "title": title,
                "url": url,
                "reason": _non_primary_reason(title),
            }
            continue

        metadata_source = "title"
        try:
            fiscal_year = _parse_fiscal_year(title)
            fiscal_month = _parse_fiscal_month(title)
            period = _parse_period(title)
        except JpxDownloadError:
            period_hint = _parse_period(title)
            fiscal_year, fiscal_month, period = _metadata_from_pdf(
                url,
                timeout=timeout,
                period_hint=period_hint,
            )
            metadata_source = "pdf_first_pages"

        disclosure = Disclosure(
            security_code=code,
            manager_code=manager_code,
            company_name=company_name,
            disclosure_date=date_match.group(1).replace("/", "-"),
            title=title,
            url=url,
            disclosure_id=Path(anchor.group(3)).stem,
            fiscal_year=fiscal_year,
            fiscal_month=fiscal_month,
            period=period,
            metadata_source=metadata_source,
        )
        primary[url] = disclosure

    primary_groups: dict[tuple[int, int | None, str], list[Disclosure]] = {}
    for disclosure in primary.values():
        key = (
            disclosure.fiscal_year,
            disclosure.fiscal_month,
            disclosure.period,
        )
        primary_groups.setdefault(key, []).append(disclosure)

    metadata_recheck_urls: set[str] = {
        item.url
        for item in primary.values()
        if "年度" in unicodedata.normalize("NFKC", item.title)
    }
    for group in primary_groups.values():
        if len(group) < 2:
            continue
        dates = sorted(
            datetime.fromisoformat(item.disclosure_date)
            for item in group
        )
        if (dates[-1] - dates[0]).days >= 180:
            metadata_recheck_urls.update(item.url for item in group)

    for url in metadata_recheck_urls:
        item = primary[url]
        fiscal_year, fiscal_month, period = _metadata_from_pdf(
            item.url,
            timeout=timeout,
            period_hint=item.period,
        )
        primary[url] = replace(
            item,
            fiscal_year=fiscal_year,
            fiscal_month=fiscal_month,
            period=period,
            metadata_source="pdf_first_pages_verified",
        )

    disclosures = sorted(
        primary.values(),
        key=lambda item: (item.disclosure_date, item.disclosure_id),
        reverse=True,
    )
    if not disclosures:
        raise JpxNoPrimaryTanshinError(
            code,
            company_name,
            manager_code,
        )
    return disclosures, sorted(
        excluded.values(),
        key=lambda item: item["disclosure_date"],
        reverse=True,
    )


def discover_tanshin_with_retries(
    security_code: str,
    *,
    timeout: float = 30.0,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> tuple[list[Disclosure], list[dict[str, str]]]:
    if attempts < 1:
        raise ValueError("attempts must be positive.")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return discover_tanshin(security_code, timeout=timeout)
        except (
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_delay_seconds * attempt)
    raise JpxDownloadError(
        f"JPX discovery failed after {attempts} attempts for {security_code}."
    ) from last_error


def _filenames(disclosures: list[Disclosure]) -> dict[str, str]:
    duplicate_periods = Counter(
        (item.fiscal_year, item.period) for item in disclosures
    )
    names: dict[str, str] = {}
    for ordinal, disclosure in enumerate(disclosures, start=1):
        fiscal_label = str(disclosure.fiscal_year)
        if duplicate_periods[(disclosure.fiscal_year, disclosure.period)] > 1:
            if disclosure.fiscal_month is None:
                raise JpxCoverageError(
                    "ambiguous_duplicate_period",
                    (
                        "Multiple selected filings share fiscal year "
                        f"{disclosure.fiscal_year} and period {disclosure.period}, "
                        "but their fiscal closing month could not be identified."
                    ),
                )
            fiscal_label = (
                f"{disclosure.fiscal_year}-{disclosure.fiscal_month:02d}"
            )
        names[disclosure.url] = (
            f"{ordinal:02d}_{fiscal_label}_{disclosure.period}_tanshin.pdf"
        )
    return names


def _download_bytes(
    url: str,
    timeout: float,
    *,
    attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> bytes:
    if attempts < 1:
        raise ValueError("attempts must be positive.")
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = response.read()
            if not content.startswith(b"%PDF-"):
                raise JpxDownloadError(f"JPX response was not a PDF: {url}")
            return content
        except (
            JpxDownloadError,
            TimeoutError,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_delay_seconds * attempt)
    raise JpxDownloadError(
        f"Could not download a valid PDF after {attempts} attempts: {url}"
    ) from last_error


def _inspect_pdf(content: bytes, security_code: str) -> tuple[int, bool]:
    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.page_count < 1:
                raise JpxDownloadError("Downloaded PDF contains no pages.")
            sample_text = "".join(
                document[index].get_text("text")
                for index in range(min(2, document.page_count))
            )
            normalized_text = re.sub(r"\s+", "", sample_text)
            return document.page_count, security_code in normalized_text
    except fitz.FileDataError as exc:
        raise JpxDownloadError("Downloaded PDF could not be opened by PyMuPDF.") from exc


def download_company(
    security_code: str,
    disclosures: list[Disclosure],
    excluded: list[dict[str, str]],
    *,
    output_root: Path,
    timeout: float,
    delay_seconds: float,
    selection: RequiredFilingSelection | None = None,
    available_disclosures: list[Disclosure] | None = None,
    download_attempts: int = 3,
    retry_delay_seconds: float = 2.0,
) -> Path:
    destination = output_root / security_code
    if destination.exists():
        raise JpxDownloadError(
            f"Destination already exists; refusing to overwrite it: {destination}"
        )
    # Resolve deterministic naming before publishing any staging state. A
    # naming ambiguity is a coverage error, not a partial download.
    filenames = _filenames(disclosures)
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".jpx_{security_code}_{uuid.uuid4().hex}"
    staging.mkdir()
    manifest_rows: list[dict[str, object]] = []

    try:
        for index, disclosure in enumerate(disclosures):
            content = _download_bytes(
                disclosure.url,
                timeout,
                attempts=download_attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            page_count, code_text_verified = _inspect_pdf(
                content, disclosure.security_code
            )
            digest = hashlib.sha256(content).hexdigest()
            filename = filenames[disclosure.url]
            (staging / filename).write_bytes(content)
            manifest_rows.append(
                {
                    **asdict(disclosure),
                    "filename": filename,
                    "byte_size": len(content),
                    "sha256": digest,
                    "page_count": page_count,
                    "security_code_found_in_first_two_pages": code_text_verified,
                }
            )
            if delay_seconds and index < len(disclosures) - 1:
                time.sleep(delay_seconds)

        manifest = {
            "schema_version": "1.1",
            "source": "JPX Listed Company Search",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "security_code": security_code,
            "manager_code": disclosures[0].manager_code,
            "company_name": disclosures[0].company_name,
            "filing_count": len(manifest_rows),
            "first_disclosure_date": disclosures[-1].disclosure_date,
            "latest_disclosure_date": disclosures[0].disclosure_date,
            "excluded_non_primary_disclosures": excluded,
            "available_primary_disclosures": [
                asdict(item)
                for item in (available_disclosures or disclosures)
            ],
            "selection": (
                {
                    "mode": "latest_plus_consecutive_year_ends",
                    "latest_disclosure_id": selection.latest.disclosure_id,
                    "latest_filename": filenames[selection.latest.url],
                    "trend_fiscal_years": list(selection.trend_fiscal_years),
                    "requested_trend_year_count": (
                        selection.requested_trend_year_count
                    ),
                    "minimum_trend_year_count": (
                        selection.minimum_trend_year_count
                    ),
                    "selected_trend_year_count": (
                        selection.selected_trend_year_count
                    ),
                    "full_requested_window_selected": (
                        selection.selected_trend_year_count
                        == selection.requested_trend_year_count
                    ),
                    "superseded_primary_disclosures": list(
                        selection.superseded_primary_disclosures
                    ),
                }
                if selection is not None
                else {"mode": "all_primary_tanshin"}
            ),
            "files": manifest_rows,
        }
        (staging / "source_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        staging.replace(destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def _preview_payload(
    security_code: str,
    disclosures: list[Disclosure],
    excluded: list[dict[str, str]],
    *,
    selection: RequiredFilingSelection | None = None,
) -> dict[str, object]:
    names = _filenames(disclosures)
    payload: dict[str, object] = {
        "security_code": security_code,
        "manager_code": disclosures[0].manager_code,
        "company_name": disclosures[0].company_name,
        "filing_count": len(disclosures),
        "first_disclosure_date": disclosures[-1].disclosure_date,
        "latest_disclosure_date": disclosures[0].disclosure_date,
        "excluded_non_primary_disclosure_count": len(excluded),
        "files": [
            {
                "filename": names[item.url],
                "disclosure_date": item.disclosure_date,
                "fiscal_year": item.fiscal_year,
                "period": item.period,
                "title": item.title,
                "url": item.url,
            }
            for item in disclosures
        ],
    }
    if selection is not None:
        payload["selection"] = {
            "mode": "latest_plus_consecutive_year_ends",
            "latest_disclosure_id": selection.latest.disclosure_id,
            "trend_fiscal_years": list(selection.trend_fiscal_years),
            "requested_trend_year_count": (
                selection.requested_trend_year_count
            ),
            "minimum_trend_year_count": selection.minimum_trend_year_count,
            "selected_trend_year_count": selection.selected_trend_year_count,
            "full_requested_window_selected": (
                selection.selected_trend_year_count
                == selection.requested_trend_year_count
            ),
            "superseded_primary_disclosure_count": len(
                selection.superseded_primary_disclosures
            ),
        }
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("security_code")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output-root", type=Path, default=Path("data"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay-seconds", type=float, default=0.25)
    parser.add_argument(
        "--selected-only",
        action="store_true",
        help=(
            "Retain only the latest filing plus up to ten consecutive "
            "FY/Q4 years."
        ),
    )
    parser.add_argument(
        "--minimum-year-ends",
        type=int,
        default=None,
        help=(
            "Explicit minimum consecutive FY/Q4 years accepted with "
            "--selected-only; the default requires all ten."
        ),
    )
    args = parser.parse_args(argv)
    if args.minimum_year_ends is not None and not args.selected_only:
        parser.error("--minimum-year-ends requires --selected-only.")

    code = args.security_code.strip().upper()
    discovered, excluded = discover_tanshin_with_retries(
        code,
        timeout=args.timeout,
    )
    disclosures = discovered
    selection = None
    if args.selected_only:
        selection = select_required_disclosures(
            disclosures,
            minimum_trend_years=args.minimum_year_ends,
        )
        disclosures = list(selection.selected)
    preview = _preview_payload(
        code,
        disclosures,
        excluded,
        selection=selection,
    )
    print(json.dumps(preview, ensure_ascii=False, indent=2))

    if not args.download:
        print("\nPREVIEW ONLY: no PDF files were written.")
        return 0

    destination = download_company(
        code,
        disclosures,
        excluded,
        output_root=args.output_root,
        timeout=args.timeout,
        delay_seconds=args.delay_seconds,
        selection=selection,
        available_disclosures=discovered,
    )
    print(f"\nDOWNLOAD SUCCESS: {destination.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
