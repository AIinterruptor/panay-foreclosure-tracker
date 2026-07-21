"""Tests for the Pag-IBIG OPA scraper.

The Pag-IBIG Online Public Auction (OPA) search endpoint
(`Load_SearchListProperties_COPA`) was returning a server-side error
(HTTP 500 / "Error Occured") on 2026-07-21, reproducible across all
cities and methods -- confirmed a source-side outage, not a bot-block
(Task 2 recon). There is no real fixture for this source because it
cannot be captured while down.

The payload below is a SYNTHETIC stand-in for the documented response
shape (`{"success": true, "data": [...]}`), used only to prove that the
defensive multi-key field mapping in `parse()` works. It is NOT a
captured fixture and must not be treated as verified real-world data.
"""

from normalize import RECORD_KEYS
from scrapers.pagibig_opa import parse

# One row using the "primary" candidate key names.
ROW_PRIMARY = {
    "barangay": "Mandurriao",
    "city_municipality": "Iloilo City",
    "province": "Iloilo",
    "min_bid_price": "1,234,567.89",
    "lot_area": "150",
    "floor_area": "80",
    "property_type": "Residential",
    "disposal_desc": "Negotiated Sale",
    "image_url": "https://example.com/photo1.jpg",
}

# Second row using ALTERNATE candidate key names, to prove the
# defensive fallback chain (not just the first-choice keys) works.
ROW_ALTERNATE = {
    "address": "Brgy. Poblacion, Roxas City, Capiz",
    "appraised_value": 987654.0,
    "lot_size": "200.5",
    "floor_size": "60",
    "classification": "Commercial",
    "disposal_flag_desc": "Public Auction",
}

SYNTHETIC_PAYLOAD = {"success": True, "data": [ROW_PRIMARY, ROW_ALTERNATE]}


def test_parse_maps_both_primary_and_alternate_key_variants():
    recs = parse(SYNTHETIC_PAYLOAD, "Iloilo")
    assert len(recs) == 2
    for r in recs:
        assert r["source"] == "pagibig_opa"
        assert r["seller"] == "Pag-IBIG"
        assert r["province"] == "Iloilo"
        assert isinstance(r["price_php"], float)
        assert r["location_text"]
        assert set(r.keys()) == set(RECORD_KEYS)
        assert len(RECORD_KEYS) == 13

    primary, alternate = recs
    assert primary["price_php"] == 1234567.89
    assert primary["lot_area_sqm"] == 150.0
    assert primary["floor_area_sqm"] == 80.0
    assert primary["property_type"] == "Residential"
    assert primary["sale_type"] == "Negotiated Sale"
    assert "Mandurriao" in primary["location_text"]
    assert "Iloilo City" in primary["location_text"]

    assert alternate["price_php"] == 987654.0
    assert alternate["lot_area_sqm"] == 200.5
    assert alternate["floor_area_sqm"] == 60.0
    assert alternate["property_type"] == "Commercial"
    assert alternate["sale_type"] == "Public Auction"
    assert "Roxas City" in alternate["location_text"]


def test_parse_handles_empty_data_list():
    assert parse({"success": True, "data": []}, "Capiz") == []


def test_parse_handles_missing_or_none_payload():
    assert parse({}, "Iloilo") == []
    assert parse(None, "Iloilo") == []


def test_parse_row_with_no_recognizable_price_keys_yields_none_price():
    row = {
        "barangay": "Some Barangay",
        "city_municipality": "Some City",
        "province": "Iloilo",
        # no min_bid_price / minimum_bid / appraised_value / selling_price / tcp
    }
    recs = parse({"success": True, "data": [row]}, "Iloilo")
    assert len(recs) == 1
    assert recs[0]["price_php"] is None
