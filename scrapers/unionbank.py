"""UnionBank foreclosed-properties scraper (official source, nationwide, no TCT on list page).

UnionBank's foreclosed-properties listing is an Ant Design (antd) React app
server-rendered under Drupal. Card markup confirmed against
tests/fixtures/unionbank_sample.html (page-1 recon, 21 cards):

    a[href^="/foreclosed-properties/"]                <- card link wrapper (relative href)
      div.ant-card                                    <- card container
        div.ant-card-cover img                        <- photo (already absolute src)
        div.ant-card-meta-title                       <- property_type, e.g. "Residential • Condominium"
        p.city-arg                                    <- location_text, e.g. "Oton, Iloilo"
        p.price                                       <- "Php 5,022,000"
        p.specs                                       <- "FA: 155 sqm • LA: 245 sqm" (either part may be absent)

The list is NATIONWIDE. parse() filters to Panay + Guimaras provinces only.
tct is always None here -- only available on detail pages (mirrors metrobank.py, v1).
posted_date is always None here -- UnionBank list cards carry no posting date.
"""

import re

from bs4 import BeautifulSoup

from normalize import normalize, to_float

URL = "https://www.unionbankph.com/foreclosed-properties"
BASE = "https://www.unionbankph.com"
MAX_PAGES = 30

PANAY = {
    "iloilo": "Iloilo",
    "capiz": "Capiz",
    "aklan": "Aklan",
    "antique": "Antique",
    "guimaras": "Guimaras",
}

_FA_RE = re.compile(r"FA:\s*([\d,]+)", re.IGNORECASE)
_LA_RE = re.compile(r"LA:\s*([\d,]+)", re.IGNORECASE)


def _province_of(text):
    """Return the canonical Panay/Guimaras province name found in text, or None.

    UnionBank locations are formatted "City, Province" (e.g. "Oton, Iloilo",
    "PAVIA, Iloilo"). Prefer matching against the LAST comma-separated
    segment -- the province field -- since that's both more precise and
    avoids false positives like "Aklan Street, Quezon City" (a street name
    in Quezon City, not the province of Aklan). Fall back to a
    word-boundary search over the whole string when there's no comma.
    """
    t = (text or "").lower()
    if "," in t:
        t = t.rsplit(",", 1)[-1]
    for key, name in PANAY.items():
        if re.search(r"\b" + re.escape(key) + r"\b", t):
            return name
    return None


def _parse_areas(specs_text):
    """Parse the specs row into (floor_area_sqm, lot_area_sqm) floats-or-None.

    Forms seen:
      "FA: 155 sqm • LA: 245 sqm"  -> floor=155.0, lot=245.0
      "LA: 245 sqm"                -> floor=None, lot=245.0
      "FA: 155 sqm"                -> floor=155.0, lot=None
    """
    if not specs_text:
        return None, None
    fa_match = _FA_RE.search(specs_text)
    la_match = _LA_RE.search(specs_text)
    floor = to_float(fa_match.group(1)) if fa_match else None
    lot = to_float(la_match.group(1)) if la_match else None
    return floor, lot


def _image_url(card):
    img = card.select_one("div.ant-card-cover img")
    if img is None:
        return None
    src = img.get("src")
    if not src:
        return None
    return src


def _source_url(link):
    href = link.get("href")
    if not href:
        return None
    href = href.strip()
    if href.startswith("http://") or href.startswith("https://"):
        return href
    return BASE + href


def parse(html):
    """Parse the UnionBank property list into normalized Panay/Guimaras records.

    Pure, no network. Nationwide cards outside Iloilo/Capiz/Aklan/Antique/Guimaras
    are dropped.
    """
    soup = BeautifulSoup(html, "html.parser")
    records = []
    for link in soup.select('a[href^="/foreclosed-properties/"]'):
        card = link.select_one("div.ant-card")
        if card is None:
            continue

        location_el = card.select_one("p.city-arg")
        location_text = location_el.get_text(strip=True) if location_el else ""
        province = _province_of(location_text)
        if not province:
            continue

        title_el = card.select_one("div.ant-card-meta-title")
        property_type = title_el.get_text(" ", strip=True) if title_el else None

        price_el = card.select_one("p.price")
        price_raw = price_el.get_text(strip=True) if price_el else None

        specs_el = card.select_one("p.specs")
        specs_text = specs_el.get_text(" ", strip=True) if specs_el else ""
        floor_area, lot_area = _parse_areas(specs_text)

        raw = {
            "source": "unionbank",
            "seller": "UnionBank",
            "property_type": property_type,
            "location_text": location_text,
            "province": province,
            "price_php": price_raw,
            "lot_area_sqm": lot_area,
            "floor_area_sqm": floor_area,
            "tct": None,  # only on detail pages (mirrors metrobank.py, v1)
            "image_url": _image_url(card),
            "source_url": _source_url(link),
            "posted_date": None,  # UnionBank list cards carry no posting date (v1)
        }
        records.append(normalize(raw))
    return records


def fetch():
    """Playwright-load the UnionBank list (all pages, up to MAX_PAGES) and parse it.

    UnionBank sits behind Akamai bot detection: the `_abck` sensor cookie must
    be seeded by visiting the homepage first, in the SAME browser context,
    before hitting the listing page -- otherwise the listing request gets
    blocked/challenged. Pagination is client-side (antd `ul.ant-pagination`),
    so each page is clicked through and scraped in place rather than
    navigated to via URL.

    Never raises; returns whatever partial results were accumulated so far
    (or [] if none) on any failure.
    """
    records = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            )
            page = context.new_page()
            try:
                # Seed the Akamai _abck sensor cookie on the homepage first.
                page.goto(BASE + "/", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(6000)

                # Same context/page navigates to the listing -- cookie carries over.
                page.goto(URL, wait_until="networkidle", timeout=60000)

                for _ in range(MAX_PAGES):
                    page.wait_for_timeout(1500)  # let the grid re-render
                    records.extend(parse(page.content()))

                    next_btn = page.query_selector(
                        "ul.ant-pagination li.ant-pagination-next:not(.ant-pagination-disabled) a"
                    )
                    if next_btn is None:
                        break
                    next_btn.click()
            except Exception:
                pass
            finally:
                browser.close()
    except Exception:
        pass
    return records
