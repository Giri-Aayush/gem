"""
main.py — entry point for the Tender Scraper.

Usage:
    python main.py              # Run once and exit
    python main.py --schedule   # Run now, then repeat daily at the time in config.py
    python main.py --days 7     # Look back 7 days instead of the config default
    python main.py --score 40   # Only show tenders with score >= 40
    python main.py --dry-run    # Print results to console, don't save Excel
"""

import argparse
import logging
import os
import smtplib
import sys
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  —  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# ── Project imports ───────────────────────────────────────────────────────────
import config
from scrapers.models import Tender
from filters.tender_filter import filter_tenders
from output_engine.excel_exporter import export_to_excel


def _build_scrapers():
    """Instantiate and return enabled scrapers based on config."""
    scrapers = []
    portals = config.CONTRACTOR_PROFILE.get("portals", {})

    if portals.get("gem", True):
        from scrapers.gem_scraper import GemScraper
        scrapers.append(GemScraper())

    # CPPP covers central + defence tenders (replaces defproc which requires CAPTCHA)
    if portals.get("cppp", True):
        from scrapers.cppp_scraper import CpppScraper
        scrapers.append(CpppScraper())

    # AP eProcurement currently requires auth — skip unless credentials provided
    if portals.get("ap_eprocurement", False):
        from scrapers.ap_scraper import ApScraper
        scrapers.append(ApScraper())

    # HSL portal currently down — skip unless it comes back up
    if portals.get("hsl", False):
        from scrapers.hsl_scraper import HslScraper
        scrapers.append(HslScraper())

    return scrapers


def run_scraper(min_score: int = 20, dry_run: bool = False) -> str | None:
    """
    Full scrape cycle:  scrape → filter → export → (email).
    Returns the path to the saved Excel file, or None on dry-run.
    """
    run_start = datetime.now()
    logger.info("=" * 60)
    logger.info("Tender Scraper starting at %s", run_start.strftime("%d %b %Y %H:%M:%S IST"))
    logger.info("=" * 60)

    # ── 1. Scrape all portals ─────────────────────────────────────────────────
    scrapers = _build_scrapers()
    all_tenders: List[Tender] = []

    for scraper in scrapers:
        batch = scraper.run()
        all_tenders.extend(batch)

    logger.info("Total tenders scraped across all portals: %d", len(all_tenders))

    if not all_tenders:
        logger.warning("No tenders scraped. Check your internet connection and portal availability.")
        return None

    # ── 2. Filter & score ─────────────────────────────────────────────────────
    profile = config.CONTRACTOR_PROFILE
    matched = filter_tenders(all_tenders, profile, min_score=min_score)

    # ── 3. Console summary ────────────────────────────────────────────────────
    _print_summary(matched, all_tenders, run_start)

    if dry_run:
        logger.info("Dry-run mode — no Excel file saved.")
        return None

    # ── 4. Export to Excel ────────────────────────────────────────────────────
    filepath = export_to_excel(matched, all_tenders)
    logger.info("Report saved: %s", filepath)

    # ── 5. Email (optional) ───────────────────────────────────────────────────
    if config.SEND_EMAIL:
        _send_email(filepath, len(matched), len(all_tenders))

    return filepath


