"""BeautifulSoup extraction for individual iBay listing-detail pages.

Scrapy handles requests and pagination. This module only parses one detail-page
response and emits traceable raw evidence. Rent, room, area, location, and type
extraction is deliberately bounded to an identified listing-detail container so
Similar Items, navigation, adverts, and seller cards cannot supply values. The
``Last Updated`` metadata is page-level evidence and is read from the document
text before non-primary widgets are removed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import re
import unicodedata
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
from bs4 import BeautifulSoup, Tag

from .classification import classify_listing_type_and_use_case

BANNED_SECTION_MARKERS = (
    "similar items",
    "similar item",
    "related listings",
    "related listing",
    "recommended listings",
    "recommended listing",
    "recommended items",
    "you may also like",
    "advertisement",
    "advertisements",
    "seller profile",
    "promoted",
)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
WORD_NUMBERS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

PRICE_TOKEN_RE = re.compile(
    r"""
    (?P<currency>USD|US\$|MVR|MRF|RF\.?|\$)?
    \s*
    (?P<amount>
        (?:\d{1,3}(?:[,\s]\d{3})+(?:\.\d{1,2})?)
        |
        (?:\d{2,7}(?:\.\d{1,2})?)
    )
    \s*(?P<terminal>/-)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
AREA_RE = re.compile(
    r"(?ix)\b(?P<amount>\d{1,3}(?:,\d{3})*|\d+(?:\.\d+)?)\s*"
    r"(?:sq\.?\s*ft\.?|sqft|square\s*feet|ft²)\b"
)
FREQUENCY_PATTERNS = {
    "DAILY": re.compile(r"(?i)(?:\bdaily\b|\bper\s*day\b|/\s*day\b|\bshort\s*stay\b)"),
    "MONTHLY": re.compile(r"(?i)(?:\bmonthly\b|\bper\s*month\b|/\s*month\b|\bmonthly\s+rent\b)"),
}
ROOM_PATTERN = re.compile(
    r"(?ix)\b(?P<value>\d{1,2}|" + "|".join(WORD_NUMBERS) + r")\s*"
    r"(?:bed(?:room)?s?|rooms?)\b"
)
ROOM_LABEL_PATTERN = re.compile(
    r"(?ix)\b(?:bed(?:room)?s?|rooms?)\s*[:\-]?\s*"
    r"(?P<value>\d{1,2}|" + "|".join(WORD_NUMBERS) + r")\b"
)
MAID_ROOM_PATTERN = re.compile(
    r"""(?ix)
    (?:\bmaid(?:'s)?\s*(?:bed)?rooms?\s*[:\-]?\s*
        (?P<label_value>\d{1,2}|"""
    + "|".join(WORD_NUMBERS)
    + r""")\b)
    |
    (?:(?P<prefix_value>\d{1,2}|"""
    + "|".join(WORD_NUMBERS)
    + r""")\s*maid(?:'s)?\s*(?:bed)?rooms?\b)
    """
)
DATE_TOKEN_PATTERN = r"(?:\d{1,2}[-/ ][A-Za-z]{3,9}[-/ ]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
DATE_LABEL_RE = re.compile(
    r"(?is)\b(?:posted(?:\s+on)?|updated|last\s+updated)\b\s*[:\-]?\s*"
    rf"(?P<date>{DATE_TOKEN_PATTERN})"
)
LAST_UPDATED_RE = re.compile(
    rf"(?is)\blast\s+updated\b\s*:\s*(?P<date>{DATE_TOKEN_PATTERN})"
)
LISTING_ID_RE = re.compile(r"-o(?P<listing_id>\d+)\.html$", re.IGNORECASE)
VALID_LISTING_PATH_RE = re.compile(r"-o\d+\.html$", re.IGNORECASE)
SECONDARY_PAYMENT_LABEL_RE = re.compile(
    r"(?i)(?:security\s+deposit|deposit|advance|booking\s+fee|service\s+fee|commission)"
    r"\s*[:\-]?\s*(?:USD|US\$|MVR|MRF|RF\.?|\$)?\s*$"
)
MOJIBAKE_REPLACEMENTS = {
    "Ã©": "é",
    "Ã‰": "É",
    "Ã¨": "è",
    "Ã¡": "á",
    "Ã¢": "â",
    "Ã´": "ô",
    "Ã¼": "ü",
    "Â ": " ",
    "Â": "",
    "â€”": " ",
    "â€“": " ",
    "â€œ": '"',
    "â€": '"',
    "â€™": "'",
}


