"""Orchestrate scrape then preprocessing without adding databases or schedulers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .preprocessing import run_preprocessing
from .scraping import run_scrape_sources
from .settings import RAW_DATA_DIR
from .sources import normalise_source_selection, source_raw_dir


def run_pipeline(
    max_listings: int = 0,
    sources: Sequence[str] | None = None,
    start_url: str | None = None,
    start_urls: Sequence[str] | None = None,
    raw_dir: Path = RAW_DATA_DIR,
) -> dict[str, Any]:
    """Collect a crawl, then compile its JSONL output into dashboard data."""
    selected_sources = normalise_source_selection(sources)
    scrape_result = run_scrape_sources(
        sources=selected_sources,
        max_listings=max_listings,
        start_url=start_url,
        start_urls=start_urls,
        raw_dir=raw_dir,
    )
    preprocess_result = run_preprocessing(raw_dir=[source_raw_dir(source, raw_dir) for source in selected_sources])
    return {"scrape": scrape_result, "preprocess": preprocess_result}
