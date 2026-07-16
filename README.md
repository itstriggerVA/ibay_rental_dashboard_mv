# iBay Rental Dashboard

A reproducible rental-listing pipeline and dashboard for public iBay and Property.mv rental data:

```text
iBay / Property.mv rental search pages
  -> source-specific discovery, robots.txt checks, retry/throttle controls
  -> detail-page extraction with raw evidence JSONL
  -> pandas preprocessing, schema-aligned external imports, and validation
  -> Parquet/CSV/GZip processed dashboard datasets
  -> Streamlit + Plotly dashboard
```

## Data Sources

By default, the CLI runs all scraper sources. Use one or more repeated `--source` values to restrict a run.

Supported source names:

- `ibay`
- `property_mv`

The iBay scraper covers these public rental categories:

- Apartments & Houses for Rent (`cid=25`)
- Room for rent (`cid=601`)
- Guest houses & short stay accommodation (`cid=589`)
- Office & Commercial space (`cid=22`)

The Property.mv scraper covers:

- Residential rentals (`https://www.property.mv/properties-search/?type%5B0%5D=residential`)
- Commercial rentals (`https://www.property.mv/properties-search/?type%5B0%5D=commercial`)

Property.mv pagination uses `/properties-search/page/<page>/?type%5B0%5D=<residential|commercial>`. The scraper assigns listing type from the selected Property.mv search bucket and excludes sale/non-rental pages during preprocessing.

The dataset is an observed sample of advertised listings from these sources. It is not a census of rental supply and does not measure signed leases or transaction prices. Market comparisons should stay within the same property type, currency, rent frequency, location, status, and source mix.

Do not bypass CAPTCHA, anti-bot controls, access restrictions, authentication, or robots.txt. If a crawl is blocked, stop and document it rather than increasing concurrency or changing the user agent to impersonate a browser.

## Processed Data Contract

`data/processed/ibay_rentals_master.parquet`, `.csv`, and `.csv.gz` contain exactly these columns in this order:

```python
CANONICAL_COLUMNS = [
    "listing_type",
    "use_case",
    "room_count",
    "maid_room_count",
    "rent_amount",
    "currency_type",
    "rent_frequency",
    "area_sqft",
    "location_zone",
    "address",
    "last_updated",
    "status",
    "source_url",
    "source_name",
    "scraped_at",
]
```

Key normalized values:

- `source_name`: `ibay`, `property_mv`, or schema-aligned import provenance such as `comm_prop_dataset_v1`
- `location_zone`: `MALE`, `HULHUMALE`, `OTHERS`
- `use_case`: blank for residential listings; commercial keyword category for commercial listings, such as `Warehouse`, `Restaurant`, `Office Space`, `Showroom`, or `Retail`

## Schema-Aligned Imports

Place external canonical datasets in:

```text
data/imports/schema_aligned/
```

Excel imports must use sheet `Standardized_Data`; CSV imports must already use the canonical column names. Accepted imported rows are written to `data/processed/imports/schema_aligned_imports.csv` and appended to the dashboard master. Import exclusions are written to `data/review/schema_aligned_import_review.csv`.

The tracked commercial reference import is:

```text
data/imports/schema_aligned/comm_prop_validated.xlsx
```

## Installation

From the project root on Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Activation is optional when using the explicit `.venv` commands above. If you prefer activation:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Run From Source

Recommended local workflow:

```powershell
.\.venv\Scripts\python.exe -m ibay_rentals pipeline
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py
```

`pipeline` runs scraping and preprocessing together. It writes all dashboard datasets, including `data/processed/ibay_rentals_master.csv.gz`, so you do not need to run `scrape` and `preprocess` separately for a normal refresh.

Important defaults:

- `pipeline` defaults to all scraper sources: `ibay` and `property_mv`.
- `--max-listings 0` is the default and means a full scrape for each selected source.
- Use a positive `--max-listings` only for smoke tests or quick parser checks.
- `--source` can be repeated to select multiple sources.
- `--start-url` is source-specific; use exactly one `--source` when passing custom start URLs.
- The Streamlit dashboard only reads processed files. It does not scrape or preprocess data.

Examples:

```powershell
# Full refresh for all sources, then open the dashboard
.\.venv\Scripts\python.exe -m ibay_rentals pipeline
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py

# Fast smoke run for both sources
.\.venv\Scripts\python.exe -m ibay_rentals pipeline --source ibay --source property_mv --max-listings 25

# Property.mv only
.\.venv\Scripts\python.exe -m ibay_rentals pipeline --source property_mv

# Custom iBay category/search URLs; custom URLs require one selected source
.\.venv\Scripts\python.exe -m ibay_rentals pipeline --source ibay --max-listings 10 --start-url "https://ibay.com.mv/index.php?cid=25&page=search&s_res=AND"
.\.venv\Scripts\python.exe -m ibay_rentals pipeline --source ibay --max-listings 50 --start-url "https://ibay.com.mv/room-for-rent-b601_0.html" --start-url "https://ibay.com.mv/office-commercial-space-b22_0.html"
```

