import re
from urllib.parse import quote_plus

RECORD_KEYS = [
    "source", "seller", "property_type", "location_text", "province",
    "price_php", "lot_area_sqm", "floor_area_sqm", "tct",
    "sale_type", "auction_date", "maps_url",
]
_NUMERIC = {"price_php", "lot_area_sqm", "floor_area_sqm"}

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
