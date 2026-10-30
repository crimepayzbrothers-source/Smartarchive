"""Betriebskostenabrechnung – die zwölf verbindlichen Testfälle und Freigabe/Fassungen.

Haus: EG 60 m², OG 80 m², DG 60 m² (200 m²), Abrechnungsjahr 2026 (365 Tage).
- EG: Mieter A bis 31.03. (90 Tage), Mieter B ab 01.04. (275 Tage)       → Fälle 2, 4
- OG: Mieter C ganzjährig, BK-Vorauszahlung ab 01.07. von 100 € auf 120 € → Fälle 1, 6
- DG: Mieter D bis 30.06. (181 Tage), danach Leerstand (184 Tage)        → Fälle 3, 5
Erwartete Werte sind von Hand nachgerechnet (siehe Kommentare).
"""
from __future__ import annotations

import io

from conftest import HEADERS


def _setup(client):
    pid = client.post("/api/properties", json={"name": "Berliner Straße 23", "address": "Berliner Straße 23, 55566 Bad Sobernheim",
                                              "living_area": 200}, headers=HEADERS).json()["id"]
    units = {}
    for name, area in (("EG", 60), ("OG", 80), ("DG", 60)):
        uid = client.post(f"/api/properties/{pid}/units", json={"name": name}, headers=HEADERS).json()["id"]
        client.patch(f"/api/units/{uid}", json={"living_area": area}, headers=HEADERS)
        units[name] = uid

    def ten(unit, last, start, end, persons, bk, hk, extra=()):
        body = {"property_id": pid, "unit_id": units[unit], "contract_start": start, "contract_end": end,
                "parties": [{"first_name": "Mieter", "last_name": last}], "umlage_notes": "Umlage aller Betriebskosten nach BetrKV",
                "terms": [{"valid_from": start, "cold_rent_cents": 50000, "bk_advance_cents": bk, "hk_advance_cents": hk, "persons": persons},
                          *extra]}
        r = client.post("/api/tenancies", json=body, headers=HEADERS)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    t = {
        "A": ten("EG", "A", "2024-01-01", "2026-03-31", 1, 15000, 8000),
        "B": ten("EG", "B", "2026-04-01", None, 2, 15000, 8000),
        "C": ten("OG", "C", "2020-01-01", None, 2, 10000, 5000,
                 extra=[{"valid_from": "2026-07-01", "cold_rent_cents": 50000, "bk_advance_cents": 12000, "hk_advance_cents": 5000, "persons": 2}]),
        "D": ten("DG", "D", "2023-01-01", "2026-06-30", 1, 9000, 6000),
    }
    period = client.post("/api/bk/periods", json={"property_id": pid, "start": "2026-01-01", "end": "2026-12-31"}, headers=HEADERS).json()["id"]
    return pid, units, t, period


