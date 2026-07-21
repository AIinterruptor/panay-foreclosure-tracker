"""Pag-IBIG OPA (Online Public Auction) scraper -- official source, Iloilo + Capiz.

STATUS (2026-07-21): the OPA search endpoint (`Load_SearchListProperties_COPA`)
was returning a server-side error (HTTP 500 / "Error Occured" page) on Pag-IBIG's
own end -- reproduced across every target city and every request method during
Task 2 recon. This is NOT a bot-block; it is the source itself being down. There
is therefore no captured fixture for this source: real field names in each
`data[]` row are UNKNOWN.

Because the source cannot be observed right now, this module is built to be
resilient and self-recovering rather than built against confirmed field names:

  - `parse()` maps each row DEFENSIVELY, trying a short list of plausible key
    names per target field and taking the first one present. If/when Pag-IBIG
    recovers and the real key names turn out to differ from every candidate
    listed here, the corresponding output field will simply be None rather
    than raise -- update the candidate list once the live shape is confirmed.
  - `fetch()` guards every network/parse step individually: a non-200 response,
    invalid JSON, or JSON missing a list `data` causes that single call to be
    skipped (not the whole run). The outer call is also wrapped so fetch()
    NEVER raises -- it returns whatever rows it managed to collect (today,
    almost certainly []).

Do not treat an empty return from fetch() as a bug report -- check whether the
source has recovered before assuming this scraper is broken.
"""

from normalize import normalize

API = (
    "https://www.pagibigfundservices.com/OnlinePublicAuction/"
    "ListofProperties/Load_SearchListProperties_COPA"
)

# (province label, province code, [city/municipality codes...])
# Codes below are the ones referenced in the Task 2 recon brief; the OPA
# outage means they could not be cross-checked against a live city list.
# Iloilo = 063000000, Capiz = 061900000 (region 06 / Western Visayas).
TARGETS = [
    ("Iloilo", "063000000", ["063022000"]),  # Iloilo City -- adjust per real codes
    ("Capiz", "061900000", ["061914000"]),  # Roxas City -- adjust per real codes
]

REGION = "060000000"  # Region VI - Western Visayas


def _first_present(row, keys):
    for k in keys:
        val = row.get(k)
        if val is not None and val != "":
            return val
    return None


def _build_location(row):
    parts = [
        row.get("barangay"),
        row.get("city_municipality") or row.get("city_muni") or row.get("city"),
        row.get("province"),
    ]
    loc = ", ".join(str(p) for p in parts if p)
    if loc:
        return loc
    fallback = row.get("address") or row.get("property_location")
    return str(fallback) if fallback else ""


def parse(payload, province):
    """Map OPA API `data[]` rows to normalized records. Pure, no network.

    Defensive against the unknown/undocumented real field names: every
    target field tries a short list of plausible source keys and takes the
    first one present. Never raises on malformed/missing payloads -- always
    returns a list (possibly empty).
    """
    if not isinstance(payload, dict):
        return []
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        raw = {
            "source": "pagibig_opa",
            "seller": "Pag-IBIG",
            "province": province,
            "location_text": _build_location(row),
            "price_php": _first_present(
                row,
                [
                    "min_bid_price",
                    "minimum_bid",
                    "appraised_value",
                    "selling_price",
                    "tcp",
                ],
            ),
            "lot_area_sqm": _first_present(row, ["lot_area", "lot_size"]),
            "floor_area_sqm": _first_present(row, ["floor_area", "floor_size"]),
            "property_type": _first_present(row, ["property_type", "classification"]),
            "sale_type": _first_present(
                row, ["disposal_desc", "disposal_flag_desc"]
            )
            or "Public Auction",
            "image_url": _first_present(row, ["image_url", "photo", "image"]),
            "tct": None,  # not available via OPA list API (v1)
        }
        out.append(normalize(raw))
    return out


def fetch():
    """Query the OPA API for Iloilo + Capiz cities and return normalized rows.

    Not unit-tested: requires live network access to a source confirmed down
    as of 2026-07-21, and Playwright's browser runtime. Every request is
    individually guarded so one bad/error response never aborts the others,
    and the whole function is wrapped so it can never raise -- callers can
    treat fetch() as always returning a list, empty or otherwise.
    """
    try:
        from playwright.sync_api import sync_playwright

        out = []
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json",
        }
        with sync_playwright() as p:
            browser = p.chromium.launch()
            request_ctx = browser.new_context(extra_http_headers=headers).request
            for province, province_code, city_codes in TARGETS:
                for city_code in city_codes:
                    url = (
                        f"{API}?flag=1&region={REGION}&province={province_code}"
                        f"&city_muni={city_code}&prop_type=1&range_from=0"
                        f"&range_to=999999999&lot_from=0&lot_to=999999"
                        f"&floor_from=0&floor_to=999999&occupancy=0"
                    )
                    try:
                        resp = request_ctx.get(url, timeout=60000)
                        if resp.status != 200:
                            continue
                        try:
                            body = resp.json()
                        except Exception:
                            continue
                        if not isinstance(body, dict) or not isinstance(
                            body.get("data"), list
                        ):
                            continue
                        out.extend(parse(body, province))
                    except Exception:
                        continue
            browser.close()
        return out
    except Exception:
        return []
