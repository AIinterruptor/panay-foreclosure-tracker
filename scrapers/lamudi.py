"""Lamudi foreclosures scraper (aggregator, per-province SERPs, JSON-LD primary parse).

ARCHITECTURE DECISION (senior review): parse the `<script type="application/
ld+json">` block as the PRIMARY path, not CSS. This is Lamudi's SEO contract
(schema.org RealEstateListing) and survives class-name churn that would break
a CSS-based scraper on the next Lamudi deploy.

Confirmed against tests/fixtures/lamudi_iloilo_sample.html (30 listings,
recon Task: unionbank+lamudi). Actual JSON-LD shape found in that fixture:

    <script type="application/ld+json">
      [
        {
          "@context": "https://schema.org",
          "@graph": [
            {
              "@type": "SearchResultsPage",
              "mainEntity": [
                {
                  "@type": "ItemList",
                  "itemListElement": [
                    {"@type": "ListItem", "position": 1,
                     "item": {"@type": "RealEstateListing", ...}},
                    ...
                  ]
                }
              ]
            }
          ]
        }
      ]
    </script>

The outer document is a JSON array containing ONE object with an `@graph`
array; `@graph[0]` is a SearchResultsPage whose `mainEntity[0]` is an
ItemList. We do not hardcode that exact path -- _iter_listings() walks
the parsed JSON generically (any dict with @type == "RealEstateListing",
reached via itemListElement/mainEntity/@graph/or bare arrays) so the same
code tolerates the "single object" or "array of objects" variations the
task brief calls out, and multiple ld+json blocks on a page.

Each RealEstateListing item carries (per the fixture):
    name            -- title, e.g. "Commercial Lot For Sale in Banuyao"
    url / @id       -- absolute property URL (both identical in the fixture)
    image           -- absolute photo URL (string) or absent
    address         -- PostalAddress: addressLocality, addressRegion (province),
                       streetAddress
    floorSize       -- QuantitativeValue {value, unitCode}. NOTE: Lamudi uses
                       this field for the primary area figure regardless of
                       property type -- a "Commercial Lot" listing's 2-hectare
                       lot area lives under floorSize too (there is no
                       separate lotSize field in this fixture). We route it
                       to lot_area_sqm when the derived property_type is
                       Lot/Land, else floor_area_sqm (Condo/House/Townhouse).
    offers.price    -- numeric string, PHP

SELLER: Lamudi's `agency-name` is the lister, not always a bank -- it is
NOT present in JSON-LD, so it requires the one tolerated CSS lookup
(span[data-test="agency-name"], title attribute) per the task's exception
clause. Cards appear in the same order in the DOM as itemListElement
positions in the fixture, so we zip agency names to items BEFORE filtering
to Panay (an index shift after filtering would misalign a mixed-province
SERP). Missing agency -> None -> normalize.map_seller(None) -> "Lamudi
listing".

FAIL LOUD: if no RealEstateListing items are extracted from non-trivial
HTML (>50KB), we log a warning and return [] -- we do NOT fall back to a
fragile CSS parse of listing data. A loud empty list is safe (the
pipeline's merge_with_prior retains prior rows on empty); a silently-wrong
CSS parse is not.
"""

import json
import logging
import re

from bs4 import BeautifulSoup

from normalize import map_seller, normalize, to_float

logger = logging.getLogger(__name__)

BASE = "https://www.lamudi.com.ph"
PROVINCES = ["iloilo", "guimaras", "capiz", "aklan", "antique"]
MAX_PAGES = 3

PANAY = {
    "iloilo": "Iloilo",
    "capiz": "Capiz",
    "aklan": "Aklan",
    "antique": "Antique",
    "guimaras": "Guimaras",
}

_FAIL_LOUD_THRESHOLD = 50_000

_LOT_KEYWORDS = ("lot", "land")
_TYPE_KEYWORDS = [
    ("condo", "Condominium"),
    ("townhouse", "Townhouse"),
    ("house", "House"),
    ("lot", "Lot"),
    ("land", "Lot"),
    ("commercial", "Commercial"),
    ("residential", "Residential"),
    ("apartment", "Apartment"),
    ("building", "Building"),
    ("warehouse", "Warehouse"),
]


def _province_of(text):
    """Return the canonical Panay/Guimaras province name found in text, or None.

    Mirrors metrobank.py's _province_of: word-boundary match against the
    known province keys. Lamudi's addressRegion is already just the province
    name (e.g. "Iloilo"), so this is mostly a direct hit, but we keep the
    same tolerant word-boundary search other scrapers use for consistency
    and to survive minor formatting differences ("Iloilo City" etc.).
    """
    t = (text or "").lower()
    for key, name in PANAY.items():
        if re.search(r"\b" + re.escape(key) + r"\b", t):
            return name
    return None


def _property_type_of(name):
    """Derive a simple property type from the listing title's keywords.

    Don't overfit -- first keyword match wins; None if nothing recognized.
    """
    t = (name or "").lower()
    for kw, ptype in _TYPE_KEYWORDS:
        if kw in t:
            return ptype
    return None


def _is_lot_type(property_type):
    if not property_type:
        return False
    return property_type.lower() in _LOT_KEYWORDS or property_type == "Lot"


def _location_text(address):
    if not isinstance(address, dict):
        return ""
    locality = (address.get("addressLocality") or "").strip()
    region = (address.get("addressRegion") or "").strip()
    parts = [p for p in (locality, region) if p]
    return ", ".join(parts)


