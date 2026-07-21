import pathlib

from scrapers.metrobank import _parse_area, _province_of, parse

FIX = pathlib.Path(__file__).parent / "fixtures"
PANAY = {"Iloilo", "Capiz", "Aklan", "Antique", "Guimaras"}


def test_parse_returns_normalized_metrobank_records_only_panay():
    html = (FIX / "metrobank_region6.html").read_text(encoding="utf-8")
    recs = parse(html)
    assert len(recs) > 0
    for r in recs:
        assert r["source"] == "metrobank"
        assert r["seller"] == "Metrobank"
        assert r["province"] in PANAY
        assert r["price_php"] is None or isinstance(r["price_php"], float)
        assert r["tct"] is None


def test_parse_finds_known_pavia_iloilo_card():
    html = (FIX / "metrobank_region6.html").read_text(encoding="utf-8")
    recs = parse(html)
    pavia = [
        r
        for r in recs
        if r["province"] == "Iloilo" and "Pavia" in (r["location_text"] or "")
    ]
    assert len(pavia) == 1
    r = pavia[0]
    assert r["price_php"] == 792000.0
    assert r["image_url"]
    assert r["image_url"].startswith("https://")
    assert "s3" in r["image_url"]
    assert r["floor_area_sqm"] == 40.0
    assert r["lot_area_sqm"] == 45.0
    assert r["property_type"] == "Residential With Improvement"
    assert r["source_url"] is not None
    assert r["source_url"].startswith(
        "https://www.metrobank.com.ph/assets-for-sale/properties/details?id="
    )
    assert r["posted_date"] is None


def test_parse_excludes_non_panay_cards():
    html = (FIX / "metrobank_region6.html").read_text(encoding="utf-8")
    recs = parse(html)
    assert not any(r["province"] not in PANAY for r in recs)
    # sanity: known non-Panay locations from recon must not leak through
    assert not any("Rizal" in (r["location_text"] or "") for r in recs)
    assert not any("Bataan" in (r["location_text"] or "") for r in recs)
    assert not any("Cavite" in (r["location_text"] or "") for r in recs)
    assert not any("Metro Manila" in (r["location_text"] or "") for r in recs)


def test_area_parsing_fa_la_form():
    floor, lot = _parse_area("40 / 45 sqm (FA/LA)")
    assert floor == 40.0
    assert lot == 45.0


def test_area_parsing_lot_only_form():
    floor, lot = _parse_area("1,871 sqm (LA)")
    assert floor is None
    assert lot == 1871.0


def test_province_of_helper():
    assert _province_of("Pavia, Iloilo") == "Iloilo"
    assert _province_of("Binangonan, Rizal") is None
    assert _province_of("Some Town, Guimaras") == "Guimaras"


def test_province_of_rejects_substring_false_positive():
    # "Aklan" must NOT match inside "Aklan Street, Quezon City" -- that's a
    # Quezon City street name, not the province of Aklan.
    assert _province_of("Aklan Street, Quezon City") is None


def test_province_of_still_matches_panay_last_segment():
    assert _province_of("Pavia, Iloilo") == "Iloilo"
    assert _province_of("Jordan, Guimaras") == "Guimaras"


def test_province_of_still_rejects_non_panay_last_segment():
    assert _province_of("Binangonan, Rizal") is None