@dataclass(frozen=True)
class PriceCandidate:
    """A possible rent token and the bounded evidence that produced it."""

    raw_token: str
    amount: float
    source: str
    context: str
    explicit: bool
    currency_hint: str | None
    is_secondary_payment: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise_for_matching(value: str) -> str:
    """Case-fold text, remove diacritics, and make class/id tokens comparable."""
    for mojibake, replacement in MOJIBAKE_REPLACEMENTS.items():
        value = value.replace(mojibake, replacement)
    if any(marker in value for marker in ("Ã", "Â")):
        try:
            value = value.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    without_marks = without_marks.replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", without_marks).strip().casefold()


def canonicalize_url(url: str) -> str:
    """Remove fragments and tracking query parameters while preserving content path."""
    parts = urlsplit(url)
    cleaned_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in TRACKING_QUERY_KEYS and not key.casefold().startswith("utm_")
    ]
    cleaned_query = urlencode(cleaned_pairs, doseq=True)
    host = parts.netloc.casefold()
    scheme = parts.scheme.casefold() or "https"
    return urlunsplit((scheme, host, parts.path, cleaned_query, ""))


def listing_id_from_url(url: str) -> str | None:
    match = LISTING_ID_RE.search(urlsplit(url).path)
    return match.group("listing_id") if match else None


def is_valid_ibay_listing_url(url: str) -> bool:
    """Accept only iBay detail URLs with the observed ``-o<id>.html`` ending."""
    parts = urlsplit(url)
    return (
        parts.scheme in {"http", "https"}
        and parts.netloc.casefold() in {"ibay.com.mv", "www.ibay.com.mv"}
        and bool(VALID_LISTING_PATH_RE.search(parts.path))
    )


def unwrap_saved_view_source_html(html: str) -> str:
    """Reconstruct source from Chrome's *view-source:* save format when present.

    The crawler receives ordinary iBay HTML. This compatibility layer exists so
    genuine browser-saved responses can be used as regression fixtures without
    rewriting them by hand. Non-view-source responses are returned unchanged.
    """
    outer = BeautifulSoup(html, "html.parser")
    line_cells = outer.select("td.line-content")
    is_view_source_save = bool(line_cells) and (
        outer.select_one(".line-gutter-backdrop") is not None
        or outer.select_one(".line-wrap-control") is not None
    )
    if not is_view_source_save:
        return html

    reconstructed = "\n".join(cell.get_text("", strip=False) for cell in line_cells)
    return reconstructed if "<html" in reconstructed.casefold() else html


def _tag_text(tag: Tag | None) -> str:
    if tag is None:
        return ""
    return " ".join(tag.stripped_strings)


def _attribute_text(tag: Tag) -> str:
    values: list[str] = []
    for key in ("id", "class", "aria-label", "data-section", "data-testid"):
        value = tag.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(values)


def _is_banned(tag: Tag) -> bool:
    """Identify non-primary widgets without examining ancestor body text.

    Checking a whole ancestor's text is unsafe: a valid listing root can contain
    a Similar Items widget later in the tree. Only the element's own attributes
    and directly owned headings are inspected.
    """
    attr_haystack = normalise_for_matching(_attribute_text(tag))
    if any(marker in attr_haystack for marker in BANNED_SECTION_MARKERS):
        return True

    direct_heading_text = " ".join(
        _tag_text(child)
        for child in tag.find_all(["h2", "h3", "h4", "h5", "h6"], recursive=False)
    )
    heading_haystack = normalise_for_matching(direct_heading_text)
    return any(marker in heading_haystack for marker in BANNED_SECTION_MARKERS)


def _remove_non_primary_sections(soup: BeautifulSoup) -> None:
    """Remove obvious non-listing sections before selecting the primary scope."""
    for tag in soup.find_all(["script", "style", "noscript", "svg", "nav", "footer", "aside"]):
        tag.decompose()

    banned = [tag for tag in soup.find_all(True) if _is_banned(tag)]
    for tag in reversed(banned):
        if tag.parent is not None:
            tag.decompose()


def _contains_listing_signal(tag: Tag) -> bool:
    attrs = normalise_for_matching(_attribute_text(tag))
    if "details page" in attrs or "details page product info" in attrs:
        return True
    if tag.find(["h1", "h5"]) is not None:
        return True
    if tag.find(lambda candidate: isinstance(candidate, Tag) and "item info table" in normalise_for_matching(_attribute_text(candidate))):
        return True
    return "price" in attrs or "listing" in attrs