Advanced partial workflow, useful only when you intentionally want to separate crawling from compilation:

```powershell
.\.venv\Scripts\python.exe -m ibay_rentals scrape --source ibay --max-listings 25
.\.venv\Scripts\python.exe -m ibay_rentals preprocess
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Desktop App

Run the desktop launcher from source:

```powershell
.\.venv\Scripts\python.exe -m ibay_rentals.desktop
```

The desktop UI exposes these actions:

- **Run Pipeline** runs scraping and preprocessing together for the selected source(s). This is the normal GUI refresh path.
- **Import Data** copies a schema-aligned `.xlsx` or `.csv` into `data/imports/schema_aligned/`, then preprocesses.
- **Show Dashboard** starts the local Streamlit dashboard and opens it in the browser.

Use the command-line `scrape` and `preprocess` commands only when you intentionally want the advanced partial workflow.

The desktop launcher binds the dashboard to `127.0.0.1`, so it is available only from the local computer by default. Closing the desktop app stops the Streamlit dashboard process that it launched.

## Build The Portable Desktop App

Preferred build command from the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1
```

The build script creates `.venv` if needed, installs the project with the `packaging` extra, runs PyInstaller, creates runtime data folders, and copies the compressed seed dataset and schema-aligned imports into the distribution.

The finished app is created at:

```text
dist/IbayRentalDashboard/IbayRentalDashboard.exe
```

Run the packaged app with:

```powershell
.\dist\IbayRentalDashboard\IbayRentalDashboard.exe
```

Distribute the entire `dist/IbayRentalDashboard/` folder, not just the executable. The build is intentionally one-folder instead of one-file to keep startup predictable, keep files inspectable, avoid runtime binary extraction into a temporary directory, and reduce endpoint-security friction.

If you change source code or bundled dashboard files, rebuild before testing the packaged `.exe`. Running from source does not require a rebuild.

The portable app stores generated files beside the executable:

```text
data/raw/ibay/
data/raw/property_mv/
data/imports/schema_aligned/
data/processed/
data/processed/imports/
data/review/
reports/
```

## Docker And Cloud Hosting

The Docker image is dashboard-only. It installs `requirements-dashboard.txt`, copies `dashboard/`, and copies `data/processed/ibay_rentals_master.csv.gz`. It does not install the crawler package or run scraping/preprocessing inside the container.

Create or refresh the compressed dashboard dataset first:

```powershell
.\.venv\Scripts\python.exe -m ibay_rentals pipeline
```

Then build and run the dashboard image:

```powershell
docker build -t ibay-rental-dashboard .
docker run --rm -p 8501:8501 ibay-rental-dashboard
```

Then open <http://localhost:8501>.

For local development with the current `data/` and `dashboard/` directories mounted into the container:

```powershell
docker compose up --build
```

`docker build` and `docker compose up --build` require `data/processed/ibay_rentals_master.csv.gz` to exist before the image is built. The pipeline writes that file automatically.

## Outputs

```text
data/raw/ibay/ibay_raw_<UTC timestamp>.jsonl
data/raw/ibay/ibay_raw_<UTC timestamp>_crawl_stats.json
data/raw/property_mv/property_mv_raw_<UTC timestamp>.jsonl
data/raw/property_mv/property_mv_raw_<UTC timestamp>_crawl_stats.json
data/raw/property_mv/property_mv_raw_<UTC timestamp>_failed_urls.jsonl
data/processed/ibay_rentals_master.parquet
data/processed/ibay_rentals_master.csv
data/processed/ibay_rentals_master.csv.gz
data/processed/imports/schema_aligned_imports.csv
data/review/ibay_extraction_review.csv
data/review/schema_aligned_import_review.csv
data/review/ibay_validation_issues.csv
reports/ibay_compilation_summary.csv
```

Only `data/processed/ibay_rentals_master.csv.gz` is tracked as the seed dashboard dataset. The plain CSV, Parquet, raw JSONL, review CSVs, and reports are generated outputs.

## Extraction Rules

### Similar Items protection

For the captured iBay markup, the parser uses the bounded `.details-page` listing root and removes Similar Items/related widgets before extraction. It then reads the title, price block, structured information table, and description only from that root. Generic `main`, `article`, `role=main`, and local `item-info-table`/heading fallbacks exist for safe degradation. It never scans `body` as a fallback for rent, rooms, area, location, or listing type. Page-level text is used only for the explicit `Last Updated` metadata label.

### Price hierarchy

1. Explicit USD rent evidence if permitted candidates contain both USD and MVR values.
2. Explicit primary price block.
3. Matching title-and-description value.
4. Title value.
5. Description value.
6. Blank plus review reason.

A primary value such as `MVR 15.00` is not multiplied by 1,000. It is overridden only if title and description independently agree on a materially different explicit value, such as `MVR 15,000`.

### Currency, frequency, dates, and status

