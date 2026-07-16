from __future__ import annotations

from pathlib import Path

from ibay_rentals.parsing import extract_price_candidates, parse_listing_html

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_URL = "https://ibay.com.mv/2-room-apartment-15-000-00-7734494-4th-floor-no-lift-530-sqft-o6562191.html"
USD_URL = "https://ibay.com.mv/big-one-room-apartment-near-igmh-no-lift-13500-7946882-o6581568.html"


def test_terminal_slash_price_normalises_to_integer() -> None:
    candidates = extract_price_candidates("Rent MVR 10,500/- monthly", "main_description")
    assert [candidate.amount for candidate in candidates] == [10500.0]


def test_price_conflict_uses_matching_title_and_description_not_similar_items() -> None:
    record = parse_listing_html((FIXTURES / "synthetic_listing_detail.html").read_text(), LISTING_URL, "2026-07-01T00:00:00Z")
    assert record["rent_amount"] == 15000.0
    assert record["selected_price_source"] == "main_title"
    assert "material_price_conflict: primary price block disagrees with matching title and description evidence" in record["review_reasons"]
    assert record["currency_type"] == "MVR"
    assert record["rent_frequency"] == "MONTHLY"
    assert record["room_count"] == 2
    assert record["maid_room_count"] == 1
    assert record["area_sqft"] == 530.0
    assert record["location_zone"] == "MALE"
    assert record["status"] == "AVAILABLE"


def test_main_h5_frequency_and_usd_are_selected_without_related_content() -> None:
    record = parse_listing_html((FIXTURES / "synthetic_usd_listing_detail.html").read_text(), USD_URL, "2026-07-01T00:00:00Z")
    assert record["rent_amount"] == 750.0
    assert record["currency_type"] == "USD"
    assert record["rent_frequency"] == "MONTHLY"
    assert record["location_zone"] == "HULHUMALE"
    assert record["room_count"] == 1
    assert record["area_sqft"] == 500.0
    assert all(candidate["amount"] != 20000 for candidate in record["price_candidates"])


def test_title_room_parse_ignores_phone_listing_id_and_area() -> None:
    html = """
    <html><body><main role='main'>
      <h5>Big one room apartment near IGMH no lift 13500 7946882</h5>
      <p>Area 530 sqft. Monthly rent.</p>
    </main></body></html>
    """
    record = parse_listing_html(html, USD_URL, "2026-07-01T00:00:00Z")
    assert record["room_count"] == 1
    assert record["rent_amount"] == 13500.0
    assert record["area_sqft"] == 530.0


def test_similar_items_cannot_supply_price_rooms_currency_or_frequency() -> None:
    html = """
    <html><body>
      <main role='main'><h5>One room apartment in Male</h5><p>Monthly rent 14000.</p></main>
      <aside class='similar items'><h5>7 Rooms USD 800 daily</h5><p>Area 9999 sqft</p></aside>
    </body></html>
    """
    record = parse_listing_html(html, LISTING_URL, "2026-07-01T00:00:00Z")
    assert record["rent_amount"] == 14000.0
    assert record["currency_type"] == "MVR"
    assert record["rent_frequency"] == "MONTHLY"
    assert record["room_count"] == 1
    assert record["area_sqft"] is None


def test_mixed_mvr_and_usd_candidates_selects_usd() -> None:
    html = """
    <html><body><main role='main'>
      <h5>Office space for rent in Male</h5>
      <div class="price">MVR 15,000.00</div>
      <p>Rent: USD 1,200 per month. Area 500 sqft.</p>
    </main></body></html>
    """
    record = parse_listing_html(html, LISTING_URL, "2026-07-01T00:00:00Z")
    assert record["listing_type"] == "COMMERCIAL"
    assert record["use_case"] == "Office Space"
    assert record["rent_amount"] == 1200.0
    assert record["currency_type"] == "USD"
    assert "mixed_currency_price_candidates: explicit USD rent candidate selected over MVR candidate" in record["review_reasons"]


def test_last_updated_metadata_is_extracted() -> None:
    html = """
    <html><body><main role='main'>
      <h5>One room apartment in Male</h5>
      <div class="price">MVR 12,000.00</div>
      <div style="color:#666; font-size:12px;">Listing ID : 6590656 | Last Updated : 30-Jun-2026</div>
      <p>Monthly rent.</p>
    </main></body></html>
    """
    record = parse_listing_html(html, LISTING_URL, "2026-07-01T00:00:00Z")
    assert record["last_updated"] == "2026-06-30"


def test_page_level_last_updated_metadata_is_extracted_outside_primary_container() -> None:
    html = """
    <html><body>
      <main role='main'>
        <h5>One room apartment in Male</h5>
        <div class="price">MVR 12,000.00</div>
        <p>Monthly rent.</p>
      </main>
      <div style="color:#666; font-size:12px;">Listing ID : 6590656 | Last Updated : 30-Jun-2026</div>
    </body></html>
    """
    record = parse_listing_html(html, LISTING_URL, "2026-07-01T00:00:00Z")
    assert record["last_updated"] == "2026-06-30"


def test_contact_number_reused_in_price_field_is_not_treated_as_rent() -> None:
    html = """
    <html><body><main role='main'>
      <h5>Rooms for daily rent. Contact 9940965.</h5>
      <div class="price">MVR 9,940,965.00</div>
      <p>Viber 9940965 for details.</p>
    </main></body></html>
    """

    record = parse_listing_html(html, LISTING_URL, "2026-07-01T00:00:00Z")

    assert record["rent_amount"] is None
    assert "selected_price_matches_contact_number" in record["review_reasons"]
