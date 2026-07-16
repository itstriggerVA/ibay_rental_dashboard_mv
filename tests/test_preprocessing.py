from __future__ import annotations

import pandas as pd

from ibay_rentals.preprocessing import (
    _schema_aligned_import_paths,
    compile_records,
    load_schema_aligned_imports,
    normalise_raw_record,
)
from ibay_rentals.schemas import CANONICAL_COLUMNS


def _record(**overrides):
    record = {
        "listing_type": "RESIDENTIAL",
        "room_count": 2,
        "maid_room_count": None,
        "rent_amount": 15000,
        "currency_type": None,
        "rent_frequency": "MONTHLY",
        "area_sqft": 530,
        "location_zone": "OTHER",
        "address": "Example",
        "last_updated": "2026-06-23",
        "status": "AVAILABLE",
        "source_url": "https://ibay.com.mv/example-o123.html",
        "source_name": "ibay",
        "scraped_at": "2026-07-01T00:00:00Z",
        "is_rental_candidate": True,
        "review_reasons": [],
    }
    record.update(overrides)
    return record


def test_currency_defaults_to_mvr_only_after_positive_rent() -> None:
    normalized, reasons = normalise_raw_record(_record())
    assert normalized["currency_type"] == "MVR"
    assert "currency_defaulted_to_mvr_after_missing_explicit_currency" in reasons

    no_rent, _ = normalise_raw_record(_record(rent_amount=None, currency_type=None))
    assert no_rent["currency_type"] is None


def test_missing_frequency_defaults_to_monthly_only_after_positive_rent() -> None:
    normalized, reasons = normalise_raw_record(_record(rent_frequency=None))
    assert normalized["rent_frequency"] == "MONTHLY"
    assert "rent_frequency_defaulted_to_monthly_after_missing_explicit_frequency" in reasons

    no_rent, _ = normalise_raw_record(_record(rent_amount=None, rent_frequency=None))
    assert no_rent["rent_frequency"] is None


def test_processed_output_has_exact_canonical_columns_and_contract_location() -> None:
    accepted, review, validation, metrics = compile_records([_record()])
    assert list(accepted.columns) == CANONICAL_COLUMNS
    assert accepted.loc[0, "location_zone"] == "OTHERS"
    assert accepted["room_count"].dtype.name == "Int64"
    assert accepted["rent_amount"].dtype.name == "Float64"
    assert len(review) == 1  # Documents the deliberate default-MVR decision.
    assert metrics["accepted_rental_records"] == 1
    assert validation.empty


def test_property_mv_raw_record_is_accepted_with_canonical_contract() -> None:
    accepted, review, validation, metrics = compile_records(
        [
            _record(
                source_name="property_mv",
                source_url="https://www.property.mv/property/2-bedroom-apartment-hm-556",
                currency_type="USD",
                location_zone="HULHUMALE",
                last_updated="July 1, 2026",
            )
        ]
    )

    assert list(accepted.columns) == CANONICAL_COLUMNS
    assert len(accepted) == 1
    assert accepted.loc[0, "source_name"] == "property_mv"
    assert accepted.loc[0, "source_url"] == "https://www.property.mv/property/2-bedroom-apartment-hm-556"
    assert accepted.loc[0, "last_updated"] == "2026-07-01"
    assert metrics["accepted_rental_records"] == 1
    assert review.empty
    assert validation.empty


def test_commercial_use_case_is_derived_from_raw_keywords() -> None:
    accepted, review, validation, metrics = compile_records(
        [
            _record(
                listing_type="COMMERCIAL",
                use_case=None,
                room_count=None,
                currency_type="MVR",
                source_url="https://www.property.mv/property/warehouse-ml-556",
                source_name="property_mv",
                raw_title="Warehouse for rent in Male",
                raw_description="Large commercial warehouse space.",
            )
        ]
    )

    assert len(accepted) == 1
    assert accepted.loc[0, "use_case"] == "Warehouse"
    assert metrics["accepted_rental_records"] == 1
    assert review.empty
    assert validation.empty


def test_land_title_overrides_unrelated_page_keywords() -> None:
    accepted, review, validation, metrics = compile_records(
        [
            _record(
                listing_type="COMMERCIAL",
                use_case="Retail",
                room_count=None,
                source_name="property_mv",
                source_url="https://www.property.mv/property/land-plot-for-rent-is-121",
                raw_title="Land Plot for Rent (IS 121)",
                raw_description="Land for rent. Nearby page navigation also mentions shops and offices.",
            )
        ]
    )

    assert len(accepted) == 1
    assert accepted.loc[0, "listing_type"] == "COMMERCIAL"
    assert accepted.loc[0, "use_case"] == "Land"
    assert "use_case_corrected_from_title_evidence" in review.loc[0, "review_reasons"]
    assert metrics["accepted_rental_records"] == 1
    assert validation.empty


