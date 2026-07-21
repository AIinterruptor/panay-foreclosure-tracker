"""Build pipeline: runs all scrapers, normalizes, dedups, sorts, retains-on-empty,
and emits data/listings.json, data/listings.csv, data/meta.json.

Schema: normalize.RECORD_KEYS (15 keys, ending in source_url, posted_date). The CSV writer
uses csv.DictWriter(fieldnames=RECORD_KEYS) so it tracks the schema automatically
-- nothing here hardcodes a fixed-length field list.

dedup() collapses same-fingerprint rows across sources, preferring OFFICIAL
sources (pagibig_opa, metrobank, unionbank) over aggregators
(foreclosurephilippines, lamudi), and merges seller names from both sides. It
also preserves image_url across the collision: the winner's photo is kept if
present, else the loser's photo is carried over, so a listing does not lose
its picture just because the official source that "won" the dedup didn't
happen to have one. When neither side is OFFICIAL (aggregator-vs-aggregator),
the first-seen row wins -- see merge_with_prior's deterministic source
ordering below, which is why scrapers are registered foreclosurephilippines-
before-lamudi in main().

guard_source_rows() is an anti-wipeout guard that runs before merge_with_prior:
merge_with_prior already retains a source's prior rows when it returns exactly
0, but a PARTIAL scrape (e.g. a Cloudflare soft-block letting 5 rows through
where ~65 is normal) would otherwise overwrite good data with a thin result.
The guard discards fresh rows for a source when they fall under `threshold`
(default 50%) of that source's prior row count -- but only when the prior
count is >=10, so new/small sources are never guarded for lack of a baseline.
"""
import concurrent.futures
import csv, json, re, pathlib
from datetime import datetime, timezone, timedelta
from normalize import RECORD_KEYS

# Data lives INSIDE docs/ so GitHub Pages (served from /docs) can fetch it.
# The dashboard requests "data/listings.json" relative to docs/index.html.
DATA = pathlib.Path(__file__).resolve().parent / "docs" / "data"
PROV_ORDER = ["Iloilo", "Capiz", "Aklan", "Antique", "Guimaras"]
OFFICIAL = {"pagibig_opa", "metrobank", "unionbank"}

# Detail-page enrichment (enrich.parse_detail) only knows how to parse
# foreclosurephilippines and metrobank detail pages. UnionBank and Lamudi
# detail pages are NOT parseable by parse_detail -- routing their source_urls
# into enrich_listings would waste the per-run fetch budget (cap=12) on pages
# that can never yield a branch/tct, and would pollute detail_cache.json with
# permanent "error"/"no_branch" entries. Only records whose source is in this
# set are passed to enrich_listings(); everything else is recombined
# untouched (branch/tct stay whatever the base scrape produced, i.e. None).
ENRICHABLE = {"foreclosurephilippines", "metrobank"}

# Per-scraper wall-clock budget for run_with_timeout() in the fetch loop below.
# Tune this if a legitimate slow source needs more room, or to shrink CI time.
SCRAPER_TIMEOUT_S = 180  # 3 minutes