def _cost(client, period, cost_type, cents, desc, day="2026-03-15", **kw):
    r = client.post(f"/api/bk/periods/{period}/costs", json={"cost_type": cost_type, "amount_cents": cents, "description": desc,
                                                             "invoice_date": day, **kw}, headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def _put(client, period, path, body):
    r = client.put(f"/api/bk/periods/{period}/{path}", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    return r.json()


def _res(data, t, name):
    return next(r for r in data["results"] if r["tenancy_id"] == t[name])


def _pos(r, ct):
    return next(p for p in r["positions"] if p["cost_type"] == ct)


def _codes(data, level=None):
    return {i["code"].split(":")[0] for i in data["issues"] if level is None or i["level"] == level}


def test_mandatory_cases(client):
    pid, units, t, period = _setup(client)

    # Fall 7: unterschiedliche Umlageschlüssel je Kostenart
    _cost(client, period, "grundsteuer", 100000, "Grundsteuer 2026")                  # Wohnfläche (Standard)
    _cost(client, period, "muell", 36500, "Abfallgebühren 2026")
    _put(client, period, "keys/muell", {"key": "einheiten", "reason": "Gebühr je Haushalt"})
    _cost(client, period, "wasser", 60000, "Wasser 2026")
    _put(client, period, "keys/wasser", {"key": "verbrauch", "reason": "Wasserzähler in jeder Wohnung"})
    _cost(client, period, "beleuchtung", 10000, "Allgemeinstrom")
    # Standardtext eines anderen Schlüssels darf nicht als Begründung mitwandern
    from app.betriebskosten import KEYS
    data = _put(client, period, "keys/beleuchtung", {"key": "personen", "reason": KEYS["flaeche"]["why"]})
    assert next(l for l in data["lines"] if l["cost_type"] == "beleuchtung")["key_reason"] == KEYS["personen"]["why"]
    assert data["keys"]["beleuchtung"]["reason"] == "" and data["keys"]["grundsteuer"]["reason"] == ""
    _put(client, period, "keys/beleuchtung", {"key": "personen", "reason": "Laut Mietvertrag nach Personen"})
    _cost(client, period, "heizung", 300000, "Heizkosten laut ista")                   # Abrechnung der Ablesefirma
    seg = {"A": (units["EG"], t["A"]), "B": (units["EG"], t["B"]), "C": (units["OG"], t["C"]), "D": (units["DG"], t["D"]),
           "leer": (units["DG"], None)}
    for k, cents in {"A": 30000, "B": 70000, "C": 120000, "D": 50000, "leer": 30000}.items():
        _put(client, period, "values", {"cost_type": "heizung", "unit_id": seg[k][0], "tenancy_id": seg[k][1], "amount_cents": cents})

    # Fall 8: Verbrauchswerte nur teilweise vorhanden → kritischer Fehler, keine Freigabe
    for k, v in {"A": 10, "B": 30, "C": 50, "leer": 0}.items():
        data = _put(client, period, "values", {"cost_type": "wasser", "unit_id": seg[k][0], "tenancy_id": seg[k][1], "value": v})
    assert any(i["level"] == "kritisch" and "Verbrauchswert fehlt" in i["text"] and "D" in i["text"] for i in data["issues"])
    assert not data["can_release"]
    # Ersatzverfahren: geschätzt nur mit Begründung, dann Warnung statt Fehler
    r = client.put(f"/api/bk/periods/{period}/values", json={"cost_type": "wasser", "unit_id": units["DG"], "tenancy_id": t["D"],
                                                             "value": 20, "estimated": True}, headers=HEADERS)
    assert r.status_code == 400
    data = _put(client, period, "values", {"cost_type": "wasser", "unit_id": units["DG"], "tenancy_id": t["D"], "value": 20,
                                           "estimated": True, "note": "Zähler defekt, Vorjahresverbrauch"})
    assert "geschaetzt" in _codes(data, "warnung") and "verbrauch" not in _codes(data, "kritisch")

    # Fall 11: Rechnung versehentlich doppelt erfasst
    data = _cost(client, period, "grundsteuer", 100000, "Grundsteuer 2026 (doppelt)")
    assert "doppelt" in _codes(data, "warnung")
    dup = next(c for l in data["lines"] if l["cost_type"] == "grundsteuer" for c in l["items"] if "doppelt" in c["description"])
    data = client.delete(f"/api/bk/costs/{dup['id']}", headers=HEADERS).json()
    assert "doppelt" not in _codes(data)

    # Fall 12: nicht umlagefähige Position importiert (Verwaltung aus der Ausgaben-Erfassung)
    e = client.post("/api/expenses", json={"property_id": pid, "amount_cents": 50000, "category_key": "verwaltung", "paid_on": "2026-05-02",
                                           "invoice_date": "2026-05-02", "payee": "Hausverwaltung Schmitz", "status": "bestaetigt"},
                    headers=HEADERS)
    assert e.status_code == 200, e.text
    imp = client.get(f"/api/bk/periods/{period}/importable").json()
    assert [(x["payee"], x["suggested_type"]) for x in imp] == [("Hausverwaltung Schmitz", "nicht_umlagefaehig")]
    data = client.post(f"/api/bk/periods/{period}/costs/import", json={"items": [{"expense_id": imp[0]["id"]}]}, headers=HEADERS).json()
    assert data["imported"] == 1 and data["landlord"]["excluded"] == 50000
    assert client.get(f"/api/bk/periods/{period}/importable").json() == []  # keine Doppelübernahme
    # Wird sie trotzdem als Betriebskosten eingeordnet, blockiert die Prüfung die Freigabe
    cost_id = next(c["id"] for l in data["lines"] if l["cost_type"] == "nicht_umlagefaehig" for c in l["items"])
    data = client.patch(f"/api/bk/costs/{cost_id}", json={"cost_type": "versicherung"}, headers=HEADERS).json()
    assert "herkunft" in _codes(data, "kritisch")
    data = client.patch(f"/api/bk/costs/{cost_id}", json={"cost_type": "nicht_umlagefaehig"}, headers=HEADERS).json()
    assert "herkunft" not in _codes(data)

    # Verteilung prüfen (Cent-genau, von Hand gerechnet)
    A, B, C, D = (_res(data, t, k) for k in "ABCD")
    # Fall 1: ganzjährig – Grundsteuer 80/200 m² × 365/365 = 400,00 €
    assert _pos(C, "grundsteuer")["amount"] == 40000 and C["days"] == 365
    # Fall 2/4: Mieterwechsel im EG – 60 m² × 90 bzw. 275 Tage von 200 m² × 365 Tagen
    assert (A["from"], A["to"], A["days"], B["from"], B["days"]) == ("2026-01-01", "2026-03-31", 90, "2026-04-01", 275)
    assert (_pos(A, "grundsteuer")["amount"], _pos(B, "grundsteuer")["amount"]) == (7397, 22603)
    # Fall 3/5: Auszug 30.06., danach Leerstand – Anteil des Leerstands trägt der Vermieter
    assert (D["to"], D["days"], _pos(D, "grundsteuer")["amount"]) == ("2026-06-30", 181, 14877)
    assert data["vacancy"] == [{"unit_id": units["DG"], "unit_name": "DG", "days": 184, "amount": 15123 + 6133 + 0 + 1061 + 30000}]
    # Fall 7: Müll nach Wohneinheiten (Wohnungstage), Strom nach Personentagen, Wasser nach Verbrauch, Heizung extern
    assert [_pos(x, "muell")["amount"] for x in (A, B, C, D)] == [3000, 9167, 12167, 6033]
    assert [_pos(x, "beleuchtung")["amount"] for x in (A, B, C, D)] == [519, 3170, 4207, 1043]
    assert [_pos(x, "wasser")["amount"] for x in (A, B, C, D)] == [5454, 16364, 27273, 10909]
    assert [_pos(x, "heizung")["amount"] for x in (A, B, C, D)] == [30000, 70000, 120000, 50000]
    # Summe der verteilten Kosten == umlagefähige Gesamtkosten (inkl. Leerstand)
    assert data["totals"]["distributed"] == data["totals"]["distributable"] == 100000 + 36500 + 60000 + 10000 + 300000
    assert sum(r["total"] for r in data["results"]) + data["landlord"]["vacancy"] == data["totals"]["distributable"]

    # Fall 6: Vorauszahlung ändert sich – Soll C: 6 × 100 € + 6 × 120 € = 1.320 €, HK 12 × 50 €
    assert C["advances_soll"] == {"bk": 132000, "hk": 60000}
    assert A["advances_soll"] == {"bk": 45000, "hk": 24000}       # 3 Monate
    assert "vz" in _codes(data, "kritisch")                         # Ist noch nicht bestätigt
    # Ist ≠ Soll wird nicht gleichgesetzt: Vorschlag aus dem Mietkonto, Bestätigung durch Sie
    sug = client.get(f"/api/bk/periods/{period}/advances/{t['C']}/suggestion").json()
    assert sug["suggestion"] is None and "keine zugeordneten Mieteingänge" in sug["text"]
    _put(client, period, f"advances/{t['A']}", {"bk_cents": 45000, "hk_cents": 24000})
    _put(client, period, f"advances/{t['B']}", {"bk_cents": 135000, "hk_cents": 72000})
    _put(client, period, f"advances/{t['C']}", {"bk_cents": 132000, "hk_cents": 60000})
    data = _put(client, period, f"advances/{t['D']}", {"bk_cents": 45000, "hk_cents": 36000, "note": "Juni nicht gezahlt"})
    A, B, C, D = (_res(data, t, k) for k in "ABCD")
    assert D["advances_soll"] == {"bk": 54000, "hk": 36000} and "vz_abw" in _codes(data, "info")

    # Fall 9: Nachzahlung – C: 2.036,47 € Kosten – 1.920,00 € Vorauszahlungen = 116,47 €
    assert (C["total"], C["paid"], C["balance"]) == (203647, 192000, 11647)
    # Fall 10: Guthaben – A: 463,70 € Kosten – 690,00 € Vorauszahlungen = –226,30 €
    assert (A["total"], A["paid"], A["balance"]) == (46370, 69000, -22630)
    assert (A["bk_total"], A["hk_total"]) == (16370, 30000)

    # Erläuterung der Verteilungseinheiten liegt bei
    gs = _pos(B, "grundsteuer")
    assert gs["basis"]["mine"] == "60,00 m² × 275 Tage" and gs["basis"]["total"] == "200,00 m² × 365 Tage"
    assert "Leerstand" in {i["code"].split(":")[0].capitalize() for i in data["issues"]} or "leerstand" in _codes(data, "info")
    assert "co2" in _codes(data, "warnung") and "zwischenablesung" in _codes(data, "info")


def _complete(client):
    """Kleiner vollständiger Fall für Freigabe/PDF."""
    pid, units, t, period = _setup(client)
    _cost(client, period, "grundsteuer", 100000, "Grundsteuer 2026")
    _cost(client, period, "versicherung", 73000, "Wohngebäudeversicherung")
    for k in "ABCD":
        r = client.get(f"/api/bk/periods/{period}/advances/{t[k]}/suggestion").json()
        _put(client, period, f"advances/{t[k]}", {"bk_cents": r["soll"]["bk"], "hk_cents": 0})
    return pid, units, t, period


def test_release_versions_pdf_and_archive(client):
    from pypdf import PdfReader
    pid, units, t, period = _complete(client)
    client.put("/api/bk/landlord", json={"name": "Can Beispiel", "address": "Musterweg 1, 55566 Bad Sobernheim", "iban": "DE00 1234",
                                         "place": "Bad Sobernheim"}, headers=HEADERS)
    data = client.get(f"/api/bk/periods/{period}").json()
    assert not _codes(data, "kritisch"), data["issues"]
    warnings = [i["code"] for i in data["issues"] if i["level"] == "warnung"]
    if warnings:
        assert not data["can_release"]
        assert client.post(f"/api/bk/periods/{period}/release", json={}, headers=HEADERS).status_code == 400
        data = client.post(f"/api/bk/periods/{period}/acknowledge", json={"codes": warnings}, headers=HEADERS).json()
    assert data["can_release"]

    # Entwurfs-PDF
    r = client.get(f"/api/bk/periods/{period}/preview.pdf", params={"tenancy_id": t["C"]})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    for needle in ("Betriebskostenabrechnung 2026", "Abrechnungszeitraum", "01.01.2026 – 31.12.2026", "Umlageschlüssel",
                   "Wohnfläche", "abzüglich geleistete", "Guthaben", "Mieter C", "ENTWURF", "§ 556 Abs. 3"):
        assert needle in text, needle

    out = client.post(f"/api/bk/periods/{period}/release", json={"note": "Erstfassung"}, headers=HEADERS).json()
    assert out["period"]["status"] == "freigegeben" and out["statements"][0]["version"] == 1
    sid = out["statement_id"]
    st = client.get(f"/api/bk/statements/{sid}").json()
    assert len(st["snapshot"]["archived_documents"]) == 4
    docs = client.get("/api/documents", params={"q": "Betriebskostenabrechnung", "area": "vermietung"}).json()
    assert docs["total"] >= 4
    pdf1 = client.get(f"/api/bk/statements/{sid}/pdf", params={"tenancy_id": t["C"]}).content
    assert "Fassung 1" in "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf1)).pages)

    # Korrektur nach Freigabe: zurück auf Entwurf, neue Fassung; die alte bleibt unverändert reproduzierbar
    data = _cost(client, period, "schornstein", 12000, "Schornsteinfeger")
    assert data["period"]["status"] == "entwurf" and data["acknowledged"] == []
    warnings = [i["code"] for i in data["issues"] if i["level"] == "warnung"]
    client.post(f"/api/bk/periods/{period}/acknowledge", json={"codes": warnings}, headers=HEADERS)
    out2 = client.post(f"/api/bk/periods/{period}/release", json={"note": "Korrektur Schornstein", "archive": False}, headers=HEADERS).json()
    assert [s["version"] for s in out2["statements"]] == [2, 1]
    again = client.get(f"/api/bk/statements/{sid}/pdf", params={"tenancy_id": t["C"]}).content
    old = "".join(p.extract_text() for p in PdfReader(io.BytesIO(again)).pages)
    assert "Schornstein" not in old and "Fassung 1" in old
    assert client.delete(f"/api/bk/periods/{period}", headers=HEADERS).status_code == 400  # Fassungen schützen den Zeitraum

    # Belege zur Abrechnung (Belegeinsicht) als ZIP mit Belegliste
    z = client.get(f"/api/bk/periods/{period}/receipts.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_bank_suggestion_and_overlap(client):
    pid, units, t, period = _setup(client)
    assert client.post("/api/bk/periods", json={"property_id": pid, "start": "2026-06-01", "end": "2027-05-31"},
                       headers=HEADERS).status_code == 400
    # Mieteingänge für C im Mietkonto (einnahme, OG) – voll gezahlt
    acc = client.post("/api/bank/accounts", json={"name": "Mietkonto"}, headers=HEADERS).json()["id"]
    lines = "\n".join(f"01.{m:02d}.2026;01.{m:02d}.2026;Gutschrift;Mieter C Miete OG;{(650 if m <= 6 else 670)},00;EUR;DE00;"
                      for m in range(1, 13))
    csv = ("Buchungstag;Wertstellung;Umsatzart;Buchungstext;Betrag;Währung;IBAN Kontoinhaber;Kategorie\n" + lines + "\n").encode()
    client.post("/api/bank/import", data={"account_id": str(acc)}, files=[("file", ("u.csv", csv, "text/csv"))], headers=HEADERS)
    for tx in client.get("/api/transactions/review").json()["open"]:
        client.post(f"/api/transactions/{tx['id']}/decide", json={"kind": "einnahme", "property_id": pid, "unit_id": units["OG"]},
                    headers=HEADERS)
    s = client.get(f"/api/bk/periods/{period}/advances/{t['C']}/suggestion").json()
    assert s["count"] == 12 and s["suggestion"] == {"bk": 132000, "hk": 60000}
    # Vorschlag ist nur Vorschlag: ohne Bestätigung bleibt die Prüfung kritisch
    assert "vz" in _codes(client.get(f"/api/bk/periods/{period}").json(), "kritisch")


def test_delete_draft_period_and_cost(client):
    pid, units, t, period = _setup(client)
    data = _cost(client, period, "grundsteuer", 60000, "Grundsteuer")
    cid = data["lines"][0]["items"][0]["id"]
    assert client.delete(f"/api/bk/costs/{cid}", headers=HEADERS).json()["lines"] == []
    _cost(client, period, "muell", 30000, "Müll")
    assert client.delete(f"/api/bk/periods/{period}", headers=HEADERS).status_code == 200
    assert client.get("/api/bk/periods", params={"property_id": pid}).json() == []
    assert client.get(f"/api/bk/periods/{period}").status_code == 404
