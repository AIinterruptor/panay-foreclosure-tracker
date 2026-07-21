"""Build pipeline: runs all scrapers, normalizes, dedups, sorts, retains-on-empty,
and emits data/listings.json, data/listings.csv, data/meta.json.

Schema: normalize.RECORD_KEYS (15 keys, ending in source_url, posted_date). The CSV writer
uses csv.DictWriter(fieldnames=RECORD_KEYS) so it tracks the schema automatically
-- nothing here hardcodes a fixed-length field list.

dedup() collapses same-fingerprint rows across sources, preferring OFFICIAL
sources (pagibig_opa, metrobank) over the aggregator (foreclosurephilippines),
and merges seller names from both sides. It also preserves image_url across
the collision: the winner's photo is kept if present, else the loser's photo
is carried over, so a listing does not lose its picture just because the
official source that "won" the dedup didn't happen to have one.

guard_source_rows() is an anti-wipeout guard that runs before merge_with_prior:
merge_with_prior already retains a source's prior rows when it returns exactly
0, but a PARTIAL scrape (e.g. a Cloudflare soft-block letting 5 rows through
where ~65 is normal) would otherwise overwrite good data with a thin result.
The guard discards fresh rows for a source when they fall under `threshold`
(default 50%) of that source's prior row count -- but only when the prior
count is >=10, so new/small sources are never guarded for lack of a baseline.
"""
import csv, json, re, pathlib
from datetime import datetime, timezone, timedelta
from normalize import RECORD_KEYS

# Data lives INSIDE docs/ so GitHub Pages (served from /docs) can fetch it.
# The dashboard requests "data/listings.json" relative to docs/index.html.
DATA = pathlib.Path(__file__).resolve().parent / "docs" / "data"
PROV_ORDER = ["Iloilo", "Capiz", "Aklan", "Antique", "Guimaras"]
OFFICIAL = {"pagibig_opa", "metrobank"}

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
    all_sources = set(fresh_by_source) | set(prior_by_source)
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
    from scrapers import foreclosurephilippines, pagibig_opa, metrobank
    scrapers = {"foreclosurephilippines": foreclosurephilippines,
                "pagibig_opa": pagibig_opa, "metrobank": metrobank}
    DATA.mkdir(exist_ok=True)
    prior = []
    if (DATA / "listings.json").exists():
        prior = json.loads((DATA / "listings.json").read_text(encoding="utf-8"))

    fresh_by_source, status = {}, {}
    for name, mod in scrapers.items():
        try:
            rows = mod.fetch()
            fresh_by_source[name] = rows
            status[name] = {"count": len(rows), "ok": True, "error": None}
        except Exception as e:  # defensive; fetch() shouldn't raise
            fresh_by_source[name] = []
            status[name] = {"count": 0, "ok": False, "error": str(e)}

    fresh_by_source = guard_source_rows(fresh_by_source, prior, status)
    merged = merge_with_prior(fresh_by_source, prior, status)
    records = sort_records(dedup(merged))

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
    }
    (DATA / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {len(records)} records")

if __name__ == "__main__":
    main()
