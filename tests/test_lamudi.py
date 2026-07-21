import json
import pathlib

from scrapers.lamudi import parse

FIX = pathlib.Path(__file__).parent / "fixtures"
PANAY = {"Iloilo", "Capiz", "Aklan", "Antique", "Guimaras"}


def test_parse_returns_normalized_lamudi_records_only_panay():
    html = (FIX / "lamudi_iloilo_sample.html").read_text(encoding="utf-8")
    recs = parse(html)
    assert isinstance(recs, list)
    assert len(recs) > 0
    for r in recs:
        assert r["source"] == "lamudi"
        assert r["province"] in PANAY
        assert r["price_php"] is None or isinstance(r["price_php"], float)
        assert r["source_url"] is not None
        assert r["source_url"].startswith("https://www.lamudi.com.ph/property/")
        assert r["image_url"] is None or isinstance(r["image_url"], str)


def test_parse_finds_known_commercial_lot_listing_with_real_price_and_url():
    # Fixture position 1: "Commercial Lot For Sale in Banuyao", offers.price
    # 220000000, @id/url ".../41032-73-ba1c2596a20-bc76-19ae7a4-ac58-7f5e".
    html = (FIX / "lamudi_iloilo_sample.html").read_text(encoding="utf-8")
    recs = parse(html)
    matches = [r for r in recs if "Banuyao" in (r.get("location_text") or "") or
               (r.get("source_url") or "").endswith("41032-73-ba1c2596a20-bc76-19ae7a4-ac58-7f5e")]
    assert len(matches) == 1
    r = matches[0]
    assert r["price_php"] == 220000000.0
    assert r["source_url"] == (
        "https://www.lamudi.com.ph/property/41032-73-ba1c2596a20-bc76-19ae7a4-ac58-7f5e"
    )
    assert r["province"] == "Iloilo"
    # Commercial Lot: floorSize value 20000 is really lot area, not floor area.
    assert r["lot_area_sqm"] == 20000.0
    assert r["floor_area_sqm"] is None


def test_parse_maps_buena_mano_seller_to_bpi():
    html = (FIX / "lamudi_iloilo_sample.html").read_text(encoding="utf-8")
    recs = parse(html)
    buena_mano_mapped = [r for r in recs if r["seller"] == "BPI (Buena Mano)"]
    assert len(buena_mano_mapped) > 0


def test_parse_condo_listing_uses_floor_area_not_lot_area():
    # Position 6 in fixture: "Condo For Sale in Mandurriao", floorSize value 40.
    html = (FIX / "lamudi_iloilo_sample.html").read_text(encoding="utf-8")
    recs = parse(html)
    condos = [r for r in recs if r.get("property_type") == "Condominium"
              and r.get("floor_area_sqm") == 40.0]
    assert len(condos) == 1
    assert condos[0]["lot_area_sqm"] is None


def test_parse_returns_empty_list_when_jsonld_missing_and_does_not_raise():
    html = "<html>" + ("x" * 60000) + "</html>"
    recs = parse(html)
    assert recs == []


def test_parse_excludes_non_panay_listing_from_synthetic_mixed_jsonld():
    # Synthetic JSON-LD mirroring the real shape: one Panay + one non-Panay
    # (Cebu) RealEstateListing. Confirms the province filter actually runs
    # against addressRegion (not a vacuous pass on an all-Iloilo fixture).
    ld = [{
        "@context": "https://schema.org",
        "@graph": [{
            "@type": "SearchResultsPage",
            "mainEntity": [{
                "@type": "ItemList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "item": {
                            "@type": "RealEstateListing",
                            "name": "House For Sale in Oton",
                            "url": "https://www.lamudi.com.ph/property/panay-one",
                            "image": "https://img.lamudi.com/panay-one.jpg",
                            "address": {
                                "@type": "PostalAddress",
                                "addressLocality": "Oton",
                                "addressRegion": "Iloilo",
                            },
                            "floorSize": {"@type": "QuantitativeValue", "value": "80", "unitCode": "MTK"},
                            "offers": {"@type": "Offer", "price": "1000000"},
                        },
                    },
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "item": {
                            "@type": "RealEstateListing",
                            "name": "House For Sale in Cebu City",
                            "url": "https://www.lamudi.com.ph/property/non-panay-one",
                            "image": "https://img.lamudi.com/non-panay-one.jpg",
                            "address": {
                                "@type": "PostalAddress",
                                "addressLocality": "Cebu City",
                                "addressRegion": "Cebu",
                            },
                            "floorSize": {"@type": "QuantitativeValue", "value": "90", "unitCode": "MTK"},
                            "offers": {"@type": "Offer", "price": "2000000"},
                        },
                    },
                ],
            }],
        }],
    }]
    html = (
        "<html><body>"
        '<script type="application/ld+json">' + json.dumps(ld) + "</script>"
        "</body></html>"
    )
    recs = parse(html)
    assert len(recs) == 1
    assert recs[0]["province"] == "Iloilo"
    assert recs[0]["source_url"] == "https://www.lamudi.com.ph/property/panay-one"
