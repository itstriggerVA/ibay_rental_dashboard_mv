"""Raw JSONL compilation, canonicalisation, review outputs, and reporting."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import pandas as pd

from .classification import classify_listing_type_and_use_case, extract_commercial_use_case
from .schemas import CANONICAL_COLUMNS, apply_canonical_dtypes, empty_canonical_frame
from .settings import PROCESSED_DIR, PROCESSED_IMPORTS_DIR, RAW_DATA_DIR, REPORTS_DIR, REVIEW_DIR, SCHEMA_ALIGNED_IMPORT_DIR
from .sources import DEFAULT_SOURCE_RAW_DIRS, SCRAPER_SOURCES, normalise_source_name
from .validation import build_validation_issues

CATEGORY_COLUMNS = {
    "listing_type": {"RESIDENTIAL", "COMMERCIAL", "UNKNOWN"},
    "currency_type": {"MVR", "USD"},
    "rent_frequency": {"DAILY", "MONTHLY"},
    "location_zone": {"MALE", "HULHUMALE", "OTHERS"},
    "status": {"AVAILABLE", "RENTED", "UNKNOWN"},
}
SCHEMA_ALIGNED_IMPORT_SHEET = "Standardized_Data"
OPTIONAL_IMPORT_COLUMNS = {"use_case"}
CONTACT_NUMBER_PATTERN = re.compile(
    r"(?i)\b(?:call|contact|phone|mobile|tel|viber|whatsapp|message)\b[^\d]{0,24}"
    r"(?P<number>(?:\+?960[\s-]*)?\d(?:[\s-]?\d){6,8})"
)
MULTI_YEAR_TENURE_PATTERN = re.compile(
    r"(?i)\b(?:advance|adv\.?|upfront|payment\s+in\s+full|lease)\b.*?\b\d{1,2}\s*(?:years?|yrs?)\b"
    r"|\b\d{1,2}\s*(?:years?|yrs?)\b.*?\b(?:advance|adv\.?|upfront|payment\s+in\s+full|lease)\b"
)


def _clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None




def _valid_source_url(source_name: Any, value: Any) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    parts = urlsplit(text)
    source = normalise_source_name(source_name)
    if source == "ibay":
        return (
            parts.scheme in {"http", "https"}
            and parts.netloc.casefold() in {"ibay.com.mv", "www.ibay.com.mv"}
            and parts.path.endswith(".html")
        )
    if source == "property_mv":
        return (
            parts.scheme in {"http", "https"}
            and parts.netloc.casefold() in {"property.mv", "www.property.mv"}
            and "/property/" in parts.path
        )
    return False

def _normalise_category(value: Any, allowed: set[str], aliases: dict[str, str] | None = None) -> tuple[str | None, bool]:
    text = _clean_text(value)
    if text is None:
        return None, False
    normalized = text.upper()
    if aliases:
        normalized = aliases.get(normalized, normalized)
    if normalized in allowed:
        return normalized, False
    return None, True


def _normalise_last_updated(value: Any) -> tuple[str | None, bool]:
    text = _clean_text(value)
    if text is None:
        return None, False
    parsed = pd.to_datetime(
        text,
        errors="coerce",
        dayfirst=not bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", text)),
    )
    if pd.isna(parsed):
        return None, True
    return parsed.date().isoformat(), False


def _normalise_scraped_at(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    parsed = pd.to_datetime(text, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _normalise_use_case(value: Any, evidence: Iterable[Any], listing_type: Any) -> str | None:
    if listing_type != "COMMERCIAL":
        return None
    text = _clean_text(value)
    if text:
        return text
    return extract_commercial_use_case(str(item) for item in evidence if item is not None)


def _property_mv_sale_evidence(record: dict[str, Any]) -> bool:
    evidence = " ".join(
        str(value)
        for value in (
            record.get("source_url"),
            record.get("raw_title"),
        )
        if value is not None
    ).casefold()
    has_sale_marker = bool(re.search(r"\b(?:for[\s-]+sale|sold|investment)\b", evidence))
    has_rent_marker = bool(re.search(r"\b(?:for[\s-]+rent|rented|monthly|daily)\b", evidence))
    return has_sale_marker and not has_rent_marker


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value))


def _market_price_exclusion_reason(
    raw: dict[str, Any],
    normalized: dict[str, Any],
    review_reasons: Iterable[str],
) -> str | None:
    """Identify non-recurring or contact-number values that cannot represent rent."""
    if any("material_price_conflict" in str(reason) for reason in review_reasons):
        return "unresolved_material_price_conflict"
    if any("selected_price_matches_contact_number" in str(reason) for reason in review_reasons):
        return "contact_number_used_as_rent"

    rent = normalized.get("rent_amount")
    if rent is None:
        return None
    rent_digits = str(int(round(float(rent)))) if float(rent).is_integer() else _digits(rent)
    if 6 <= len(rent_digits) <= 10:
        for text in (raw.get("raw_title"), raw.get("raw_description")):
            if not text:
                continue
            for match in CONTACT_NUMBER_PATTERN.finditer(str(text)):
                contact_digits = _digits(match.group("number"))
                if rent_digits == contact_digits or contact_digits.startswith(rent_digits) or contact_digits.endswith(rent_digits):
                    return "contact_number_used_as_rent"

    raw_frequency = _clean_text(raw.get("rent_frequency"))
    evidence = re.sub(r"\s+", " ", " ".join(str(raw.get(field) or "") for field in ("raw_title", "raw_description")))
    if raw_frequency is None and MULTI_YEAR_TENURE_PATTERN.search(evidence):
        return "multi_year_tenure_payment_not_monthly_rent"
    return None


def _schema_aligned_import_paths(import_dir: Path = SCHEMA_ALIGNED_IMPORT_DIR) -> list[Path]:
    paths: list[Path] = []
    if import_dir.exists():
        paths.extend(sorted(path for path in import_dir.iterdir() if path.suffix.casefold() in {".xlsx", ".csv"}))
    return paths


def _read_schema_aligned_import(path: Path) -> pd.DataFrame:
    if path.suffix.casefold() == ".xlsx":
        return pd.read_excel(path, sheet_name=SCHEMA_ALIGNED_IMPORT_SHEET)
    if path.suffix.casefold() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported schema-aligned import file type: {path}")


def _normalise_schema_aligned_import_frame(frame: pd.DataFrame, source_file: str) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Validate and normalize an already schema-shaped external dataset."""
    missing_columns = set(CANONICAL_COLUMNS) - set(frame.columns)
    extra_columns = set(frame.columns) - set(CANONICAL_COLUMNS)
    if extra_columns or (missing_columns - OPTIONAL_IMPORT_COLUMNS):
        missing = sorted(missing_columns)
        extra = sorted(extra_columns)
        raise ValueError(f"{source_file} does not match canonical schema. Missing={missing}; extra={extra}")
    for column in missing_columns:
        frame[column] = pd.NA

    imported = frame.loc[:, CANONICAL_COLUMNS].copy()
    review_rows: list[dict[str, Any]] = []
    accepted_rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    metrics = {
        "schema_aligned_import_rows": len(imported),
        "schema_aligned_import_accepted_rows": 0,
        "schema_aligned_import_excluded_rows": 0,
    }

    for row_index, raw_row in imported.iterrows():
        row = raw_row.to_dict()
        reasons: list[str] = []

        for column, allowed in CATEGORY_COLUMNS.items():
            value, invalid = _normalise_category(row.get(column), allowed, {"OTHER": "OTHERS"} if column == "location_zone" else None)
            row[column] = value
            if invalid:
                reasons.append(f"invalid_{column}_normalised_to_blank")

        for column in ("room_count", "maid_room_count"):
            value = pd.to_numeric(row.get(column), errors="coerce")
            row[column] = None if pd.isna(value) else int(round(float(value)))
        for column in ("rent_amount", "area_sqft"):
            value = pd.to_numeric(row.get(column), errors="coerce")
            row[column] = None if pd.isna(value) else float(value)

        row["address"] = _clean_text(row.get("address"))
        row["source_url"] = _clean_text(row.get("source_url"))
        row["source_name"] = _clean_text(row.get("source_name"))
        row["use_case"] = _normalise_use_case(
            row.get("use_case"),
            [row.get("address"), row.get("source_url")],
            row.get("listing_type"),
        )
        row["last_updated"], date_error = _normalise_last_updated(row.get("last_updated"))
        row["scraped_at"] = _normalise_scraped_at(row.get("scraped_at"))
        if date_error:
            reasons.append("last_updated_parse_failure")

        source_url = row.get("source_url")
        if source_url and source_url in seen_urls:
            reasons.append("duplicate_import_source_url_removed")
        elif source_url:
            seen_urls.add(source_url)

        exclusion_reason = None
        if not source_url:
            exclusion_reason = "missing_source_url_excluded"
        elif "duplicate_import_source_url_removed" in reasons:
            exclusion_reason = "duplicate_import_source_url_removed"
        elif not row.get("source_name"):
            exclusion_reason = "missing_source_name_excluded"
        elif row.get("rent_amount") is None:
            exclusion_reason = "price_unavailable_excluded"
        elif row["rent_amount"] <= 0:
            exclusion_reason = "non_positive_rent_excluded"
        elif not row.get("currency_type"):
            exclusion_reason = "missing_currency_excluded"
        elif not row.get("rent_frequency"):
            exclusion_reason = "missing_rent_frequency_excluded"
        if row.get("area_sqft") is not None and row["area_sqft"] <= 0:
            row["area_sqft"] = None
            reasons.append("non_positive_area_normalised_to_blank")
        if exclusion_reason is None and row.get("area_sqft") is not None and row["area_sqft"] < 100:
            exclusion_reason = "sqft_area_below_100_excluded"

        if exclusion_reason:
            reasons.append(exclusion_reason)
            metrics["schema_aligned_import_excluded_rows"] += 1
            review_rows.append(
                {
                    "source_file": source_file,
                    "row_index": row_index,
                    "source_url": row.get("source_url"),
                    "source_name": row.get("source_name"),
                    "review_reasons": " | ".join(dict.fromkeys(reasons)),
                    "exclusion_reason": exclusion_reason,
                }
            )
            continue

        accepted_rows.append(row)

    accepted = apply_canonical_dtypes(pd.DataFrame(accepted_rows)) if accepted_rows else empty_canonical_frame()
    review = pd.DataFrame(
        review_rows,
        columns=["source_file", "row_index", "source_url", "source_name", "review_reasons", "exclusion_reason"],
    )
    metrics["schema_aligned_import_accepted_rows"] = len(accepted)
    return accepted, review, metrics