def _image_url(item):
    img = item.get("image")
    if isinstance(img, dict):
        img = img.get("url")
    if not isinstance(img, str) or not img.strip():
        return None
    if "no-image-placeholder" in img.lower():
        return None
    return img


def _source_url(item):
    url = item.get("url") or item.get("@id")
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return BASE + url


def _price_of(item):
    offers = item.get("offers")
    if isinstance(offers, dict):
        price = offers.get("price")
        if price is not None:
            return price
    return item.get("price")


def _area_sqm(item):
    """Return the floorSize value as a float, or None."""
    size = item.get("floorSize") or item.get("lotSize") or item.get("area")
    if isinstance(size, dict):
        return to_float(size.get("value"))
    return to_float(size)


def _iter_realestate_listings(node):
    """Recursively walk a parsed JSON-LD structure, yielding RealEstateListing dicts.

    Tolerates the documented variations: a single object with @graph, an
    array wrapping that object, itemListElement arrays of {item: {...}} or
    bare listing dicts, and multiple nesting depths -- without hardcoding
    one exact path, so the same code works if Lamudi's ld+json shape shifts
    slightly between provinces/pages.
    """
    if isinstance(node, dict):
        if node.get("@type") == "RealEstateListing":
            yield node
            return
        # ListItem wrapper: {"@type": "ListItem", "item": {...}}
        if "item" in node and isinstance(node["item"], dict):
            yield from _iter_realestate_listings(node["item"])
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                yield from _iter_realestate_listings(node[key])
    elif isinstance(node, list):
        for child in node:
            yield from _iter_realestate_listings(child)


def _extract_listings(html):
    """Find all ld+json script blocks and extract RealEstateListing items.

    Returns (listings, provenance_order) where provenance_order is unused
    externally but kept as a plain list in document order for clarity.
    Never raises -- a malformed block is skipped, not fatal.
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text()
        if not text or not text.strip():
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        listings.extend(_iter_realestate_listings(data))
    return listings


def _agency_names_in_order(html):
    """Tolerant CSS lookup for the one field JSON-LD doesn't carry: seller name.

    Returns a list of raw agency-name strings (or None per missing card) in
    DOM order, aligned by position with the JSON-LD itemListElement order
    (confirmed 1:1 in the recon fixture). Never raises.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        spans = soup.select('span[data-test="agency-name"]')
        names = []
        for span in spans:
            title = span.get("title")
            names.append(title.strip() if title else None)
        return names
    except Exception:
        return []


def parse(html):
    """Parse a Lamudi province SERP into normalized Panay/Guimaras records.

    Pure, no network. JSON-LD is the primary and only source for listing
    data; a single tolerant CSS lookup fills in the seller/agency name
    (not present in JSON-LD). Non-Panay listings are dropped. If the
    JSON-LD block is missing or yields zero listings while the HTML is
    non-trivial, logs a warning and returns [] rather than falling back to
    CSS for listing data.
    """
    try:
        listings = _extract_listings(html)
    except Exception:
        logger.warning("lamudi.parse: unexpected error extracting JSON-LD listings")
        return []

    if not listings:
        if html and len(html) > _FAIL_LOUD_THRESHOLD:
            logger.warning(
                "lamudi.parse: JSON-LD block missing or empty on non-trivial HTML "
                "(%d bytes) -- returning [] rather than falling back to CSS",
                len(html),
            )
        return []

    agency_names = _agency_names_in_order(html)

    records = []
    for idx, item in enumerate(listings):
        address = item.get("address") or {}
        province = _province_of(address.get("addressRegion") or "")
        if not province:
            continue

        name = item.get("name")
        property_type = _property_type_of(name)
        area = _area_sqm(item)
        if _is_lot_type(property_type):
            lot_area, floor_area = area, None
        else:
            lot_area, floor_area = None, area

        raw_agency = agency_names[idx] if idx < len(agency_names) else None

        raw = {
            "source": "lamudi",
            "seller": map_seller(raw_agency),
            "property_type": property_type,
            "location_text": _location_text(address),
            "province": province,
            "price_php": _price_of(item),
            "lot_area_sqm": lot_area,
            "floor_area_sqm": floor_area,
            "tct": None,
            "image_url": _image_url(item),
            "source_url": _source_url(item),
            "posted_date": None,
        }
        records.append(normalize(raw))
    return records


def _province_url(province_slug, page):
    url = f"{BASE}/{province_slug}/foreclosures/buy/"
    if page > 1:
        url += f"?page={page}"
    return url


def fetch():
    """Playwright-load the 5 Panay+Guimaras province SERPs and parse them.

    Standard Chrome UA (no anti-bot hit in recon). Per province, fetches up
    to MAX_PAGES (hard cap of 3) via the `?page=N` query param, stopping
    early if a page returns no listings. Never raises; returns whatever was
    accumulated so far (or [] if nothing).
    """
    records = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page_obj = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            ).new_page()
            try:
                for slug in PROVINCES:
                    province_count = 0
                    for page_num in range(1, MAX_PAGES + 1):
                        url = _province_url(slug, page_num)
                        try:
                            page_obj.goto(url, wait_until="networkidle", timeout=60000)
                        except Exception:
                            break
                        page_records = parse(page_obj.content())
                        if not page_records:
                            break
                        records.extend(page_records)
                        province_count += len(page_records)
                    logger.info("lamudi.fetch: %s -> %d records", slug, province_count)
            finally:
                browser.close()
    except Exception:
        logger.warning("lamudi.fetch: failed, returning partial results", exc_info=True)
    return records