def test_property_mv_commercial_slug_corrects_legacy_residential_type() -> None:
    accepted, review, validation, metrics = compile_records(
        [
            _record(
                listing_type="RESIDENTIAL",
                use_case=None,
                room_count=3,
                currency_type="MVR",
                source_url="https://www.property.mv/property/commercial-space-for-rent-ml-428",
                source_name="property_mv",
                raw_title="Commercial Space for Rent (ML 428)",
                raw_description="Bedrooms 3. Commercial office space in Maafannu.",
            )
        ]
    )

    assert len(accepted) == 1
    assert accepted.loc[0, "listing_type"] == "COMMERCIAL"
    assert accepted.loc[0, "use_case"] in {"Commercial Space", "Office Space"}
    assert "listing_type_corrected_from_property_mv_commercial_evidence" in review.loc[0, "review_reasons"]
    assert metrics["accepted_rental_records"] == 1
    assert validation.empty


def test_property_mv_sale_url_is_excluded_even_from_legacy_raw() -> None:
    accepted, review, _, metrics = compile_records(
        [
            _record(
                source_name="property_mv",
                source_url="https://www.property.mv/property/fully-furnished-3-br-for-sale-hm-122s",
                raw_title="Fully Furnished 3 BR for Sale",
                listing_type="RESIDENTIAL",
                rent_amount=6000000,
                currency_type="MVR",
                location_zone="HULHUMALE",
            )
        ]
    )

    assert accepted.empty
    assert metrics["sale_non_rental_records_excluded"] == 1
    assert "sale_or_non_rental_excluded" in review.loc[0, "review_reasons"]


def test_duplicate_raw_urls_keep_newest_scraped_record() -> None:
    source_url = "https://www.property.mv/property/rental-unit-ml-1"
    accepted, review, validation, metrics = compile_records(
        [
            _record(
                listing_type="UNKNOWN",
                source_name="property_mv",
                source_url=source_url,
                currency_type="MVR",
                scraped_at="2026-07-01T00:00:00Z",
            ),
            _record(
                listing_type="COMMERCIAL",
                use_case="Office Space",
                source_name="property_mv",
                source_url=source_url,
                currency_type="MVR",
                scraped_at="2026-07-08T00:00:00Z",
            ),
        ]
    )

    assert len(accepted) == 1
    assert accepted.loc[0, "listing_type"] == "COMMERCIAL"
    assert accepted.loc[0, "use_case"] == "Office Space"
    assert metrics["duplicate_urls_removed"] == 1
    assert validation.empty
    assert "duplicate_canonical_url_removed" in " | ".join(review["review_reasons"].tolist())


def test_missing_price_and_sale_are_excluded_and_reviewed() -> None:
    missing_price = _record(rent_amount=None, source_url="https://ibay.com.mv/missing-o124.html")
    sale = _record(
        is_rental_candidate=False,
        exclusion_reason="sale_or_non_rental",
        source_url="https://ibay.com.mv/sale-o125.html",
    )
    accepted, review, _, metrics = compile_records([missing_price, sale])
    assert accepted.empty
    assert metrics["records_with_missing_rent"] == 1
    assert metrics["sale_non_rental_records_excluded"] == 1
    combined_reasons = " | ".join(review["review_reasons"].tolist())
    assert "price_unavailable_excluded" in combined_reasons
    assert "sale_or_non_rental_excluded" in combined_reasons


def test_sqft_area_below_100_is_excluded() -> None:
    accepted, review, _, metrics = compile_records([_record(area_sqft=99)])
    assert accepted.empty
    assert metrics["records_with_sqft_area_below_100_excluded"] == 1
    assert "sqft_area_below_100_excluded" in review.loc[0, "review_reasons"]


def test_material_price_conflicts_and_non_recurring_tenure_payments_are_excluded() -> None:
    conflict = _record(
        rent_amount=1_000_000,
        source_url="https://ibay.com.mv/conflict-o126.html",
        review_reasons=["material_price_conflict: price field differs from described monthly rent"],
    )
    tenure = _record(
        rent_amount=750_000,
        rent_frequency=None,
        source_url="https://ibay.com.mv/tenure-o127.html",
        raw_title="Two room apartment for 13 years",
        raw_description="Upfront payment in full for the 13 year period.",
    )

    accepted, review, _, metrics = compile_records([conflict, tenure])

    assert accepted.empty
    assert metrics["records_with_material_price_conflict_excluded"] == 1
    assert metrics["records_with_multi_year_tenure_payment_excluded"] == 1
    reasons = " | ".join(review["review_reasons"].tolist())
    assert "unresolved_material_price_conflict_excluded" in reasons
    assert "multi_year_tenure_payment_not_monthly_rent_excluded" in reasons


