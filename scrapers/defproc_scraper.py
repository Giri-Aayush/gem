"""
Defence eProcurement Portal Scraper
Portal: https://defproc.gov.in

defproc.gov.in is the MoD's dedicated eProcurement system for all three
armed services (Army, Navy, Air Force).  Eastern Naval Command tenders for
Visakhapatnam come through here.

The portal is built on NIC's eProcure platform — same underlying tech as
AP eProcurement, so we reuse a lot of the same parsing logic.

Public tender listing (no login required):
  https://defproc.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from scrapers.models import Tender

logger = logging.getLogger(__name__)

DEFPROC_BASE = "https://defproc.gov.in"
DEFPROC_TENDERS_URL = (
    f"{DEFPROC_BASE}/nicgep/app"
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


class DefprocScraper(BaseScraper):
    portal_name = "defproc (Defence)"
    portal_url = DEFPROC_TENDERS_URL

    def scrape(self) -> List[Tender]:
        tenders: List[Tender] = []
        page_index = 1

        while page_index <= self.max_pages:
            url = f"{DEFPROC_TENDERS_URL}&pageIndex={page_index}"
            resp = self.get(url)
            if resp is None:
                break

            if self._is_js_shell(resp.text):
                logger.info("defproc: JS-only page — switching to Playwright.")
                return self._scrape_with_playwright()

            batch = self._parse_nic_page(resp.text, page_index)
            if not batch:
                logger.info("defproc: no more tenders at page %d.", page_index)
                break

            tenders.extend(batch)

            # Cutoff check
            all_dates = [
                t.published_date or t.deadline
                for t in batch
                if (t.published_date or t.deadline)
            ]
            if all_dates and min(d for d in all_dates if d) < self.cutoff_date:
                logger.info("defproc: reached lookback cutoff at page %d.", page_index)
                break

            page_index += 1

        return tenders

    def _parse_nic_page(self, html: str, page_index: int) -> List[Tender]:
        soup = BeautifulSoup(html, "html.parser")
        tenders = []

        # defproc uses the same NIC table structure
        table = (
            soup.select_one("table#table1")
            or soup.select_one("table.list_table")
            or soup.find("table", {"id": re.compile(r"table", re.I)})
        )

        if table is None:
            if "no tender" in html.lower() or "no records" in html.lower():
                return []
            logger.debug("defproc: listing table not found on page %d.", page_index)
            return []

        rows = table.select("tbody tr") or table.select("tr")[1:]

        for row in rows:
            cells = row.select("td")
            if len(cells) < 4:
                continue

            def cell(n: int) -> str:
                return cells[n].get_text(strip=True) if n < len(cells) else ""

            # NIC defproc standard columns:
            # 0: S.No | 1: Tender Ref No | 2: Tender Title | 3: Organisation
            # 4: Tender Type | 5: Published Date | 6: Closing Date | 7: Opening Date
            tender_id = cell(1)
            title = cell(2)
            dept = cell(3)

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

            # Link
            link_el = cells[1].find("a") or cells[2].find("a")
            if link_el and link_el.get("href"):
                href = link_el["href"]
                url = href if href.startswith("http") else DEFPROC_BASE + "/" + href.lstrip("/")
            else:
                url = DEFPROC_TENDERS_URL

            # Try to infer location from org name
            location = dept  # e.g., "Eastern Naval Command, Visakhapatnam"

            tender = Tender(
                tender_id=tender_id,
                title=title,
                portal="defproc (Defence)",
                department=dept,
                location=location,
                published_date=pub_date,
                deadline=deadline,
                url=url,
            )
            if tender.title:
                tenders.append(tender)

        return tenders

    def _scrape_with_playwright(self) -> List[Tender]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
            return []

        import config
        import time

        tenders: List[Tender] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=config.HEADLESS_BROWSER)
            page = browser.new_page()
            page.set_extra_http_headers({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                )
            })

            page_index = 1
            while page_index <= self.max_pages:
                url = f"{DEFPROC_TENDERS_URL}&pageIndex={page_index}"
                logger.info("defproc (Playwright): loading page %d …", page_index)

                try:
                    page.goto(url, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
                    # Wait for the table to appear
                    page.wait_for_selector("table", timeout=15000)
                except Exception as exc:
                    logger.warning("defproc: page load error at page %d: %s", page_index, exc)
                    break

                time.sleep(1)
                html = page.content()
                batch = self._parse_nic_page(html, page_index)

                if not batch:
                    break
                tenders.extend(batch)

                # Cutoff check
                dates = [t.published_date or t.deadline for t in batch if (t.published_date or t.deadline)]
                if dates and min(d for d in dates if d) < self.cutoff_date:
                    break

                page_index += 1

            browser.close()

        return tenders

    @staticmethod
    def _is_js_shell(html: str) -> bool:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(strip=True)
        return len(text) < 500 and ("Loading" in html or "noscript" in html.lower())
