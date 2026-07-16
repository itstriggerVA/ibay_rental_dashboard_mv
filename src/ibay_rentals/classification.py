"""Shared listing classification helpers."""

from __future__ import annotations

from collections.abc import Iterable
import re
import unicodedata

COMMERCIAL_USE_CASE_PATTERNS = (
    ("Warehouse", (r"\bwarehouse\b", r"\bgodown\b", r"\bstorage\b")),
    ("Restaurant", (r"\brestaurant\b", r"\bcafe\b", r"\bcaf[eé]\b")),
    ("Office Space", (r"\boffice\s+space\b", r"\boffice\b")),
    ("Showroom", (r"\bshowroom\b",)),
    ("Retail", (r"\bretail\b", r"\bshop\s+space\b", r"\bshop\b", r"\bstore\b")),
    ("Commercial Space", (r"\bcommercial\s+space\b", r"\bbusiness\s+space\b")),
    ("Land", (r"\bland\s+for\s+rent\b", r"\bland\b")),
)
RESIDENTIAL_PATTERNS = (
    r"\bapartments?\b",
    r"\bflats?\b",
    r"\brooms?\b",
    r"\bstudio\b",
    r"\bhouses?\b",
    r"\bvillas?\b",
    r"\bbedrooms?\b",
    r"\bbr\b",
    r"\bpenthouse\b",
    r"\bguest\s+houses?\b",
)
STRONG_COMMERCIAL_PATTERNS = (
    r"\bcommercial\s+(?:space|office|unit|property|building)\b",
    r"\b(?:warehouse|godown|showroom)\b",
    r"\boffice\s+space\b",
    r"\bshop\s+space\b",
    r"\bretail\s+space\b",
)


def normalise_for_classification(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_marks.replace("-", " ").replace("_", " ")).strip().casefold()


def extract_commercial_use_case(texts: Iterable[str | None]) -> str | None:
    evidence = normalise_for_classification(" ".join(text for text in texts if text))
    for label, patterns in COMMERCIAL_USE_CASE_PATTERNS:
        if any(re.search(pattern, evidence, re.IGNORECASE) for pattern in patterns):
            return label
    return None


def classify_listing_type_and_use_case(
    texts: Iterable[str | None],
    *,
    strong_commercial_overrides_residential: bool = False,
) -> tuple[str, str | None]:
    values = list(texts)
    evidence = normalise_for_classification(" ".join(text for text in values if text))
    use_case = extract_commercial_use_case(values)
    residential = any(re.search(pattern, evidence, re.IGNORECASE) for pattern in RESIDENTIAL_PATTERNS)
    strong_commercial = any(re.search(pattern, evidence, re.IGNORECASE) for pattern in STRONG_COMMERCIAL_PATTERNS)
    if use_case and not residential:
        return "COMMERCIAL", use_case
    if use_case and strong_commercial_overrides_residential and strong_commercial:
        return "COMMERCIAL", use_case
    if residential and not use_case:
        return "RESIDENTIAL", None
    if (
        residential
        and use_case == "Restaurant"
        and re.search(r"\bguest\s+houses?\b", evidence, re.IGNORECASE)
        and not re.search(r"\bshop\b", evidence, re.IGNORECASE)
    ):
        return "RESIDENTIAL", None
    return "UNKNOWN", use_case if use_case else None
