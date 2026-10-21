"""Conservative Scrapy spider for discovering and parsing public iBay rentals."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
from collections.abc import Sequence
from urllib.parse import parse_qs, urljoin, urlsplit

import scrapy
from scrapy.crawler import CrawlerProcess
from scrapy.settings import Settings

from ..parsing import canonicalize_url, is_valid_ibay_listing_url, parse_listing_html
from ..settings import RAW_IBAY_DIR

DEFAULT_START_URL = "https://ibay.com.mv/index.php?cid=25&page=search&s_res=AND"
DEFAULT_START_URLS = (
    DEFAULT_START_URL,
    "https://ibay.com.mv/index.php?cid=601&page=search&s_res=AND",
    "https://ibay.com.mv/index.php?cid=589&page=search&s_res=AND",
    "https://ibay.com.mv/index.php?cid=22&page=search&s_res=AND",
)
CATEGORY_PATH_RE = re.compile(r"-b(?P<cid>\d+)_\d+\.html$", re.IGNORECASE)


def _is_same_domain(url: str) -> bool:
    return urlsplit(url).netloc.casefold() in {"ibay.com.mv", "www.ibay.com.mv"}


def _is_search_page(url: str) -> bool:
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    return _is_same_domain(url) and parts.path.endswith("/index.php") and query.get("page") == ["search"]


def _category_url_to_search_url(url: str) -> str | None:
    parts = urlsplit(url)
    match = CATEGORY_PATH_RE.search(parts.path)
    if not match or not _is_same_domain(url):
        return None
    return f"{parts.scheme or 'https'}://{parts.netloc.casefold()}/index.php?cid={match.group('cid')}&page=search&s_res=AND"


def _normalise_start_url(url: str) -> str:
    canonical = canonicalize_url(url)
    converted = _category_url_to_search_url(canonical)
    if converted:
        canonical = canonicalize_url(converted)
    if not _is_search_page(canonical):
        raise ValueError("start_url must be an iBay search URL or category URL such as ...-b25_0.html")
    return canonical


def _search_scope(url: str) -> tuple[str | None, str | None]:
    query = parse_qs(urlsplit(url).query)
    return (
        query.get("cid", [None])[0],
        query.get("s_res", [None])[0],
    )


def _stats_key_counts(stats: dict[object, object], prefix: str) -> dict[str, int]:
    prefix_with_separator = f"{prefix}/"
    return {
        str(key).removeprefix(prefix_with_separator): int(value)
        for key, value in stats.items()
        if str(key).startswith(prefix_with_separator)
    }


def _feed_uri(raw_path: Path) -> str:
    # Scrapy applies Python %-formatting to feed URIs before opening them.
    # as_uri() percent-encodes spaces, parentheses, Unicode, and literal %;
    # double every % so those escapes survive Scrapy's interpolation unchanged.
    return raw_path.resolve().as_uri().replace("%", "%%")


def _validate_feed_output(raw_path: Path, stats: dict) -> int:
    """A finished crawl is useful only if every fetched listing was saved."""
    if not raw_path.is_file():
        raise RuntimeError(f"iBay crawl did not save its JSONL output: {raw_path}")
    count = 0
    try:
        with raw_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    if not isinstance(json.loads(line), dict):
                        raise ValueError("expected a JSON object")
                    count += 1
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"iBay JSONL output is unreadable or invalid: {raw_path}") from exc
    fetched = int(stats.get("ibay/pages_fetched", 0))
    scraped = int(stats.get("item_scraped_count", 0))
    failures = sum(_stats_key_counts(stats, "feedexport/failed_count").values())
    if failures or count != fetched or count != scraped:
        raise RuntimeError(
            f"iBay output is incomplete: fetched {fetched}, parsed {scraped}, saved {count}, "
            f"feed failures {failures}. Output: {raw_path}"
        )
    return count


class IbayRentalSpider(scrapy.Spider):
    name = "ibay_rentals"
    allowed_domains = ["ibay.com.mv", "www.ibay.com.mv"]

    def __init__(
        self,
        max_listings: int = 25,
        start_url: str | None = None,
        start_urls: Sequence[str] | None = None,
        max_pages: int = 0,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        try:
            parsed_max_listings = int(max_listings)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_listings must be a non-negative integer") from exc
        if parsed_max_listings < 0:
            raise ValueError("max_listings must be a non-negative integer")
        self.max_listings: int | None = None if parsed_max_listings == 0 else parsed_max_listings
        self.max_pages = int(max_pages)
        if self.max_pages < 0:
            raise ValueError("max_pages must be a non-negative integer")

        supplied_urls = list(start_urls or ([start_url] if start_url else DEFAULT_START_URLS))
        canonical_starts = list(dict.fromkeys(_normalise_start_url(url) for url in supplied_urls if url))
        if not canonical_starts:
            raise ValueError("at least one start URL is required")

        self.start_urls = canonical_starts
        self.search_scopes = {_search_scope(url) for url in canonical_starts}
        self.seen_listing_urls: set[str] = set()
        self.seen_search_urls: set[str] = set(canonical_starts)
        self.pages_discovered = 0
        self.pages_fetched = 0
        self.duplicate_urls_removed = 0

    def _limit_reached(self) -> bool:
        return self.max_listings is not None and len(self.seen_listing_urls) >= self.max_listings

    def _limit_label(self) -> str:
        return "all" if self.max_listings is None else str(self.max_listings)

    def _record_stats(self) -> None:
        self.crawler.stats.set_value("ibay/pages_discovered", self.pages_discovered)
        self.crawler.stats.set_value("ibay/pages_fetched", self.pages_fetched)
        self.crawler.stats.set_value("ibay/duplicate_urls_removed", self.duplicate_urls_removed)

    def parse(self, response: scrapy.http.Response):
        """Discover detail pages only from the configured rental search scope."""
        queued_before = len(self.seen_listing_urls)
        for href in response.css("a::attr(href)").getall():
            absolute = canonicalize_url(urljoin(response.url, href))
            if not is_valid_ibay_listing_url(absolute):
                continue
            self.pages_discovered += 1
            if absolute in self.seen_listing_urls:
                self.duplicate_urls_removed += 1
                continue
            if self._limit_reached():
                break
            self.seen_listing_urls.add(absolute)
            yield response.follow(absolute, callback=self.parse_listing)

        queued_now = len(self.seen_listing_urls)
        self.logger.info(
            "Search page processed: queued %s/%s listing requests (%s new from this page)",
            queued_now,
            self._limit_label(),
            queued_now - queued_before,
        )

        # Search pagination stays within the original iBay search route. Detail
        # pages never contribute links, so Similar Items cannot enter the crawl.
        current_scope = _search_scope(response.url)
        if not self._limit_reached():
            for href in response.css("ul.pagination a::attr(href)").getall():
                absolute = canonicalize_url(urljoin(response.url, href))
                # iBay uses zero-based page offsets (off=0 is page one).
                offset = parse_qs(urlsplit(absolute).query).get("off", ["0"])[0]
                if self.max_pages and (not offset.isdigit() or int(offset) >= self.max_pages):
                    continue
                if (
                    not _is_search_page(absolute)
                    or current_scope not in self.search_scopes
                    or _search_scope(absolute) != current_scope
                    or absolute in self.seen_search_urls
                ):
                    continue
                self.seen_search_urls.add(absolute)
                yield response.follow(absolute, callback=self.parse)

        self._record_stats()

    def parse_listing(self, response: scrapy.http.Response):
        self.pages_fetched += 1
        scraped_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        record = parse_listing_html(response.text, response.url, scraped_at=scraped_at)
        record["http_status"] = response.status
        self._record_stats()
        self.logger.info(
            "Fetched listing %s/%s: %s",
            self.pages_fetched,
            self._limit_label(),
            record["source_url"],
        )
        yield record


def run_scrape(
    *,
    max_listings: int = 25,
    start_url: str | None = None,
    start_urls: Sequence[str] | None = None,
    raw_dir: Path = RAW_IBAY_DIR,
    max_pages: int = 0,
) -> dict[str, object]:
    """Run the spider and save raw evidence plus a crawl-statistics sidecar JSON."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_path = raw_dir / f"ibay_raw_{run_id}.jsonl"
    stats_path = raw_dir / f"ibay_raw_{run_id}_crawl_stats.json"

    settings = Settings()
    settings.setmodule("ibay_rentals.settings")
    settings.set(
        "FEEDS",
        {
            _feed_uri(raw_path): {
                "format": "jsonlines",
                "encoding": "utf-8",
                "overwrite": True,
                "store_empty": True,
            }
        },
        priority="project",
    )
    process = CrawlerProcess(settings)
    crawler = process.create_crawler(IbayRentalSpider)
    process.crawl(crawler, max_listings=max_listings, start_url=start_url, start_urls=start_urls, max_pages=max_pages)
    process.start(stop_after_crawl=True)

    stats = crawler.stats.get_stats()
    exception_types = _stats_key_counts(stats, "downloader/exception_type_count")
    summary = {
        "pages_discovered": int(stats.get("ibay/pages_discovered", 0)),
        "pages_fetched": int(stats.get("ibay/pages_fetched", 0)),
        "duplicate_urls_removed": int(stats.get("ibay/duplicate_urls_removed", 0)),
        "finish_reason": stats.get("finish_reason"),
        "response_status_count": _stats_key_counts(stats, "downloader/response_status_count"),
        "downloader_exception_count": int(stats.get("downloader/exception_count", 0)),
        "downloader_exception_types": exception_types,
        "robots_forbidden": int(stats.get("robotstxt/forbidden", 0)),
        "raw_output": str(raw_path),
    }
    stats_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    try:
        summary["records_exported"] = _validate_feed_output(raw_path, stats)
    except RuntimeError as exc:
        summary["export_error"] = str(exc)
        stats_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        raise
    stats_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    downloader_exceptions = int(stats.get("downloader/exception_count", 0))
    robots_forbidden = int(stats.get("robotstxt/forbidden", 0))
    if summary["pages_fetched"] == 0 and (downloader_exceptions > 0 or robots_forbidden > 0):
        raise RuntimeError(
            "Crawl stopped without fetching a listing page. "
            f"Network/robots details were saved to {stats_path}."
        )

    return {"raw_path": str(raw_path), "stats_path": str(stats_path), **summary}
