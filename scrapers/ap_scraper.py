"""
Andhra Pradesh eProcurement Portal Scraper
Portal: https://tender.apeprocurement.gov.in

AP eProcurement uses the NIC eProcure platform (same as many state portals).
The public tender listing page is:
  https://tender.apeprocurement.gov.in/nicgep/app?page=FrontEndLatestActiveTenders&service=page

The page is server-side rendered with pagination via query params.
We use requests + BeautifulSoup; fall back to Playwright if the server
returns a JavaScript-only shell.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from scrapers.models import Tender

logger = logging.getLogger(__name__)

AP_BASE = "https://tender.apeprocurement.gov.in"
AP_TENDERS_URL = (
    f"{AP_BASE}/nicgep/app"
    "?page=FrontEndLatestActiveTenders&service=page"
)

# NIC date formats
_NIC_DATE_FMTS = [
    "%d-%b-%Y %I:%M %p",
    "%d-%b-%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M",
    "%d-%b-%Y",
    "%d/%m/%Y",
]


def _parse_nic_date(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    raw = raw.strip()
    for fmt in _NIC_DATE_FMTS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


class ApScraper(BaseScraper):
    portal_name = "AP eProcurement"
    portal_url = AP_TENDERS_URL

    def scrape(self) -> List[Tender]:
        tenders: List[Tender] = []
        page_index = 1  # NIC uses 1-based page index

        while page_index <= self.max_pages:
            url = f"{AP_TENDERS_URL}&pageIndex={page_index}"
            resp = self.get(url)
            if resp is None:
                break

            # Check if the response is a real HTML page or a JS shell
            if self._is_js_shell(resp.text):
                logger.info("AP eProcurement: JS-rendered page detected — switching to Playwright.")
                tenders = self._scrape_with_playwright()
                return tenders

            batch = self._parse_nic_page(resp.text, page_index)
            if not batch:
                logger.info("AP eProcurement: no more tenders at page %d.", page_index)
                break

            tenders.extend(batch)

            # Check if oldest tender is beyond lookback window
            dates = [t.published_date or t.deadline for t in batch if (t.published_date or t.deadline)]
            if dates and min(d for d in dates if d) < self.cutoff_date:
                logger.info("AP eProcurement: reached lookback cutoff at page %d.", page_index)
                break

            page_index += 1

        return tenders

    def _parse_nic_page(self, html: str, page_index: int) -> List[Tender]:
        """Parse a standard NIC eProcure tender listing table."""
        soup = BeautifulSoup(html, "html.parser")
        tenders = []

        # NIC listing table — multiple possible selectors across NIC versions
        table = (
            soup.select_one("table#table1")
            or soup.select_one("table.list_table")
            or soup.select_one("table.tablesorter")
            or soup.find("table", {"id": re.compile(r"table", re.I)})
        )

        if table is None:
            # Check for "no tenders" message
            if "no tender" in html.lower() or "no records" in html.lower():
                return []
            logger.debug("AP eProcurement: could not find listing table on page %d.", page_index)
            return []

        rows = table.select("tbody tr")
        if not rows:
            rows = table.select("tr")[1:]  # skip header row

        for row in rows:
            cells = row.select("td")
            if len(cells) < 5:
                continue

            # Standard NIC table columns (may vary slightly):
            # 0: S.No | 1: Tender ID / Ref No | 2: Title | 3: Org | 4: Tender Type
            # 5: Published Date | 6: Bid Submission Closing Date | 7: Opening Date

            def cell(n: int) -> str:
                return cells[n].get_text(strip=True) if n < len(cells) else ""

            tender_id = cell(1) or cell(0)
            title = cell(2)
            dept = cell(3)

            # Dates — columns vary; search for ones that look like dates
            pub_date = None
            deadline = None
            for i in range(4, len(cells)):
                dt = _parse_nic_date(cell(i))
                if dt:
                    if pub_date is None:
                        pub_date = dt
                    else:
                        deadline = dt
                        break

            # Budget / estimate — NIC sometimes puts it in the title or a specific column
            budget_raw = ""
            budget_min = None
            budget_max = None
            for i in range(4, len(cells)):
                txt = cell(i)
                if "₹" in txt or "rs" in txt.lower() or re.search(r"\d[\d,]+", txt):
                    budget_raw = txt
                    val = self.parse_inr(txt)
                    if val:
                        budget_max = val
                    break

            # Link
            link_el = cells[1].find("a") or cells[2].find("a")
            if link_el and link_el.get("href"):
                href = link_el["href"]
                url = href if href.startswith("http") else AP_BASE + href
            else:
                url = AP_TENDERS_URL

            tender = Tender(
                tender_id=tender_id,
                title=title,
                portal="AP eProcurement",
                department=dept,
                location="Andhra Pradesh",
                published_date=pub_date,
                deadline=deadline,
                budget_raw=budget_raw,
                budget_max=budget_max,
                url=url,
            )
            if tender.title:
                tenders.append(tender)

        return tenders

    def _scrape_with_playwright(self) -> List[Tender]:
        """Playwright-based scrape for when the page requires JS."""
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
                url = f"{AP_TENDERS_URL}&pageIndex={page_index}"
                logger.info("AP eProcurement (Playwright): loading page %d …", page_index)

                try:
                    page.goto(url, timeout=config.BROWSER_TIMEOUT_MS, wait_until="networkidle")
                except Exception as exc:
                    logger.warning("AP eProcurement: page load timeout at page %d: %s", page_index, exc)
                    break

                time.sleep(1)
                html = page.content()
                batch = self._parse_nic_page(html, page_index)

                if not batch:
                    break
                tenders.extend(batch)
                page_index += 1

            browser.close()

        return tenders

    @staticmethod
    def _is_js_shell(html: str) -> bool:
        """Return True if the page has minimal content (JS-rendered shell)."""
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(strip=True)
        return len(text) < 500 and ("Loading" in html or "noscript" in html.lower())
