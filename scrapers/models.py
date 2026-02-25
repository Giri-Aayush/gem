"""
Data model for a single tender record.
All scrapers return lists of Tender objects.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Tender:
    # ── Identity ─────────────────────────────────────────────────────────────
    tender_id: str = ""
    title: str = ""
    portal: str = ""          # "GeM" | "AP eProcurement" | "defproc" | "HSL"

    # ── Organisation ─────────────────────────────────────────────────────────
    department: str = ""
    location: str = ""

    # ── Financials ───────────────────────────────────────────────────────────
    budget_min: Optional[float] = None   # ₹ — None if not published
    budget_max: Optional[float] = None   # ₹ — None if not published
    budget_raw: str = ""                 # Original text from portal

    # ── Dates ────────────────────────────────────────────────────────────────
    published_date: Optional[datetime] = None
    deadline: Optional[datetime] = None

    # ── Category & description ───────────────────────────────────────────────
    category: str = ""
    description: str = ""

    # ── Link ─────────────────────────────────────────────────────────────────
    url: str = ""

    # ── Filter results (set by filter engine, not scrapers) ──────────────────
    match_score: int = 0              # 0-100; higher = better match
    matched_keywords: list = field(default_factory=list)
    budget_in_range: Optional[bool] = None   # None = budget unknown
    location_match: bool = False

    def display_budget(self) -> str:
        if self.budget_raw:
            return self.budget_raw
        if self.budget_max:
            return f"₹{self.budget_max:,.0f}"
        if self.budget_min:
            return f"₹{self.budget_min:,.0f}+"
        return "Not disclosed"

    def display_deadline(self) -> str:
        if self.deadline:
            return self.deadline.strftime("%d %b %Y %H:%M")
        return "—"

    def display_published(self) -> str:
        if self.published_date:
            return self.published_date.strftime("%d %b %Y")
        return "—"