def _choose_primary_container(soup: BeautifulSoup) -> tuple[Tag | None, str]:
    """Return a bounded listing-detail scope; never fall back to ``<body>``."""
    observed_ibay_selectors = (
        (".details-page", "ibay_details_page"),
        (".details-page_product-info", "ibay_product_info"),
    )
    for selector, strategy in observed_ibay_selectors:
        candidates = [tag for tag in soup.select(selector) if not _is_banned(tag) and _contains_listing_signal(tag)]
        if candidates:
            candidates.sort(key=lambda tag: len(_tag_text(tag)), reverse=True)
            return candidates[0], strategy

    semantic_candidates = [
        tag
        for tag in soup.select("main, article, [role='main']")
        if not _is_banned(tag) and _contains_listing_signal(tag)
    ]
    if semantic_candidates:
        semantic_candidates.sort(key=lambda tag: len(_tag_text(tag)), reverse=True)
        return semantic_candidates[0], "semantic_main_container"

    tables = [
        tag
        for tag in soup.find_all(True)
        if "item info table" in normalise_for_matching(_attribute_text(tag)) and not _is_banned(tag)
    ]
    for table in tables:
        ancestor = table.parent
        while ancestor is not None and isinstance(ancestor, Tag) and ancestor.name not in {"body", "html"}:
            if _contains_listing_signal(ancestor) and 30 <= len(_tag_text(ancestor)) <= 12000:
                return ancestor, "item_info_table_ancestor"
            ancestor = ancestor.parent

    headings = [tag for tag in soup.find_all(["h1", "h5"]) if not _is_banned(tag)]
    for heading in headings:
        if normalise_for_matching(_tag_text(heading)) in {"description", "similar items", "general"}:
            continue
        ancestor = heading.parent
        while ancestor is not None and isinstance(ancestor, Tag) and ancestor.name not in {"body", "html"}:
            text_length = len(_tag_text(ancestor))
            if 30 <= text_length <= 12000:
                return ancestor, "heading_ancestor"
            ancestor = ancestor.parent

    return None, "not_found"


def _find_title(container: Tag) -> tuple[str | None, str | None]:
    for selector in (".iw-details-heading h5", ".details-page_product-info h5"):
        for heading in container.select(selector):
            text = _tag_text(heading)
            if text and normalise_for_matching(text) not in {"description", "similar items", "general"}:
                return text, "main_title"

    for tag_name in ("h1", "h5"):
        for heading in container.find_all(tag_name):
            text = _tag_text(heading)
            if text and normalise_for_matching(text) not in {"description", "similar items", "general"}:
                return text, "main_title"
    return None, None


def _find_description(container: Tag) -> tuple[str | None, str | None]:
    for candidate in container.select(".details-page_product-desc"):
        if _is_banned(candidate):
            continue
        paragraphs = [_tag_text(paragraph) for paragraph in candidate.find_all("p") if _tag_text(paragraph)]
        if paragraphs:
            return "\n".join(paragraphs), "main_description"
        text = _tag_text(candidate)
        if len(text) >= 20:
            return text, "main_description"

    tagged_candidates = [
        tag
        for tag in container.find_all(True)
        if any(word in normalise_for_matching(_attribute_text(tag)) for word in ("description", "detail"))
        and not _is_banned(tag)
    ]
    for candidate in tagged_candidates:
        text = _tag_text(candidate)
        if len(text) >= 20:
            return text, "main_description"

    paragraphs = [_tag_text(tag) for tag in container.find_all("p") if _tag_text(tag) and not _is_banned(tag)]
    if paragraphs:
        return "\n".join(paragraphs), "main_description"
    return None, None


def _normalise_amount(token: str) -> float | None:
    cleaned = token.strip().replace(" ", "").replace(",", "")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    return amount if amount > 0 else None


def _currency_from_text(text: str) -> str | None:
    normalized = normalise_for_matching(text)
    usd = bool(re.search(r"(?:\busd\b|\bus\$|\$)", normalized, re.IGNORECASE))
    mvr = bool(re.search(r"(?:\bmvr\b|\bmrf\b|\brf\.?\b)", normalized, re.IGNORECASE))
    if usd and mvr:
        return "CONFLICT"
    if usd:
        return "USD"
    if mvr:
        return "MVR"
    return None


def _price_context_is_forbidden(context: str) -> bool:
    return bool(
        re.search(
            r"(?i)(?:\bcall\b|\bphone\b|\bmobile\b|\btel\b|\bviber\b|\bwhatsapp\b|"
            r"\bcontact\b|\blisting\s*(?:id|no)?\b|\bsq\.?\s*ft\b|\bsqft\b|"
            r"\bsquare\s*feet\b|\bfloor\b|\b20\d{2}\b)",
            context,
        )
    )


def _candidate_is_bare_price_allowed(match: re.Match[str], source_text: str) -> bool:
    raw_amount = match.group("amount")
    digits = re.sub(r"\D", "", raw_amount)
    if not (4 <= len(digits) <= 6):
        return False
    before = source_text[max(0, match.start() - 30) : match.start()]
    after = source_text[match.end() : match.end() + 30]
    return not _price_context_is_forbidden(before + " " + after)


