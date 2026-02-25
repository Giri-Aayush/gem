"""
Hindustan Shipyard Limited (HSL) eProcurement Scraper
Portal: https://eprocurehsl.nic.in

HSL is a defence shipyard in Visakhapatnam that builds vessels for the
Indian Navy.  It runs its own NIC-hosted eProcurement portal.
The listing page is the standard NIC "FrontEndLatestActiveTenders" page.

This is one of the highest-priority portals for a Vizag contractor with
Navy experience — painting, civil works, scaffolding, supply contracts.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from scrapers.models import Tender

logger = logging.getLogger(__name__)

HSL_BASE = "https://eprocurehsl.nic.in"
HSL_TENDERS_URL = (
    f"{HSL_BASE}/nicgep/app"
    "?page=FrontEndLatestActiveTenders&service=page"
)

# Fallback: HSL sometimes lists tenders on the main site too
HSL_MAIN_TENDERS = "https://www.hslvizag.in/tenders.aspx"

_DATE_FMTS = [
    "%d-%b-%Y %I:%M %p",
    "%d-%b-%Y %H:%M",
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M",
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
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


class HslScraper(BaseScraper):
    portal_name = "HSL (Hindustan Shipyard)"
    portal_url = HSL_TENDERS_URL

    def scrape(self) -> List[Tender]:
        # Try the NIC eProcure portal first
        tenders = self._scrape_nic_portal()

        # Also scrape the main HSL website's tender page (simpler HTML)
        tenders += self._scrape_hsl_main_site()

        # Deduplicate by tender_id
        seen = set()
        unique = []
        for t in tenders:
            key = t.tender_id or t.title
            if key not in seen:
                seen.add(key)
                unique.append(t)

        return unique

    def _scrape_nic_portal(self) -> List[Tender]:
        tenders: List[Tender] = []
        page_index = 1

        while page_index <= self.max_pages:
            url = f"{HSL_TENDERS_URL}&pageIndex={page_index}"
            resp = self.get(url)
            if resp is None:
                logger.info("HSL NIC portal: could not connect. Skipping.")
                break

            if self._is_js_shell(resp.text):
                logger.info("HSL NIC portal: JS-only response — switching to Playwright.")
                return self._scrape_nic_with_playwright()

            batch = self._parse_nic_page(resp.text, page_index)
            if not batch:
                break

            tenders.extend(batch)

            dates = [t.published_date or t.deadline for t in batch if (t.published_date or t.deadline)]
            if dates and min(d for d in dates if d) < self.cutoff_date:
                break

            page_index += 1

        return tenders

    def _parse_nic_page(self, html: str, page_index: int) -> List[Tender]:
        soup = BeautifulSoup(html, "html.parser")
        tenders = []

        table = (
            soup.select_one("table#table1")
            or soup.select_one("table.list_table")
            or soup.find("table", {"id": re.compile(r"table", re.I)})
        )

        if table is None:
            if "no tender" in html.lower() or "no records" in html.lower():
                return []
            logger.debug("HSL NIC: table not found on page %d.", page_index)
            return []

        rows = table.select("tbody tr") or table.select("tr")[1:]

        for row in rows:
            cells = row.select("td")
            if len(cells) < 4:
                continue

            def cell(n: int) -> str:
                return cells[n].get_text(strip=True) if n < len(cells) else ""

            tender_id = cell(1)
            title = cell(2)
            dept = cell(3) or "HSL, Visakhapatnam"

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

            link_el = cells[1].find("a") or cells[2].find("a")
            if link_el and link_el.get("href"):
                href = link_el["href"]
                url = href if href.startswith("http") else HSL_BASE + "/" + href.lstrip("/")
            else:
                url = HSL_TENDERS_URL

            tender = Tender(
                tender_id=tender_id,
                title=title,
                portal="HSL (Hindustan Shipyard)",
                department=dept,
                location="Visakhapatnam",   # HSL is always Vizag
                published_date=pub_date,
                deadline=deadline,
                url=url,
            )
            if tender.title:
                tenders.append(tender)

        return tenders

    def _scrape_hsl_main_site(self) -> List[Tender]:
        """
        Scrape HSL's official website tender notice section.
        HSL also publishes tender notices as a simple HTML table on hslvizag.in.
        """
        resp = self.get(HSL_MAIN_TENDERS)
        if resp is None:
            logger.debug("HSL main site: could not connect to %s", HSL_MAIN_TENDERS)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        tenders = []

        # HSL main site usually has a simple table or list of tender notices
        rows = soup.select("table tr, .tender-row, .notice-item")

        for row in rows:
            # Skip header rows
            if row.find("th"):
                continue

            cells = row.select("td")
            link_el = row.find("a", href=True)

            title_el = row.select_one("td:nth-child(2), .tender-title, a")
            date_el = row.select_one("td:nth-child(3), .tender-date, .due-date")

            title = title_el.get_text(strip=True) if title_el else ""
            if not title or len(title) < 5:
                continue

            date_text = date_el.get_text(strip=True) if date_el else ""
            deadline = _parse_date(date_text)

            href = link_el["href"] if link_el else ""
            if href and not href.startswith("http"):
                href = "https://www.hslvizag.in/" + href.lstrip("/")

            tender = Tender(
                tender_id=f"HSL-{hash(title) & 0xFFFF:04X}",
                title=title,
                portal="HSL (Hindustan Shipyard)",
                department="Hindustan Shipyard Limited",
                location="Visakhapatnam",
                deadline=deadline,
                url=href or HSL_MAIN_TENDERS,
            )
            tenders.append(tender)

        logger.info("HSL main site: found %d tender notices.", len(tenders))
        return tenders

    def _scrape_nic_with_playwright(self) -> List[Tender]:
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
                url = f"{HSL_TENDERS_URL}&pageIndex={page_index}"
                logger.info("HSL (Playwright): loading page %d …", page_index)

                try:
                    page.goto(url, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")
                    page.wait_for_selector("table", timeout=15000)
                except Exception as exc:
                    logger.warning("HSL: page load error at page %d: %s", page_index, exc)
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
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(strip=True)
        return len(text) < 500 and ("Loading" in html or "noscript" in html.lower())
