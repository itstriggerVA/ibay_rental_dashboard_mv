from __future__ import annotations

from ibay_rentals.spiders.ibay_spider import (
    DEFAULT_START_URLS,
    IbayRentalSpider,
    _normalise_start_url,
    _search_scope,
    _stats_key_counts,
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
