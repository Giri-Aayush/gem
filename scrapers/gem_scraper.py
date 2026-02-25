"""
GeM (Government e-Marketplace) Bids Scraper
Portal: https://bidplus.gem.gov.in/all-bids

Confirmed live HTML structure:
  div.card                     — one card per bid
  a.bid_no_hover               — bid number (text) and href="/showbidDocument/{id}"
  .col-md-4 a[data-content]    — FULL title (truncated visible text, full in data-content attr)
  .col-md-5 .row               — Department Name And Address (with <br/> between lines)
  span.start_date              — start date  e.g. "14-01-2026 1:26 PM"
  span.end_date                — end date    e.g. "23-02-2026 9:00 AM"
  a.page-link.next             — next page link  (href="#page-N")
  input#search                 — keyword search box

GeM has ~5,000+ pages of bids. Rather than paginating through all of them,
we search for each of your work keywords separately and collect matching
results. This is fast and precise.
"""

import logging
import time
from datetime import datetime
from typing import List, Optional, Set

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper
from scrapers.models import Tender
import config

logger = logging.getLogger(__name__)

GEM_BASE = "https://bidplus.gem.gov.in"
GEM_ALL_BIDS_PAGE = f"{GEM_BASE}/all-bids"

# GeM date format: "14-01-2026 1:26 PM"
_DATE_FMTS = [
    "%d-%m-%Y %I:%M %p",    # 14-01-2026 1:26 PM  ← GeM's actual format
    "%d-%m-%Y %H:%M",
    "%d-%b-%Y %I:%M %p",
    "%d-%b-%Y %H:%M",
    "%d/%m/%Y %I:%M %p",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%d-%b-%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
]

# Search terms to use on GeM — each drives a separate keyword search.
# Results from all searches are merged and deduplicated.
GEM_SEARCH_TERMS = [
    "painting",
    "housekeeping",
    "civil works",
    "scaffolding",
    "safety net",
    "AMC maintenance",
    "waterproofing",
    "fabrication",
    "cleaning sanitation",
    "manpower supply",
]

# Pages to scrape per search term (10 bids/page → 30 bids per keyword)
PAGES_PER_SEARCH = 3


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


class GemScraper(BaseScraper):
    portal_name = "GeM"
    portal_url = GEM_ALL_BIDS_PAGE

    def scrape(self) -> List[Tender]:
        try:
            return self._scrape_with_playwright()
        except ImportError:
            logger.error("Playwright not installed. Run: playwright install chromium")
            return []
        except Exception as exc:
            logger.error("GeM scrape failed: %s", exc, exc_info=True)
            return []

    def _scrape_with_playwright(self) -> List[Tender]:
        from playwright.sync_api import sync_playwright

        tenders: List[Tender] = []
        seen_ids: Set[str] = set()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=config.HEADLESS_BROWSER)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()

            for term in GEM_SEARCH_TERMS:
                logger.info("GeM: searching for '%s' …", term)
                try:
                    self._search_and_collect(page, term, tenders, seen_ids)
                except Exception as exc:
                    logger.warning("GeM: search '%s' failed: %s", term, exc)
                time.sleep(self.delay)

            browser.close()

        logger.info("GeM: collected %d unique bids across all searches.", len(tenders))
        return tenders

    def _search_and_collect(
        self,
        page,
        term: str,
        tenders: List[Tender],
        seen_ids: Set[str],
    ) -> None:
        """Navigate to GeM, search for `term`, then collect up to PAGES_PER_SEARCH pages."""
        page.goto(GEM_ALL_BIDS_PAGE, timeout=config.BROWSER_TIMEOUT_MS, wait_until="domcontentloaded")

        # Wait for the page to settle
        try:
            page.wait_for_selector("div.card", timeout=20000)
        except Exception:
            logger.warning("GeM: initial cards did not load for search '%s'", term)
            return

        # Type the search keyword into the bid search box (confirmed id: searchBid)
        search_box = (
            page.query_selector("input#searchBid")
            or page.query_selector("input[placeholder='Enter Keyword']")
            or page.query_selector("input[type='search']")
        )
        if search_box and search_box.is_visible():
            search_box.fill(term)
            search_box.press("Enter")
            time.sleep(2)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                time.sleep(2)
        else:
            logger.warning("GeM: bid search input not found/visible; collecting front-page bids only.")

        # Collect up to PAGES_PER_SEARCH pages of results
        for page_num in range(1, PAGES_PER_SEARCH + 1):
            html = page.content()
            batch = self._parse_page(html)

            new_count = 0
            for t in batch:
                if t.tender_id and t.tender_id not in seen_ids:
                    seen_ids.add(t.tender_id)
                    tenders.append(t)
                    new_count += 1

            logger.info(
                "GeM [%s] page %d: %d total, %d new",
                term, page_num, len(batch), new_count,
            )

            if not batch:
                break

            # Click "Next" (confirmed selector: a.page-link.next)
            next_btn = page.query_selector("a.page-link.next")
            if not next_btn:
                break

            next_btn.click()
            time.sleep(self.delay)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                time.sleep(2)

    def _parse_page(self, html: str) -> List[Tender]:
        """Parse all bid cards from one page of HTML using confirmed selectors."""
        soup = BeautifulSoup(html, "html.parser")
        tenders = []

        cards = soup.select("div.card")
        if not cards:
            return []

        for card in cards:
            # ── Bid number + link ──────────────────────────────────────────
            bid_link = card.select_one("a.bid_no_hover")
            if not bid_link:
                continue

            tender_id = bid_link.get_text(strip=True)
            href = bid_link.get("href", "")
            url = (GEM_BASE + href) if href.startswith("/") else (href or GEM_ALL_BIDS_PAGE)

            # ── Full title from data-content attribute ─────────────────────
            col4 = card.select_one(".col-md-4")
            category = ""
            if col4:
                # Full title is in data-content of the anchor inside .col-md-4
                title_anchor = col4.select_one("a[data-content]")
                if title_anchor:
                    category = title_anchor.get("data-content", "").strip()
                # Fallback: text of the row
                if not category:
                    for row in col4.select(".row"):
                        txt = row.get_text(strip=True)
                        if txt.startswith("Items:"):
                            category = txt.replace("Items:", "").strip()
                            break

            # ── Department (handle <br/> between ministry and org name) ───
            col5 = card.select_one(".col-md-5")
            department = ""
            if col5:
                rows = col5.select(".row")
                for i, row in enumerate(rows):
                    if "Department" in row.get_text():
                        if i + 1 < len(rows):
                            dept_row = rows[i + 1]
                            # Replace <br/> with ", " for clean text
                            for br in dept_row.find_all("br"):
                                br.replace_with(", ")
                            department = dept_row.get_text(separator=" ", strip=True)
                        break

            # ── Dates ──────────────────────────────────────────────────────
            start_el = card.select_one("span.start_date")
            end_el = card.select_one("span.end_date")
            start_dt = _parse_date(start_el.get_text(strip=True)) if start_el else None
            end_dt = _parse_date(end_el.get_text(strip=True)) if end_el else None

            tender = Tender(
                tender_id=tender_id,
                title=category,
                portal="GeM",
                department=department,
                location=department,   # dept address contains state/city for location filter
                category=category,
                published_date=start_dt,
                deadline=end_dt,
                url=url,
            )
            tenders.append(tender)

        return tenders
