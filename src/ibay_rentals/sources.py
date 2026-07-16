"""Known scraper sources and source-specific path helpers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .settings import RAW_DATA_DIR, RAW_IBAY_DIR, RAW_PROPERTY_MV_DIR

SOURCE_IBAY = "ibay"
SOURCE_PROPERTY_MV = "property_mv"
SCRAPER_SOURCES = (SOURCE_IBAY, SOURCE_PROPERTY_MV)
SOURCE_LABELS = {
    SOURCE_IBAY: "iBay",
    SOURCE_PROPERTY_MV: "Property.mv",
}
SOURCE_ALIASES = {
    "ibay": SOURCE_IBAY,
    "ibay.com.mv": SOURCE_IBAY,
    "property_mv": SOURCE_PROPERTY_MV,
    "property.mv": SOURCE_PROPERTY_MV,
    "propertymv": SOURCE_PROPERTY_MV,
}
DEFAULT_SOURCE_RAW_DIRS = {
    SOURCE_IBAY: RAW_IBAY_DIR,
    SOURCE_PROPERTY_MV: RAW_PROPERTY_MV_DIR,
}


def normalise_source_name(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().casefold()
    if not text:
        return None
    return SOURCE_ALIASES.get(text.replace("-", "_"), SOURCE_ALIASES.get(text))


def normalise_source_selection(values: Iterable[str] | None) -> list[str]:
    selected = list(values or SCRAPER_SOURCES)
    result: list[str] = []
    for value in selected:
        source = normalise_source_name(value)
        if source not in SCRAPER_SOURCES:
            raise ValueError(f"Unsupported source {value!r}; choose from {', '.join(SCRAPER_SOURCES)}")
        if source not in result:
            result.append(source)
    return result


def source_raw_dir(source_name: str, raw_root: Path = RAW_DATA_DIR) -> Path:
    source = normalise_source_name(source_name)
    if source is None:
        raise ValueError("source_name is required")
    if raw_root == RAW_DATA_DIR:
        return DEFAULT_SOURCE_RAW_DIRS[source]
    if raw_root.name == source:
        return raw_root
    return raw_root / source
