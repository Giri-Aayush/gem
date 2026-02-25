"""
Filter engine — scores and filters tenders based on the contractor profile.

Each tender gets a match_score from 0-100:
  - 0-29:  Poor match (excluded from output by default)
  - 30-59: Possible match (shown with a yellow flag)
  - 60-79: Good match (shown in green)
  - 80+:   Excellent match (shown in bold green)

Scoring breakdown:
  +40 pts  — at least one work keyword matches in title/description/category
  +20 pts  — location keyword matches
  +15 pts  — budget is within your range (if budget is disclosed)
  +15 pts  — HSL / defproc portal (higher priority for Navy-related tenders)
  -30 pts  — exclude keyword found in title
  +5 pts   — each additional keyword match (up to +20 bonus)
"""

import logging
import re
from typing import List

from scrapers.models import Tender
import config

logger = logging.getLogger(__name__)


def _normalise(text: str) -> str:
    return text.lower().strip()


def _contains_any(text: str, keywords: List[str]) -> List[str]:
    """Return list of keywords found in text (case-insensitive substring)."""
    text_lower = _normalise(text)
    return [kw for kw in keywords if kw.lower() in text_lower]


def _contains_any_word(text: str, keywords: List[str]) -> List[str]:
    """Return keywords that appear as whole words/phrases in text.

    Uses regex \\b word boundaries so that short keywords like "AP" match
    "AP" or "(AP)" but NOT inside "weapons", "capacity", etc.
    """
    text_lower = _normalise(text)
    hits = []
    for kw in keywords:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        if re.search(pattern, text_lower):
            hits.append(kw)
    return hits


def score_tender(tender: Tender, profile: dict) -> Tender:
    """
    Score a single tender against the contractor profile.
    Modifies tender in-place (sets match_score, matched_keywords,
    budget_in_range, location_match) and returns it.
    """
    score = 0
    matched_keywords: List[str] = []

    # ── Build search corpus ───────────────────────────────────────────────────
    corpus = " ".join([
        tender.title,
        tender.description,
        tender.category,
        tender.department,
    ])

    # ── Exclude keywords check (hard disqualifier) ────────────────────────────
    exclude_hits = _contains_any(corpus, profile.get("exclude_keywords", []))
    if exclude_hits:
        tender.match_score = 0
        tender.matched_keywords = [f"EXCLUDED:{kw}" for kw in exclude_hits]
        return tender

    # ── Work keyword matching ─────────────────────────────────────────────────
    work_hits = _contains_any(corpus, profile.get("work_keywords", []))
    if work_hits:
        score += 40
        matched_keywords.extend(work_hits)
        # Bonus for each additional keyword match (max 4 extra, +5 each)
        bonus = min(len(work_hits) - 1, 4) * 5
        score += bonus

    # ── Location matching (word-boundary to avoid "AP" matching in "weapons") ─
    location_corpus = " ".join([
        tender.location,
        tender.department,
        tender.title,
    ])
    location_hits = _contains_any_word(location_corpus, profile.get("locations", []))
    if location_hits:
        score += 20
        tender.location_match = True
        matched_keywords.extend([f"LOC:{h}" for h in location_hits])
    else:
        tender.location_match = False

    # ── Budget range check ────────────────────────────────────────────────────
    min_budget = profile.get("budget_range", {}).get("min", 0)
    max_budget = profile.get("budget_range", {}).get("max", float("inf"))

    if tender.budget_max is not None:
        in_range = min_budget <= tender.budget_max <= max_budget
        tender.budget_in_range = in_range
        if in_range:
            score += 15
    elif tender.budget_min is not None:
        # Only a lower bound — check if it's not wildly above our ceiling
        tender.budget_in_range = tender.budget_min <= max_budget
        if tender.budget_in_range:
            score += 8   # Partial credit (budget might be in range)
    else:
        tender.budget_in_range = None   # Unknown — no deduction

    # ── Portal priority bonus ─────────────────────────────────────────────────
    if tender.portal in ("HSL (Hindustan Shipyard)", "defproc (Defence)"):
        score += 15   # These are your speciality

    # ── No keyword match at all — likely irrelevant ───────────────────────────
    if not work_hits and not location_hits:
        score = max(score, 0)   # Floor at 0

    tender.match_score = min(score, 100)
    tender.matched_keywords = matched_keywords
    return tender


def filter_tenders(
    tenders: List[Tender],
    profile: dict,
    min_score: int = 20,
) -> List[Tender]:
    """
    Score all tenders, remove obvious mismatches, and return sorted results.

    Args:
        tenders:    Raw list of Tender objects from scrapers.
        profile:    Contractor profile dict from config.py.
        min_score:  Minimum score to include in output (default 20).
                    Set lower to see more results, higher for tighter filtering.

    Returns:
        List of tenders sorted by match_score descending.
    """
    logger.info("Filtering %d tender(s) …", len(tenders))

    scored = [score_tender(t, profile) for t in tenders]

    # Remove tenders with excluded keywords or below threshold
    filtered = [t for t in scored if t.match_score >= min_score]

    # Sort: score descending, then by deadline ascending (closer deadlines first)
    filtered.sort(
        key=lambda t: (-t.match_score, t.deadline or __import__("datetime").datetime.max),
    )

    logger.info(
        "Filter result: %d/%d tenders kept (min_score=%d).",
        len(filtered), len(tenders), min_score,
    )
    return filtered
