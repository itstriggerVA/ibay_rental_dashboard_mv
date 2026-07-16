from __future__ import annotations

from ibay_rentals.scrapers import property_mv


PROPERTY_HTML = """
<html>
  <body>
    <a class="rhea-ultra-status" href="https://www.property.mv/for-rent/">For Rent</a>
    <h1>2 Bedroom Apartment for Rent in Hulhumalé</h1>
    <section>
      <div>Bedrooms 2</div>
      <div>Area 1,050 sqft</div>
      <p>MVR 25,000 per month. Added: July 1, 2026</p>
    </section>
  </body>
</html>
"""


def test_property_mv_parser_emits_project_raw_record() -> None:
    record = property_mv.parse_listing_html(
        PROPERTY_HTML,
        "https://www.property.mv/property/2-bedroom-apartment-hm-556/?utm_source=x",
        "MONTHLY",
        scraped_at="2026-07-07T00:00:00Z",
    )

    assert record["source_name"] == "property_mv"
    assert record["source_url"] == "https://www.property.mv/property/2-bedroom-apartment-hm-556"
    assert record["listing_type"] == "RESIDENTIAL"
    assert record["use_case"] is None
    assert record["room_count"] == 2
    assert record["maid_room_count"] is None
    assert record["rent_amount"] == 25000.0
    assert record["currency_type"] == "MVR"
    assert record["rent_frequency"] == "MONTHLY"
    assert record["area_sqft"] == 1050.0
    assert record["location_zone"] == "HULHUMALE"
    assert record["last_updated"] == "2026-07-01"
    assert record["status"] == "AVAILABLE"
    assert record["is_rental_candidate"] is True


def test_property_mv_commercial_parser_sets_use_case() -> None:
    html = """
    <html><body>
      <a class="rhea-ultra-status" href="https://www.property.mv/for-rent/">For Rent</a>
      <h1>Warehouse for Rent in Male</h1>
      <section><div>Area 2,000 sqft</div><p>USD 4,000. Added: July 1, 2026</p></section>
    </body></html>
    """

    record = property_mv.parse_listing_html(
        html,
        "https://www.property.mv/property/warehouse-ml-777",
        "MONTHLY",
        scraped_at="2026-07-07T00:00:00Z",
    )

    assert record["listing_type"] == "COMMERCIAL"
    assert record["use_case"] == "Warehouse"


def test_property_mv_commercial_space_overrides_bedroom_detail_fields() -> None:
    html = """
    <html><body>
      <a class="rhea-ultra-status" href="https://www.property.mv/for-rent/">For Rent</a>
      <h1>Commercial Space for Rent (ML 428)</h1>
      <section>
        <div>Bedrooms 3</div>
        <div>Bathrooms 3</div>
        <div>Area 1,350 sqft</div>
        <p>Commercial office space in Maafannu. MVR 40,000 / Monthly. Added: July 1, 2026</p>
      </section>
    </body></html>
    """

    for source_url in (
        "https://www.property.mv/property/commercial-space-for-rent-ml-428/",
        "https://www.property.mv/property/commercial-space-for-rent-ml-186/",
        "https://www.property.mv/property/commercial-space-for-rent-ml-132/",
    ):
        record = property_mv.parse_listing_html(html, source_url, "MONTHLY")

        assert record["listing_type"] == "COMMERCIAL"
        assert record["use_case"] in {"Commercial Space", "Office Space"}


def test_property_mv_listing_type_hint_overrides_weak_page_text() -> None:
    html = """
    <html><body>
      <a class="rhea-ultra-status" href="https://www.property.mv/for-rent/">For Rent</a>
      <h1>Rental Unit in Male</h1>
      <section><div>Area 1,200 sqft</div><p>MVR 35,000. Added: July 1, 2026</p></section>
    </body></html>
    """

    record = property_mv.parse_listing_html(
        html,
        "https://www.property.mv/property/rental-unit-ml-1",
        "MONTHLY",
        listing_type_hint="COMMERCIAL",
    )

    assert record["listing_type"] == "COMMERCIAL"


def test_property_mv_parser_excludes_sale_listing() -> None:
    html = """
    <html><body>
      <a class="rhea-ultra-status" href="https://www.property.mv/for-sale/">For Sale</a>
      <h1>Fully Furnished 3 BR for Sale</h1>
      <section><div>Area 1,200 sqft</div><p>MVR 6,000,000. Added: July 1, 2026</p></section>
    </body></html>
    """

    record = property_mv.parse_listing_html(
        html,
        "https://www.property.mv/property/fully-furnished-3-br-for-sale-hm-122s",
        "MONTHLY",
        listing_type_hint="RESIDENTIAL",
    )

    assert record["is_rental_candidate"] is False
    assert record["exclusion_reason"] == "sale_or_non_rental"


