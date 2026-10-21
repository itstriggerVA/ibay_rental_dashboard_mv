"""Bounded online refreshes that preserve the existing master and local imports."""

from datetime import UTC, datetime
import json
from pathlib import Path

import pandas as pd

from .preprocessing import compile_records
from .schemas import apply_canonical_dtypes, empty_canonical_frame
from .scraping import run_scrape_sources
from .settings import PROCESSED_DIR, RAW_DATA_DIR, REPORTS_DIR
from .sources import SCRAPER_SOURCES, normalise_source_selection
from .storage import dataset_lock, write_master


def upsert_records(existing: pd.DataFrame, incoming: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    existing = apply_canonical_dtypes(existing)
    incoming = apply_canonical_dtypes(incoming)
    keys = ["source_name", "source_url"]
    online = existing["source_name"].isin(SCRAPER_SOURCES)
    old_web = existing.loc[online].drop_duplicates(keys, keep="last")
    new_web = incoming.loc[incoming["source_name"].isin(SCRAPER_SOURCES)].drop_duplicates(keys, keep="last")
    old_keys = set(old_web[keys].itertuples(index=False, name=None))
    new_keys = set(new_web[keys].itertuples(index=False, name=None))
    merged_web = pd.concat([old_web, new_web], ignore_index=True).drop_duplicates(keys, keep="last")
    combined = pd.concat([existing.loc[~online], merged_web], ignore_index=True)
    return apply_canonical_dtypes(combined), {
        "inserted": len(new_keys - old_keys), "updated": len(new_keys & old_keys),
        "master_records": len(combined),
    }


def run_delta(*, pages: int = 3, sources=None, raw_dir: Path = RAW_DATA_DIR,
              processed_dir: Path = PROCESSED_DIR, reports_dir: Path = REPORTS_DIR) -> dict:
    if pages not in (2, 3):
        raise ValueError("Delta scrapes require 2 or 3 pages per category")
    results, errors, records = {}, {}, []
    for source in normalise_source_selection(sources):
        try:
            result = run_scrape_sources(sources=[source], max_listings=0, max_pages=pages, raw_dir=raw_dir)[source]
            results[source] = result
            with Path(result["raw_path"]).open(encoding="utf-8") as handle:
                records.extend(json.loads(line) for line in handle if line.strip())
        except Exception as exc:
            errors[source] = str(exc)
    incoming, review, validation, metrics = compile_records(records)
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    with dataset_lock(processed_dir):
        existing = empty_canonical_frame()
        for name in ("ibay_rentals_master.csv.gz", "ibay_rentals_master.csv", "ibay_rentals_master.parquet"):
            path = processed_dir / name
            if path.exists():
                existing = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
                break
        merged, counts = upsert_records(existing, incoming)
        outputs = write_master(merged, processed_dir) if len(incoming) else {}
    report = {"run_at": run_id, "pages_per_category": pages, "sources": results,
              "errors": errors, "metrics": {**metrics, **counts},
              "outputs": {key: str(path) for key, path in outputs.items()}}
    (reports_dir / f"delta_{run_id}.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    review.to_csv(reports_dir / f"delta_{run_id}_review.csv", index=False)
    validation.to_csv(reports_dir / f"delta_{run_id}_validation.csv", index=False)
    if errors:
        raise RuntimeError(f"Delta refresh had source failures: {errors}. Report: {reports_dir / f'delta_{run_id}.json'}")
    return report
