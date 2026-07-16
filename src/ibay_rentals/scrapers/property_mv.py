"""Property.mv rental scraper that emits project-compatible raw JSONL."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..classification import classify_listing_type_and_use_case
from ..settings import RAW_PROPERTY_MV_DIR
from ..sources import SOURCE_PROPERTY_MV

BASE_URL = "https://www.property.mv"
DEFAULT_CATEGORY_CONFIGS = (
    ("https://www.property.mv/properties-search/?type%5B0%5D=residential", "MONTHLY", "RESIDENTIAL"),
    ("https://www.property.mv/properties-search/?type%5B0%5D=commercial", "MONTHLY", "COMMERCIAL"),
)
REQUEST_HEADERS = {"User-Agent": "ibay-rental-dashboard/1.0.0 (+rental research; contact project owner)"}
MAX_WORKERS = 16
MAX_CATEGORY_PAGES = 500
CATEGORY_PAGE_DELAY_SECONDS = 0.0
DETAIL_REQUEST_DELAY_SECONDS = 0.01
_THREAD_LOCAL = threading.local()
_ROBOTS: RobotFileParser | None = None


@dataclass(frozen=True)
class ListingRef:
    source_url: str
    rent_frequency: str
    listing_type: str | None = None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _normalise_text(value: str) -> str:
    return (
        _clean_text(value)
        .casefold()
        .replace("hulhumalÃ©", "hulhumale")
        .replace("hulhumalé", "hulhumale")
        .replace("malÃ©", "male")
        .replace("malé", "male")
    )


def _canonicalize_property_url(url: str) -> str:
    parts = urlsplit(urljoin(BASE_URL, url))
    scheme = parts.scheme or "https"
    netloc = parts.netloc.casefold()
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def _canonicalize_index_url(url: str) -> str:
    parts = urlsplit(urljoin(BASE_URL, url))
    scheme = parts.scheme or "https"
    netloc = parts.netloc.casefold()
    path = parts.path.rstrip("/")
    pairs = [
        ("type[0]" if key == "type[]" else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    query = urlencode(pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_property_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and parts.netloc.casefold() in {"property.mv", "www.property.mv"}


def _is_listing_url(url: str) -> bool:
    parts = urlsplit(url)
    return _is_property_url(url) and "/property/" in parts.path


def _infer_frequency_from_url(url: str) -> str:
    return "DAILY" if "daily" in urlsplit(url).path.casefold() else "MONTHLY"


def _infer_listing_type_from_url(url: str) -> str | None:
    parts = urlsplit(url)
    evidence = f"{parts.path} {parts.query}".casefold()
    if "commercial" in evidence:
        return "COMMERCIAL"
    if "residential" in evidence:
        return "RESIDENTIAL"
    return None


def _get_robots() -> RobotFileParser:
    global _ROBOTS
    if _ROBOTS is not None:
        return _ROBOTS
    parser = RobotFileParser()
    parser.set_url(urljoin(BASE_URL, "/robots.txt"))
    try:
        response = requests.get(parser.url, headers=REQUEST_HEADERS, timeout=15)
        if response.status_code == 404:
            parser.parse([])
        else:
            response.raise_for_status()
            parser.parse(response.text.splitlines())
    except requests.RequestException:
        parser.parse([])
    _ROBOTS = parser
    return parser


def _assert_robots_allowed(url: str) -> None:
    if not _get_robots().can_fetch(REQUEST_HEADERS["User-Agent"], url):
        raise RuntimeError(f"Property.mv robots.txt disallows scraping {url}")


def _get_session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(REQUEST_HEADERS)
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(408, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _THREAD_LOCAL.session = session
    return session


def fetch_html(url: str) -> str:
    _assert_robots_allowed(url)
    response = _get_session().get(url, timeout=25)
    response.raise_for_status()
    return response.text


def build_page_url(base_url: str, page_number: int) -> str:
    if page_number == 1:
        return base_url
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/") + f"/page/{page_number}/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _extract_listing_urls_and_max_page_from_html(html: str) -> tuple[list[str], int]:
    soup = BeautifulSoup(html, "html.parser")
    urls: set[str] = set()
    max_page = 1
    for link in soup.find_all("a", href=True):
        href = str(link["href"])
        absolute = _canonicalize_property_url(href)
        if _is_listing_url(absolute):
            urls.add(absolute)
        parts = urlsplit(urljoin(BASE_URL, href))
        page_match = re.search(r"/properties-search/page/(\d+)/?$", parts.path)
        if page_match:
            max_page = max(max_page, int(page_match.group(1)))
    return sorted(urls), min(max_page, MAX_CATEGORY_PAGES)


def extract_listing_urls_and_max_page(index_url: str) -> tuple[list[str], int]:
    return _extract_listing_urls_and_max_page_from_html(fetch_html(index_url))


def extract_listing_urls(index_url: str) -> list[str]:
    urls, _ = extract_listing_urls_and_max_page(index_url)
    return urls


def _fetch_listing_urls_for_page(page_url: str) -> list[str]:
    try:
        return extract_listing_urls(page_url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in {403, 404}:
            return []
        raise


def discover_listing_refs(
    *,
    max_listings: int,
    start_urls: Sequence[str] | None = None,
) -> tuple[list[ListingRef], dict[str, int]]:
    if max_listings < 0:
        raise ValueError("max_listings must be a non-negative integer")
    listing_limit = None if max_listings == 0 else max_listings
    url_config = {
        _canonicalize_index_url(url): (frequency, listing_type)
        for url, frequency, listing_type in DEFAULT_CATEGORY_CONFIGS
    }
    if start_urls:
        url_config = {
            _canonicalize_index_url(url): (_infer_frequency_from_url(url), _infer_listing_type_from_url(url))
            for url in start_urls
        }

    listing_config: dict[str, tuple[str, str | None]] = {}
    pages_scanned = 0
    duplicate_urls_removed = 0

    def add_listing_urls(urls: Iterable[str], frequency: str, listing_type: str | None) -> int:
        nonlocal duplicate_urls_removed
        new_count = 0
        for url in urls:
            if url in listing_config:
                duplicate_urls_removed += 1
                existing_frequency, existing_listing_type = listing_config[url]
                listing_config[url] = (
                    frequency if frequency == "DAILY" else existing_frequency,
                    listing_type or existing_listing_type,
                )
                continue
            listing_config[url] = (frequency, listing_type)
            new_count += 1
            if listing_limit is not None and len(listing_config) >= listing_limit:
                break
        return new_count

    for category_url, (category_frequency, category_listing_type) in url_config.items():
        if listing_limit is not None and len(listing_config) >= listing_limit:
            break
        try:
            first_page_urls, max_page = extract_listing_urls_and_max_page(build_page_url(category_url, 1))
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in {403, 404}:
                continue
            raise
        if first_page_urls:
            pages_scanned += 1
            add_listing_urls(first_page_urls, category_frequency, category_listing_type)
        if max_page <= 1 or (listing_limit is not None and len(listing_config) >= listing_limit):
            continue

        page_numbers = list(range(2, max_page + 1))
        if listing_limit is not None:
            remaining = listing_limit - len(listing_config)
            estimated_per_page = max(1, len(first_page_urls))
            page_numbers = page_numbers[: max(0, (remaining + estimated_per_page - 1) // estimated_per_page)]

        page_results: dict[int, list[str]] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="property_mv_index") as executor:
            future_to_page = {
                executor.submit(_fetch_listing_urls_for_page, build_page_url(category_url, page_number)): page_number
                for page_number in page_numbers
            }
            for future in as_completed(future_to_page):
                page_results[future_to_page[future]] = future.result()
                time.sleep(CATEGORY_PAGE_DELAY_SECONDS)

        for page_number in sorted(page_results):
            urls = page_results[page_number]
            if not urls:
                continue
            pages_scanned += 1
            add_listing_urls(urls, category_frequency, category_listing_type)
            if listing_limit is not None and len(listing_config) >= listing_limit:
                break

    refs = [ListingRef(url, frequency, listing_type) for url, (frequency, listing_type) in listing_config.items()]
    return refs, {
        "pages_discovered": len(refs),
        "category_pages_scanned": pages_scanned,
        "duplicate_urls_removed": duplicate_urls_removed,
    }


def extract_price(text: str) -> tuple[float | None, str | None, str | None]:
    currency_before = r"\b(?P<currency_before>MVR|USD)\s*(?P<amount_before>\d{1,3}(?:,\d{3})*|\d+(?:\.\d+)?)"
    currency_after = r"\b(?P<amount_after>\d{1,3}(?:,\d{3})*|\d+(?:\.\d+)?)\s*(?P<currency_after>MVR|USD)\b"
    match = re.search(f"(?:{currency_before})|(?:{currency_after})", text, re.IGNORECASE)
    if not match:
        return None, None, None
    currency = (match.group("currency_before") or match.group("currency_after")).upper()
    amount_text = match.group("amount_before") or match.group("amount_after")
    amount = float(amount_text.replace(",", ""))
    return amount, currency, match.group(0)


def extract_listing_type(title: str, page_text: str, source_url: str = "") -> str:
    return classify_listing_type_and_use_case(
        [title, page_text, source_url],
        strong_commercial_overrides_residential=True,
    )[0]


def extract_use_case(title: str, page_text: str, source_url: str = "") -> str | None:
    listing_type, use_case = classify_listing_type_and_use_case(
        [title, page_text, source_url],
        strong_commercial_overrides_residential=True,
    )
    return use_case if listing_type == "COMMERCIAL" else None


def extract_location_zone(source_url: str, text: str = "") -> str | None:
    path = urlsplit(source_url).path.lower().rstrip("/")
    code_match = re.search(r"-(hm|ml|is)-\d+$", path, re.IGNORECASE)
    if code_match:
        return {"hm": "HULHUMALE", "ml": "MALE", "is": "OTHERS"}[code_match.group(1).lower()]
    lower = _normalise_text(text)
    if "hulhumale" in lower:
        return "HULHUMALE"
    male_markers = ("male city", "henveiru", "maafannu", "galolhu", "machchangolhi")
    if any(marker in lower for marker in male_markers) or re.search(r"\bmale\b", lower):
        return "MALE"
    return None


def extract_address(text: str) -> str | None:
    pattern = r"\bin\s+([^.\n]+?,\s*(?:Hulhumale|HulhumalÃ©|Hulhumalé|Male|MalÃ©|Malé)[’']?)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return _clean_text(match.group(1))
    fallback = re.search(
        r"([A-Za-z0-9\s,'’.-]+,\s*(?:Hulhumale|HulhumalÃ©|Hulhumalé|Male|MalÃ©|Malé)[’']?)",
        text,
        re.IGNORECASE,
    )
    return _clean_text(fallback.group(1)) if fallback else None


def extract_post_date(text: str) -> tuple[str | None, bool]:
    match = re.search(r"Added:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", text, re.IGNORECASE)
    if not match:
        return None, False
    try:
        parsed = datetime.strptime(match.group(1), "%B %d, %Y")
    except ValueError:
        return None, True
    return parsed.date().isoformat(), False


def extract_status(soup: BeautifulSoup) -> str:
    status_tag = soup.find("a", class_="rhea-ultra-status")
    if not status_tag:
        return "UNKNOWN"
    status_text = _normalise_text(status_tag.get_text(" "))
    status_href = str(status_tag.get("href", "")).casefold()
    if "rented-out" in status_href or "rented out" in status_text:
        return "RENTED"
    if "for-rent" in status_href or status_text == "for rent":
        return "AVAILABLE"
    return "UNKNOWN"


def classify_rental_candidate(source_url: str, title: str, text: str, status: str) -> tuple[bool, str | None]:
    evidence = _normalise_text(f"{source_url} {title} {text}")
    sale = bool(re.search(r"\b(?:for\s+sale|sold|investment)\b", evidence))
    rental = bool(re.search(r"\b(?:for\s+rent|rent|rented|monthly|daily)\b", evidence))
    if sale and not rental:
        return False, "sale_or_non_rental"
    if status == "UNKNOWN" and sale:
        return False, "sale_or_non_rental"
    return True, None


def extract_room_count_fallback(text: str) -> int | None:
    lower = _normalise_text(text)
    if "studio" in lower:
        return 1
    plus_match = re.search(r"\b(\d+)\s*\+\s*(\d+)\s*(?:br|bed|bedroom|room)", lower)
    if plus_match:
        return int(plus_match.group(1)) + int(plus_match.group(2))
    normal_match = re.search(r"\b(\d+)\s*(?:br|bed|bedroom|room|rooms)\b", lower)
    return int(normal_match.group(1)) if normal_match else None


def extract_area_sqft_fallback(text: str) -> float | None:
    match = re.search(r"\b([\d,]+(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square feet)\b", text, re.IGNORECASE)
    return float(match.group(1).replace(",", "")) if match else None


def extract_property_details(soup: BeautifulSoup) -> dict[str, int | float | None]:
    details: dict[str, int | float | None] = {"bedrooms": None, "area_sqft": None}
    for tag in soup.find_all(["div", "li", "span", "section"]):
        text = _clean_text(tag.get_text(" "))
        lower = _normalise_text(text)
        if details["bedrooms"] is None:
            bedroom_match = re.search(r"bedrooms?\s+(\d+)", lower)
            if bedroom_match:
                details["bedrooms"] = int(bedroom_match.group(1))
        if details["area_sqft"] is None:
            area_match = re.search(r"area\s+([\d,]+(?:\.\d+)?)\s*(?:sq\s*ft|sqft|square feet)", lower)
            if area_match:
                details["area_sqft"] = float(area_match.group(1).replace(",", ""))
        if details["bedrooms"] is not None and details["area_sqft"] is not None:
            break
    return details


def parse_listing_html(
    html: str,
    source_url: str,
    rent_frequency: str,
    scraped_at: str | None = None,
    listing_type_hint: str | None = None,
) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    page_text = _clean_text(soup.get_text(" "))
    title_tag = soup.find("h1")
    title = _clean_text(title_tag.get_text(" ")) if title_tag else ""
    combined_text = _clean_text(f"{title} {page_text}")

    amount, currency, raw_price = extract_price(combined_text)
    details = extract_property_details(soup)
    room_count = details["bedrooms"] if details["bedrooms"] is not None else extract_room_count_fallback(title)
    area_sqft = details["area_sqft"] if details["area_sqft"] is not None else extract_area_sqft_fallback(combined_text)
    last_updated, date_parse_failed = extract_post_date(combined_text)
    status = extract_status(soup)
    address = extract_address(combined_text)
    canonical_url = _canonicalize_property_url(source_url)
    is_rental_candidate, exclusion_reason = classify_rental_candidate(canonical_url, title, page_text, status)

    review_reasons: list[str] = []
    if raw_price is None:
        review_reasons.append("rent_amount_missing")
    if date_parse_failed:
        review_reasons.append("last_updated_parse_failed")

    inferred_listing_type, use_case = classify_listing_type_and_use_case(
        [title, page_text, canonical_url],
        strong_commercial_overrides_residential=True,
    )
    listing_type = listing_type_hint or inferred_listing_type
    if listing_type == "COMMERCIAL" and use_case is None:
        use_case = extract_use_case(title, page_text, canonical_url)
    return {
        "source_url": canonical_url,
        "source_name": SOURCE_PROPERTY_MV,
        "scraped_at": scraped_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "listing_id": urlsplit(canonical_url).path.rsplit("-", 1)[-1],
        "raw_title": title,
        "raw_description": page_text,
        "selected_price_token": raw_price,
        "selected_price_source": "property_mv_page_text" if raw_price else None,
        "price_candidates": [{"raw_token": raw_price, "amount": amount, "source": "property_mv_page_text"}] if raw_price else [],
        "review_reasons": review_reasons,
        "listing_type": listing_type,
        "use_case": use_case if listing_type == "COMMERCIAL" else None,
        "room_count": room_count,
        "maid_room_count": None,
        "rent_amount": amount,
        "currency_type": currency,
        "rent_frequency": rent_frequency,
        "area_sqft": area_sqft,
        "location_zone": extract_location_zone(canonical_url, title),
        "address": address,
        "last_updated": last_updated,
        "status": status,
        "is_rental_candidate": is_rental_candidate,
        "exclusion_reason": exclusion_reason,
        "date_parse_failed": date_parse_failed,
    }


def scrape_listing_task(ref: ListingRef) -> tuple[dict[str, object] | None, dict[str, str] | None]:
    try:
        html = fetch_html(ref.source_url)
        record = parse_listing_html(html, ref.source_url, ref.rent_frequency, listing_type_hint=ref.listing_type)
        return record, None
    except Exception as exc:
        return None, {"source_url": ref.source_url, "error": str(exc)}
    finally:
        time.sleep(DETAIL_REQUEST_DELAY_SECONDS)


def _write_jsonl(path: Path, records: Iterable[dict[str, object]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, default=str, sort_keys=True) + "\n")
            count += 1
    return count


def run_scrape(
    *,
    max_listings: int = 25,
    start_urls: Sequence[str] | None = None,
    raw_dir: Path = RAW_PROPERTY_MV_DIR,
    max_workers: int = MAX_WORKERS,
) -> dict[str, object]:
    max_listings = int(max_listings)
    if max_listings < 0:
        raise ValueError("max_listings must be a non-negative integer")
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    raw_path = raw_dir / f"property_mv_raw_{run_id}.jsonl"
    stats_path = raw_dir / f"property_mv_raw_{run_id}_crawl_stats.json"
    failed_path = raw_dir / f"property_mv_raw_{run_id}_failed_urls.jsonl"

    refs, discovery_stats = discover_listing_refs(max_listings=max_listings, start_urls=start_urls)
    records: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="property_mv") as executor:
        future_to_ref = {executor.submit(scrape_listing_task, ref): ref for ref in refs}
        for future in as_completed(future_to_ref):
            record, failure = future.result()
            if record is not None:
                records.append(record)
            if failure is not None:
                failures.append(failure)

    records.sort(key=lambda row: str(row.get("source_url", "")))
    failures.sort(key=lambda row: row.get("source_url", ""))
    fetched_count = _write_jsonl(raw_path, records)
    failed_count = _write_jsonl(failed_path, failures) if failures else 0

    summary = {
        **discovery_stats,
        "pages_fetched": fetched_count,
        "failed_urls": failed_count,
        "finish_reason": "finished",
        "raw_output": str(raw_path),
    }
    stats_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    if fetched_count == 0 and failures:
        raise RuntimeError(f"Property.mv crawl stopped without fetching listings. Details saved to {failed_path}.")
    return {"raw_path": str(raw_path), "stats_path": str(stats_path), **summary}
