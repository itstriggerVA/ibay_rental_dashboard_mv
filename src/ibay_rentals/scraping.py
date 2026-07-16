"""Run selected web scrapers and keep their raw outputs source-scoped."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .settings import RAW_DATA_DIR
from .sources import SOURCE_IBAY, SOURCE_PROPERTY_MV, normalise_source_selection, source_raw_dir


def run_scrape_sources(
    *,
    sources: Sequence[str] | None = None,
    max_listings: int = 25,
    start_url: str | None = None,
    start_urls: Sequence[str] | None = None,
    raw_dir: Path = RAW_DATA_DIR,
) -> dict[str, Any]:
    selected_sources = normalise_source_selection(sources)
    if len(selected_sources) > 1 and (start_url or start_urls):
        raise ValueError("Custom start URLs are source-specific; select one source when using --start-url.")
    results: dict[str, Any] = {}

    for source in selected_sources:
        target_raw_dir = source_raw_dir(source, raw_dir)
        if source == SOURCE_IBAY:
            from .spiders.ibay_spider import run_scrape

            results[source] = run_scrape(
                max_listings=max_listings,
                start_url=start_url,
                start_urls=start_urls,
                raw_dir=target_raw_dir,
            )
        elif source == SOURCE_PROPERTY_MV:
            from .scrapers.property_mv import run_scrape

            results[source] = run_scrape(
                max_listings=max_listings,
                start_urls=start_urls,
                raw_dir=target_raw_dir,
            )
        else:  # pragma: no cover - guarded by normalise_source_selection.
            raise ValueError(f"Unsupported source {source!r}")

    return results