def test_contact_number_used_as_rent_is_excluded() -> None:
    contact_price = _record(
        rent_amount=9_940_965,
        rent_frequency="DAILY",
        source_url="https://ibay.com.mv/contact-o128.html",
        raw_title="Daily rooms, contact 9940965",
    )

    accepted, review, _, metrics = compile_records([contact_price])

    assert accepted.empty
    assert metrics["records_with_contact_number_as_rent_excluded"] == 1
    assert "contact_number_used_as_rent_excluded" in review.loc[0, "review_reasons"]


def test_parser_flagged_contact_price_uses_the_contact_exclusion_metric() -> None:
    parser_flagged = _record(
        rent_amount=None,
        source_url="https://ibay.com.mv/contact-o130.html",
        review_reasons=["selected_price_matches_contact_number"],
    )

    accepted, review, _, metrics = compile_records([parser_flagged])

    assert accepted.empty
    assert metrics["records_with_contact_number_as_rent_excluded"] == 1
    assert "contact_number_used_as_rent_excluded" in review.loc[0, "review_reasons"]


def test_split_country_code_and_phone_number_cannot_become_rent() -> None:
    contact_price = _record(
        rent_amount=960_777,
        rent_frequency="DAILY",
        source_url="https://ibay.com.mv/contact-o129.html",
        raw_description="Please message +960 7772516 via Viber for details.",
    )

    accepted, review, _, metrics = compile_records([contact_price])

    assert accepted.empty
    assert metrics["records_with_contact_number_as_rent_excluded"] == 1
    assert "contact_number_used_as_rent_excluded" in review.loc[0, "review_reasons"]


def test_schema_aligned_excel_import_is_normalised_and_reviewed(tmp_path) -> None:
    import_dir = tmp_path / "schema_aligned"
    import_dir.mkdir()
    frame = pd.DataFrame(
        [
            {
                "listing_type": "COMMERCIAL",
                "room_count": None,
                "maid_room_count": None,
                "rent_amount": 20000,
                "currency_type": "MVR",
                "rent_frequency": "MONTHLY",
                "area_sqft": 1000,
                "location_zone": "OTHER",
                "address": "Imported unit",
                "last_updated": "2026-01-02",
                "status": "RENTED",
                "source_url": "dataset://comm_prop_dataset_v1/1",
                "source_name": "comm_prop_dataset_v1",
                "scraped_at": "2026-06-28T10:33:21Z",
            },
            {
                "listing_type": "COMMERCIAL",
                "room_count": None,
                "maid_room_count": None,
                "rent_amount": None,
                "currency_type": None,
                "rent_frequency": "MONTHLY",
                "area_sqft": 900,
                "location_zone": "MALE",
                "address": "Missing rent",
                "last_updated": "2026-01-03",
                "status": "RENTED",
                "source_url": "dataset://comm_prop_dataset_v1/2",
                "source_name": "comm_prop_dataset_v1",
                "scraped_at": "2026-06-28T10:33:21Z",
            },
        ],
        columns=CANONICAL_COLUMNS,
    )
    frame.to_excel(import_dir / "comm_prop_validated.xlsx", sheet_name="Standardized_Data", index=False)

    accepted, review, metrics = load_schema_aligned_imports(import_dir)

    assert list(accepted.columns) == CANONICAL_COLUMNS
    assert len(accepted) == 1
    assert accepted.loc[0, "location_zone"] == "OTHERS"
    assert accepted.loc[0, "source_name"] == "comm_prop_dataset_v1"
    assert len(review) == 1
    assert review.loc[0, "exclusion_reason"] == "price_unavailable_excluded"
    assert metrics["schema_aligned_import_files"] == 1
    assert metrics["schema_aligned_import_accepted_rows"] == 1
    assert metrics["schema_aligned_import_excluded_rows"] == 1


def test_schema_aligned_import_paths_only_use_requested_import_directory(tmp_path) -> None:
    import_dir = tmp_path / "schema_aligned"
    processed_dir = tmp_path / "processed"
    import_dir.mkdir()
    processed_dir.mkdir()
    canonical = import_dir / "comm_prop_validated.xlsx"
    legacy_duplicate = processed_dir / "comm_prop_validated.xlsx"
    canonical.touch()
    legacy_duplicate.touch()

    assert _schema_aligned_import_paths(import_dir) == [canonical]
