"""Detail-page enrichment: fetches each listing's detail page (once, cached) to
extract bank BRANCH and TCT number that don't appear on the list pages.

Enrichment is keyed by the CANONICAL ADVERT URL (a record's source_url), NOT by
the dedup fingerprint used in build.py -- the same physical property can appear
under slightly different fingerprints across scrape runs (price/area rounding
drift), but its detail-page URL is stable.

Cache file: docs/data/detail_cache.json (see build.CACHE_PATH). It lives inside
docs/ so it is inside the GitHub Pages tree and is committed to the repo,
surviving across CI runs (no re-fetch flood on every build).

Cache structure:
    {
      "<source_url>": {
          "status": "ok" | "no_branch" | "error",
          "branch": str | None,
          "tct": str | None,
          "fetched_at": "<ISO8601 UTC>",
          "attempts": int,
          "last_error": str | None,
      },
      ...
    }

Terminal states ("ok", "no_branch") are never re-fetched. "error" is retried
until attempts reaches 3, after which it is parked (treated as terminal) and
nulls are applied.

Nothing in this module may raise out of enrich_listings() -- a single bad
record, a parse failure, or a total fetch outage must never shrink or corrupt
the base pipeline's output. See tests/test_enrich.py::
test_enrich_listings_total_block_returns_records_unchanged_and_does_not_raise
for the binding regression test.
"""

import json
import time
import random
from datetime import datetime, timezone

from bs4 import BeautifulSoup

MAX_ATTEMPTS = 3

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def load_cache(path):
    """Load the detail cache from `path`. Never raises -- returns {} if the
    file is missing, unreadable, or contains corrupt JSON."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def save_cache(path, cache):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _tail_text(strong_tag):
    """Return the stripped text that follows a <strong> label tag, up to the
    next tag boundary (i.e. the strong tag's next NavigableString sibling)."""
    nxt = strong_tag.next_sibling
    if nxt is None:
        return None
    text = str(nxt).strip()
    return text or None


def _find_label_value(soup, label_substring):
    """Find a <strong> tag whose text contains `label_substring` and return
    the trailing text (the value after the label)."""
    for strong in soup.find_all("strong"):
        if label_substring.lower() in strong.get_text().lower():
            val = _tail_text(strong)
            if val:
                return val
    return None


def _parse_fp(html):
    soup = BeautifulSoup(html, "html.parser")
    branch = _find_label_value(soup, "Handling Branch")
    tct = _find_label_value(soup, "TCT/CCT Number")
    return {"branch": branch, "tct": tct}


def _parse_metrobank(html):
    soup = BeautifulSoup(html, "html.parser")
    tct = None
    for label_div in soup.find_all(class_=lambda c: c and "labelName" in c):
        if label_div.get_text(strip=True) == "TCT Number":
            value_tag = label_div.find_next_sibling()
            if value_tag is not None:
                text = value_tag.get_text(strip=True)
                tct = text or None
            break
    # Metrobank has no bank-branch concept on the detail page -- only a court
    # branch inside legal annotation prose (e.g. "RTC Branch 119, Pasay City").
    # Recon-confirmed: do NOT extract that. branch is always None here.
    return {"branch": None, "tct": tct}


def parse_detail(html, source):
    """Pure function, no network. Extracts {"branch": ..., "tct": ...} from a
    detail-page HTML string. Returns None for a field that isn't found."""
    try:
        if source == "metrobank":
            return _parse_metrobank(html)
        # Default / foreclosurephilippines-style parsing.
        return _parse_fp(html)
    except Exception:
        return {"branch": None, "tct": None}


def classify(parsed, source):
    """Determine terminal status for a parsed result.

    - metrobank has no branch concept: "ok" if tct found, else "no_branch".
    - other sources (foreclosurephilippines): "ok" if branch found (tct is a
      bonus), else "no_branch".
    """
    branch = parsed.get("branch")
    tct = parsed.get("tct")
    if source == "metrobank":
        return "ok" if tct else "no_branch"
    if branch:
        return "ok"
    return "no_branch"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _apply_cache_entry(record, entry):
    """Additive only: enrichment may ADD branch/tct to a record, never destroy
    a value the base scrape already set. A failed/capped/parked fetch yields a
    None branch/tct in the cache entry; applying that must not overwrite a
    pre-existing base-schema value. So only copy non-None cache values."""
    if entry.get("branch") is not None:
        record["branch"] = entry["branch"]
    if entry.get("tct") is not None:
        record["tct"] = entry["tct"]


def enrich_listings(records, cache, fetch_detail, cap=12, now_iso=None):
    """Enrich `records` in place (returns new list) using `cache`, fetching at
    most `cap` new/retryable URLs this run via the injected `fetch_detail`.

    Returns (records, cache, n_fetched). MUST NOT raise -- any per-record
    failure is caught and treated as a fetch error for that record so it
    cannot abort the batch.
    """
    if now_iso is None:
        now_iso = _now_iso()

    cache = dict(cache)  # don't mutate caller's dict in place unexpectedly
    out_records = []
    n_fetched = 0

    for rec in records:
        try:
            rec = dict(rec)
            url = rec.get("source_url")
            if not url:
                out_records.append(rec)
                continue

            entry = cache.get(url)

            if entry is not None and entry.get("status") in ("ok", "no_branch"):
                _apply_cache_entry(rec, entry)
                out_records.append(rec)
                continue

            if entry is not None and entry.get("status") == "error" \
                    and entry.get("attempts", 0) >= MAX_ATTEMPTS:
                _apply_cache_entry(rec, entry)
                out_records.append(rec)
                continue

            # New or retryable-error entry: only fetch if under cap this run.
            if n_fetched >= cap:
                if entry is not None:
                    _apply_cache_entry(rec, entry)
                # No cache entry and over cap: leave the record's existing
                # branch/tct untouched (additive-only — never null out base data).
                out_records.append(rec)
                continue

            prior_attempts = entry.get("attempts", 0) if entry is not None else 0
            n_fetched += 1
            try:
                html = fetch_detail(url)
            except Exception as e:
                html = None
                fetch_error = str(e)
            else:
                fetch_error = None

            if html:
                try:
                    source = rec.get("source")
                    parsed = parse_detail(html, source)
                    status = classify(parsed, source)
                    new_entry = {
                        "status": status,
                        "branch": parsed.get("branch"),
                        "tct": parsed.get("tct"),
                        "fetched_at": now_iso,
                        "attempts": prior_attempts + 1,
                        "last_error": None,
                    }
                except Exception as e:
                    new_entry = {
                        "status": "error",
                        "branch": None,
                        "tct": None,
                        "fetched_at": now_iso,
                        "attempts": prior_attempts + 1,
                        "last_error": f"parse error: {e}",
                    }
            else:
                new_entry = {
                    "status": "error",
                    "branch": None,
                    "tct": None,
                    "fetched_at": now_iso,
                    "attempts": prior_attempts + 1,
                    "last_error": fetch_error or "fetch_detail returned no content",
                }

            cache[url] = new_entry
            _apply_cache_entry(rec, new_entry)
            out_records.append(rec)
        except Exception:
            # Absolute last resort: never let one bad record abort the batch.
            out_records.append(rec if isinstance(rec, dict) else {"_enrich_error": True})

    return out_records, cache, n_fetched


def playwright_fetch_detail(url):
    """Fetch a detail page's HTML via Playwright chromium. Returns None on any
    failure -- never raises."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=_UA)
                page.goto(url, timeout=30000)
                time.sleep(2 + random.random())
                html = page.content()
                return html
            finally:
                browser.close()
    except Exception:
        return None