def load_schema_aligned_imports(import_dir: Path = SCHEMA_ALIGNED_IMPORT_DIR) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Load external canonical datasets kept outside scraper raw output."""
    accepted_frames: list[pd.DataFrame] = []
    review_frames: list[pd.DataFrame] = []
    metrics = {
        "schema_aligned_import_files": 0,
        "schema_aligned_import_rows": 0,
        "schema_aligned_import_accepted_rows": 0,
        "schema_aligned_import_excluded_rows": 0,
    }
    for path in _schema_aligned_import_paths(import_dir):
        raw = _read_schema_aligned_import(path)
        accepted, review, file_metrics = _normalise_schema_aligned_import_frame(raw, path.name)
        accepted_frames.append(accepted)
        review_frames.append(review)
        metrics["schema_aligned_import_files"] += 1
        for key, value in file_metrics.items():
            metrics[key] += value

    accepted_all = pd.concat(accepted_frames, ignore_index=True) if accepted_frames else empty_canonical_frame()
    review_all = pd.concat(review_frames, ignore_index=True) if review_frames else pd.DataFrame(
        columns=["source_file", "row_index", "source_url", "source_name", "review_reasons", "exclusion_reason"]
    )
    return apply_canonical_dtypes(accepted_all), review_all, metrics


def _extract_review_reasons(record: dict[str, Any]) -> list[str]:
    reasons = record.get("review_reasons", [])
    if isinstance(reasons, str):
        try:
            parsed = json.loads(reasons)
            reasons = parsed if isinstance(parsed, list) else [reasons]
        except json.JSONDecodeError:
            reasons = [reasons]
    return [str(reason) for reason in reasons or [] if str(reason).strip()]


def normalise_raw_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Map one raw record into the canonical contract and retain review causes."""
    review_reasons = _extract_review_reasons(record)
    normalized: dict[str, Any] = {column: None for column in CANONICAL_COLUMNS}

    aliases = {"OTHER": "OTHERS"}
    for column, allowed in CATEGORY_COLUMNS.items():
        value, invalid = _normalise_category(record.get(column), allowed, aliases if column == "location_zone" else None)
        normalized[column] = value
        if invalid:
            review_reasons.append(f"invalid_{column}_normalised_to_blank")

    normalized["source_name"] = normalise_source_name(record.get("source_name")) or _clean_text(record.get("source_name"))
    if normalized["source_name"] not in SCRAPER_SOURCES:
        review_reasons.append("wrong_source_name")

    for column in ("room_count", "maid_room_count"):
        value = pd.to_numeric(record.get(column), errors="coerce")
        normalized[column] = None if pd.isna(value) else int(round(float(value)))
    for column in ("rent_amount", "area_sqft"):
        value = pd.to_numeric(record.get(column), errors="coerce")
        normalized[column] = None if pd.isna(value) else float(value)

    normalized["address"] = _clean_text(record.get("address"))
    normalized["source_url"] = _clean_text(record.get("source_url"))
    classification_evidence = [
        record.get("raw_title"),
        record.get("raw_description"),
        record.get("address"),
        record.get("source_url"),
    ]
    if normalized["source_name"] == "property_mv":
        inferred_type, _ = classify_listing_type_and_use_case(
            classification_evidence,
            strong_commercial_overrides_residential=True,
        )
        if inferred_type == "COMMERCIAL" and normalized.get("listing_type") != "COMMERCIAL":
            normalized["listing_type"] = "COMMERCIAL"
            review_reasons.append("listing_type_corrected_from_property_mv_commercial_evidence")
    title_use_case = extract_commercial_use_case([record.get("raw_title")])
    if title_use_case:
        if normalized.get("listing_type") != "COMMERCIAL":
            normalized["listing_type"] = "COMMERCIAL"
            review_reasons.append("listing_type_corrected_from_title_use_case_evidence")
        source_use_case = _clean_text(record.get("use_case"))
        if source_use_case and source_use_case != title_use_case:
            review_reasons.append("use_case_corrected_from_title_evidence")
        normalized["use_case"] = title_use_case
    else:
        normalized["use_case"] = _normalise_use_case(
            record.get("use_case"),
            classification_evidence,
            normalized.get("listing_type"),
        )
    normalized["last_updated"], date_error = _normalise_last_updated(record.get("last_updated", record.get("post_date")))
    normalized["scraped_at"] = _normalise_scraped_at(record.get("scraped_at"))
    if date_error or record.get("date_parse_failed"):
        review_reasons.append("last_updated_parse_failure")
    if record.get("date_parse_failed"):
        normalized["last_updated"] = None

    # Default MVR only after a positive rent exists and no explicit currency is present.
    if normalized["rent_amount"] is not None and normalized["rent_amount"] > 0 and not normalized["currency_type"]:
        normalized["currency_type"] = "MVR"
        review_reasons.append("currency_defaulted_to_mvr_after_missing_explicit_currency")
    if normalized["rent_amount"] is not None and normalized["rent_amount"] > 0 and not normalized["rent_frequency"]:
        normalized["rent_frequency"] = "MONTHLY"
        review_reasons.append("rent_frequency_defaulted_to_monthly_after_missing_explicit_frequency")

    return normalized, list(dict.fromkeys(review_reasons))


