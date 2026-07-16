from __future__ import annotations

from ibay_rentals.sources import SCRAPER_SOURCES, normalise_source_selection


def test_default_source_selection_uses_all_sources() -> None:
    assert normalise_source_selection(None) == list(SCRAPER_SOURCES)