def _is_secondary_payment(match: re.Match[str], source_text: str) -> bool:
    preceding_text = source_text[max(0, match.start() - 75) : match.start()]
    following_text = source_text[match.end() : match.end() + 45]
    follows_secondary_label = re.match(
        r"(?i)^\s*[),\-]*\s*(?:security\s+deposit|deposit|advance|booking\s+fee|service\s+fee|commission)\b",
        following_text,
    )
    # Some listings write ``(MVR 30000 advance ...)``. The parenthesis makes
    # this a descriptor of the amount, unlike a later separate “Security
    # Deposit” line after a valid rent value.
    inline_secondary = bool(follows_secondary_label and "(" in preceding_text[-24:])
    return bool(SECONDARY_PAYMENT_LABEL_RE.search(preceding_text) or inline_secondary)


def extract_price_candidates(text: str | None, source: str) -> list[PriceCandidate]:
    """Extract plausible rent tokens from one permitted primary-content field."""
    if not text:
        return []

    candidates: list[PriceCandidate] = []
    for match in PRICE_TOKEN_RE.finditer(text):
        raw_token = match.group(0).strip()
        amount = _normalise_amount(match.group("amount"))
        if amount is None:
            continue

        context = text[max(0, match.start() - 45) : match.end() + 45]
        currency_hint = _currency_from_text(raw_token)
        explicit = bool(currency_hint or match.group("terminal")) or bool(
            re.search(r"(?i)\b(?:rent|price|monthly|daily|per\s*month|per\s*day)\b", context)
        )

        # A number without a currency marker or terminal ``/-`` must pass the
        # stricter bare-number rule even if nearby prose says “daily” or “rent”.
        # Otherwise telephone numbers in titles can be mistaken for a price.
        if not currency_hint and not match.group("terminal") and not _candidate_is_bare_price_allowed(match, text):
            continue
        if _price_context_is_forbidden(context) and not currency_hint and not match.group("terminal"):
            continue

        candidates.append(
            PriceCandidate(
                raw_token=raw_token,
                amount=amount,
                source=source,
                context=context.strip(),
                explicit=explicit,
                currency_hint=None if currency_hint == "CONFLICT" else currency_hint,
                is_secondary_payment=_is_secondary_payment(match, text),
            )
        )
    return _deduplicate_candidates(candidates)


def _deduplicate_candidates(candidates: Iterable[PriceCandidate]) -> list[PriceCandidate]:
    seen: set[tuple[str, float, str]] = set()
    result: list[PriceCandidate] = []
    for candidate in candidates:
        key = (candidate.source, candidate.amount, candidate.raw_token)
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _extract_price_block_texts(container: Tag) -> list[str]:
    texts: list[str] = []
    for selector in (".iw-price-row .price", ".iw-d-price-col .price"):
        for tag in container.select(selector):
            if not _is_banned(tag):
                text = _tag_text(tag)
                if text:
                    texts.append(text)
    if texts:
        return list(dict.fromkeys(texts))

    for tag in container.find_all(True):
        attrs = normalise_for_matching(_attribute_text(tag))
        if "price" in attrs and not _is_banned(tag):
            text = _tag_text(tag)
            if text:
                texts.append(text)
    return list(dict.fromkeys(texts))


def _extract_attribute_pairs(container: Tag) -> list[tuple[str, str]]:
    """Read label/value pairs from the observed iBay ``.item-info-table``."""
    pairs: list[tuple[str, str]] = []
    for info_table in container.select(".item-info-table"):
        if _is_banned(info_table):
            continue
        for row in info_table.select("tr"):
            cells = [_tag_text(cell) for cell in row.find_all(["th", "td"], recursive=False)]
            if len(cells) >= 2 and cells[0] and cells[1]:
                pair = (cells[0], cells[1])
                if pair not in pairs:
                    pairs.append(pair)
    return pairs


def _extract_attribute_texts(container: Tag, pairs: Iterable[tuple[str, str]]) -> list[str]:
    """Return only structured/listing-attribute evidence, not whole-page prose."""
    texts = [f"{label}: {value}" for label, value in pairs]
    if texts:
        return list(dict.fromkeys(texts))

    for tag in container.find_all(["table", "dl", "div", "ul", "ol"]):
        attrs = normalise_for_matching(_attribute_text(tag))
        is_structured = tag.name in {"table", "dl"} or "item info table" in attrs
        if is_structured and not _is_banned(tag):
            text = _tag_text(tag)
            if text:
                texts.append(text)
    return list(dict.fromkeys(texts))


def _material_difference(left: float, right: float) -> bool:
    return abs(left - right) > max(1000.0, min(left, right) * 0.25)


