"""Canonical rental-listing schema and dtype helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

CANONICAL_COLUMNS = [
    "listing_type",
    "use_case",
    "room_count",
    "maid_room_count",
    "rent_amount",
    "currency_type",
    "rent_frequency",
    "area_sqft",
    "location_zone",
    "address",
    "last_updated",
    "status",
    "source_url",
    "source_name",
    "scraped_at",
]

ALLOWED_VALUES = {
    "listing_type": {"RESIDENTIAL", "COMMERCIAL", "UNKNOWN"},
    "currency_type": {"MVR", "USD"},
    "rent_frequency": {"DAILY", "MONTHLY"},
    "location_zone": {"MALE", "HULHUMALE", "OTHERS"},
    "status": {"AVAILABLE", "RENTED", "UNKNOWN"},
}

STRING_COLUMNS = [
    "listing_type",
    "use_case",
    "currency_type",
    "rent_frequency",
    "location_zone",
    "address",
    "last_updated",
    "status",
    "source_url",
    "source_name",
    "scraped_at",
]
INTEGER_COLUMNS = ["room_count", "maid_room_count"]
FLOAT_COLUMNS = ["rent_amount", "area_sqft"]


def empty_canonical_frame() -> pd.DataFrame:
    """Return an empty dataframe in exactly the canonical column order."""
    frame = pd.DataFrame(columns=CANONICAL_COLUMNS)
    return apply_canonical_dtypes(frame)


def _clean_string(value: Any) -> Any:
    if value is None or pd.isna(value):
        return pd.NA
    text = str(value).strip()
    return text if text else pd.NA


def apply_canonical_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with canonical columns, order, and nullable pandas dtypes."""
    result = frame.copy()
    for column in CANONICAL_COLUMNS:
        if column not in result.columns:
            result[column] = pd.NA

    result = result.loc[:, CANONICAL_COLUMNS]

    for column in STRING_COLUMNS:
        result[column] = result[column].map(_clean_string).astype("string")
    for column in INTEGER_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce").round().astype("Int64")
    for column in FLOAT_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("Float64")

    return result


def canonical_records(records: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Create a dtype-safe canonical dataframe from iterable records."""
    return apply_canonical_dtypes(pd.DataFrame(list(records)))
