import json

import pandas as pd
import pytest

from ibay_rentals import delta
from ibay_rentals.schemas import CANONICAL_COLUMNS, canonical_records
from ibay_rentals.storage import dataset_lock, write_master


def test_upsert_updates_inserts_preserves_history_and_local_rows():
    old = canonical_records([
        {"source_name": "ibay", "source_url": "https://ibay.com.mv/a", "rent_amount": 10},
        {"source_name": "ibay", "source_url": "https://ibay.com.mv/older", "rent_amount": 20},
        {"source_name": "local", "source_url": "file://import", "rent_amount": 30},
    ])
    incoming = canonical_records([
        {"source_name": "ibay", "source_url": "https://ibay.com.mv/a", "rent_amount": 15},
        {"source_name": "property_mv", "source_url": "https://www.property.mv/property/new", "rent_amount": 40},
    ])
    merged, counts = delta.upsert_records(old, incoming)
    assert list(merged.columns) == CANONICAL_COLUMNS
    assert counts == {"inserted": 1, "updated": 1, "master_records": 4}
    assert sorted(merged.rent_amount) == [15, 20, 30, 40]
    again, counts = delta.upsert_records(merged, incoming)
    pd.testing.assert_frame_equal(again, merged)
    assert counts["inserted"] == 0


def test_delta_shapes_fresh_raw_preserves_seed_and_does_not_import(tmp_path, monkeypatch):
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps({
        "source_name": "ibay", "source_url": "https://ibay.com.mv/house-o12345.html",
        "raw_title": "Apartment for rent", "rent_amount": 12000, "currency_type": "MVR",
        "rent_frequency": "MONTHLY", "is_rental_candidate": True,
        "scraped_at": "2026-10-21T00:00:00Z",
    }) + "\n")
    calls = []
    def scrape(**kwargs):
        calls.append(kwargs)
        return {"ibay": {"raw_path": str(raw)}}
    monkeypatch.setattr(delta, "run_scrape_sources", scrape)
    processed = tmp_path / "processed"
    write_master(canonical_records([{"source_name": "local", "source_url": "file://seed", "rent_amount": 50}]), processed)
    report = delta.run_delta(sources=["ibay"], pages=2, processed_dir=processed, reports_dir=tmp_path / "reports")
    frame = pd.read_csv(processed / "ibay_rentals_master.csv.gz")
    assert len(frame) == 2
    assert report["metrics"]["inserted"] == 1
    assert calls[0]["max_pages"] == 2
    assert calls[0]["max_listings"] == 0
    pd.testing.assert_frame_equal(pd.read_parquet(processed / "ibay_rentals_master.parquet"), canonical_records(frame.to_dict("records")))


def test_dataset_lock_prevents_overlapping_writers(tmp_path):
    with dataset_lock(tmp_path):
        with pytest.raises(OSError):
            with dataset_lock(tmp_path):
                pytest.fail("Second writer acquired dataset lock")
