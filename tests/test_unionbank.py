import pathlib

from scrapers.unionbank import _parse_areas, parse

FIX = pathlib.Path(__file__).parent / "fixtures"
PANAY = {"Iloilo", "Capiz", "Aklan", "Antique", "Guimaras"}


def test_parse_returns_normalized_unionbank_records_only_panay():
    html = (FIX / "unionbank_sample.html").read_text(encoding="utf-8")
    recs = parse(html)
    assert isinstance(recs, list)
    assert len(recs) > 0
    for r in recs:
        assert r["source"] == "unionbank"
        assert r["seller"] == "UnionBank"
        assert r["province"] in PANAY
        assert r["price_php"] is None or isinstance(r["price_php"], float)
        assert r["source_url"] is not None
        assert r["source_url"].startswith(
            "https://www.unionbankph.com/foreclosed-properties/"
        )
        assert r["tct"] is None


def _find_iloilo_record(recs, needle):
    for r in recs:
        if r["province"] != "Iloilo":
            continue
        location = (r.get("location_text") or "")
        if needle.lower() in location.lower():
            return r
    raise AssertionError(
        f"expected an Iloilo record containing {needle!r}, none found in {recs!r}"
    )


def test_parse_finds_known_oton_or_pavia_iloilo_card():
    html = (FIX / "unionbank_sample.html").read_text(encoding="utf-8")
    recs = parse(html)

    try:
        r = _find_iloilo_record(recs, "Oton")
    except AssertionError:
        r = _find_iloilo_record(recs, "Pavia")

    assert isinstance(r["price_php"], float)
    assert r["price_php"] > 0
    assert r["source_url"]
    assert r["image_url"]


def test_parse_excludes_non_panay_cards():
    html = (FIX / "unionbank_sample.html").read_text(encoding="utf-8")
    # Sanity: confirm a known non-Panay card exists in the raw fixture, so
    # the exclusion assertion below actually proves filtering happened
    # (not that the selector is broken and finds nothing at all).
    assert "Las Pi" in html and "NCR" in html

    recs = parse(html)
    assert len(recs) > 0
    assert not any(r["province"] not in PANAY for r in recs)
    assert not any("Las Pi" in (r.get("location_text") or "") for r in recs)
    assert not any("Cavite" in (r.get("location_text") or "") for r in recs)
    assert not any("Quezon City" in (r.get("location_text") or "") for r in recs)


def test_area_parsing_both_present():
    floor, lot = _parse_areas("FA: 155 sqm • LA: 245 sqm")
    assert floor == 155.0
    assert lot == 245.0


def test_area_parsing_lot_only():
    floor, lot = _parse_areas("LA: 245 sqm")
    assert floor is None
    assert lot == 245.0


def test_area_parsing_floor_only():
    floor, lot = _parse_areas("FA: 155 sqm")
    assert floor == 155.0
    assert lot is None