- USD indicators (`USD`, `US$`, `$`) are checked before MVR indicators (`MVR`, `MRF`, `Rf`). When both USD and MVR rent candidates are present, the USD candidate is selected and the decision is recorded for review.
- MVR is defaulted only after a positive rent is selected with no explicit currency evidence.
- Daily/monthly frequency prefers the title, then primary detail/description evidence. Missing frequency defaults to `MONTHLY` during preprocessing only when the source evidence is not a multi-year, advance, upfront, or full-payment term.
- `last_updated` is parsed from iBay listing metadata text such as `Listing ID : 6590656 | Last Updated : 30-Jun-2026` and is used for date-based dashboard charts.
- `AVAILABLE` means scrape-time page evidence had no explicit rented/unavailable language; it is not a promise of current availability.
- A price is rejected when its numeric value is repeated as a labelled contact number, and a primary price with unresolved material conflict is excluded rather than treated as a market rent.

## Validation And Exclusions

The pipeline reports invalid URLs/categories, wrong source names, non-positive values, suspiciously low or high rent, room-count anomalies, dates, and price conflicts. High-price rules are warnings for review; they do not use statistical trimming to delete valid expensive inventory.

Sale/non-rental pages, missing/contact-price listings, contact numbers parsed as rent, multi-year/advance/upfront lump sums without recurring rent evidence, unresolved material price conflicts, and listings with parsed SQFT area below 100 are excluded from the processed master dataset. Every exclusion remains traceable through review outputs and raw JSONL.

## Dashboard

The dashboard reads only `data/processed/ibay_rentals_master.csv.gz`, `.csv`, or `.parquet`; it does not start scrapers. It includes all status values by default for historical analysis. Overview shows source coverage and its share of the filtered sample so a source-skewed sample is not mistaken for a market census.

Rows with missing chart categories are omitted from visualizations instead of shown as `<blank>`. Location charts use a stable order: `MALE`, `HULHUMALE`, then `OTHERS`, displayed as `Male'`, `Hulhumale'`, and `Others`.

Rent comparisons use medians as the primary estimate and preserve count on every rate chart. Means, minima, and maxima remain visible in summary tables as distribution diagnostics. The percentile control excludes the upper tail within matching source, property-type, currency, and rent-frequency segments, reducing the influence of source listings that mislabel sale or long-term lease values as monthly rent. Overview summary tables and Rent Explorer tables include count, median, mean, minimum, and maximum. The Residential Explorer splits monthly MVR and monthly USD room-count boxplots into room counts 1-2, 3-4, and 5+. Daily MVR residential room-count charts are hidden because daily room listings can describe one rented room inside a larger apartment, which can make parsed room counts misleading.

The Commercial Explorer includes commercial use-case counts with blank use cases so the row tallies to total commercial listings. Use-case charts use fixed colors: Commercial Space `#E81416`, Land `#4B369D`, Office Space `#FFA500`, Restaurant `#FAEB36`, Retail `#79C314`, and Warehouse `#487DE7`. Commercial charts include sqft area by use case, monthly rent by use case in MVR and USD, rent per sqft area by location, and monthly rent per sqft area by use case in MVR and USD.

The Listings Table page shows an overview row with listing counts by source.

Location and currency counts may not tally to total listings when records have blank listing type, blank location, blank currency, or a category outside the displayed buckets.

## Current Seed Dataset

The tracked dashboard seed dataset at `data/processed/ibay_rentals_master.csv.gz` currently contains:

```text
dashboard master rows:       3,796
source rows:
  ibay:                      2,369
  property_mv:                 930
  comm_prop_dataset_v1:        497
listing types:
  residential:               2,985
  commercial:                  744
  unknown:                      67
rent frequencies:
  monthly:                   2,064
  daily:                     1,732
currencies:
  MVR:                       3,314
  USD:                         482
status:
  available:                 2,399
  rented:                    1,397
latest scraped_at:      2026-07-08T08:57:51Z
latest last_updated:    2026-07-08
```

Latest raw crawl stats currently present under `data/raw/`:

```text
ibay latest stats file:         ibay_raw_20260708T085312Z_crawl_stats.json
  pages discovered:            1,790
  pages fetched:                  21
  duplicate URLs removed:        955
  robots forbidden:                0
property_mv latest stats file:  property_mv_raw_20260708T085348Z_crawl_stats.json
  category pages scanned:        256
  pages discovered:            1,018
  pages fetched:               1,018
  failed URLs:                    0
```

## Test Fixtures

`tests/fixtures/ibay/` contains nine genuine browser-saved iBay responses supplied by the project owner on 2026-07-01. They cover MVR price blocks, a USD price available only in description text, structured area/room fields, daily listings, price ranges, Similar Items, and phone-number traps. The parser transparently reconstructs Chrome `view-source:` saves for tests; production Scrapy responses do not need this compatibility layer.

The original synthetic fixtures remain for isolated edge cases such as the `15.00` versus `15,000` conflict rule and maid-room extraction. See `tests/fixtures/ibay/README.md` and `reports/ibay_fixture_characterization.md`.
