from __future__ import annotations

import json
import pytest

from ibay_rentals.spiders.ibay_spider import (
    DEFAULT_START_URLS,
    IbayRentalSpider,
    _normalise_start_url,
    _search_scope,
    _stats_key_counts,
    _feed_uri,
    _validate_feed_output,
)


def test_stats_key_counts_extracts_scrapy_flattened_count_keys() -> None:
    stats = {
        "downloader/response_status_count/200": 5,
        "downloader/response_status_count/404": 1,
        "downloader/request_count": 6,
    }

    assert _stats_key_counts(stats, "downloader/response_status_count") == {"200": 5, "404": 1}


def test_search_scope_tracks_category_and_search_mode_only() -> None:
    assert _search_scope("https://ibay.com.mv/index.php?page=search&cid=25&s_res=AND&off=2") == ("25", "AND")
    assert _search_scope("https://ibay.com.mv/index.php?page=search&cid=600&s_res=AND&off=2") != ("25", "AND")


def test_default_start_urls_cover_requested_rental_categories() -> None:
    scopes = {_search_scope(url) for url in DEFAULT_START_URLS}
    assert scopes == {("25", "AND"), ("601", "AND"), ("589", "AND"), ("22", "AND")}


def test_category_url_is_normalised_to_search_scope() -> None:
    search_url = _normalise_start_url("https://ibay.com.mv/guest-houses-short-stay-accomodation-b589_0.html")
    assert _search_scope(search_url) == ("589", "AND")


def test_zero_max_listings_means_unlimited_ibay_scrape() -> None:
    spider = IbayRentalSpider(max_listings=0)

    assert spider.max_listings is None
    assert spider._limit_label() == "all"
    assert spider._limit_reached() is False


def test_delta_pagination_only_follows_first_three_pages():
    from scrapy.http import HtmlResponse
    spider = IbayRentalSpider(max_listings=0, max_pages=3, start_urls=[DEFAULT_START_URLS[0]])
    spider._record_stats = lambda: None
    links = ''.join(f'<a href="?cid=25&amp;page=search&amp;s_res=AND&amp;off={n}">{n}</a>' for n in range(1, 20))
    response = HtmlResponse(url=spider.start_urls[0], body=f'<ul class="pagination">{links}</ul>'.encode(), encoding="utf-8")
    requests = list(spider.parse(response))
    assert len(requests) == 2
    assert all("off=1" in request.url or "off=2" in request.url for request in requests)


@pytest.mark.parametrize("folder", ["portable folder", "release (1)", "100% complete", "café rentals"])
def test_feed_uri_survives_scrapy_interpolation_and_opens_expected_file(tmp_path, folder):
    from scrapy.extensions.feedexport import FileFeedStorage
    path = tmp_path / folder / "ibay.jsonl"
    # Exercise the same interpolation and file storage used by FeedExporter.
    storage = FileFeedStorage(_feed_uri(path) % {"time": "unused"})
    handle = storage.open(None)
    handle.write(b'{"source_name":"ibay"}\n')
    storage.store(handle)
    assert json.loads(path.read_text())["source_name"] == "ibay"


def test_missing_feed_is_an_error_even_when_crawl_fetched_listings(tmp_path):
    with pytest.raises(RuntimeError, match="did not save"):
        _validate_feed_output(tmp_path / "missing.jsonl", {"ibay/pages_fetched": 12, "item_scraped_count": 12})


def test_truncated_or_failed_feed_cannot_be_reported_as_success(tmp_path):
    path = tmp_path / "ibay.jsonl"
    path.write_text('{"source_name":"ibay"}\n')
    with pytest.raises(RuntimeError, match="incomplete"):
        _validate_feed_output(path, {"ibay/pages_fetched": 2, "item_scraped_count": 2})
    with pytest.raises(RuntimeError, match="feed failures 1"):
        _validate_feed_output(path, {"ibay/pages_fetched": 1, "item_scraped_count": 1,
                                     "feedexport/failed_count/FileFeedStorage": 1})
    assert _validate_feed_output(path, {"ibay/pages_fetched": 1, "item_scraped_count": 1}) == 1