def _raw_record_sort_key(record: dict[str, Any]) -> tuple[int, str]:
    scraped_at = pd.to_datetime(record.get("scraped_at"), errors="coerce", utc=True)
    timestamp = -1 if pd.isna(scraped_at) else int(scraped_at.timestamp())
    return timestamp, str(record.get("_raw_file", ""))


def _raw_jsonl_paths(raw_dirs: Path | Iterable[Path]) -> list[Path]:
    if isinstance(raw_dirs, Path):
        roots = [raw_dirs]
    else:
        roots = list(raw_dirs)
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        paths.extend(root.glob("*.jsonl"))
        if root == RAW_DATA_DIR:
            for source_dir in DEFAULT_SOURCE_RAW_DIRS.values():
                paths.extend(source_dir.glob("*.jsonl"))
        else:
            for source_name in SCRAPER_SOURCES:
                paths.extend((root / source_name).glob("*.jsonl"))
    return sorted(path for path in paths if not path.name.endswith("_failed_urls.jsonl"))


def read_raw_jsonl(raw_dir: Path | Iterable[Path] = RAW_DATA_DIR) -> list[dict[str, Any]]:
    """Read all JSONL crawl outputs, ignoring stats files and malformed blank lines."""
    records: list[dict[str, Any]] = []
    for path in _raw_jsonl_paths(raw_dir):
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
                if not isinstance(decoded, dict):
                    raise ValueError(f"Expected an object in {path} line {line_number}")
                decoded["_raw_file"] = path.name
                records.append(decoded)
    return records


