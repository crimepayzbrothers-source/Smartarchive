"""Mieterverwaltung: Mietverhältnisse, Mieterwechsel, Historie, Konditionen, Belegung und Leerstand."""
from __future__ import annotations

from conftest import HEADERS


def _prop(client, name="Berliner Straße 23", address="Berliner Straße 23, 55566 Bad Sobernheim"):
    return client.post("/api/properties", json={"name": name, "address": address}, headers=HEADERS).json()["id"]


def _unit(client, pid, name, area=None):
    uid = client.post(f"/api/properties/{pid}/units", json={"name": name}, headers=HEADERS).json()["id"]
    if area:
        client.patch(f"/api/units/{uid}", json={"living_area": area}, headers=HEADERS)
    return uid


def _tenancy(client, pid, uid, start, end=None, last="Muster", first="Anna", bk=15000, hk=8000, rent=50000, persons=1,
             extra_terms=(), parties=None, expect=200):
    body = {"property_id": pid, "unit_id": uid, "contract_start": start, "move_in": start, "contract_end": end, "move_out": end,
            "parties": parties or [{"first_name": first, "last_name": last, "email": "a@example.org"}],
            "terms": [{"valid_from": start, "cold_rent_cents": rent, "bk_advance_cents": bk, "hk_advance_cents": hk, "persons": persons},
                      *extra_terms]}
    r = client.post("/api/tenancies", json=body, headers=HEADERS)
    assert r.status_code == expect, r.text
    return r.json()


def _occ(client, pid, year=2026):
    return client.get(f"/api/properties/{pid}/occupancy", params={"year": year}).json()["units"]


def test_full_year_and_status(client):
    pid = _prop(client)
    uid = _unit(client, pid, "EG links", 62.5)
    t = _tenancy(client, pid, uid, "2020-05-01")
    assert t["status"] == "aktiv" and t["names"] == "Anna Muster" and t["unit_name"] == "EG links"
    u = _occ(client, pid)[0]
    assert len(u["segments"]) == 1 and u["segments"][0]["days"] == 365 and u["days_vacant"] == 0
    assert u["segments"][0]["from"] == "2026-01-01" and u["segments"][0]["names"] == "Anna Muster"


def test_move_in_april_move_out_june_and_vacancy(client):
    pid = _prop(client)
    a = _unit(client, pid, "EG")
    b = _unit(client, pid, "OG")
    _tenancy(client, pid, a, "2026-04-01")                  # Einzug zum 1. April
    _tenancy(client, pid, b, "2019-01-01", "2026-06-30")    # Auszug zum 30. Juni, danach Leerstand
    eg, og = {u["unit_name"]: u for u in _occ(client, pid)}.values()
    assert [(s["tenancy_id"] is None, s["days"]) for s in eg["segments"]] == [(True, 90), (False, 275)]
    assert [(s["tenancy_id"] is None, s["days"]) for s in og["segments"]] == [(False, 181), (True, 184)]
    assert eg["days_vacant"] == 90 and og["days_vacant"] == 184


def test_tenant_change_keeps_history_and_rejects_overlap(client):
    pid = _prop(client)
    uid = _unit(client, pid, "Wohnung 1")
    a = _tenancy(client, pid, uid, "2025-01-01", last="Alt")
    # Mieter B kann nicht beginnen, solange A unbefristet läuft
    r = _tenancy(client, pid, uid, "2026-04-01", last="Neu", expect=400)
    assert "Überschneidet" in r["error"] and "Alt" in r["error"]
    # Mieterwechsel: A beenden, B anlegen
    ended = client.post(f"/api/tenancies/{a['id']}/end", json={"contract_end": "2026-03-31"}, headers=HEADERS).json()
    assert ended["contract_end"] == "2026-03-31" and ended["move_out"] == "2026-03-31" and ended["status"] == "beendet"
    b = _tenancy(client, pid, uid, "2026-04-01", last="Neu")
    segs = _occ(client, pid)[0]["segments"]
    assert [(s["names"], s["days"]) for s in segs] == [("Anna Alt", 90), ("Anna Neu", 275)]
    # Historie bleibt vollständig erhalten
    all_ = client.get("/api/tenancies", params={"property_id": pid}).json()
    assert [t["id"] for t in all_] == [b["id"], a["id"]]
    assert [t["id"] for t in client.get("/api/tenancies", params={"status": "beendet"}).json()] == [a["id"]]
    assert client.get("/api/tenancies", params={"q": "alt"}).json()[0]["id"] == a["id"]


def test_two_tenancies_and_vacancy_in_one_year(client):
    pid = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")  # Objekt ohne Einheiten
    _tenancy(client, pid, None, "2024-01-01", "2026-02-28", last="A")
    _tenancy(client, pid, None, "2026-06-01", "2026-09-15", last="B")
    segs = _occ(client, pid)[0]["segments"]
    assert [(s["names"] or "Leerstand", s["days"]) for s in segs] == [
        ("Anna A", 59), ("Leerstand", 92), ("Anna B", 107), ("Leerstand", 107)]
    assert sum(s["days"] for s in segs) == 365


def test_leap_year_days(client):
    pid = _prop(client)
    uid = _unit(client, pid, "1")
    _tenancy(client, pid, uid, "2028-02-15", "2028-03-15")
    u = _occ(client, pid, 2028)[0]
    assert u["days_total"] == 366 and [s["days"] for s in u["segments"]] == [45, 30, 291]