def _matching_title_description_candidate(
    title_candidates: list[PriceCandidate], description_candidates: list[PriceCandidate]
) -> PriceCandidate | None:
    for title_candidate in title_candidates:
        if title_candidate.is_secondary_payment:
            continue
        for description_candidate in description_candidates:
            if description_candidate.is_secondary_payment:
                continue
            if abs(title_candidate.amount - description_candidate.amount) < 0.01:
                return title_candidate
    return None


def select_price(
    price_block_candidates: list[PriceCandidate],
    title_candidates: list[PriceCandidate],
    description_candidates: list[PriceCandidate],
    attribute_candidates: list[PriceCandidate],
) -> tuple[PriceCandidate | None, list[str]]:
    """Apply the documented price hierarchy and report material disagreements."""
    review_reasons: list[str] = []
    allowed_primary = [candidate for candidate in price_block_candidates if not candidate.is_secondary_payment]
    allowed_title = [candidate for candidate in title_candidates if not candidate.is_secondary_payment]
    allowed_description = [candidate for candidate in description_candidates if not candidate.is_secondary_payment]
    allowed_attribute = [candidate for candidate in attribute_candidates if not candidate.is_secondary_payment]

    primary = next((candidate for candidate in allowed_primary if candidate.explicit), None)
    title_description_match = _matching_title_description_candidate(allowed_title, allowed_description)
    title = next((candidate for candidate in allowed_title if candidate.explicit), None) or (
        allowed_title[0] if allowed_title else None
    )
    description = next((candidate for candidate in allowed_description if candidate.explicit), None) or (
        allowed_description[0] if allowed_description else None
    )
    attribute = next((candidate for candidate in allowed_attribute if candidate.explicit), None)

    if primary:
        support = title_description_match
        if support and _material_difference(primary.amount, support.amount):
            review_reasons.append(
                "material_price_conflict: primary price block disagrees with matching title and description evidence"
            )
            return support, review_reasons
        competing = [candidate for candidate in (title, description, attribute) if candidate]
        if any(_material_difference(primary.amount, candidate.amount) for candidate in competing):
            review_reasons.append("material_price_conflict: primary price block differs from another permitted source")
        return primary, review_reasons

    if title_description_match:
        return title_description_match, review_reasons
    if title:
        return title, review_reasons
    if description:
        return description, review_reasons
    if attribute:
        return attribute, review_reasons
    return None, review_reasons


def prefer_usd_when_mixed_currency(
    selected_price: PriceCandidate | None,
    candidates: Iterable[PriceCandidate],
) -> tuple[PriceCandidate | None, list[str]]:
    """Prefer explicit USD rent evidence when permitted candidates mix USD and MVR."""
    usable = [candidate for candidate in candidates if not candidate.is_secondary_payment]
    currencies = {candidate.currency_hint for candidate in usable if candidate.currency_hint}
    if not {"USD", "MVR"}.issubset(currencies):
        return selected_price, []

    usd_candidates = [candidate for candidate in usable if candidate.currency_hint == "USD"]
    preferred = next((candidate for candidate in usd_candidates if candidate.explicit), None) or usd_candidates[0]
    if selected_price is not preferred:
        return preferred, ["mixed_currency_price_candidates: explicit USD rent candidate selected over MVR candidate"]
    return selected_price, []


def _currency_for_selected_price(
    selected_price: PriceCandidate | None,
    candidates: Iterable[PriceCandidate],
) -> tuple[str | None, list[str]]:
    """Choose currency from the selected price's own evidence before defaulting MVR."""
    if selected_price is None:
        return None, []
    if selected_price.currency_hint == "USD":
        return "USD", []

    matching_candidates = [
        candidate
        for candidate in candidates
        if not candidate.is_secondary_payment and abs(candidate.amount - selected_price.amount) < 0.01
    ]
    currency_hints = {candidate.currency_hint for candidate in matching_candidates if candidate.currency_hint}
    if selected_price.currency_hint:
        currency_hints.add(selected_price.currency_hint)

    if len(currency_hints) > 1:
        return None, ["currency_conflict: selected rent amount has conflicting explicit currency evidence"]
    if currency_hints:
        return next(iter(currency_hints)), []
    return "MVR", []


def _frequency_in_text(text: str | None) -> set[str]:
    if not text:
        return set()
    return {name for name, pattern in FREQUENCY_PATTERNS.items() if pattern.search(text)}


def extract_frequency(title: str | None, body_texts: Iterable[str]) -> tuple[str | None, list[str]]:
    title_values = _frequency_in_text(title)
    body_values = set().union(*(_frequency_in_text(text) for text in body_texts)) if body_texts else set()
    review_reasons: list[str] = []
    if len(title_values) > 1 or len(body_values) > 1 or (title_values and body_values and title_values != body_values):
        review_reasons.append("frequency_conflict: permitted title and detail evidence disagree")
    if title_values:
        return sorted(title_values)[0], review_reasons
    if body_values:
        return sorted(body_values)[0], review_reasons
    return None, review_reasons


