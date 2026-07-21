import concurrent.futures
import json
import time

import pytest

import build
from build import (fingerprint, dedup, sort_records, merge_with_prior, guard_source_rows,
                    run_with_timeout, count_near_miss_dupes, ENRICHABLE)

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

    from scrapers import foreclosurephilippines, pagibig_opa, metrobank, unionbank, lamudi

    monkeypatch.setattr(foreclosurephilippines, "fetch",
                         lambda: [_rec(source="foreclosurephilippines")])
    monkeypatch.setattr(pagibig_opa, "fetch", lambda: [])
    monkeypatch.setattr(metrobank, "fetch",
                         lambda: [_rec(source="metrobank", location_text="Roxas, Capiz",
                                        province="Capiz")])
    monkeypatch.setattr(unionbank, "fetch",
                         lambda: [_rec(source="unionbank", location_text="Kalibo, Aklan",
                                        province="Aklan")])
    monkeypatch.setattr(lamudi, "fetch",
                         lambda: [_rec(source="lamudi", location_text="San Jose, Antique",
                                        province="Antique")])

    build.main()

    assert (tmp_path / "listings.json").exists()
    assert (tmp_path / "listings.csv").exists()
    assert (tmp_path / "meta.json").exists()

    records = json.loads((tmp_path / "listings.json").read_text(encoding="utf-8"))
    assert len(records) == 4

    meta = json.loads((tmp_path / "meta.json").read_text(encoding="utf-8"))
    assert meta["total"] == 4
    assert meta["per_source"]["pagibig_opa"]["stale"] is True
    assert set(meta["per_source"].keys()) == {
        "foreclosurephilippines", "pagibig_opa", "metrobank", "unionbank", "lamudi"}
    assert "near_miss_dupes" in meta


def test_run_with_timeout_returns_fast_fn_result():
    assert run_with_timeout(lambda: 42, timeout_s=5) == 42


def test_run_with_timeout_returns_list_result_end_to_end():
    assert run_with_timeout(lambda: [1, 2, 3], timeout_s=5) == [1, 2, 3]


def test_run_with_timeout_raises_on_hang_and_does_not_block():
    def _hang():
        time.sleep(2)
        return "never"

    start = time.monotonic()
    with pytest.raises(concurrent.futures.TimeoutError):
        run_with_timeout(_hang, timeout_s=0.3)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0


# ---------- OFFICIAL set + scraper registration ----------

def test_official_set_includes_unionbank():
    assert build.OFFICIAL == {"pagibig_opa", "metrobank", "unionbank"}
    assert "lamudi" not in build.OFFICIAL
    assert "foreclosurephilippines" not in build.OFFICIAL


def test_dedup_aggregator_tie_prefers_first_seen_fp_over_lamudi():
    # Neither source is OFFICIAL -> first-seen wins. merge_with_prior's
    # registration-order-preserving fix (fresh_by_source dict order, not a
    # hash-ordered set) is what guarantees "first-seen" actually means
    # "registered first" (foreclosurephilippines before lamudi in main()).
    fp = _rec(source="foreclosurephilippines", seller="FP Seller")
    lamudi = _rec(source="lamudi", seller="Lamudi Seller")

    fresh_by_source = {"foreclosurephilippines": [fp], "lamudi": [lamudi]}
    merged = merge_with_prior(fresh_by_source, [], {})
    out = dedup(merged)

    assert len(out) == 1
    assert out[0]["source"] == "foreclosurephilippines"


# ---------- near-miss dedup counter ----------

def test_near_miss_counts_close_price_same_municipality_different_fingerprint():
    # Same province+municipality (first token before comma), price within 1%,
    # but different fingerprints (different lot area) -> did not dedup -> near-miss.
    a = _rec(source="foreclosurephilippines", location_text="Oton, Iloilo",
              price_php=1000000.0, lot_area_sqm=100.0)
    b = _rec(source="lamudi", location_text="Oton, Iloilo",
              price_php=1005000.0, lot_area_sqm=120.0)  # 0.5% price diff, diff fp
    assert fingerprint(a) != fingerprint(b)
    assert count_near_miss_dupes([a, b]) == 1


