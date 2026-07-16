"""Validation rules that report data-quality problems without silently deleting them."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from .schemas import ALLOWED_VALUES, CANONICAL_COLUMNS
from .sources import normalise_source_name

SUSPICIOUS_LOW_MONTHLY_RENT = {"MVR": 500.0, "USD": 20.0}
SUSPICIOUS_HIGH_RENT = {
    ("MVR", "DAILY"): 100_000.0,
    ("USD", "DAILY"): 10_000.0,
    ("MVR", "MONTHLY"): 500_000.0,
    ("USD", "MONTHLY"): 50_000.0,
}


def _is_valid_source_url(source_name: Any, value: Any) -> bool:
    if value is None or pd.isna(value) or not str(value).strip():
        return False
    parts = urlsplit(str(value))
    if parts.scheme == "dataset":
        return bool(parts.netloc or parts.path)
    source = normalise_source_name(source_name)
    if source == "property_mv":
        return (
            parts.scheme in {"http", "https"}
            and parts.netloc.casefold() in {"property.mv", "www.property.mv"}
            and "/property/" in parts.path
        )
    return (
        parts.scheme in {"http", "https"}
        and parts.netloc.casefold() in {"ibay.com.mv", "www.ibay.com.mv"}
        and bool(parts.path)
    )


def _normalised_issue_row(index: Any, row: pd.Series, issue_type: str, severity: str, detail: str) -> dict[str, Any]:
    return {
        "record_index": index,
        "source_url": row.get("source_url"),
        "issue_type": issue_type,
        "severity": severity,
        "detail": detail,
        "listing_type": row.get("listing_type"),
        "rent_amount": row.get("rent_amount"),
        "currency_type": row.get("currency_type"),
        "rent_frequency": row.get("rent_frequency"),
        "room_count": row.get("room_count"),
        "area_sqft": row.get("area_sqft"),
    }


def build_validation_issues(
    frame: pd.DataFrame,
    raw_records: Iterable[dict[str, Any]] | None = None,
) -> pd.DataFrame:
    """Return structured validation findings for canonical records and raw evidence."""
    issues: list[dict[str, Any]] = []

    for index, row in frame.iterrows():
        if not _is_valid_source_url(row.get("source_name"), row.get("source_url")):
            issues.append(_normalised_issue_row(index, row, "invalid_source_url", "error", "source_url is missing or invalid"))

        source_name = row.get("source_name")
        if pd.isna(source_name) or not str(source_name).strip():
            issues.append(_normalised_issue_row(index, row, "missing_source_name", "error", "source_name is required for provenance"))

        for column, allowed in ALLOWED_VALUES.items():
            if column == "source_name":
                continue
            value = row.get(column)
            if pd.notna(value) and value not in allowed:
                issues.append(
                    _normalised_issue_row(
                        index,
                        row,
                        f"invalid_{column}",
                        "error",
                        f"{column} has unsupported value {value!r}",
                    )
                )

        rent = row.get("rent_amount")
        area = row.get("area_sqft")
        room_count = row.get("room_count")
        if pd.notna(rent) and float(rent) <= 0:
            issues.append(_normalised_issue_row(index, row, "non_positive_rent", "error", "rent_amount must be positive"))
        if pd.notna(area) and float(area) <= 0:
            issues.append(_normalised_issue_row(index, row, "non_positive_area", "error", "area_sqft must be positive"))
        frequency = row.get("rent_frequency")
        listing_type = row.get("listing_type")
        use_case = row.get("use_case")
        if pd.notna(rent) and pd.isna(frequency):
            issues.append(_normalised_issue_row(index, row, "rent_without_frequency", "warning", "rent has no DAILY or MONTHLY frequency"))
        if pd.notna(listing_type) and listing_type == "RESIDENTIAL" and pd.isna(room_count):
            issues.append(_normalised_issue_row(index, row, "residential_missing_room_count", "warning", "residential listing has no room_count"))
        if pd.notna(listing_type) and listing_type == "COMMERCIAL" and (pd.isna(use_case) or not str(use_case).strip()):
            issues.append(_normalised_issue_row(index, row, "commercial_missing_use_case", "warning", "commercial listing has no use_case keyword"))
        if pd.notna(room_count) and float(room_count) > 10:
            issues.append(_normalised_issue_row(index, row, "unusual_room_count", "warning", "room_count is above 10"))
        if pd.notna(room_count) and float(room_count) <= 0:
            issues.append(_normalised_issue_row(index, row, "impossible_room_count", "error", "room_count must be positive"))

        if pd.notna(rent) and pd.notna(frequency) and frequency == "MONTHLY":
            currency = row.get("currency_type")
            threshold = SUSPICIOUS_LOW_MONTHLY_RENT.get(currency) if pd.notna(currency) else None
            if threshold is not None and float(rent) < threshold:
                issues.append(
                    _normalised_issue_row(
                        index,
                        row,
                        "suspiciously_low_monthly_rent",
                        "warning",
                        f"monthly {currency} rent is below the conservative review threshold of {threshold:g}",
                    )
                )

        if pd.notna(rent) and pd.notna(frequency):
            currency = row.get("currency_type")
            threshold = SUSPICIOUS_HIGH_RENT.get((currency, frequency)) if pd.notna(currency) else None
            if threshold is not None and float(rent) > threshold:
                issues.append(
                    _normalised_issue_row(
                        index,
                        row,
                        "suspiciously_high_rent",
                        "warning",
                        f"{frequency.lower()} {currency} rent exceeds the conservative review threshold of {threshold:g}",
                    )
                )

        last_updated = row.get("last_updated")
        if pd.notna(last_updated):
            parsed = pd.to_datetime(last_updated, errors="coerce")
            if pd.isna(parsed):
                issues.append(_normalised_issue_row(index, row, "last_updated_parse_failure", "warning", "last_updated is not parseable"))

    if raw_records is not None:
        raw_by_url: dict[str, dict[str, Any]] = {
            str(record.get("source_url")): record for record in raw_records if record.get("source_url")
        }
        for index, row in frame.iterrows():
            raw = raw_by_url.get(str(row.get("source_url")))
            if not raw:
                continue
            for reason in raw.get("review_reasons", []) or []:
                if "price_conflict" in str(reason):
                    issues.append(_normalised_issue_row(index, row, "price_conflict", "warning", str(reason)))
            if raw.get("date_parse_failed"):
                issues.append(_normalised_issue_row(index, row, "last_updated_parse_failure", "warning", "raw last_updated could not be parsed"))

    issue_columns = [
        "record_index",
        "source_url",
        "issue_type",
        "severity",
        "detail",
        "listing_type",
        "rent_amount",
        "currency_type",
        "rent_frequency",
        "room_count",
        "area_sqft",
    ]
    return pd.DataFrame(issues, columns=issue_columns)


def validate_canonical_contract(frame: pd.DataFrame) -> list[str]:
    """Return contract violations useful for unit tests and dashboard loading."""
    problems: list[str] = []
    if list(frame.columns) != CANONICAL_COLUMNS:
        problems.append("canonical column order does not match CANONICAL_COLUMNS")
    for column, allowed in ALLOWED_VALUES.items():
        if column not in frame:
            problems.append(f"missing column: {column}")
            continue
        invalid = frame[column].dropna()[~frame[column].dropna().isin(allowed)]
        if not invalid.empty:
            problems.append(f"invalid values in {column}: {sorted(set(invalid.astype(str)))}")
    return problems