def _word_or_number_to_int(value: str) -> int | None:
    normalized = value.casefold().strip()
    if normalized in WORD_NUMBERS:
        return WORD_NUMBERS[normalized]
    try:
        numeric = int(normalized)
    except ValueError:
        return None
    return numeric if 0 < numeric < 100 else None


def _extract_maid_rooms(text: str | None) -> int | None:
    if not text:
        return None
    match = MAID_ROOM_PATTERN.search(text)
    if not match:
        return None
    return _word_or_number_to_int(match.group("label_value") or match.group("prefix_value"))


def _extract_general_rooms(text: str | None) -> int | None:
    if not text:
        return None
    normalized = normalise_for_matching(text)
    if re.search(r"\bstudio\b", normalized):
        return 1
    for pattern in (ROOM_LABEL_PATTERN, ROOM_PATTERN):
        for match in pattern.finditer(text):
            before = text[max(0, match.start() - 18) : match.start()]
            if re.search(r"(?i)maid|bath", before):
                continue
            value = _word_or_number_to_int(match.group("value"))
            if value:
                return value
    return None


def _url_slug_text(url: str) -> str:
    path = urlsplit(url).path.rsplit("/", 1)[-1]
    path = re.sub(r"-o\d+\.html$", "", path, flags=re.IGNORECASE)
    return path.replace("-", " ")


def extract_rooms(
    title: str | None,
    description: str | None,
    attribute_texts: Iterable[str],
    source_url: str,
) -> tuple[int | None, int | None, str | None]:
    """Extract general and maid rooms without using prices, IDs, or phone numbers."""
    attributes = list(attribute_texts)
    for text in attributes:
        room_count = _extract_general_rooms(text)
        maid_count = _extract_maid_rooms(text)
        if room_count is not None:
            return room_count, maid_count, "main_attribute_table"

    for source, text in (("main_title", title), ("main_description", description)):
        room_count = _extract_general_rooms(text)
        maid_count = _extract_maid_rooms(text)
        if room_count is not None:
            return room_count, maid_count, source

    slug_room_count = _extract_general_rooms(_url_slug_text(source_url))
    if slug_room_count is not None:
        return slug_room_count, None, "canonical_url_slug"
    return None, _extract_maid_rooms(" ".join(attributes + [title or "", description or ""])), None


def _area_from_attribute_pairs(pairs: Iterable[tuple[str, str]]) -> float | None:
    for label, value in pairs:
        normalized_label = normalise_for_matching(label)
        if not re.search(r"\b(?:square feet|sq ft|sqft|area)\b", normalized_label):
            continue
        direct = _normalise_amount(value)
        if direct:
            return direct
        number = re.search(r"\d{1,3}(?:,\d{3})*|\d+(?:\.\d+)?", value)
        if number:
            amount = _normalise_amount(number.group(0))
            if amount:
                return amount
    return None


def extract_area(
    texts: Iterable[tuple[str, str]],
    attribute_pairs: Iterable[tuple[str, str]] = (),
) -> tuple[float | None, str | None]:
    structured_area = _area_from_attribute_pairs(attribute_pairs)
    if structured_area is not None:
        return structured_area, "main_attribute_table"

    for source, text in texts:
        if not text:
            continue
        match = AREA_RE.search(text)
        if match:
            amount = _normalise_amount(match.group("amount"))
            if amount:
                return amount, source
    return None, None


def classify_listing_type(texts: Iterable[str]) -> str:
    return classify_listing_type_and_use_case(texts)[0]


def classify_use_case(texts: Iterable[str]) -> str | None:
    listing_type, use_case = classify_listing_type_and_use_case(texts)
    return use_case if listing_type == "COMMERCIAL" else None


def extract_location_zone(texts: Iterable[str]) -> str | None:
    evidence = normalise_for_matching(" ".join(text for text in texts if text))
    # Specific Hulhumalé check must precede Malé because its name contains Male.
    if re.search(r"\bhulhumale\b", evidence):
        return "HULHUMALE"
    if re.search(r"\bmale\b", evidence):
        return "MALE"
    return "OTHERS" if re.search(r"\b(?:addu|fuvahmulah|gan|island)\b", evidence) else None


def _extract_labelled_value(texts: Iterable[str], labels: Iterable[str]) -> str | None:
    label_group = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"(?im)\b(?:{label_group})\b\s*[:\-]\s*(?P<value>[^|\n]{{2,120}})")
    for text in texts:
        match = pattern.search(text)
        if match:
            value = re.sub(r"\s+", " ", match.group("value")).strip(" -:|")
            if value:
                return value
    return None


def _clean_structured_address(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"(?i)^(?:male|hulhumale)\s*--\s*", "", value).strip()
    return cleaned or None