def _print_summary(matched: List[Tender], all_tenders: List[Tender], run_start: datetime) -> None:
    """Print a readable summary table to stdout."""
    elapsed = (datetime.now() - run_start).seconds

    print()
    print("━" * 72)
    print(f"  TENDER SCRAPER RESULTS  —  {datetime.now().strftime('%d %b %Y')}")
    print("━" * 72)
    print(f"  Total scraped : {len(all_tenders):>4}")
    print(f"  Matched       : {len(matched):>4}")
    print(f"  Elapsed       : {elapsed}s")
    print("━" * 72)

    if not matched:
        print("  No matching tenders found today. Try lowering min_score in config.")
        print()
        return

    print(f"  {'#':>3}  {'Score':>5}  {'Portal':<20}  {'Title':<38}  {'Deadline'}")
    print(f"  {'─'*3}  {'─'*5}  {'─'*20}  {'─'*38}  {'─'*14}")

    for i, t in enumerate(matched[:30], 1):   # Show top 30 in console
        score_str = f"{t.match_score:>3}/100"
        portal = t.portal[:20]
        title = (t.title[:37] + "…") if len(t.title) > 38 else t.title.ljust(38)
        deadline = t.display_deadline()[:14]
        print(f"  {i:>3}  {score_str}  {portal:<20}  {title}  {deadline}")

    if len(matched) > 30:
        print(f"  … and {len(matched) - 30} more — see the Excel file for full list.")

    print()
    print("  Top 3 matches (open these first):")
    for t in matched[:3]:
        print(f"    [{t.match_score}/100]  {t.title}")
        print(f"           Portal: {t.portal}")
        print(f"           Dept  : {t.department}")
        print(f"           Budget: {t.display_budget()}")
        print(f"           Due   : {t.display_deadline()}")
        print(f"           URL   : {t.url}")
        print()
    print("━" * 72)


def _send_email(filepath: str, n_matched: int, n_total: int) -> None:
    """Send the Excel report by email."""
    cfg = config.EMAIL_CONFIG
    try:
        msg = MIMEMultipart()
        msg["From"] = cfg["sender_email"]
        msg["To"] = cfg["recipient_email"]
        msg["Subject"] = (
            f"[Tender Scraper] {n_matched} matches today — "
            f"{datetime.now().strftime('%d %b %Y')}"
        )

        body = (
            f"Hello,\n\n"
            f"Today's tender scrape is complete.\n\n"
            f"  Matched tenders : {n_matched}\n"
            f"  Total scraped   : {n_total}\n\n"
            f"Please find the full report attached.\n\n"
            f"— Tender Scraper Bot"
        )
        msg.attach(MIMEText(body, "plain"))

        # Attach Excel file
        with open(filepath, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename={os.path.basename(filepath)}",
        )
        msg.attach(part)

        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["sender_email"], cfg["sender_password"])
            server.sendmail(cfg["sender_email"], cfg["recipient_email"], msg.as_string())

        logger.info("Email sent to %s", cfg["recipient_email"])
    except Exception as exc:
        logger.error("Email send failed: %s", exc)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Indian Government Tender Scraper — Visakhapatnam Contractor"
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Keep running and repeat daily at the time set in config.py",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Override LOOKBACK_DAYS from config (e.g. --days 3 to look back 3 days)",
    )
    parser.add_argument(
        "--score",
        type=int,
        default=None,
        help="Minimum match score to include in report (default: read from my_profile.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to console only — do not save Excel or send email",
    )
    args = parser.parse_args()

    # Apply CLI overrides to config
    if args.days is not None:
        config.LOOKBACK_DAYS = args.days

    # Score: CLI flag wins; otherwise use what's in my_profile.yaml
    min_score = args.score if args.score is not None else config.DEFAULT_MIN_SCORE

    if args.schedule:
        _run_scheduled(min_score, args.dry_run)
    else:
        run_scraper(min_score=min_score, dry_run=args.dry_run)


def _run_scheduled(min_score: int, dry_run: bool) -> None:
    """Run now, then repeat daily using APScheduler."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error(
            "APScheduler not installed. Run: pip install apscheduler\n"
            "Or run without --schedule for a one-off scrape."
        )
        sys.exit(1)

    h, m = config.SCHEDULE_TIME.split(":")
    logger.info(
        "Scheduler mode: will run every day at %s IST (%s)",
        config.SCHEDULE_TIME,
        config.SCHEDULE_TIMEZONE,
    )

    # Run immediately on start
    run_scraper(min_score=min_score, dry_run=dry_run)

    scheduler = BlockingScheduler(timezone=config.SCHEDULE_TIMEZONE)
    scheduler.add_job(
        func=run_scraper,
        trigger=CronTrigger(hour=int(h), minute=int(m), timezone=config.SCHEDULE_TIMEZONE),
        kwargs={"min_score": min_score, "dry_run": dry_run},
        id="daily_scrape",
        name="Daily Tender Scrape",
        replace_existing=True,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped by user.")


if __name__ == "__main__":
    main()
