from __future__ import annotations

import pandas as pd

from ibay_rentals.schemas import canonical_records
from ibay_rentals.validation import build_validation_issues


def test_invalid_non_positive_rent_area_and_missing_frequency_are_flagged() -> None:
    frame = canonical_records(
        [
            {
                "listing_type": "RESIDENTIAL",
                "room_count": 12,
                "maid_room_count": 0,
                "rent_amount": 0,
                "currency_type": "MVR",
                "rent_frequency": None,
                "area_sqft": -1,
                "location_zone": "MALE",
                "address": None,
                "last_updated": "bad-date",
                "status": "AVAILABLE",
                "source_url": "not-a-url",
                "source_name": "wrong",
                "scraped_at": "2026-07-01T00:00:00Z",
            }
        ]
    )
    issues = build_validation_issues(frame)
    issue_types = set(issues["issue_type"])
    assert {"invalid_source_url", "non_positive_rent", "non_positive_area", "rent_without_frequency", "unusual_room_count", "last_updated_parse_failure"}.issubset(issue_types)


def test_price_conflict_from_raw_evidence_is_reported() -> None:
    frame = canonical_records(
        [
            {
                "listing_type": "RESIDENTIAL",
                "room_count": 1,
                "maid_room_count": None,
                "rent_amount": 15000,
                "currency_type": "MVR",
                "rent_frequency": "MONTHLY",
                "area_sqft": 500,
                "location_zone": "HULHUMALE",
                "address": "Example",
                "last_updated": "2026-06-23",
                "status": "AVAILABLE",
                "source_url": "https://ibay.com.mv/example-o123.html",
                "source_name": "ibay",
                "scraped_at": "2026-07-01T00:00:00Z",
            }
        ]
    )
    raw_records = [{"source_url": "https://ibay.com.mv/example-o123.html", "review_reasons": ["material_price_conflict: test"]}]
    issues = build_validation_issues(frame, raw_records)
    assert "price_conflict" in set(issues["issue_type"])


def test_dataset_source_urls_and_external_source_names_are_valid() -> None:
    frame = canonical_records(
        [
            {
                "listing_type": "COMMERCIAL",
                "room_count": None,
                "maid_room_count": None,
                "rent_amount": 25000,
                "currency_type": "MVR",
                "rent_frequency": "MONTHLY",
                "area_sqft": 900,
                "location_zone": "HULHUMALE",
                "address": "Imported unit",
                "last_updated": "2026-01-01",
                "status": "RENTED",
                "source_url": "dataset://comm_prop_dataset_v1/EXTRACT/1",
                "source_name": "comm_prop_dataset_v1",
                "scraped_at": "2026-06-28T10:33:21Z",
            }
        ]
    )

    issues = build_validation_issues(frame)

    assert "invalid_source_url" not in set(issues["issue_type"])
    assert "missing_source_name" not in set(issues["issue_type"])


def test_implausibly_high_rent_is_reported_for_review() -> None:
    frame = canonical_records(
        [
            {
                "listing_type": "RESIDENTIAL",
                "room_count": 1,
                "maid_room_count": None,
                "rent_amount": 9_940_965,
                "currency_type": "MVR",
                "rent_frequency": "DAILY",
                "area_sqft": None,
                "location_zone": "MALE",
                "address": "Example",
                "last_updated": "2026-07-01",
                "status": "AVAILABLE",
                "source_url": "https://ibay.com.mv/example-o123.html",
                "source_name": "ibay",
                "scraped_at": "2026-07-01T00:00:00Z",
            }
        ]
    )

    issues = build_validation_issues(frame)

    assert "suspiciously_high_rent" in set(issues["issue_type"])