def test_near_miss_does_not_count_identical_fingerprints_already_deduped():
    # Same fingerprint records are what dedup() collapses -- must NOT be counted
    # as a near-miss (that's an exact dupe, not a "near" one).
    a = _rec(source="foreclosurephilippines", location_text="Oton, Iloilo",
              price_php=1000000.0, lot_area_sqm=100.0)
    b = _rec(source="lamudi", location_text="Oton, Iloilo",
              price_php=1000000.0, lot_area_sqm=100.0)
    assert fingerprint(a) == fingerprint(b)
    assert count_near_miss_dupes([a, b]) == 0


def test_near_miss_ignores_different_municipality():
    a = _rec(location_text="Oton, Iloilo", price_php=1000000.0, lot_area_sqm=100.0)
    b = _rec(location_text="Roxas, Capiz", price_php=1005000.0, lot_area_sqm=120.0)
    assert count_near_miss_dupes([a, b]) == 0


def test_near_miss_ignores_price_far_apart():
    a = _rec(location_text="Oton, Iloilo", price_php=1000000.0, lot_area_sqm=100.0)
    b = _rec(location_text="Oton, Iloilo", price_php=2000000.0, lot_area_sqm=120.0)
    assert count_near_miss_dupes([a, b]) == 0


def test_near_miss_ignores_none_price():
    a = _rec(location_text="Oton, Iloilo", price_php=None, lot_area_sqm=100.0)
    b = _rec(location_text="Oton, Iloilo", price_php=1000000.0, lot_area_sqm=120.0)
    assert count_near_miss_dupes([a, b]) == 0


def test_near_miss_counts_each_pair_once_across_multiple_records():
    a = _rec(location_text="Oton, Iloilo", price_php=1000000.0, lot_area_sqm=100.0)
    b = _rec(location_text="Oton, Iloilo", price_php=1005000.0, lot_area_sqm=120.0)
    c = _rec(location_text="Roxas, Capiz", price_php=500000.0, lot_area_sqm=50.0)
    assert count_near_miss_dupes([a, b, c]) == 1


# ---------- enrichment scoping ----------

def test_enrichable_set_excludes_unionbank_and_lamudi():
    assert ENRICHABLE == {"foreclosurephilippines", "metrobank"}


def test_main_only_enriches_enrichable_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "DATA", tmp_path)

    from scrapers import foreclosurephilippines, pagibig_opa, metrobank, unionbank, lamudi

    fp_rec = _rec(source="foreclosurephilippines", location_text="Oton, Iloilo",
                   province="Iloilo", source_url="https://fp.example/listing/1")
    ub_rec = _rec(source="unionbank", location_text="Kalibo, Aklan",
                   province="Aklan", source_url="https://unionbank.example/listing/2")
    lm_rec = _rec(source="lamudi", location_text="San Jose, Antique",
                   province="Antique", source_url="https://lamudi.example/listing/3")

    monkeypatch.setattr(foreclosurephilippines, "fetch", lambda: [fp_rec])
    monkeypatch.setattr(pagibig_opa, "fetch", lambda: [])
    monkeypatch.setattr(metrobank, "fetch", lambda: [])
    monkeypatch.setattr(unionbank, "fetch", lambda: [ub_rec])
    monkeypatch.setattr(lamudi, "fetch", lambda: [lm_rec])

    fetched_urls = []

    def spy_fetch_detail(url):
        fetched_urls.append(url)
        return "<html><strong>Handling Branch</strong>Iloilo City Branch</html>"

    import enrich
    monkeypatch.setattr(enrich, "playwright_fetch_detail", spy_fetch_detail)

    build.main()

    assert fp_rec["source_url"] in fetched_urls
    assert ub_rec["source_url"] not in fetched_urls
    assert lm_rec["source_url"] not in fetched_urls

    records = json.loads((tmp_path / "listings.json").read_text(encoding="utf-8"))
    # No record dropped in the recombine.
    assert len(records) == 3
    sources = {r["source"] for r in records}
    assert sources == {"foreclosurephilippines", "unionbank", "lamudi"}
