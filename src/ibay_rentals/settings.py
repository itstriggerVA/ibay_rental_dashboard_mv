"""Project paths and conservative Scrapy settings."""

from __future__ import annotations

from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parent


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PACKAGE_ROOT.parent.parent


PROJECT_ROOT = _project_root()
DATA_ROOT = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_ROOT / "raw"
RAW_IBAY_DIR = RAW_DATA_DIR / "ibay"
RAW_PROPERTY_MV_DIR = RAW_DATA_DIR / "property_mv"
IMPORTS_DIR = DATA_ROOT / "imports"
SCHEMA_ALIGNED_IMPORT_DIR = IMPORTS_DIR / "schema_aligned"
PROCESSED_DIR = DATA_ROOT / "processed"
PROCESSED_IMPORTS_DIR = PROCESSED_DIR / "imports"
REVIEW_DIR = DATA_ROOT / "review"
REPORTS_DIR = PROJECT_ROOT / "reports"

BOT_NAME = "ibay_rentals"
SPIDER_MODULES = ["ibay_rentals.spiders"]
NEWSPIDER_MODULE = "ibay_rentals.spiders"

# Respect the site's published crawl policy. Do not override this setting for
# convenience: a blocked robots.txt path means the crawl should not continue.
ROBOTSTXT_OBEY = True

# Polite but practical defaults for category-wide research crawls.
CONCURRENT_REQUESTS = 6
CONCURRENT_REQUESTS_PER_DOMAIN = 4
DOWNLOAD_DELAY = 0.5
RANDOMIZE_DOWNLOAD_DELAY = True
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.5
AUTOTHROTTLE_MAX_DELAY = 5.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0
RETRY_ENABLED = True
RETRY_TIMES = 2
RETRY_HTTP_CODES = [408, 429, 500, 502, 503, 504]
DOWNLOAD_TIMEOUT = 30

# Identify the crawler without impersonating a browser.
USER_AGENT = "ibay-rental-dashboard/1.0.0"
LOG_LEVEL = "INFO"
LOGSTATS_INTERVAL = 10
TELNETCONSOLE_ENABLED = False
COOKIES_ENABLED = False
WARN_ON_GENERATOR_RETURN_VALUE = False

# No JavaScript browser automation is used by this project.
