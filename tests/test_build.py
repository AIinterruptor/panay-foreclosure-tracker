from build import fingerprint, dedup, sort_records, merge_with_prior

def _rec(**kw):
    base = {"source": "x", "seller": "S", "location_text": "Oton, Iloilo",
            "province": "Iloilo", "price_php": 1000.0, "lot_area_sqm": 100.0}
    base.update(kw); return base

def test_fingerprint_stable_across_case_and_price_rounding():
    a = _rec(location_text="Oton, Iloilo", price_php=1000.0)
    b = _rec(location_text="OTON,  ILOILO", price_php=1000.49)
    assert fingerprint(a) == fingerprint(b)

def test_dedup_prefers_official_and_merges_sellers():
    off = _rec(source="pagibig_opa", seller="Pag-IBIG")
    agg = _rec(source="foreclosurephilippines", seller="BDO")
    out = dedup([agg, off])
    assert len(out) == 1
    assert out[0]["source"] == "pagibig_opa"
    assert "BDO" in out[0]["seller"] and "Pag-IBIG" in out[0]["seller"]

def test_dedup_preserves_image_url_across_collision():
    # Winner (official) lacks a photo; loser (aggregator) has one -> result keeps the photo.
    off = _rec(source="pagibig_opa", seller="Pag-IBIG", image_url=None)
    agg = _rec(source="foreclosurephilippines", seller="BDO",
                image_url="https://example.com/photo.jpg")
    out = dedup([agg, off])
    assert len(out) == 1
    assert out[0]["image_url"] == "https://example.com/photo.jpg"

def test_dedup_keeps_winner_image_url_when_present():
    # Winner (official) already has a photo; loser's different photo must NOT overwrite it.
    off = _rec(source="pagibig_opa", seller="Pag-IBIG",
                image_url="https://example.com/official.jpg")
    agg = _rec(source="foreclosurephilippines", seller="BDO",
                image_url="https://example.com/aggregator.jpg")
    out = dedup([agg, off])
    assert len(out) == 1
    assert out[0]["image_url"] == "https://example.com/official.jpg"

def test_sort_province_then_price_none_last():
    recs = [_rec(province="Guimaras", price_php=5.0),
            _rec(province="Iloilo", price_php=None),
            _rec(province="Iloilo", price_php=10.0)]
    out = sort_records(recs)
    assert out[0]["province"] == "Iloilo" and out[0]["price_php"] == 10.0
    assert out[1]["province"] == "Iloilo" and out[1]["price_php"] is None
    assert out[2]["province"] == "Guimaras"

def test_merge_with_prior_retains_empty_source():
    prior = [_rec(source="metrobank", location_text="Roxas, Capiz", province="Capiz")]
    status = {}
    out = merge_with_prior({"metrobank": []}, prior, status)
    assert any(r["source"] == "metrobank" for r in out)
    assert status["metrobank"]["stale"] is True
