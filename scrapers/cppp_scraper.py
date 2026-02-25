"""
Central Public Procurement Portal (CPPP / eProcure) Scraper
Portal: https://eprocure.gov.in

The CPPP is run by NIC for all central government ministries.
It covers MoD, Navy, Army, DRDO, HPCL, BPCL, port trusts, etc.
Unlike defproc.gov.in, the public tender listing here does NOT require
a CAPTCHA — tenders are visible directly.

Public listing URL (no login needed):
  https://eprocure.gov.in/eprocure/app?page=FrontEndLatestActiveTenders&service=page

We filter results by keywords and location after scraping.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from scrapers.models import Tender

logger = logging.getLogger(__name__)

CPPP_BASE = "https://eprocure.gov.in"
CPPP_TENDERS_URL = (
    f"{CPPP_BASE}/eprocure/app"
    "?page=FrontEndLatestActiveTenders&service=page"
)

_DATE_FMTS = [
    "%d-%b-%Y %I:%M %p",
    "%d-%b-%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M",
    "%d-%b-%Y",
    "%d/%m/%Y",
]


def _parse_date(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


class CpppScraper(BaseScraper):
    portal_name = "CPPP (Central eProcure)"
    portal_url = CPPP_TENDERS_URL

    def scrape(self) -> List[Tender]:
        tenders: List[Tender] = []
        page_index = 1

        while page_index <= self.max_pages:
            url = f"{CPPP_TENDERS_URL}&pageIndex={page_index}"
            resp = self.get(url)
            if resp is None:
                break

            if self._is_js_shell(resp.text):
                logger.info("CPPP: JS-rendered page — switching to Playwright.")
                return self._scrape_with_playwright()

            batch = self._parse_nic_page(resp.text, page_index)
            if not batch:
                logger.info("CPPP: no more tenders at page %d.", page_index)
                break

            tenders.extend(batch)

            dates = [
                t.published_date or t.deadline
                for t in batch
                if (t.published_date or t.deadline)
            ]
            if dates and min(d for d in dates if d) < self.cutoff_date:
                logger.info("CPPP: reached lookback cutoff at page %d.", page_index)
                break

            page_index += 1

        return tenders

    def _parse_nic_page(self, html: str, page_index: int) -> List[Tender]:
        """
        NIC eProcure standard table format.
        Columns (typical): S.No | Tender Ref No | Title | Organisation | Type
                           Published Date | Closing Date | Opening Date
        """
        soup = BeautifulSoup(html, "html.parser")
        tenders = []

        table = (
            soup.select_one("table#table1")
            or soup.select_one("table.list_table")
            or soup.find("table", {"id": re.compile(r"table", re.I)})
            or soup.select_one("table.tablesorter")
        )

        if table is None:
            if "no tender" in html.lower() or "no records" in html.lower():
                return []
            # Try any table with more than 5 rows
            tables = soup.find_all("table")
            for t in tables:
                rows = t.select("tr")
                if len(rows) > 5:
                    table = t
                    break

        if table is None:
            logger.debug("CPPP: listing table not found on page %d.", page_index)
            return []

        rows = table.select("tbody tr")
        if not rows:
            rows = table.select("tr")[1:]   # skip header row

        for row in rows:
            cells = row.select("td")
            if len(cells) < 4:
                continue

            def cell(n: int) -> str:
                return cells[n].get_text(strip=True) if n < len(cells) else ""

            # Skip if it looks like a header row
            if cells[0].find("th") or cell(0).lower() in ("s.no", "sno", "#"):
                continue

            tender_id = cell(1)
            title = cell(2)
            dept = cell(3)

            if not title:
                continue

            pub_date = None
            deadline = None
            for i in range(4, len(cells)):
                dt = _parse_date(cell(i))
                if dt:
                    if pub_date is None:
                        pub_date = dt
                    else:
                        deadline = dt
                        break

            # Extract link
            link_el = cells[1].find("a") or cells[2].find("a")
            if link_el and link_el.get("href"):
                href = link_el["href"]
                url = href if href.startswith("http") else CPPP_BASE + "/" + href.lstrip("/")
            else:
                url = CPPP_TENDERS_URL

            tender = Tender(
                tender_id=tender_id,
                title=title,
                portal="CPPP (Central eProcure)",
                department=dept,
                location=dept,   # org name usually includes city
                published_date=pub_date,
                deadline=deadline,
                url=url,
            )
            tenders.append(tender)

        return tenders

    def _scrape_with_playwright(self) -> List[Tender]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed.")
            return []

        import config
        import time

        tenders: List[Tender] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=config.HEADLESS_BROWSER)
            page = browser.new_page()

            page_index = 1
            while page_index <= self.max_pages:
                url = f"{CPPP_TENDERS_URL}&pageIndex={page_index}"
                logger.info("CPPP (Playwright): loading page %d …", page_index)
                try:
                    page.goto(url, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
                    page.wait_for_selector("table", timeout=15000)
                except Exception as exc:
                    logger.warning("CPPP: page load error: %s", exc)
                    break

                time.sleep(1)
                batch = self._parse_nic_page(page.content(), page_index)
                if not batch:
                    break
                tenders.extend(batch)
                page_index += 1

            browser.close()

        return tenders

    @staticmethod
    def _is_js_shell(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(strip=True)
        return len(text) < 500
