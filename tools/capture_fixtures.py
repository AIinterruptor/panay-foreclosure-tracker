"""Run online once to record real responses as test fixtures.
Usage: python tools/capture_fixtures.py
"""
import json
import pathlib

from playwright.sync_api import sync_playwright

FIX = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FIX.mkdir(parents=True, exist_ok=True)

PAGES = {
    "foreclosurephilippines_iloilo.html":
        "https://www.foreclosurephilippines.com/location/iloilo",
    "foreclosurephilippines_guimaras.html":
        "https://www.foreclosurephilippines.com/location/guimaras",
    "metrobank_region6.html":
        "https://www.metrobank.com.ph/assets-for-sale/properties",
}

# Pag-IBIG OPA JSON (Iloilo). Region VI = 060000000, Iloilo province = 063000000.
# city_muni codes must match what the live dropdown actually sends (verified
# via network capture, not guessed PSGC codes) -- see task-2-report.md for
# how the working code was found.
PAGIBIG_API = ("https://www.pagibigfundservices.com/OnlinePublicAuction/"
               "ListofProperties/Load_SearchListProperties_COPA")
PAGIBIG_CANDIDATES = [
    ("Iloilo City", "063022000"),
    ("Leganes", "063017000"),
    ("Oton", "063030000"),
    ("Pavia", "063031000"),
    ("Pototan", "063033000"),
    ("Santa Barbara", "063037000"),
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36"))
        page = ctx.new_page()

        for name, url in PAGES.items():
            try:
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                except Exception:
                    # Some pages (e.g. foreclosurephilippines/location/guimaras)
                    # keep a connection open (ads/analytics) and never reach
                    # networkidle. Fall back to domcontentloaded + a settle
                    # delay so we still capture rendered content.
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(5000)
                content = page.content()
                (FIX / name).write_text(content, encoding="utf-8")
                print("saved", name, len(content), "bytes")
            except Exception as exc:
                print("FAILED", name, url, "->", repr(exc))

        # Pag-IBIG OPA JSON: try candidate city_muni codes until one returns
        # rows. The endpoint requires XHR-style headers or it falls back to
        # serving the SPA shell (HTML) with a 500 status.
        saved_pagibig = False
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        for city_name, city_code in PAGIBIG_CANDIDATES:
            api = (f"{PAGIBIG_API}?flag=1&region=060000000&province=063000000"
                   f"&city_muni={city_code}&prop_type=1"
                   f"&range_from=0&range_to=999999999"
                   f"&lot_from=0&lot_to=999999&floor_from=0&floor_to=999999"
                   f"&occupancy=0")
            try:
                resp = page.request.get(api, headers=headers)
                text = resp.text()
                try:
                    payload = json.loads(text)
                    rows = payload.get("data", []) if isinstance(payload, dict) else []
                except Exception:
                    rows = None
                print("pagibig try", city_name, city_code, "status=", resp.status,
                      "rows=", (len(rows) if rows is not None else "parse-error"))
                if rows:
                    (FIX / "pagibig_opa_iloilo.json").write_text(text, encoding="utf-8")
                    print("saved pagibig_opa_iloilo.json (", city_name, ")", len(text), "bytes")
                    saved_pagibig = True
                    break
            except Exception as exc:
                print("FAILED pagibig", city_name, city_code, "->", repr(exc))

        if not saved_pagibig:
            print("Pag-IBIG OPA: no candidate city_muni code returned rows/valid JSON. "
                  "See task-2-report.md for details.")

        browser.close()


if __name__ == "__main__":
    main()
