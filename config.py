"""
config.py — reads all settings from my_profile.yaml and exposes them
as the constants that the rest of the application uses.

You should NOT need to edit this file.
Edit my_profile.yaml instead.
"""

import os
import sys

import yaml

# ── Load my_profile.yaml ──────────────────────────────────────────────────────

_HERE = os.path.dirname(os.path.abspath(__file__))
_PROFILE_FILE = os.path.join(_HERE, "my_profile.yaml")

if not os.path.exists(_PROFILE_FILE):
    print(
        "ERROR: my_profile.yaml not found.\n"
        f"Expected it at: {_PROFILE_FILE}\n"
        "Please make sure the file exists and try again."
    )
    sys.exit(1)

with open(_PROFILE_FILE, encoding="utf-8") as _f:
    _p = yaml.safe_load(_f)

# ── Build the contractor profile dict ────────────────────────────────────────

CONTRACTOR_PROFILE = {
    "name": _p.get("your_name", "Contractor"),

    "locations": _p.get("locations", []),

    "work_keywords": _p.get("my_work_types", []),

    "budget_range": {
        "min": _p.get("budget", {}).get("minimum", 0),
        "max": _p.get("budget", {}).get("maximum", 999_999_999),
    },

    "exclude_keywords": _p.get("exclude_these_work_types", []),

    # Portal toggles — controlled by the app, not the profile file
    "portals": {
        "gem": True,
        "cppp": True,
        "ap_eprocurement": False,
        "hsl": False,
    },
}

# ── Scheduler ─────────────────────────────────────────────────────────────────

SCHEDULE_TIME     = str(_p.get("run_every_day_at", "08:00"))
SCHEDULE_TIMEZONE = "Asia/Kolkata"

# ── Output ────────────────────────────────────────────────────────────────────

OUTPUT_DIR      = "reports"
OUTPUT_FILENAME = "tenders_{date}.xlsx"

# ── Email ─────────────────────────────────────────────────────────────────────

_email = _p.get("email", {})
_send  = str(_email.get("send_email", "no")).strip().lower()

SEND_EMAIL = _send in ("yes", "true", "1", "on")
EMAIL_CONFIG = {
    "sender_email":    _email.get("gmail_address", ""),
    "sender_password": _email.get("app_password", ""),
    "recipient_email": _email.get("send_report_to", _email.get("gmail_address", "")),
    "smtp_server":     "smtp.gmail.com",
    "smtp_port":       587,
}

# ── Filter sensitivity ────────────────────────────────────────────────────────

DEFAULT_MIN_SCORE = int(_p.get("minimum_match_score", 30))

# ── Scraper behaviour ─────────────────────────────────────────────────────────

LOOKBACK_DAYS        = int(_p.get("look_back_days", 1))
REQUEST_DELAY        = 2.5
MAX_PAGES_PER_PORTAL = 20
HEADLESS_BROWSER     = True
BROWSER_TIMEOUT_MS   = 30_000