def _make_review_frame(raw_records: Iterable[dict[str, Any]], decisions: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for raw, decision in zip(raw_records, decisions, strict=True):
        reasons = decision["review_reasons"]
        if reasons:
            rows.append(
                {
                    "source_url": raw.get("source_url"),
                    "listing_id": raw.get("listing_id"),
                    "raw_title": raw.get("raw_title"),
                    "selected_price_token": raw.get("selected_price_token"),
                    "selected_price_source": raw.get("selected_price_source"),
                    "price_candidates": json.dumps(raw.get("price_candidates", []), ensure_ascii=False),
                    "review_reasons": " | ".join(reasons),
                    "exclusion_reason": raw.get("exclusion_reason"),
                    "raw_file": raw.get("_raw_file"),
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "source_url",
            "listing_id",
            "raw_title",
            "selected_price_token",
            "selected_price_source",
            "price_candidates",
            "review_reasons",
            "exclusion_reason",
            "raw_file",
        ],
    )


def compile_records(raw_records: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Create accepted canonical records, review rows, validation rows, and summary metrics."""
    ordered_raw_records = sorted(raw_records, key=_raw_record_sort_key, reverse=True)
    normalized_records: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    excluded_rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    duplicate_urls_removed = 0

    for raw in ordered_raw_records:
        normalized, review_reasons = normalise_raw_record(raw)
        source_url = normalized.get("source_url")
        exclusion_reason = raw.get("exclusion_reason")
        is_rental = raw.get("is_rental_candidate") is not False
        if normalized.get("source_name") == "property_mv" and _property_mv_sale_evidence(raw):
            is_rental = False
            exclusion_reason = "sale_or_non_rental"

        if source_url and source_url in seen_urls:
            duplicate_urls_removed += 1
            review_reasons.append("duplicate_canonical_url_removed")
            excluded_rows.append({"reason": "duplicate_canonical_url_removed", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        if source_url:
            seen_urls.add(source_url)

        if exclusion_reason == "sale_or_non_rental":
            review_reasons.append("sale_or_non_rental_excluded")
            excluded_rows.append({"reason": "sale_or_non_rental_excluded", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        if not is_rental:
            reason = f"{exclusion_reason or 'not_rental_candidate'}_excluded"
            review_reasons.append(reason)
            excluded_rows.append({"reason": reason, **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        if normalized.get("source_name") not in SCRAPER_SOURCES:
            review_reasons.append("wrong_source_name_excluded")
            excluded_rows.append({"reason": "wrong_source_name_excluded", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        if not _valid_source_url(normalized.get("source_name"), normalized.get("source_url")):
            review_reasons.append("invalid_source_url_excluded")
            excluded_rows.append({"reason": "invalid_source_url_excluded", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        market_price_exclusion = _market_price_exclusion_reason(raw, normalized, review_reasons)
        if market_price_exclusion:
            review_reasons.append(f"{market_price_exclusion}_excluded")
            excluded_rows.append({"reason": f"{market_price_exclusion}_excluded", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        if normalized.get("rent_amount") is None:
            review_reasons.append("price_unavailable_excluded")
            excluded_rows.append({"reason": "price_unavailable_excluded", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        if normalized["rent_amount"] <= 0:
            review_reasons.append("non_positive_rent_excluded")
            excluded_rows.append({"reason": "non_positive_rent_excluded", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue
        if normalized.get("area_sqft") is not None and normalized["area_sqft"] <= 0:
            normalized["area_sqft"] = None
            review_reasons.append("non_positive_area_normalised_to_blank")
        if normalized.get("area_sqft") is not None and normalized["area_sqft"] < 100:
            review_reasons.append("sqft_area_below_100_excluded")
            excluded_rows.append({"reason": "sqft_area_below_100_excluded", **normalized})
            decisions.append({"review_reasons": review_reasons})
            continue

        normalized_records.append(normalized)
        decisions.append({"review_reasons": review_reasons})

    accepted = apply_canonical_dtypes(pd.DataFrame(normalized_records)) if normalized_records else empty_canonical_frame()
    review = _make_review_frame(ordered_raw_records, decisions)
    validation = build_validation_issues(accepted, raw_records)

    summary = {
        "raw_records": len(raw_records),
        "accepted_rental_records": len(accepted),
        "duplicate_urls_removed": duplicate_urls_removed,
        "sale_non_rental_records_excluded": sum(row["reason"] == "sale_or_non_rental_excluded" for row in excluded_rows),
        "records_with_missing_rent": sum(row["reason"] == "price_unavailable_excluded" for row in excluded_rows),
        "records_with_sqft_area_below_100_excluded": sum(row["reason"] == "sqft_area_below_100_excluded" for row in excluded_rows),
        "records_with_material_price_conflict_excluded": sum(
            row["reason"] == "unresolved_material_price_conflict_excluded" for row in excluded_rows
        ),
        "records_with_contact_number_as_rent_excluded": sum(
            row["reason"] == "contact_number_used_as_rent_excluded" for row in excluded_rows
        ),
        "records_with_multi_year_tenure_payment_excluded": sum(
            row["reason"] == "multi_year_tenure_payment_not_monthly_rent_excluded" for row in excluded_rows
        ),
        "records_sent_for_review": len(review),
        "excluded_records": len(excluded_rows),
    }
    return accepted, review, validation, summary


def _latest_crawl_stats(raw_dir: Path | Iterable[Path]) -> dict[str, Any]:
    roots = [raw_dir] if isinstance(raw_dir, Path) else list(raw_dir)
    latest_by_dir: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        candidates = list(root.glob("*_crawl_stats.json"))
        if root == RAW_DATA_DIR:
            for source_dir in DEFAULT_SOURCE_RAW_DIRS.values():
                candidates.extend(source_dir.glob("*_crawl_stats.json"))
        else:
            for source_name in SCRAPER_SOURCES:
                candidates.extend((root / source_name).glob("*_crawl_stats.json"))
        grouped: dict[Path, list[Path]] = {}
        for path in candidates:
            grouped.setdefault(path.parent, []).append(path)
        latest_by_dir.extend(sorted(paths)[-1] for paths in grouped.values())
    stat_files = sorted(latest_by_dir)
    if not stat_files:
        return {}
    combined = {"pages_discovered": 0, "pages_fetched": 0}
    for path in stat_files:
        try:
            stats = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        combined["pages_discovered"] += int(stats.get("pages_discovered", 0))
        combined["pages_fetched"] += int(stats.get("pages_fetched", 0))
    return combined


def create_compilation_summary(
    accepted: pd.DataFrame,
    metrics: dict[str, Any],
    raw_dir: Path | Iterable[Path] = RAW_DATA_DIR,
) -> pd.DataFrame:
    """Create a long-form CSV report with counts and field-level missingness."""
    stats = _latest_crawl_stats(raw_dir)
    rows: list[dict[str, Any]] = [
        {"section": "crawl", "metric": "pages_discovered", "value": stats.get("pages_discovered", 0)},
        {"section": "crawl", "metric": "pages_fetched", "value": stats.get("pages_fetched", 0)},
    ]
    rows.extend({"section": "pipeline", "metric": key, "value": value} for key, value in metrics.items())
    rows.extend(
        {"section": "missingness", "metric": column, "value": int(accepted[column].isna().sum())}
        for column in CANONICAL_COLUMNS
    )
    for column in ("listing_type", "currency_type", "rent_frequency", "location_zone", "status"):
        counts = accepted[column].fillna("<blank>").value_counts(dropna=False)
        rows.extend(
            {"section": f"counts_by_{column}", "metric": str(value), "value": int(count)}
            for value, count in counts.items()
        )
    return pd.DataFrame(rows, columns=["section", "metric", "value"])


def write_outputs(
    accepted: pd.DataFrame,
    extraction_review: pd.DataFrame,
    validation_issues: pd.DataFrame,
    summary: pd.DataFrame,
    imported: pd.DataFrame | None = None,
    import_review: pd.DataFrame | None = None,
    *,
    processed_dir: Path = PROCESSED_DIR,
    processed_imports_dir: Path = PROCESSED_IMPORTS_DIR,
    review_dir: Path = REVIEW_DIR,
    reports_dir: Path = REPORTS_DIR,
) -> dict[str, Path]:
    """Write all required artifacts. Parquet requires pyarrow from project dependencies."""
    processed_dir.mkdir(parents=True, exist_ok=True)
    processed_imports_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    csv_path = processed_dir / "ibay_rentals_master.csv"
    csv_gzip_path = processed_dir / "ibay_rentals_master.csv.gz"
    parquet_path = processed_dir / "ibay_rentals_master.parquet"
    extraction_review_path = review_dir / "ibay_extraction_review.csv"
    imported_path = processed_imports_dir / "schema_aligned_imports.csv"
    import_review_path = review_dir / "schema_aligned_import_review.csv"
    validation_path = review_dir / "ibay_validation_issues.csv"
    summary_path = reports_dir / "ibay_compilation_summary.csv"

    accepted.to_csv(csv_path, index=False)
    accepted.to_csv(csv_gzip_path, index=False, compression="gzip")
    try:
        accepted.to_parquet(parquet_path, index=False, engine="pyarrow")
    except ImportError as exc:
        raise RuntimeError("pyarrow is required to create the processed Parquet dataset") from exc
    extraction_review.to_csv(extraction_review_path, index=False)
    (imported if imported is not None else empty_canonical_frame()).to_csv(imported_path, index=False)
    (
        import_review
        if import_review is not None
        else pd.DataFrame(columns=["source_file", "row_index", "source_url", "source_name", "review_reasons", "exclusion_reason"])
    ).to_csv(import_review_path, index=False)
    validation_issues.to_csv(validation_path, index=False)
    summary.to_csv(summary_path, index=False)
    return {
        "master_csv": csv_path,
        "master_csv_gzip": csv_gzip_path,
        "master_parquet": parquet_path,
        "extraction_review": extraction_review_path,
        "schema_aligned_imports": imported_path,
        "schema_aligned_import_review": import_review_path,
        "validation_issues": validation_path,
        "summary": summary_path,
    }


def run_preprocessing(raw_dir: Path | Iterable[Path] = RAW_DATA_DIR) -> dict[str, Any]:
    """Read raw JSONL and write canonical, review, validation, and report outputs."""
    raw_records = read_raw_jsonl(raw_dir)
    accepted, review, _, metrics = compile_records(raw_records)
    imported, import_review, import_metrics = load_schema_aligned_imports()
    combined = apply_canonical_dtypes(pd.concat([accepted, imported], ignore_index=True))
    metrics.update(import_metrics)
    metrics["dashboard_master_records"] = len(combined)
    validation = build_validation_issues(combined, raw_records)
    summary = create_compilation_summary(combined, metrics, raw_dir)
    outputs = write_outputs(combined, review, validation, summary, imported=imported, import_review=import_review)
    return {
        "run_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
        "outputs": {name: str(path) for name, path in outputs.items()},
    }
