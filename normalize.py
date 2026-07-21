import re
from urllib.parse import quote_plus

RECORD_KEYS = [
    "source", "seller", "property_type", "location_text", "province",
    "price_php", "lot_area_sqm", "floor_area_sqm", "tct",
    "sale_type", "auction_date", "maps_url", "image_url",
    "source_url", "posted_date", "branch",
]
_NUMERIC = {"price_php", "lot_area_sqm", "floor_area_sqm"}

# Lamudi's `agency-name` field is the lister, not always a bank -- map known
# bank-brand substrings (case-insensitive) to a canonical seller name; any
# other raw agency name (individual broker, unlisted brand) passes through
# unchanged. Keep this small -- 3-5 entries, add only on confirmed sightings.
SELLER_BRAND_MAP = {
    "buena mano": "BPI (Buena Mano)",
    "bdo": "BDO",
    "pnb": "PNB",
    "metrobank": "Metrobank",
    "unionbank": "UnionBank",
}

def map_seller(raw):
    """Map a raw agency/seller name to a canonical brand name.

    Case-insensitive substring match against SELLER_BRAND_MAP. Unknown or
    individual-broker names pass through unchanged. None/blank -> "Lamudi
    listing" (Lamudi-specific default for when no agency name is found).
    """
    if not raw or not str(raw).strip():
        return "Lamudi listing"
    low = raw.lower()
    for key, canonical in SELLER_BRAND_MAP.items():
        if key in low:
            return canonical
    return raw

def maps_url(location_text):
    q = quote_plus((location_text or "").strip() + ", Philippines")
    return "https://www.google.com/maps/search/?api=1&query=" + q

def to_float(raw):
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = re.sub(r"[^\d.]", "", str(raw))
    if cleaned in ("", "."):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None

def normalize(raw):
    rec = {k: raw.get(k) for k in RECORD_KEYS}
    for k in _NUMERIC:
        rec[k] = to_float(rec[k])
    rec["maps_url"] = maps_url(rec.get("location_text") or "")
    return rec