def test_terms_change_during_year_and_validation(client):
    pid = _prop(client)
    uid = _unit(client, pid, "1")
    t = _tenancy(client, pid, uid, "2025-01-01", bk=15000, persons=2,
                 extra_terms=[{"valid_from": "2026-07-01", "cold_rent_cents": 50000, "bk_advance_cents": 18000,
                               "hk_advance_cents": 8000, "persons": 3, "note": "Anpassung nach Abrechnung 2025"}],
                 parties=[{"first_name": "Anna", "last_name": "Muster"}, {"first_name": "Ben", "last_name": "Muster", "phone": "0170"}])
    assert [x["bk_advance_cents"] for x in t["terms"]] == [15000, 18000] and t["names"] == "Anna Muster, Ben Muster"
    assert t["current_terms"]["persons"] == 3

    base = {k: t[k] for k in ("property_id", "unit_id", "contract_start", "move_in", "contract_end", "move_out")}
    def put(**over):
        return client.put(f"/api/tenancies/{t['id']}", json={**base, "parties": t["parties"], "terms": t["terms"], **over},
                          headers=HEADERS)
    assert "vor dem Mietbeginn" in put(terms=[{"valid_from": "2024-12-01"}]).json()["error"]
    assert "ab Mietbeginn" in put(terms=[{"valid_from": "2025-02-01"}]).json()["error"]
    assert "Nachnamen" in put(parties=[{"first_name": "X"}]).json()["error"]
    assert "Auszug liegt vor dem Einzug" in put(move_in="2025-03-01", move_out="2025-02-01", contract_end=None).json()["error"]
    # Beenden vor einer späteren Änderung ist widersprüchlich
    r = client.post(f"/api/tenancies/{t['id']}/end", json={"contract_end": "2026-03-31"}, headers=HEADERS)
    assert r.status_code == 400 and "nach dem Mietende" in r.json()["error"]


def test_object_rules_and_unit_protection(client):
    pid = _prop(client)
    uid = _unit(client, pid, "1")
    r = _tenancy(client, pid, None, "2026-01-01", expect=400)
    assert "Einheit" in r["error"]
    other = _prop(client, "Anderes Objekt", "Weg 1")
    assert "gehört nicht" in _tenancy(client, other, uid, "2026-01-01", expect=400)["error"]
    t = _tenancy(client, pid, uid, "2026-01-01")
    r = client.delete(f"/api/units/{uid}", headers=HEADERS)
    assert r.status_code == 400 and "Historie" in r.json()["error"]
    # Löschen eines Mietverhältnisses (z. B. versehentlich angelegt) – ohne Namen im Protokoll
    assert client.delete(f"/api/tenancies/{t['id']}", headers=HEADERS).status_code == 200
    from app.db import connect
    conn = connect()
    try:
        log = " ".join(r[0] for r in conn.execute("SELECT details FROM audit_log WHERE entity = 'tenancy'"))
    finally:
        conn.close()
    assert "Muster" not in log and "example.org" not in log
    assert client.get(f"/api/tenancies/{t['id']}").status_code == 404


def test_tenant_change_with_gap_is_vacancy(client):
    pid = _prop(client, "Hauptstraße 15", "Hauptstraße 15, 55758 Schmidt-Hachenbach")
    units = {n: _unit(client, pid, n, a) for n, a in (("EG", 70), ("OG links", 55), ("OG Mitte", 40), ("OG rechts", 55), ("Galerie", 35))}
    old = _tenancy(client, pid, units["OG Mitte"], "2022-03-01", last="Vorher")
    client.post(f"/api/tenancies/{old['id']}/end", json={"contract_end": "2026-03-31", "move_out": "2026-03-28"}, headers=HEADERS)
    # Nachmieter erst ab 15.05. – die Lücke ist ohne weitere Eingabe Leerstand
    new = _tenancy(client, pid, units["OG Mitte"], "2026-05-15", last="Nachher",
                   parties=[{"first_name": "Eva", "last_name": "Nachher"}, {"first_name": "Tom", "last_name": "Nachher"}])
    assert new["names"] == "Eva Nachher, Tom Nachher"            # mehrere Mieter in einer Wohnung
    assert new["previous"]["id"] == old["id"] and new["vacancy_before"] == {"from": "2026-04-01", "to": "2026-05-14", "days": 44}
    old = client.get(f"/api/tenancies/{old['id']}").json()
    assert old["next"]["id"] == new["id"] and old["vacancy_after"]["days"] == 44
    occ = {u["unit_name"]: u for u in _occ(client, pid)}
    mitte = occ["OG Mitte"]["segments"]
    assert [(s["names"] or "Leerstand", s["from"], s["days"]) for s in mitte] == [
        ("Anna Vorher", "2026-01-01", 90), ("Leerstand", "2026-04-01", 44), ("Eva Nachher, Tom Nachher", "2026-05-15", 231)]
    assert occ["Galerie"]["days_vacant"] == 365 and len(occ) == 5
    # Direkt anschließender Wechsel: kein Leerstand
    nxt = client.get(f"/api/tenancies/{new['id']}").json()
    assert nxt["vacancy_after"] is None and nxt["next"] is None
