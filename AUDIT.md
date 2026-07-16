# Audit Findings - iBay Rental Dashboard

## Current Scope

The project has two scraper sources plus schema-aligned external imports:

- `ibay`: public iBay rental categories for apartments/houses, rooms, guest houses/short stay accommodation, and office/commercial space.
- `property_mv`: public Property.mv residential and commercial rental search buckets.
- Schema-aligned imports: canonical `.xlsx` or `.csv` files under `data/imports/schema_aligned/`.

The CLI `pipeline` command is the normal refresh path. It runs scraping first, then preprocessing, and writes the processed dashboard datasets. Separate `scrape` and `preprocess` commands remain available for advanced partial workflows.

The Docker image is dashboard-only. It requires an existing `data/processed/ibay_rentals_master.csv.gz` and does not run the pipeline inside the container. The desktop launcher binds Streamlit to `127.0.0.1`; Docker binds inside the container for host port publishing.

## Contract Decisions

| Topic | Decision |
|---|---|
| Default sources | `pipeline` and `scrape` default to all scraper sources unless `--source` is provided |
| Supported scraper source names | `ibay`, `property_mv` |
| Custom start URLs | Source-specific; select exactly one source when using `--start-url` |
| Other location | `OTHERS` |
| Dates | canonical column is `last_updated`; iBay parses this from listing metadata |
| Missing frequency | default to `MONTHLY` after a positive rent exists |
| Mixed USD/MVR evidence | choose explicit USD rent candidate |
| Small SQFT areas | exclude parsed `area_sqft < 100` records |
| Contact-number price | exclude when the selected numeric value is labelled as contact data |
| Non-recurring payment | exclude unresolved material price conflicts and multi-year/upfront/advance amounts without recurring rent evidence |
| Market default | dashboard includes all status values and applies a percentile cap within comparable rent segments |
| Dashboard input | processed `.csv.gz`, `.csv`, or `.parquet` only; dashboard does not scrape |

## Parser And Scraper Findings

1. iBay parsing uses the observed `.details-page` root; title, price, attributes, and description are read from bounded listing content.
2. iBay Similar Items and related widgets are removed before extraction and are covered by regression tests.
3. Property.mv uses public residential/commercial search buckets and paginated result pages, then extracts compatible raw listing records.
4. Price selection records review reasons for material conflicts, ambiguous ranges, defaulted values, and USD-over-MVR decisions; unresolved price conflicts, labelled contact numbers, and non-recurring multi-year payments are excluded from market analysis.
5. `Last Updated` metadata is parsed from iBay page-level text such as `Listing ID : 6590656 | Last Updated : 30-Jun-2026`, while rent and attribute extraction remains bounded to primary listing content.
6. Bare number extraction remains strict to avoid phone numbers, listing IDs, years, and area values being treated as rent.

## Dashboard Findings

The dashboard includes all status values by default for historical analysis. It shows source coverage and source share alongside filtered counts, separates currencies/frequencies/property types, and uses median rent for all rate bars. Counts remain visible on rate charts; mean, minimum, and maximum are retained only as summary-table diagnostics. The percentile cap removes only upper-tail values within matching source, property-type, currency, and rent-frequency segments to limit known source mislabelling.

Nullable category values are omitted from visualizations so missing locations/currencies do not render as blank bars or data points. Overview summary rows and explorer tables use the same aggregation path for consistency. Canonical `MALE` and `HULHUMALE` values display as `Male'` and `Hulhumale'`.

## Current Seed Dataset Snapshot

The tracked seed dataset `data/processed/ibay_rentals_master.csv.gz` currently contains:

| Metric | Value |
|---|---:|
| Dashboard master rows | 3,796 |
| iBay source rows | 2,369 |
| Property.mv source rows | 930 |
| Schema-aligned import rows | 497 |
| Residential rows | 2,985 |
| Commercial rows | 744 |
| Unknown listing-type rows | 67 |
| MVR / USD listings | 3,314 / 482 |
| Monthly / Daily listings | 2,064 / 1,732 |
| Available / rented listings | 2,399 / 1,397 |
| Male / Hulhumale / Others / blank location | 2,337 / 1,319 / 113 / 27 |
| Latest `scraped_at` | 2026-07-08T08:57:51Z |
| Latest `last_updated` | 2026-07-08 |

Latest raw stats currently present:

| Source | Stats file | Pages discovered | Pages fetched | Notes |
|---|---|---:|---:|---|
| iBay | `ibay_raw_20260708T085312Z_crawl_stats.json` | 1,790 | 21 | 955 duplicate URLs removed; 0 robots-forbidden |
| Property.mv | `property_mv_raw_20260708T085348Z_crawl_stats.json` | 1,018 | 1,018 | 256 category pages scanned; 0 failed URLs |

## Remaining Boundaries

Availability is scrape-time page evidence only. Listing markup is not a formal API and can change. The project deliberately avoids authentication, CAPTCHA bypassing, browser automation for scraping, background schedulers, and databases.
