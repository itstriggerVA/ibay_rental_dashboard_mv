"""Windows-friendly command line entry point for the rental dashboard pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from . import __version__
from .preprocessing import run_preprocessing
from .settings import RAW_DATA_DIR
from .sources import SCRAPER_SOURCES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ibay-rentals",
        description="Rental dashboard pipeline for iBay and Property.mv",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scrape = subparsers.add_parser("scrape", help="Crawl public rental detail pages for selected sources")
    scrape.add_argument(
        "--max-listings",
        "--max_listing",
        dest="max_listings",
        type=int,
        default=0,
        help="Maximum detail pages to fetch; use 0 for a full scrape (default: 0)",
    )
    scrape.add_argument(
        "--source",
        action="append",
        choices=SCRAPER_SOURCES,
        default=None,
        help="Scraper source to run. Repeat for multiple sources. Defaults to all sources.",
    )
    scrape.add_argument(
        "--start-url",
        action="append",
        default=None,
        help="Optional source-specific rental search/category URL. Repeat to crawl multiple custom categories.",
    )
    scrape.add_argument("--raw-dir", type=Path, default=RAW_DATA_DIR, help="Raw JSONL root directory")

    preprocess = subparsers.add_parser("preprocess", help="Compile raw JSONL into processed dashboard datasets")
    preprocess.add_argument("--raw-dir", type=Path, default=RAW_DATA_DIR, help="Raw JSONL input root directory")

    pipeline = subparsers.add_parser("pipeline", help="Run scrape then preprocess")
    pipeline.add_argument(
        "--max-listings",
        "--max_listing",
        dest="max_listings",
        type=int,
        default=0,
        help="Maximum detail pages to fetch; use 0 for a full scrape (default: 0)",
    )
    pipeline.add_argument(
        "--source",
        action="append",
        choices=SCRAPER_SOURCES,
        default=None,
        help="Scraper source to run. Repeat for multiple sources. Defaults to all sources.",
    )
    pipeline.add_argument(
        "--start-url",
        action="append",
        default=None,
        help="Optional source-specific rental search/category URL. Repeat to crawl multiple custom categories.",
    )
    pipeline.add_argument("--raw-dir", type=Path, default=RAW_DATA_DIR, help="Raw JSONL root directory")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "scrape":
        from .scraping import run_scrape_sources

        result = run_scrape_sources(
            sources=args.source,
            max_listings=args.max_listings,
            start_urls=args.start_url,
            raw_dir=args.raw_dir,
        )
    elif args.command == "preprocess":
        result = run_preprocessing(raw_dir=args.raw_dir)
    else:
        from .pipeline import run_pipeline

        result = run_pipeline(
            sources=args.source,
            max_listings=args.max_listings,
            start_urls=args.start_url,
            raw_dir=args.raw_dir,
        )
    print(json.dumps(result, indent=2, default=str))