def extract_address(description: str | None, pairs: Iterable[tuple[str, str]]) -> str | None:
    # A labelled description location can include a street/building and is more
    # specific than the standardised neighbourhood table, so prefer it.
    explicit = _extract_labelled_value([description or ""], ["address", "location", "neighbourhood", "neighborhood"])
    cleaned_explicit = _clean_structured_address(explicit)
    if cleaned_explicit:
        return cleaned_explicit

    for label, value in pairs:
        if normalise_for_matching(label) in {"neighborhood", "neighbourhood", "address"}:
            cleaned = _clean_structured_address(value)
            if cleaned:
                return cleaned
    return None


def extract_last_updated(texts: Iterable[str]) -> tuple[str | None, bool]:
    for text in texts:
        if not text:
            continue
        match = LAST_UPDATED_RE.search(text) or DATE_LABEL_RE.search(text)
        if not match:
            continue
        parsed = pd.to_datetime(match.group("date").strip(), errors="coerce", dayfirst=True)
        if pd.isna(parsed):
            return None, True
        return parsed.date().isoformat(), False
    return None, False


def _classify_rental_candidate(title: str | None, description: str | None, source_url: str) -> tuple[bool, str | None]:
    evidence = normalise_for_matching(" ".join(part for part in (title, description, _url_slug_text(source_url)) if part))
    sale = bool(re.search(r"\b(?:for sale|sale|selling|buy)\b", evidence))
    rental = bool(re.search(r"\b(?:rent|rental|lease|monthly|daily|short stay)\b", evidence))
    if sale and not rental:
        return False, "sale_or_non_rental"
    return True, None


def _status_from_text(texts: Iterable[str]) -> str:
    evidence = normalise_for_matching(" ".join(text for text in texts if text))
    if re.search(r"\b(?:rented|unavailable|not available|no longer available)\b", evidence):
        return "RENTED"
    return "AVAILABLE"


def _has_ambiguous_price_range(candidates: Iterable[PriceCandidate], selected_price: PriceCandidate | None) -> bool:
    if selected_price is None:
        return False
    relevant = [
        candidate
        for candidate in candidates
        if not candidate.is_secondary_payment and candidate.source == selected_price.source
    ]
    unique_amounts = {candidate.amount for candidate in relevant}
    return len(unique_amounts) > 1 and any(
        re.search(r"(?i)\b(?:ranging|from)\b.*\bto\b", candidate.context) for candidate in relevant
    )


def _normalised_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _selected_price_matches_contact_number(
    selected_price: PriceCandidate | None,
    title: str | None,
    description: str | None,
) -> bool:
    """Reject a price-field value when the listing repeats it as contact data."""
    if selected_price is None:
        return False
    selected_digits = str(int(round(selected_price.amount))) if selected_price.amount.is_integer() else _normalised_digits(selected_price.raw_token)
    if not 7 <= len(selected_digits) <= 10:
        return False

    contact_pattern = re.compile(
        r"(?i)\b(?:call|contact|phone|mobile|tel|viber|whatsapp)\b[^\d]{0,24}"
        r"(?P<number>(?:\+?960[\s-]*)?\d(?:[\s-]?\d){6,8})"
    )
    for text in (title, description):
        if not text:
            continue
        for match in contact_pattern.finditer(text):
            contact_digits = _normalised_digits(match.group("number"))
            if selected_digits == contact_digits or contact_digits.endswith(selected_digits):
                return True
    return False


