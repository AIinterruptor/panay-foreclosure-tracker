# Panay Foreclosure Tracker

An auto-refreshing register of bank/government foreclosure property listings across
Panay Island (Iloilo, Capiz, Aklan, Antique) and Guimaras, Philippines. A GitHub
Actions workflow scrapes multiple sources on a schedule, merges and dedupes the
results, and republishes a static site via GitHub Pages.

## Sources

- **foreclosurephilippines.com** — aggregator, backbone source, covers all five
  provinces. Cloudflare-fronted, so it can soft-block or challenge requests from
  unfamiliar IPs (see the CI caveat below).
- **Pag-IBIG OPA (Online Public Auction)** — official source, Iloilo + Capiz.
  As of writing, Pag-IBIG's own OPA search endpoint is returning server errors
  on their end (not a bot-block). The scraper is built to self-recover: it
  degrades to an empty result rather than raising, and the build guard retains
  the last-known-good rows until Pag-IBIG's endpoint comes back.
- **Metrobank assets-for-sale** — official source, nationwide list filtered to
  Panay + Guimaras.
- **foreclosedbahay.com is intentionally excluded** — its robots.txt bans AI/bot
  crawlers, so it is out of scope for this project.

## How it works

1. A GitHub Actions workflow (`.github/workflows/refresh.yml`) runs every 6
   hours (`workflow_dispatch` also allows manual runs).
2. It installs dependencies (including Playwright/Chromium) and runs
   `python build.py`.
3. `build.py` calls each scraper's `fetch()`, then:
   - **guards** against partial/blocked scrapes (see below),
   - **merges** fresh rows with the prior `data/listings.json` (a source that
     returns nothing keeps its previous rows and is marked `stale`),
   - **dedupes** matching listings across sources (preferring official sources
     over the aggregator, merging seller names, preserving photos),
   - **sorts** and writes `data/listings.json`, `data/listings.csv`, and
     `data/meta.json` (per-source status/counts/timestamps).
4. If `data/` changed, the workflow commits and pushes the update. If nothing
   changed (e.g. every source came back stale), it commits nothing — the job
   still succeeds.
5. `docs/` is a static site reading from `data/`, served via GitHub Pages.

## Anti-wipeout guard

`merge_with_prior` already handles a source returning **exactly 0** rows: it
retains the prior rows and marks the source `stale`. That alone doesn't catch a
**partial** scrape — e.g. foreclosurephilippines normally yields ~60-90 rows,
but a Cloudflare soft-block/challenge might let only a handful through. A
partial result like that would otherwise silently overwrite good data with a
thin one.

`guard_source_rows()` runs before the merge: for any source with at least 10
prior rows, if the fresh count is greater than 0 but less than 50% of the prior
count, the fresh rows are discarded and the source is marked `ok: false`,
`stale: true` with a `"partial scrape guarded"` error — so `merge_with_prior`
falls back to the last-known-good rows instead. New or small sources (fewer
than 10 prior rows) are never guarded, since there's no baseline to judge them
against.

## CI-fetch caveat — why `workflow_dispatch` matters

This workflow has **never run from GitHub's hosted-runner datacenter IP
range**. foreclosurephilippines.com is Cloudflare-fronted, and datacenter IPs
are exactly what Cloudflare's bot heuristics are tuned to challenge or block.
It's an open question whether scraping will even work from CI at all.

That's why the workflow is manually triggerable (`workflow_dispatch`): **run it
by hand first** and check the Actions log / `data/meta.json` before trusting
the schedule. If CI gets soft-blocked, `build.py` doesn't raise and the guard
above prevents bad data from being committed — the run will just produce "no
changes" and succeed quietly. That is expected, tolerated behavior, not a
failure to fix reflexively.

## Coverage note

Coverage is uneven by design of the underlying sources, not a bug:

- **Iloilo** and **Capiz** have the deepest coverage (aggregator + Pag-IBIG when
  it's up).
- **Guimaras** and **Antique** are comparatively thin — fewer listings exist
  from these sources for those provinces.
- **Aklan** currently has little to no coverage — none of the three sources
  are yielding rows there at time of writing. Treat a zero/near-zero Aklan
  count as a real coverage gap, not necessarily a scraper bug.

## Local development

```bash
# install deps
python -m pip install -r requirements.txt
python -m playwright install --with-deps chromium

# run the full pipeline (scrapes live sites, writes data/)
python build.py

# run tests
python -m pytest -q
```

## Enabling GitHub Pages

Settings → Pages → Source: **Deploy from a branch** → Branch: **`master`**
(this repo's actual default branch — see note below) → Folder: **`/docs`**.

> Note: this repo's local default branch is `master`, and the refresh workflow
> commits/pushes to whatever branch is checked out (i.e. `master`). If the
> GitHub remote's default branch ends up renamed to `main`, update the Pages
> source (and double-check the workflow still targets the right branch)
> accordingly — don't point Pages at a branch nothing ever pushes to.
