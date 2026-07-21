import pathlib
import re
from scrapers.foreclosurephilippines import parse

FIX = pathlib.Path(__file__).parent / "fixtures"


def test_parse_iloilo_returns_normalized_records():
    html = (FIX / "foreclosurephilippines_iloilo.html").read_text(encoding="utf-8")
    recs = parse(html, "Iloilo")
    assert len(recs) > 0
    r = recs[0]
    assert r["source"] == "foreclosurephilippines"
    assert r["province"] == "Iloilo"
    assert r["location_text"]  # non-empty
    assert r["maps_url"].startswith("https://www.google.com/maps/search/")
    # price is float-or-None, never a string
    assert r["price_php"] is None or isinstance(r["price_php"], float)


def test_parse_iloilo_finds_pag_ibig_seller():
    html = (FIX / "foreclosurephilippines_iloilo.html").read_text(encoding="utf-8")
    recs = parse(html, "Iloilo")
    assert any(r["seller"] == "Pag-IBIG" for r in recs)


def test_parse_guimaras_smoke():
    html = (FIX / "foreclosurephilippines_guimaras.html").read_text(encoding="utf-8")
    recs = parse(html, "Guimaras")
    # Guimaras is thin; assert it parses without error and tags province.
    assert all(r["province"] == "Guimaras" for r in recs)


def test_parse_guimaras_finds_pdic_seller():
    html = (FIX / "foreclosurephilippines_guimaras.html").read_text(encoding="utf-8")
    recs = parse(html, "Guimaras")
    assert any(r["seller"] == "PDIC" for r in recs)


def test_image_url_placeholder_is_nulled_but_real_photos_pass_through():
    iloilo = parse(
        (FIX / "foreclosurephilippines_iloilo.html").read_text(encoding="utf-8"),
        "Iloilo",
    )
    guimaras = parse(
        (FIX / "foreclosurephilippines_guimaras.html").read_text(encoding="utf-8"),
        "Guimaras",
    )
    all_recs = iloilo + guimaras
    assert any(
        r["image_url"] and r["image_url"].startswith("https://") for r in all_recs
    )
    assert not any(
        r["image_url"] and "no-image-4" in r["image_url"] for r in all_recs
    )


def test_missing_floor_area_yields_none_without_error():
    html = (FIX / "foreclosurephilippines_guimaras.html").read_text(encoding="utf-8")
    recs = parse(html, "Guimaras")
    assert len(recs) > 0
    # Guimaras listings are all vacant lots / no floor-area node in this fixture
    assert all(r["floor_area_sqm"] is None for r in recs)


def test_source_url_and_posted_date_pass_through_from_list_page():
    html = (FIX / "foreclosurephilippines_iloilo.html").read_text(encoding="utf-8")
    recs = parse(html, "Iloilo")
    assert len(recs) > 0
    assert any(
        r["source_url"]
        and r["source_url"].startswith(
            "https://www.foreclosurephilippines.com/advert"
        )
        for r in recs
    )
    assert any(
        r["posted_date"] and re.match(r"^\d{4}/\d{2}/\d{2}$", r["posted_date"])
        for r in recs
    )
    # every record with a source_url must have a non-empty (not just truthy-looking) value
    for r in recs:
        if r["source_url"] is not None:
            assert r["source_url"] != ""