def parse_listing_html(html: str, source_url: str, scraped_at: str | None = None) -> dict[str, Any]:
    """Parse one listing page into traceable raw evidence.

    If no bounded primary listing container is found, a reviewable raw record is
    returned rather than using a whole-document text fallback.
    """
    canonical_url = canonicalize_url(source_url)
    timestamp = scraped_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    source_html = unwrap_saved_view_source_html(html)
    soup = BeautifulSoup(source_html, "html.parser")
    page_last_updated, page_date_parse_failed = extract_last_updated([soup.get_text(" ", strip=True)])
    _remove_non_primary_sections(soup)
    container, container_strategy = _choose_primary_container(soup)

    raw_record: dict[str, Any] = {
        "source_url": canonical_url,
        "source_name": "ibay",
        "scraped_at": timestamp,
        "listing_id": listing_id_from_url(canonical_url),
        "primary_container_strategy": container_strategy,
        "raw_title": None,
        "raw_description": None,
        "title_source": None,
        "description_source": None,
        "price_candidates": [],
        "selected_price_source": None,
        "selected_price_token": None,
        "review_reasons": [],
        "listing_type": "UNKNOWN",
        "use_case": None,
        "room_count": None,
        "maid_room_count": None,
        "rent_amount": None,
        "currency_type": None,
        "rent_frequency": None,
        "area_sqft": None,
        "location_zone": None,
        "address": None,
        "last_updated": None,
        "status": "UNKNOWN",
        "is_rental_candidate": False,
        "exclusion_reason": None,
        "date_parse_failed": False,
    }

    if container is None:
        raw_record["review_reasons"].append("primary_listing_container_not_found")
        raw_record["exclusion_reason"] = "primary_container_not_found"
        return raw_record

    title, title_source = _find_title(container)
    description, description_source = _find_description(container)
    attribute_pairs = _extract_attribute_pairs(container)
    attribute_texts = _extract_attribute_texts(container, attribute_pairs)
    price_block_texts = _extract_price_block_texts(container)

    price_block_candidates = [
        candidate for text in price_block_texts for candidate in extract_price_candidates(text, "main_price_block")
    ]
    title_candidates = extract_price_candidates(title, "main_title")
    description_candidates = extract_price_candidates(description, "main_description")
    attribute_candidates = [
        candidate for text in attribute_texts for candidate in extract_price_candidates(text, "main_attribute_table")
    ]
    all_price_candidates = _deduplicate_candidates(
        price_block_candidates + title_candidates + description_candidates + attribute_candidates
    )
    selected_price, price_review_reasons = select_price(
        price_block_candidates,
        title_candidates,
        description_candidates,
        attribute_candidates,
    )
    selected_price, mixed_currency_review_reasons = prefer_usd_when_mixed_currency(selected_price, all_price_candidates)
    if _selected_price_matches_contact_number(selected_price, title, description):
        selected_price = None
        price_review_reasons.append("selected_price_matches_contact_number")
    currency_type, currency_review_reasons = _currency_for_selected_price(selected_price, all_price_candidates)

    evidence_texts = [text for text in (title, description, *attribute_texts) if text]
    frequency, frequency_review_reasons = extract_frequency(title, [description or "", *attribute_texts])
    room_count, maid_room_count, room_source = extract_rooms(title, description, attribute_texts, canonical_url)
    area_sqft, area_source = extract_area(
        [("main_attribute_table", text) for text in attribute_texts]
        + [("main_title", title or ""), ("main_description", description or "")],
        attribute_pairs,
    )
    container_last_updated, container_date_parse_failed = extract_last_updated(
        attribute_texts + [_tag_text(container), description or ""]
    )
    last_updated = container_last_updated or page_last_updated
    date_parse_failed = not last_updated and (container_date_parse_failed or page_date_parse_failed)
    is_rental_candidate, exclusion_reason = _classify_rental_candidate(title, description, canonical_url)
    location_evidence = [value for _, value in attribute_pairs] + evidence_texts

    listing_type, use_case = classify_listing_type_and_use_case(evidence_texts)
    raw_record.update(
        {
            "raw_title": title,
            "raw_description": description,
            "title_source": title_source,
            "description_source": description_source,
            "price_candidates": [candidate.to_dict() for candidate in all_price_candidates],
            "selected_price_source": selected_price.source if selected_price else None,
            "selected_price_token": selected_price.raw_token if selected_price else None,
            "listing_type": listing_type,
            "use_case": use_case if listing_type == "COMMERCIAL" else None,
            "room_count": room_count,
            "maid_room_count": maid_room_count,
            "room_count_source": room_source,
            "rent_amount": selected_price.amount if selected_price else None,
            "currency_type": currency_type,
            "rent_frequency": frequency,
            "area_sqft": area_sqft,
            "area_source": area_source,
            "location_zone": extract_location_zone(location_evidence),
            "address": extract_address(description, attribute_pairs),
            "last_updated": last_updated,
            "status": _status_from_text(evidence_texts),
            "is_rental_candidate": is_rental_candidate,
            "exclusion_reason": exclusion_reason,
            "date_parse_failed": date_parse_failed,
        }
    )
    raw_record["review_reasons"].extend(
        price_review_reasons + mixed_currency_review_reasons + currency_review_reasons + frequency_review_reasons
    )
    if _has_ambiguous_price_range(all_price_candidates, selected_price):
        raw_record["review_reasons"].append("ambiguous_price_range: selected the first supported price in a stated range")
    if date_parse_failed:
        raw_record["review_reasons"].append("last_updated_parse_failed")
    if not selected_price:
        raw_record["review_reasons"].append("rent_amount_missing")
    if not frequency and selected_price:
        raw_record["review_reasons"].append("rent_frequency_missing")
    if not is_rental_candidate and exclusion_reason:
        raw_record["review_reasons"].append(exclusion_reason)
    return raw_record


def raw_record_to_json(record: dict[str, Any]) -> str:
    """Serialize a raw record deterministically for JSONL output or debugging."""
    return json.dumps(record, ensure_ascii=False, default=str, sort_keys=True)
