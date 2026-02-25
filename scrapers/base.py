"""
Base scraper class — shared utilities used by all portal scrapers.
"""

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scrapers.models import Tender
import config

logger = logging.getLogger(__name__)


def _make_session() -> requests.Session:
    """Build a requests.Session with retry logic and browser-like headers."""
    session = requests.Session()

    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-IN,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


class BaseScraper(ABC):
    """All portal scrapers inherit from this class."""

    portal_name: str = "Unknown Portal"
    portal_url: str = ""

    def __init__(self) -> None:
        self.session = _make_session()
        self.delay = config.REQUEST_DELAY
        self.max_pages = config.MAX_PAGES_PER_PORTAL
        self.lookback_days = config.LOOKBACK_DAYS
        self.cutoff_date = datetime.now() - timedelta(days=self.lookback_days)

    # ── Public API ────────────────────────────────────────────────────────────

    def run(self) -> List[Tender]:
        """
        Entry point called by main.py.
        Returns a list of Tender objects fetched from this portal.
        """
        logger.info("▶  Starting %s scraper …", self.portal_name)
        try:
            tenders = self.scrape()
            logger.info(
                "✓  %s: fetched %d tender(s)", self.portal_name, len(tenders)
            )
            return tenders
        except Exception as exc:
            logger.error(
                "✗  %s scraper failed: %s", self.portal_name, exc, exc_info=True
            )
            return []

    # ── Abstract methods (implement in each portal scraper) ───────────────────

    @abstractmethod
    def scrape(self) -> List[Tender]:
        """Fetch tenders from the portal and return a list of Tender objects."""
        ...

    # ── Shared helpers ────────────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> Optional[requests.Response]:
        """Polite GET with delay and error handling."""
        time.sleep(self.delay)
        try:
            resp = self.session.get(url, timeout=20, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("GET failed for %s: %s", url, exc)
            return None

    def post(self, url: str, **kwargs) -> Optional[requests.Response]:
        """Polite POST with delay and error handling."""
        time.sleep(self.delay)
        try:
            resp = self.session.post(url, timeout=20, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("POST failed for %s: %s", url, exc)
            return None

    def is_recent(self, dt: Optional[datetime]) -> bool:
        """Return True if the datetime is within the lookback window."""
        if dt is None:
            return True   # If we can't determine, include it
        return dt >= self.cutoff_date

    @staticmethod
    def parse_inr(text: str) -> Optional[float]:
        """
        Try to extract a rupee amount from strings like:
          "₹ 3,50,000", "350000", "3.5 Lakh", "1.2 Crore"
        Returns amount in rupees as a float, or None if it can't parse.
        """
        if not text:
            return None
        text = text.strip().replace(",", "").replace("₹", "").replace("Rs.", "").strip()

        try:
            if "crore" in text.lower() or "cr" in text.lower():
                num = float("".join(c for c in text if c.isdigit() or c == "."))
                return num * 1_00_00_000
            if "lakh" in text.lower() or "lac" in text.lower():
                num = float("".join(c for c in text if c.isdigit() or c == "."))
                return num * 1_00_000
            # Plain number
            num_str = "".join(c for c in text if c.isdigit() or c == ".")
            return float(num_str) if num_str else None
        except ValueError:
            return None
