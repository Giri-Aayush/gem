# Government Tender Scraper — Visakhapatnam Contractor

Scrapes Indian government tender portals daily, filters results to your
work profile, and saves matched tenders to a formatted Excel file.

## Portals covered

| Portal | What it covers |
|--------|---------------|
| **GeM** (bidplus.gem.gov.in) | National marketplace — filter by AP departments & Navy units |
| **AP eProcurement** (tender.apeprocurement.gov.in) | All Andhra Pradesh state tenders |
| **defproc.gov.in** | Defence tenders — all three armed forces incl. Eastern Naval Command |
| **HSL** (eprocurehsl.nic.in + hslvizag.in) | Hindustan Shipyard Limited, Visakhapatnam |

---

## Quick start

### 1. Install Python (3.10+)
Download from https://www.python.org/downloads/

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Install the browser (for JS-heavy portals)
```bash
playwright install chromium
```

### 4. Configure your profile
Open **`config.py`** and adjust:
- `work_keywords` — add/remove the types of work you do
- `budget_range` — set your min/max comfortable budget in rupees
- `locations` — adjust if you cover more areas beyond Vizag
- `exclude_keywords` — work types you definitely can't do
- `SCHEDULE_TIME` — what time to run the daily scrape (default 8:00 AM)

### 5. Run the scraper

**One-time run:**
```bash
python main.py
```

**Run once and then repeat every day at 8 AM:**
```bash
python main.py --schedule
```

**Check the past 7 days of tenders (useful for first run):**
```bash
python main.py --days 7
```

**Show only high-confidence matches (score 60+):**
```bash
python main.py --score 60
```

**Preview without saving a file:**
```bash
python main.py --dry-run
```

---

## Output — Excel report

Reports are saved in the `reports/` folder as `tenders_YYYY-MM-DD.xlsx`.

The workbook has two sheets:

### Sheet 1: Matched Tenders
All tenders that passed your filter, sorted by match score.

| Colour | Score | Meaning |
|--------|-------|---------|
| Dark green | 80-100 | Excellent — open these first |
| Green | 60-79 | Good match |
| Amber | 30-59 | Possible match — review manually |

### Sheet 2: All Tenders (Raw)
Everything scraped today — useful to check if the filter missed anything.

---

## Scoring system

Each tender is scored 0-100:

| Condition | Points |
|-----------|--------|
| Work keyword found in title/category | +40 |
| Each additional keyword match (up to 4) | +5 each |
| Location match (Vizag/AP/Eastern Naval) | +20 |
| Budget within your range | +15 |
| HSL or defproc portal | +15 (bonus) |
| Exclude keyword found | Score = 0 (excluded) |

---

## Email alerts (optional)

To receive the Excel report by email every day:

1. Open `config.py`
2. Set `SEND_EMAIL = True`
3. Fill in your Gmail address and App Password in `EMAIL_CONFIG`

> **Gmail App Password:** Go to your Google account → Security → 2-Step Verification → App Passwords. Generate one for "Mail".

---

## Adding more portals

Each portal is a separate file in `scrapers/`.  To add a new one:

1. Create `scrapers/new_portal_scraper.py` inheriting from `BaseScraper`
2. Implement the `scrape()` method returning a list of `Tender` objects
3. Import and instantiate it in `main.py` inside `_build_scrapers()`
4. Add a toggle key in `config.py → CONTRACTOR_PROFILE["portals"]`

---

## Project structure

```
gem/
├── config.py                    # YOUR SETTINGS — edit this
├── main.py                      # Entry point
├── requirements.txt
├── scrapers/
│   ├── models.py                # Tender data model
│   ├── base.py                  # Shared scraper utilities
│   ├── gem_scraper.py           # GeM portal
│   ├── ap_scraper.py            # AP eProcurement
│   ├── defproc_scraper.py       # Defence eProcure
│   └── hsl_scraper.py           # Hindustan Shipyard
├── filters/
│   └── tender_filter.py         # Scoring & filter engine
├── output_engine/
│   └── excel_exporter.py        # Excel formatting & export
└── reports/                     # Generated Excel files saved here
```

---

## Troubleshooting

**"No tenders scraped"**
- Check your internet connection
- The portal may be down temporarily — try again in an hour
- Run with `--days 3` to look back further

**"Playwright not installed" warning**
- Run `playwright install chromium` to install the browser
- Without it, JS-heavy portals will be skipped or return fewer results

**Score too high / too low — missing relevant tenders**
- Lower `--score` to 10 to see everything
- Add your specific work types to `work_keywords` in config.py
- Check the "All Tenders" sheet to see what was scraped but filtered out

**Budget column shows "Not disclosed"**
- Most public tender listings don't show the estimate value upfront
- Click the link to open the tender detail page on the portal for the full BOQ