def test_property_mv_location_code_uses_canonical_others() -> None:
    assert property_mv.extract_location_zone("https://www.property.mv/property/island-house-is-121") == "OTHERS"


def test_property_mv_discovery_uses_search_type_as_listing_type(monkeypatch) -> None:
    pages = {
        "https://www.property.mv/properties-search?type%5B0%5D=residential": [
            "https://www.property.mv/property/residential-listing-hm-1",
        ],
        "https://www.property.mv/properties-search/page/2/?type%5B0%5D=residential": [],
        "https://www.property.mv/properties-search?type%5B0%5D=commercial": [
            "https://www.property.mv/property/commercial-listing-ml-2",
        ],
        "https://www.property.mv/properties-search/page/2/?type%5B0%5D=commercial": [],
    }

    def fake_extract_listing_urls(url: str) -> list[str]:
        return pages[url]

    def fake_extract_listing_urls_and_max_page(url: str) -> tuple[list[str], int]:
        return pages[url], 2

    monkeypatch.setattr(property_mv, "extract_listing_urls", fake_extract_listing_urls)
    monkeypatch.setattr(property_mv, "extract_listing_urls_and_max_page", fake_extract_listing_urls_and_max_page)
    monkeypatch.setattr(property_mv.time, "sleep", lambda seconds: None)

    refs, stats = property_mv.discover_listing_refs(max_listings=10)

    configs = {ref.source_url: (ref.rent_frequency, ref.listing_type) for ref in refs}
    assert configs["https://www.property.mv/property/residential-listing-hm-1"] == ("MONTHLY", "RESIDENTIAL")
    assert configs["https://www.property.mv/property/commercial-listing-ml-2"] == ("MONTHLY", "COMMERCIAL")
    assert stats["pages_discovered"] == 2


def test_property_mv_zero_max_listings_discovers_all(monkeypatch) -> None:
    pages = {
        "https://www.property.mv/properties-search?type%5B0%5D=residential": [
            "https://www.property.mv/property/a-hm-1",
        ],
        "https://www.property.mv/properties-search/page/2/?type%5B0%5D=residential": [
            "https://www.property.mv/property/b-hm-2",
        ],
        "https://www.property.mv/properties-search/page/3/?type%5B0%5D=residential": [],
        "https://www.property.mv/properties-search?type%5B0%5D=commercial": [],
    }

    monkeypatch.setattr(property_mv, "extract_listing_urls", lambda url: pages[url])
    monkeypatch.setattr(
        property_mv,
        "extract_listing_urls_and_max_page",
        lambda url: (pages[url], 3 if "residential" in url else 1),
    )
    monkeypatch.setattr(property_mv.time, "sleep", lambda seconds: None)

    refs, stats = property_mv.discover_listing_refs(max_listings=0)

    assert [ref.source_url for ref in refs] == [
        "https://www.property.mv/property/a-hm-1",
        "https://www.property.mv/property/b-hm-2",
    ]
    assert stats["pages_discovered"] == 2


def test_property_mv_custom_search_url_normalises_paginated_type_query(monkeypatch) -> None:
    seen_urls: list[str] = []
    pages = {
        "https://www.property.mv/properties-search?type%5B0%5D=commercial": [
            "https://www.property.mv/property/a-ml-1",
        ],
        "https://www.property.mv/properties-search/page/2/?type%5B0%5D=commercial": [],
    }

    def fake_extract_listing_urls(url: str) -> list[str]:
        seen_urls.append(url)
        return pages[url]

    def fake_extract_listing_urls_and_max_page(url: str) -> tuple[list[str], int]:
        seen_urls.append(url)
        return pages[url], 2

    monkeypatch.setattr(property_mv, "extract_listing_urls", fake_extract_listing_urls)
    monkeypatch.setattr(property_mv, "extract_listing_urls_and_max_page", fake_extract_listing_urls_and_max_page)
    monkeypatch.setattr(property_mv.time, "sleep", lambda seconds: None)

    refs, _ = property_mv.discover_listing_refs(
        max_listings=0,
        start_urls=["https://www.property.mv/properties-search/?type%5B%5D=commercial"],
    )

    assert seen_urls == [
        "https://www.property.mv/properties-search?type%5B0%5D=commercial",
        "https://www.property.mv/properties-search/page/2/?type%5B0%5D=commercial",
    ]
    assert refs[0].listing_type == "COMMERCIAL"
