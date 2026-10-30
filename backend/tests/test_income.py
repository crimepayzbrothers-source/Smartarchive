"""Einnahmenverwaltung: Soll aus Mietverhältnissen, Ist aus Mietkonto und Barzahlungen, automatische Zuordnung."""
from __future__ import annotations

from conftest import HEADERS

HEAD = "Buchungstag;Wertstellung;Umsatzart;Buchungstext;Betrag;Währung;IBAN Kontoinhaber;Kategorie\n"


def _setup(client):
    pid = client.post("/api/properties", json={"name": "Hauptstraße 15", "address": "Hauptstraße 15, 55758 Schmidt-Hachenbach"},
                      headers=HEADERS).json()["id"]
    units = {}
    for n in ("EG", "OG Mitte", "Galerie"):
        units[n] = client.post(f"/api/properties/{pid}/units", json={"name": n}, headers=HEADERS).json()["id"]

    def ten(unit, first, last, start, rent, bk, hk):
        return client.post("/api/tenancies", json={"property_id": pid, "unit_id": units[unit], "contract_start": start,
                                                   "parties": [{"first_name": first, "last_name": last}],
                                                   "terms": [{"valid_from": start, "cold_rent_cents": rent, "bk_advance_cents": bk,
                                                              "hk_advance_cents": hk}]}, headers=HEADERS).json()["id"]
    t = {"mm": ten("OG Mitte", "Max", "Mustermann", "2021-05-01", 50000, 11000, 6500),
         "so": ten("Galerie", "Julia", "Sommer", "2026-04-15", 35000, 9000, 5500),
         "eg": ten("EG", "Otto", "Weber", "2019-01-01", 40000, 10000, 6000)}
    acc = client.post("/api/bank/accounts", json={"name": "Mietkonto"}, headers=HEADERS).json()["id"]
    return pid, units, t, acc


def _import(client, acc, lines):
    data = (HEAD + "\n".join(lines) + "\n").encode()
    r = client.post("/api/bank/import", data={"account_id": str(acc)}, files=[("file", ("u.csv", data, "text/csv"))], headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def test_income_soll_ist_and_auto_assignment(client):
    pid, units, t, acc = _setup(client)
    lines = ["28.12.2025;28.12.2025;Gutschrift;Max Mustermann Miete Januar;675,00;EUR;DE00;"]           # zählt für Januar 2026
    lines += [f"01.{m:02d}.2026;01.{m:02d}.2026;Gutschrift;Max Mustermann Miete;675,00;EUR;DE00;" for m in (2, 4, 5)]
    lines += ["03.03.2026;03.03.2026;Gutschrift;MUSTERMANN, MAX Miete Maerz;400,00;EUR;DE00;"]       # Teilzahlung
    lines += ["02.01.2026;02.01.2026;Gutschrift;Dauerauftrag Wohnung EG;560,00;EUR;DE00;"]           # ohne Namen
    res = _import(client, acc, lines)
    assert (res["auto"], res["open"]) == (4, 2)  # Name + exakter Betrag → automatisch; Teilzahlung und ohne Namen → Rückfrage
    rv = client.get("/api/transactions/review").json()
    auto = rv["auto"][0]
    assert auto["tenancy_id"] == t["mm"] and auto["tenancy_names"] == "Max Mustermann" and "Monatsmiete" in auto["reason"]
    part = next(x for x in rv["open"] if x["amount_cents"] == 40000)
    assert part["suggestion"]["tenancy_id"] == t["mm"] and "bitte prüfen" in part["suggestion"]["reason"]
    client.post(f"/api/transactions/{part['id']}/decide", json={"kind": "einnahme", "tenancy_id": t["mm"]}, headers=HEADERS)
    eg = next(x for x in rv["open"] if x["amount_cents"] == 56000)
    # Zuordnung über Objekt + Wohnung genügt: das Mietverhältnis wird selbst gefunden
    out = client.post(f"/api/transactions/{eg['id']}/decide", json={"kind": "einnahme", "property_id": pid, "unit_id": units["EG"]},
                      headers=HEADERS).json()
    assert out["tenancy_id"] == t["eg"]

    ov = client.get("/api/income", params={"year": 2026, "property_id": pid}).json()
    mm = next(i for i in ov["items"] if i["tenancy_id"] == t["mm"])
    months = {m["month"]: m for m in mm["months"]}
    assert months[1]["ist"] == 67500 and months[1]["status"] == "bezahlt"          # Eingang 28.12. zählt für Januar
    assert months[3]["ist"] == 40000 and months[3]["status"] == "teilweise" and months[3]["saldo"] == -27500
    assert months[2]["status"] == "bezahlt" and months[5]["saldo"] == -27500
    assert mm["soll_parts"] == {"cold": 600000, "bk": 132000, "hk": 78000}
    # Sommer: Mietbeginn 15.04. → April anteilig 16/30 von 495 € = 264 €
    so = next(i for i in ov["items"] if i["tenancy_id"] == t["so"])
    sm = {m["month"]: m for m in so["months"]}
    assert sm[3]["status"] == "frei" and sm[4]["soll"]["total"] == 26400 and sm[5]["soll"]["total"] == 49500

    # Barzahlung von Hand
    r = client.post("/api/income/receipts", json={"tenancy_id": t["so"], "paid_on": "2026-04-20", "amount_cents": 26400,
                                                  "for_month": "2026-04", "note": "bar, Quittung"}, headers=HEADERS)
    assert r.status_code == 200
    so = next(i for i in client.get("/api/income", params={"year": 2026}).json()["items"] if i["tenancy_id"] == t["so"])
    assert {m["month"]: m for m in so["months"]}[4]["status"] == "bezahlt"
    assert any(p["source"] == "hand" and p["text"] == "bar, Quittung" for p in so["payments"])
    assert client.delete(f"/api/income/receipts/{r.json()['id']}", headers=HEADERS).status_code == 200

    # Rückgängig löst auch die Verknüpfung zum Mietverhältnis
    undone = client.post(f"/api/transactions/{eg['id']}/undo", headers=HEADERS).json()
    assert undone["tenancy_id"] is None


def test_late_tenancy_links_existing_payments(client):
    pid = client.post("/api/properties", json={"name": "Hauptstraße 15", "address": "x"}, headers=HEADERS).json()["id"]
    uid = client.post(f"/api/properties/{pid}/units", json={"name": "OG links"}, headers=HEADERS).json()["id"]
    acc = client.post("/api/bank/accounts", json={"name": "Mietkonto"}, headers=HEADERS).json()["id"]
    _import(client, acc, ["01.02.2026;01.02.2026;Gutschrift;Dauerauftrag Miete;600,00;EUR;DE00;"])
    tx = client.get("/api/transactions/review").json()["open"][0]
    client.post(f"/api/transactions/{tx['id']}/decide", json={"kind": "einnahme", "property_id": pid, "unit_id": uid}, headers=HEADERS)
    # Mietverhältnis erst danach erfasst → Eingang wird beim Aufruf der Einnahmen verknüpft
    tid = client.post("/api/tenancies", json={"property_id": pid, "unit_id": uid, "contract_start": "2025-01-01",
                                              "parties": [{"last_name": "Neu"}], "terms": [{"valid_from": "2025-01-01", "cold_rent_cents": 60000}]},
                      headers=HEADERS).json()["id"]
    ov = client.get("/api/income", params={"year": 2026}).json()
    item = next(i for i in ov["items"] if i["tenancy_id"] == tid)
    assert item["ist"] == 60000 and ov["unassigned"] == []
