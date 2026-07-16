# Test Report

## Automated Suite

```text
$ .\.venv\Scripts\python.exe -m pytest --override-ini addopts=
collected 83 items

 tests\test_00_real_ibay_fixtures.py .............
 tests\test_dashboard.py ..........................
 tests\test_desktop.py ......
 tests\test_parsing.py ........
 tests\test_preprocessing.py ............
 tests\test_property_mv_scraper.py .........
 tests\test_sources.py .
 tests\test_spider.py .....
 tests\test_validation.py ...

83 passed in 18.74s
```

The suite covers parser behavior, iBay fixture parsing, Property.mv scraper helpers, preprocessing/import handling, validation, spider scope/stat helpers, dashboard helpers, and desktop process-lifecycle helpers.

## Genuine iBay Fixture Pipeline Test

All nine saved iBay pages supplied by the project owner are parsed from browser `view-source:` format and compiled through the pandas stage.

```text
fixtures parsed:      9
accepted records:     9
duplicate URLs:       0
sale/non-rental:      0
missing rent:         0
```

Expected review/validation findings are reported rather than suppressed, including defaulted monthly frequency and missing residential room counts.

The tests specifically confirm: primary MVR extraction; USD rent in description; USD selection when USD and MVR candidates coexist; security-deposit exclusion; structured area values; SQFT-below-100 exclusion; room counts; Hulhumale before Male; daily frequency; default monthly frequency in preprocessing; Last Updated extraction inside and outside the primary listing container; no Similar Items contamination; no phone-number-as-price contamination; price-range review; canonical output order; validation; dashboard import/loading without scraping; dashboard missing-dataset guidance pointing to `pipeline`; dashboard month derivation; Rent Explorer monthly type/location bars; Unknown-type graph exclusion; positive allowlist filtering; inclusive date filtering; nullable Plotly category omission; daily residential dashboard sections; Daily MVR residential room-count charts hidden to avoid misleading shared-room parsing artifacts; monthly MVR/USD residential room-count boxplot splitting into 1-2, 3-4, and 5+; exact boxplot statistic label positioning and sidebar visibility; Commercial Explorer use-case counts including blanks; fixed use-case chart colors; commercial sqft area boxplot visibility; rent-percentile filtering; desktop pipeline command construction; desktop process-tree shutdown; Streamlit wrapper launch; loopback-only dashboard binding; Windows port-listener cleanup; and canonical-only schema import discovery.

## Additional Checks

```text
$ .\.venv\Scripts\python.exe -m compileall -q src dashboard tests
compileall: passed

$ .\.venv\Scripts\python.exe -m ibay_rentals --help
usage: ibay-rentals [-h] [--version] {scrape,preprocess,pipeline} ...

$ powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1
Portable build created at: dist\IbayRentalDashboard

$ Compress-Archive dist\IbayRentalDashboard dist\IbayRentalDashboard-portable.zip
portable zip refreshed; bundled dashboard app includes the hidden Daily MVR room-count charts, monthly MVR/USD residential room-count splits, and Commercial Explorer use-case/sqft area updates
```

## Current Seed Dataset

The tracked dashboard seed dataset is `data/processed/ibay_rentals_master.csv.gz`.

```text
dashboard master rows:       3,845
source rows:
  ibay:                      2,418
  property_mv:                 930
  comm_prop_dataset_v1:        497
latest scraped_at:      2026-07-08T08:57:51Z
latest last_updated:    2026-07-08
```

The preferred command-line refresh path is:

```powershell
.\.venv\Scripts\python.exe -m ibay_rentals pipeline
.\.venv\Scripts\python.exe -m streamlit run dashboard\app.py
```
