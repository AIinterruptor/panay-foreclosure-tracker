from normalize import RECORD_KEYS, maps_url, to_float, normalize

def test_maps_url_encodes_address_and_appends_country():
    u = maps_url("Lot 5 Blk 3, Brgy Pavia, Iloilo")
    assert u == ("https://www.google.com/maps/search/?api=1&query="
                 "Lot+5+Blk+3%2C+Brgy+Pavia%2C+Iloilo%2C+Philippines")

def test_to_float_parses_peso_strings_and_blanks():
    assert to_float("₱1,590,000.00") == 1590000.0
    assert to_float("1590000") == 1590000.0
    assert to_float(1590000) == 1590000.0
    assert to_float("") is None
    assert to_float(None) is None
    assert to_float("N/A") is None

def test_normalize_fills_all_keys_and_coerces():
    raw = {"source": "test", "location_text": "Oton, Iloilo",
           "province": "Iloilo", "price_php": "₱5,020,000"}
    rec = normalize(raw)
    assert set(rec.keys()) == set(RECORD_KEYS)
    assert rec["price_php"] == 5020000.0
    assert rec["tct"] is None
    assert rec["maps_url"].endswith("Oton%2C+Iloilo%2C+Philippines")
