"""foreclosurephilippines.com scraper (backbone source, all provinces + Guimaras).

Card markup confirmed against tests/fixtures/foreclosurephilippines_iloilo.html
and tests/fixtures/foreclosurephilippines_guimaras.html (Task 2 recon):

    div.wpa-result-item                                        <- card container
      div.wpa-picture-grid img[src]                             <- photo (or placeholder)
      span.wpa-result-title-text  (or <a title="...">)          <- full listing title
      a.wpa-result-link[href]                                    <- advert URL (source_url)
      div.wpa-result-meta--pattern__post_date                   <- "2026/07/20" (posted_date)
      div.wpa-result-meta--meta__adverts_location               <- "Oton, Iloilo"
      div.wpa-result-meta--meta__advert_pretty_lot_area         <- "Lot Area: 36.00 sqm"
      div.wpa-result-meta--meta__advert_pretty_floor_area       <- "Floor Area: 22.00 sqm" (optional)
      div.wpa-result-last-text                                  <- "PHP408,690.00"
"""

import re

from bs4 import BeautifulSoup

from normalize import normalize

BASE = "https://www.foreclosurephilippines.com/location/"
PROVINCES = {
    "Iloilo": "iloilo",
    "Capiz": "capiz",
    "Aklan": "aklan",
    "Antique": "antique",
    "Guimaras": "guimaras",
}

_PLACEHOLDER_MARKER = "no-image-4"

_KNOWN_SELLERS = [
    "Pag-IBIG",
    "PDIC",
    "BDO",
    "BPI",
    "Metrobank",
    "Landbank",
    "PNB",
    "UnionBank",
    "Security Bank",
]
_SELLER_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(s) for s in _KNOWN_SELLERS) + r")",
    re.IGNORECASE,
)

# Property type tokens as they appear after "Foreclosed" and before the next " - "
# e.g. "PDIC Foreclosed Residential - Vacant Lot: ..." -> "Residential"
#      "PDIC Foreclosed Residential/Agricultural - ..." -> "Residential/Agricultural"
_PROPERTY_TYPE_RE = re.compile(
    r"Foreclosed\s+([A-Za-z/]+)\s*-", re.IGNORECASE
)


def _title_text(card):
    span = card.select_one("span.wpa-result-title-text")
    if span:
        text = span.get_text(" ", strip=True)
        if text:
            return text
    a = card.select_one("a[title]")
    if a and a.get("title"):
        return a["title"].strip()
    return ""


def _infer_seller(title):
    m = _SELLER_RE.match(title)
    if m:
        # normalize casing to the canonical known-seller spelling
        matched = m.group(1)
        for known in _KNOWN_SELLERS:
            if known.lower() == matched.lower():
                return known
    return "Unknown (foreclosurephilippines)"


def _infer_property_type(title):
    m = _PROPERTY_TYPE_RE.search(title)
    if m:
        return m.group(1).strip()
    return None


def _text_after_colon(el):
    """'Lot Area: 36.00 sqm' -> '36.00 sqm' -> numeric portion handled by normalize.to_float."""
    if el is None:
        return None
    text = el.get_text(" ", strip=True)
    if ":" not in text:
        return text
    return text.split(":", 1)[1].strip()


def _image_url(card):
    img = card.select_one("div.wpa-picture-grid img")
    if img is None:
        return None
    src = img.get("src")
    if not src:
        return None
    if _PLACEHOLDER_MARKER in src:
        return None
    return src


def _source_url(card):
    a = card.select_one("a.wpa-result-link")
    if a is None:
        return None
    href = a.get("href")
    return href.strip() if href else None


def _posted_date(card):
    el = card.select_one("div.wpa-result-meta--pattern__post_date")
    if el is None:
        return None
    text = el.get_text(strip=True)
    return text or None


def parse(html, province):
    """Parse one province listing page into normalized records. Pure, no network."""
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for card in soup.select("div.wpa-result-item"):
        title = _title_text(card)

        loc_el = card.select_one("div.wpa-result-meta--meta__adverts_location")
        location_text = loc_el.get_text(strip=True) if loc_el else ""

        price_el = card.select_one("div.wpa-result-last-text")
        price_raw = price_el.get_text(strip=True) if price_el else None

        lot_el = card.select_one("div.wpa-result-meta--meta__advert_pretty_lot_area")
        lot_raw = _text_after_colon(lot_el)

        floor_el = card.select_one(
            "div.wpa-result-meta--meta__advert_pretty_floor_area"
        )
        floor_raw = _text_after_colon(floor_el)

        raw = {
            "source": "foreclosurephilippines",
            "province": province,
            "location_text": location_text,
            "price_php": price_raw,
            "lot_area_sqm": lot_raw,
            "floor_area_sqm": floor_raw,
            "tct": None,  # not available on list pages (Commander decision, v1)
            "seller": _infer_seller(title),
            "property_type": _infer_property_type(title),
            "image_url": _image_url(card),
            "source_url": _source_url(card),
            "posted_date": _posted_date(card),
        }
        records.append(normalize(raw))
    return records


def fetch():
    """Playwright-load all province pages (+ Guimaras) and parse them.
    Never raises; returns [] on any failure.
    """
    try:
        from playwright.sync_api import sync_playwright

        out = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            ).new_page()
            for prov, slug in PROVINCES.items():
                try:
                    page.goto(BASE + slug, wait_until="networkidle", timeout=60000)
                    out.extend(parse(page.content(), prov))
                except Exception:
                    continue
            browser.close()
        return out
    except Exception:
        return []
