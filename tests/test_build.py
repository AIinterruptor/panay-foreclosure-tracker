import json

import build
from build import fingerprint, dedup, sort_records, merge_with_prior, guard_source_rows

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


def _many(source, n, **kw):
    return [_rec(source=source, location_text=f"Loc {i}, Iloilo", price_php=1000.0 + i, **kw)
            for i in range(n)]


def test_guard_discards_suspect_partial_scrape():
    # 65 prior rows, only 5 fresh -> guarded: fresh discarded, status marks stale/not-ok.
    prior = _many("foreclosurephilippines", 65)
    fresh_by_source = {"foreclosurephilippines": _many("foreclosurephilippines", 5)}
    status = {"foreclosurephilippines": {"count": 5, "ok": True, "error": None}}

    out = guard_source_rows(fresh_by_source, prior, status)

    assert out["foreclosurephilippines"] == []
    assert status["foreclosurephilippines"]["ok"] is False
    assert status["foreclosurephilippines"]["stale"] is True
    assert "partial" in status["foreclosurephilippines"]["error"]
    # original count must be preserved, not dropped by the status update
    assert status["foreclosurephilippines"]["count"] == 5

    # merge_with_prior then retains the prior rows for that source.
    merged = merge_with_prior(out, prior, status)
    assert len([r for r in merged if r["source"] == "foreclosurephilippines"]) == 65


def test_guard_allows_scrape_above_threshold():
    # 65 prior, 40 fresh (>50%) -> not guarded, fresh used as-is.
    prior = _many("foreclosurephilippines", 65)
    fresh_rows = _many("foreclosurephilippines", 40)
    fresh_by_source = {"foreclosurephilippines": fresh_rows}
    status = {"foreclosurephilippines": {"count": 40, "ok": True, "error": None}}

    out = guard_source_rows(fresh_by_source, prior, status)

    assert out["foreclosurephilippines"] == fresh_rows
    assert status["foreclosurephilippines"]["ok"] is True
    assert "stale" not in status["foreclosurephilippines"] or status["foreclosurephilippines"].get("stale") is not True
    assert "error" not in status["foreclosurephilippines"] or status["foreclosurephilippines"]["error"] is None


def test_guard_leaves_zero_fresh_untouched_for_existing_retain_behavior():
    # 65 prior, 0 fresh -> guard leaves as-is (not double-processed); merge_with_prior
    # still applies the existing retain-and-stale behavior.
    prior = _many("metrobank", 65)
    fresh_by_source = {"metrobank": []}
    status = {"metrobank": {"count": 0, "ok": True, "error": None}}

    out = guard_source_rows(fresh_by_source, prior, status)

    assert out["metrobank"] == []
    # guard must not itself flip ok/error for the zero case -- that's merge_with_prior's job
    assert status["metrobank"]["ok"] is True
    assert status["metrobank"]["error"] is None

    merged = merge_with_prior(out, prior, status)
    assert len([r for r in merged if r["source"] == "metrobank"]) == 65
    assert status["metrobank"]["stale"] is True


def test_guard_never_applies_to_new_source_with_small_prior():
    # New source, 0 prior rows, 3 fresh -> never guarded (can't judge baseline).
    prior = []
    fresh_rows = _many("newsource", 3)
    fresh_by_source = {"newsource": fresh_rows}
    status = {"newsource": {"count": 3, "ok": True, "error": None}}

    out = guard_source_rows(fresh_by_source, prior, status)

    assert out["newsource"] == fresh_rows
    assert status["newsource"]["ok"] is True


def test_main_runs_end_to_end_with_mocked_scrapers(tmp_path, monkeypatch):
    # Point build.DATA at a temp dir so this test never touches the real data/.
    monkeypatch.setattr(build, "DATA", tmp_path)

    from scrapers import foreclosurephilippines, pagibig_opa, metrobank

    monkeypatch.setattr(foreclosurephilippines, "fetch",
                         lambda: [_rec(source="foreclosurephilippines")])
    monkeypatch.setattr(pagibig_opa, "fetch", lambda: [])
    monkeypatch.setattr(metrobank, "fetch",
                         lambda: [_rec(source="metrobank", location_text="Roxas, Capiz",
                                        province="Capiz")])

    build.main()

    assert (tmp_path / "listings.json").exists()
    assert (tmp_path / "listings.csv").exists()
    assert (tmp_path / "meta.json").exists()

    records = json.loads((tmp_path / "listings.json").read_text(encoding="utf-8"))
    assert len(records) == 2

    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["total"] == 2
    assert meta["per_source"]["pagibig_opa"]["stale"] is True
