import json
import pathlib

import pytest

from enrich import load_cache, save_cache, parse_detail, classify, enrich_listings

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _fp_html():
    return (FIXTURES / "fp_detail_sample.html").read_text(encoding="utf-8")


def _metrobank_html():
    return (FIXTURES / "metrobank_detail_sample.html").read_text(encoding="utf-8")


# ---------- load_cache / save_cache ----------

def test_load_cache_missing_file_returns_empty_dict(tmp_path):
    assert load_cache(tmp_path / "nope.json") == {}


def test_load_cache_corrupt_file_returns_empty_dict(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert load_cache(p) == {}


def test_save_cache_then_load_cache_roundtrips(tmp_path):
    p = tmp_path / "detail_cache.json"
    cache = {"https://example.com/x": {"status": "ok", "branch": "B", "tct": "T",
                                        "fetched_at": "2026-07-21T00:00:00Z",
                                        "attempts": 1, "last_error": None}}
    save_cache(p, cache)
    assert load_cache(p) == cache


# ---------- parse_detail ----------

def test_parse_detail_fp_extracts_branch_and_tct():
    parsed = parse_detail(_fp_html(), "foreclosurephilippines")
    assert parsed["branch"] == "BACOLOD BRANCH"
    assert parsed["tct"] == "090-2020004231"


def test_parse_detail_metrobank_branch_none_tct_present():
    parsed = parse_detail(_metrobank_html(), "metrobank")
    assert parsed["branch"] is None
    assert parsed["tct"] == "068-2017006956"


def test_parse_detail_fp_missing_fields_returns_none():
    html = "<html><body><ul><li>nothing here</li></ul></body></html>"
    parsed = parse_detail(html, "foreclosurephilippines")
    assert parsed["branch"] is None
    assert parsed["tct"] is None


# ---------- classify ----------

def test_classify_fp_ok_when_branch_found():
    assert classify({"branch": "BACOLOD BRANCH", "tct": "090-1"}, "foreclosurephilippines") == "ok"


def test_classify_fp_no_branch_when_branch_missing():
    assert classify({"branch": None, "tct": "090-1"}, "foreclosurephilippines") == "no_branch"


def test_classify_metrobank_ok_when_tct_found_despite_no_branch_concept():
    assert classify({"branch": None, "tct": "068-1"}, "metrobank") == "ok"


def test_classify_metrobank_no_branch_when_tct_missing():
    assert classify({"branch": None, "tct": None}, "metrobank") == "no_branch"


# ---------- enrich_listings ----------

def _rec(source_url=None, **kw):
    base = {"source": "foreclosurephilippines", "seller": "Pag-IBIG",
            "location_text": "Oton, Iloilo", "province": "Iloilo",
            "price_php": 1000.0, "lot_area_sqm": 100.0, "tct": None,
            "branch": None, "source_url": source_url}
    base.update(kw)
    return base


def test_enrich_listings_fetches_new_url_and_sets_ok_status():
    records = [_rec(source_url="https://x/1")]
    cache = {}
    calls = []

    def fetch(url):
        calls.append(url)
        return _fp_html()

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert n_fetched == 1
    assert calls == ["https://x/1"]
    assert out_records[0]["branch"] == "BACOLOD BRANCH"
    assert out_records[0]["tct"] == "090-2020004231"
    assert out_cache["https://x/1"]["status"] == "ok"
    assert out_cache["https://x/1"]["attempts"] == 1


def test_enrich_listings_terminal_ok_not_refetched():
    records = [_rec(source_url="https://x/1")]
    cache = {"https://x/1": {"status": "ok", "branch": "BACOLOD BRANCH",
                              "tct": "090-2020004231", "fetched_at": "2026-07-01T00:00:00Z",
                              "attempts": 1, "last_error": None}}
    calls = []

    def fetch(url):
        calls.append(url)
        return _fp_html()

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert calls == []
    assert n_fetched == 0
    assert out_records[0]["branch"] == "BACOLOD BRANCH"
    assert out_records[0]["tct"] == "090-2020004231"


def test_enrich_listings_terminal_no_branch_not_refetched():
    records = [_rec(source_url="https://x/1")]
    cache = {"https://x/1": {"status": "no_branch", "branch": None,
                              "tct": None, "fetched_at": "2026-07-01T00:00:00Z",
                              "attempts": 1, "last_error": None}}
    calls = []

    def fetch(url):
        calls.append(url)
        return _fp_html()

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert calls == []
    assert n_fetched == 0
    assert out_records[0]["branch"] is None


def test_enrich_listings_cap_respected():
    records = [_rec(source_url=f"https://x/{i}") for i in range(15)]
    cache = {}
    calls = []

    def fetch(url):
        calls.append(url)
        return _fp_html()

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert n_fetched == 12
    assert len(calls) == 12
    fetched_urls = {u for u in cache_keys_with_status(out_cache, "ok")}
    assert len(fetched_urls) == 12


def cache_keys_with_status(cache, status):
    return [k for k, v in cache.items() if v.get("status") == status]


def test_enrich_listings_error_increments_attempts_and_parks_at_3():
    records = [_rec(source_url="https://x/1")]
    cache = {"https://x/1": {"status": "error", "branch": None, "tct": None,
                              "fetched_at": "2026-07-01T00:00:00Z",
                              "attempts": 2, "last_error": "boom"}}
    calls = []

    def fetch(url):
        calls.append(url)
        return None

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    # attempts was 2, this fetch fails -> attempts becomes 3
    assert calls == ["https://x/1"]
    assert n_fetched == 1
    assert out_cache["https://x/1"]["status"] == "error"
    assert out_cache["https://x/1"]["attempts"] == 3
    assert out_records[0]["branch"] is None

    # Now parked at 3 attempts -> must NOT be fetched again.
    calls2 = []

    def fetch2(url):
        calls2.append(url)
        return None

    out_records2, out_cache2, n_fetched2 = enrich_listings(
        out_records, out_cache, fetch2, cap=12, now_iso="2026-07-21T00:10:00Z")

    assert calls2 == []
    assert n_fetched2 == 0
    assert out_cache2["https://x/1"]["attempts"] == 3


def test_enrich_listings_fetch_raising_is_treated_as_error():
    records = [_rec(source_url="https://x/1")]
    cache = {}

    def fetch(url):
        raise RuntimeError("network exploded")

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert n_fetched == 1
    assert out_cache["https://x/1"]["status"] == "error"
    assert out_cache["https://x/1"]["attempts"] == 1
    assert out_cache["https://x/1"]["last_error"]
    assert out_records[0]["branch"] is None


def test_enrich_listings_records_without_source_url_untouched():
    records = [_rec(source_url=None)]
    cache = {}

    def fetch(url):
        raise AssertionError("should never be called")

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert n_fetched == 0
    assert out_records[0]["branch"] is None
    assert out_records[0]["tct"] is None
    assert out_cache == {}


def test_enrich_listings_one_bad_record_does_not_abort_batch():
    # A malformed record (missing keys entirely) must not blow up the whole batch.
    good = _rec(source_url="https://x/1")
    bad = {"source_url": "https://x/2"}  # missing 'source' key etc.
    records = [bad, good]
    cache = {}

    def fetch(url):
        return _fp_html()

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert len(out_records) == 2
    # good record still got enriched
    good_out = [r for r in out_records if r.get("source_url") == "https://x/1"][0]
    assert good_out["branch"] == "BACOLOD BRANCH"


# ---------- CRITICAL REGRESSION TEST ----------

def test_enrich_listings_total_block_returns_records_unchanged_and_does_not_raise():
    """Empty cache + fetch_detail that ALWAYS returns None (total Cloudflare block
    on detail pages) must return the SAME records (same count, base fields intact,
    branch/tct just None) and must NOT raise. This is the binding 'does it ship' test:
    enrichment failure cannot shrink or break the base output.
    """
    records = [
        _rec(source_url="https://x/1", price_php=1000.0),
        _rec(source_url="https://x/2", price_php=2000.0, source="metrobank"),
        _rec(source_url=None, price_php=3000.0),
    ]
    base_snapshot = [dict(r) for r in records]
    cache = {}

    def fetch_always_none(url):
        return None

    out_records, out_cache, n_fetched = enrich_listings(
        records, cache, fetch_always_none, cap=12, now_iso="2026-07-21T00:00:00Z")

    assert len(out_records) == len(base_snapshot)
    for base, out in zip(base_snapshot, out_records):
        # Every field survives a total block, INCLUDING branch/tct — enrichment
        # is additive-only and must never null out or alter base-schema data.
        for key in base:
            assert out[key] == base[key]
        assert out["branch"] is None


def test_enrich_additive_never_overwrites_preexisting_tct_on_total_block():
    """A tct the base scrape already set must survive a failed/capped fetch."""
    records = [_rec(source_url="https://x/1", price_php=1000.0, tct="T-EXISTING")]
    out_records, _, _ = enrich_listings(
        records, {}, lambda url: None, cap=12, now_iso="2026-07-21T00:00:00Z")
    assert out_records[0]["tct"] == "T-EXISTING"  # not wiped to None