def run_with_timeout(fn, timeout_s):
    """Run fn() with a wall-clock budget so a single hung scraper (e.g. a
    Playwright/browser context stalled on an Akamai bot-challenge) cannot
    block the entire CI job forever.

    Raises concurrent.futures.TimeoutError if fn() has not completed within
    timeout_s (on CPython 3.11+ this is the same class as the builtin
    TimeoutError -- see module docs).

    CAVEAT: a Python thread cannot be forcibly killed. On timeout, the hung
    fn() thread may keep running in the background as a leaked thread; the
    caller proceeds without waiting on it. We deliberately do NOT call
    executor.shutdown(wait=True) (that would block on the hung thread) --
    the executor and its thread are simply abandoned to garbage collection.
    This is an accepted tradeoff: the goal is for the pipeline to proceed,
    not to guarantee the hung thread is reaped.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_s)
    finally:
        executor.shutdown(wait=False)

def fingerprint(rec):
    loc = re.sub(r"\s+", " ", (rec.get("location_text") or "").strip().lower())
    price = rec.get("price_php")
    lot = rec.get("lot_area_sqm")
    return f"{loc}|{round(price) if price else 'x'}|{round(lot) if lot else 'x'}"

def dedup(records):
    by_fp = {}
    for r in records:
        fp = fingerprint(r)
        if fp not in by_fp:
            by_fp[fp] = dict(r)
            continue
        keep = by_fp[fp]
        winner = r if (r["source"] in OFFICIAL and keep["source"] not in OFFICIAL) else keep
        loser = keep if winner is r else r
        sellers = {s.strip() for s in
                   [winner.get("seller"), loser.get("seller")] if s}
        winner = dict(winner)
        winner["seller"] = " / ".join(sorted(sellers))
        # Preserve a photo across the collision: keep the winner's image_url if
        # present, else fall back to the loser's so a photo from either source
        # survives the merge.
        if not winner.get("image_url") and loser.get("image_url"):
            winner["image_url"] = loser["image_url"]
        by_fp[fp] = winner
    return list(by_fp.values())

def _municipality(rec):
    loc = (rec.get("location_text") or "")
    return loc.split(",")[0].strip().lower()

def count_near_miss_dupes(records):
    """Post-dedup diagnostic: count record PAIRS that look like they describe
    the same property (same province, same municipality -- first token of
    location_text before the first comma, lowercased -- and price within 1%
    of each other) but which carry DIFFERENT fingerprints and therefore did
    NOT get collapsed by dedup(). A high count is a signal the fingerprint
    (location_text + price + lot_area) may need loosening.

    Deliberately O(n^2) -- n is small (<200 records/run), simplicity wins.
    Pairs with identical fingerprints are excluded (those already deduped,
    so they are exact dupes, not "near" ones). Pairs where either price is
    None are skipped (a % comparison is undefined).
    """
    n = len(records)
    count = 0
    for i in range(n):
        a = records[i]
        pa = a.get("price_php")
        if pa is None:
            continue
        for j in range(i + 1, n):
            b = records[j]
            pb = b.get("price_php")
            if pb is None:
                continue
            if a.get("province") != b.get("province"):
                continue
            if _municipality(a) != _municipality(b):
                continue
            if fingerprint(a) == fingerprint(b):
                continue
            if pa == 0 and pb == 0:
                continue
            base = max(abs(pa), abs(pb)) or 1.0
            if abs(pa - pb) / base <= 0.01:
                count += 1
    return count

def sort_records(records):
    def key(r):
        pi = PROV_ORDER.index(r["province"]) if r["province"] in PROV_ORDER else 99
        price = r.get("price_php")
        return (pi, price is None, price if price is not None else 0.0)
    return sorted(records, key=key)

def guard_source_rows(fresh_by_source, prior, status, threshold=0.5):
    """Anti-wipeout guard: run BEFORE merge_with_prior.

    merge_with_prior already retains a source's prior rows when it returns
    EXACTLY 0 (and marks it stale). That doesn't catch a PARTIAL scrape --
    e.g. a Cloudflare soft-block/challenge that lets a handful of rows
    through instead of the usual ~65. If a source had meaningful prior data
    (>=10 rows) and the fresh count comes back under `threshold` of that
    baseline (but > 0), treat it as a suspect partial scrape: discard the
    fresh rows for that source and mark it not-ok/stale so merge_with_prior
    falls back to retaining the prior rows.
    """
    prior_by_source = {}
    for r in prior:
        prior_by_source.setdefault(r["source"], []).append(r)

    for source, fresh in list(fresh_by_source.items()):
        prior_count = len(prior_by_source.get(source, []))
        fresh_count = len(fresh)
        if prior_count >= 10 and fresh_count > 0 and fresh_count < threshold * prior_count:
            fresh_by_source[source] = []
            status.setdefault(source, {}).update({
                "ok": False,
                "stale": True,
                "error": f"partial scrape guarded: got {fresh_count}, prior {prior_count} (<{int(threshold * 100)}%)",
            })
    return fresh_by_source

def merge_with_prior(fresh_by_source, prior, source_status):
    out = []
    prior_by_source = {}
    for r in prior:
        prior_by_source.setdefault(r["source"], []).append(r)
    # Deterministic order: fresh_by_source's own iteration order first (a plain
    # dict, so this is caller registration order -- see main()'s `scrapers`
    # dict, registered so foreclosurephilippines precedes lamudi), then any
    # prior-only sources not present in this run's fresh set. A plain
    # `set(fresh_by_source) | set(prior_by_source)` is hash-ordered (and would
    # not reliably preserve registration order across runs/processes), which
    # would silently defeat the fp-before-lamudi dedup tie-break intent.
    all_sources = list(fresh_by_source) + [s for s in prior_by_source if s not in fresh_by_source]
    for src in all_sources:
        fresh = fresh_by_source.get(src, [])
        if fresh:
            out.extend(fresh)
            source_status.setdefault(src, {})["stale"] = False
        else:
            retained = prior_by_source.get(src, [])
            out.extend(retained)
            source_status.setdefault(src, {})["stale"] = True
    return out

def _manila_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))

def main():
    from scrapers import foreclosurephilippines, pagibig_opa, metrobank, unionbank, lamudi
    # Order matters: this dict's iteration order feeds merge_with_prior's
    # deterministic all_sources ordering, which in turn is dedup()'s
    # first-seen tie-break order for same-fingerprint rows where neither
    # source is OFFICIAL. foreclosurephilippines is registered BEFORE lamudi
    # so the backbone aggregator (richer detail pages + enrichment cache)
    # wins ties over the newer aggregator.
    scrapers = {"foreclosurephilippines": foreclosurephilippines,
                "pagibig_opa": pagibig_opa, "metrobank": metrobank,
                "unionbank": unionbank, "lamudi": lamudi}
    DATA.mkdir(exist_ok=True)
    prior = []
    if (DATA / "listings.json").exists():
        prior = json.loads((DATA / "listings.json").read_text(encoding="utf-8"))

    fresh_by_source, status = {}, {}
    for name, mod in scrapers.items():
        try:
            rows = run_with_timeout(mod.fetch, SCRAPER_TIMEOUT_S)
            fresh_by_source[name] = rows
            status[name] = {"count": len(rows), "ok": True, "error": None}
        except concurrent.futures.TimeoutError:
            # Must be caught before the generic Exception handler below:
            # concurrent.futures.TimeoutError is the builtin TimeoutError on
            # Python 3.11+, which IS an Exception subclass, so ordering here
            # is load-bearing -- a hung scraper must not blow the whole CI
            # job, but it should still be visibly marked stale/not-ok.
            fresh_by_source[name] = []
            status[name] = {"count": 0, "ok": False,
                             "error": f"timeout after {SCRAPER_TIMEOUT_S}s",
                             "stale": True}
        except Exception as e:  # defensive; fetch() shouldn't raise
            fresh_by_source[name] = []
            status[name] = {"count": 0, "ok": False, "error": str(e)}

    fresh_by_source = guard_source_rows(fresh_by_source, prior, status)
    merged = merge_with_prior(fresh_by_source, prior, status)
    records = sort_records(dedup(merged))

    near_miss = count_near_miss_dupes(records)

    # Detail-page enrichment (bank branch + TCT number), keyed by source_url and
    # cached across runs. Runs strictly AFTER records are finalized and BEFORE
    # anything is written, wrapped so a total enrichment failure can never
    # compromise the base pipeline's output -- listings.json/csv/meta.json must
    # still get written with the base data even if this whole block throws.
    #
    # Scoping: only records whose source is in ENRICHABLE are passed to
    # enrich_listings -- parse_detail can't parse unionbank/lamudi detail
    # pages, so routing them in would waste the fetch cap and pollute the
    # cache with permanent error entries. Non-enrichable records are
    # recombined afterward untouched (never dropped) and the list is
    # re-sorted so branch/tct-adding enrichment doesn't disturb the
    # province/price ordering the dashboard depends on.
    try:
        from enrich import load_cache, save_cache, enrich_listings, playwright_fetch_detail
        cache_path = DATA / "detail_cache.json"
        cache = load_cache(cache_path)
        enrichable = [r for r in records if r.get("source") in ENRICHABLE]
        non_enrichable = [r for r in records if r.get("source") not in ENRICHABLE]
        enriched, cache, n = enrich_listings(
            enrichable, cache, playwright_fetch_detail, cap=12,
            now_iso=datetime.now(timezone.utc).isoformat())
        save_cache(cache_path, cache)
        records = sort_records(enriched + non_enrichable)
        print(f"Enriched: {n} detail fetches this run")
    except Exception as e:
        print(f"Enrichment skipped (non-fatal): {e}")

    (DATA / "listings.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    with (DATA / "listings.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECORD_KEYS)
        w.writeheader(); w.writerows(records)
    now = _manila_now()
    meta = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(),
        "last_run_manila": now.strftime("%Y-%m-%d %H:%M %Z"),
        "total": len(records),
        "per_source": status,
        "near_miss_dupes": near_miss,
    }
    (DATA / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {len(records)} records")

if __name__ == "__main__":
    main()
    # A timed-out scraper (see run_with_timeout) leaks a non-daemon thread
    # that CPython's interpreter shutdown will otherwise join forever --
    # silently defeating the whole point of the timeout by hanging the CI
    # job at process exit instead of inside main(). All output files are
    # already written and closed above, so it's safe to hard-exit here
    # instead of falling through to the normal (blocking) interpreter
    # shutdown. Only done in the __main__ guard, not inside main() itself,
    # so tests that call build.main() in-process are unaffected.
    # os._exit() skips the normal atexit/buffer-flush machinery, so stdout
    # and stderr are flushed explicitly first -- otherwise buffered print()
    # output (including this module's own status prints) would be silently
    # dropped from CI logs.
    import os, sys
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
