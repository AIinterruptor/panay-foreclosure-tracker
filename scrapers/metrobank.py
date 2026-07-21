"""Metrobank assets-for-sale scraper (official source, nationwide, no TCT on list page).

Metrobank's listing page is a React app whose CSS classes carry HASHED suffixes
that change across deploys (e.g. PropertyCard_card__ptEcG). Selectors below
match on STABLE PREFIXES ONLY via `class*="..."` — never on a full hashed class.

Card markup confirmed against tests/fixtures/metrobank_region6.html (Task 2 recon):

    article[class*="PropertyCard_card__"]                 <- card container
      img[class*="PropertyCard_image__"]                  <- photo (S3-hosted)
      h3[class*="PropertyCard_title__"]                    <- property_type, e.g. "Residential With Improvement"
      li[class*="PropertyCard_detailRow__"]  (4, positional, same classes):
        [0] account/reference number (Metrobank internal ID) -- NOT a TCT, ignored
        [1] location_text, e.g. "Pavia, Iloilo"
        [2] area free-text: "69 / 62 sqm (FA/LA)" or "1,871 sqm (LA)"
        [3] price -- amount lives in span[class*="PropertyCard_priceAmount__"]

The list is NATIONWIDE. parse() filters to Panay + Guimaras provinces only.
tct is always None here -- only available on detail pages (Commander decision, v1).
"""

import re

from bs4 import BeautifulSoup

from normalize import normalize, to_float

URL = "https://www.metrobank.com.ph/assets-for-sale/properties"

PANAY = {
    "iloilo": "Iloilo",
    "capiz": "Capiz",
    "aklan": "Aklan",
    "antique": "Antique",
    "guimaras": "Guimaras",
}

_AREA_NUMBER_RE = re.compile(r"[\d,]+\.?\d*")


def _province_of(text):
    """Return the canonical Panay/Guimaras province name found in text, or None.

    Metrobank locations are formatted "Municipality, Province" (e.g.
    "Pavia, Iloilo"). Prefer matching against the LAST comma-separated
    segment -- the province field -- since that's both more precise and
    avoids false positives like "Aklan Street, Quezon City" (a street name
    in Quezon City, not the province of Aklan). Fall back to a
    word-boundary search over the whole string when there's no comma.

    Note: this does NOT resolve city-only formats like "Roxas City" (Capiz)
    that don't contain the literal province name -- out of scope (Minor).
    """
    t = (text or "").lower()
    if "," in t:
        t = t.rsplit(",", 1)[-1]
    for key, name in PANAY.items():
        if re.search(r"\b" + re.escape(key) + r"\b", t):
            return name
    return None


def _parse_area(text):
    """Parse the area detail row into (floor_area_sqm, lot_area_sqm) floats-or-None.

    Forms seen:
      "69 / 62 sqm (FA/LA)"  -> floor=69, lot=62
      "1,871 sqm (LA)"       -> floor=None, lot=1871
    """
    if not text:
        return None, None
    t = text.strip()
    if "/" in t and "(FA/LA)" in t.upper().replace(" ", ""):
        left, right = t.split("/", 1)
        floor = to_float(_first_number(left))
        lot = to_float(_first_number(right))
        return floor, lot
    if "(LA)" in t.upper().replace(" ", ""):
        return None, to_float(_first_number(t))
    # Fallback: unrecognized form -- try FA/LA-style split if a slash exists.
    if "/" in t:
        left, right = t.split("/", 1)
        return to_float(_first_number(left)), to_float(_first_number(right))
    return None, to_float(_first_number(t))


def _first_number(text):
    m = _AREA_NUMBER_RE.search(text or "")
    return m.group(0) if m else None


def _image_url(card):
    img = card.select_one('img[class*="PropertyCard_image__"]')
    if img is None:
        return None
    src = img.get("src")
    if not src:
        return None
    return src


def parse(html):
    """Parse the Metrobank property list into normalized Panay/Guimaras records.

    Pure, no network. Nationwide cards outside Iloilo/Capiz/Aklan/Antique/Guimaras
    are dropped.
    """
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for card in soup.select('article[class*="PropertyCard_card__"]'):
        rows = card.select('li[class*="PropertyCard_detailRow__"]')

        location_text = rows[1].get_text(" ", strip=True) if len(rows) > 1 else ""
        province = _province_of(location_text)
        if not province:
            continue

        title_el = card.select_one('h3[class*="PropertyCard_title__"]')
        property_type = title_el.get_text(strip=True) if title_el else None

        area_text = rows[2].get_text(" ", strip=True) if len(rows) > 2 else ""
        floor_area, lot_area = _parse_area(area_text)

        price_amount_el = card.select_one('span[class*="PropertyCard_priceAmount__"]')
        price_raw = price_amount_el.get_text(strip=True) if price_amount_el else None

        raw = {
            "source": "metrobank",
            "seller": "Metrobank",
            "property_type": property_type,
            "location_text": location_text,
            "province": province,
            "price_php": price_raw,
            "lot_area_sqm": lot_area,
            "floor_area_sqm": floor_area,
            "tct": None,  # only on detail pages (Commander decision, v1)
            "image_url": _image_url(card),
        }
        records.append(normalize(raw))
    return records


def fetch():
    """Playwright-load the Metrobank list and parse it.

    Never raises; returns [] on any failure.
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            ).new_page()
            page.goto(URL, wait_until="networkidle", timeout=60000)
            recs = parse(page.content())
            browser.close()
        return recs
    except Exception:
        return []
