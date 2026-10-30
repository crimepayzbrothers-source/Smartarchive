// Mieterverwaltung: Übersicht, Mietverhältnis anlegen/ändern, Mieterwechsel, Konditionen-Verlauf, Belegung je Objekt.
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { centsToInput, date, del, get, money, parseMoney, post, put } from "../api";
import { go, useLoad, useMeta, useToast, type Meta } from "../lib";
import { BackButton, Empty, ErrorBox, Icon, Loading, Sheet } from "../components/ui";

export const STATUS_LABEL: Record<string, string> = { aktiv: "aktiv", kuenftig: "künftig", beendet: "beendet" };
const STATUS_PILL: Record<string, string> = { aktiv: "bestaetigt", kuenftig: "entwurf", beendet: "" };

const nextDay = (iso: string) => {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
};
const prevDay = (iso: string) => {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
};
const toInt = (s: string) => (s.trim() === "" ? null : Number.parseInt(s, 10));

export function objectLabel(t: any) {
  return `${t.property_name}${t.unit_name ? ` · ${t.unit_name}` : ""}`;
}
function period(t: any) {
  const end = t.contract_end || t.move_out;
  return `${date(t.contract_start)} – ${end ? date(end) : "unbefristet"}`;
}

// --- Formular -----------------------------------------------------------------

type Party = { first_name: string; last_name: string; address: string; phone: string; email: string };
type Term = { valid_from: string; cold_rent: string; bk: string; hk: string; persons: string; note: string };
const EMPTY_PARTY: Party = { first_name: "", last_name: "", address: "", phone: "", email: "" };

function toTerm(t: any): Term {
  return { valid_from: t.valid_from, cold_rent: centsToInput(t.cold_rent_cents), bk: centsToInput(t.bk_advance_cents),
    hk: centsToInput(t.hk_advance_cents), persons: t.persons ?? "", note: t.note || "" };
}

export function TenancyForm({ meta, initial, onSaved, onCancel, notice, initialFiles = [] }: { meta: Meta; initial: any; onSaved: (t: any) => void; onCancel?: () => void; notice?: ReactNode; initialFiles?: File[] }) {
  const toast = useToast();
  const editing = Boolean(initial.id);
  const [propertyId, setPropertyId] = useState<number | null>(initial.property_id ?? meta.properties[0]?.id ?? null);
  const [unitId, setUnitId] = useState<number | null>(initial.unit_id ?? null);
  const [f, setF] = useState({
    contract_start: initial.contract_start || "", move_in: initial.move_in || "", contract_end: initial.contract_end || "",
    move_out: initial.move_out || "", umlage_notes: initial.umlage_notes || "", notes: initial.notes || "",
  });
  const [parties, setParties] = useState<Party[]>(initial.parties?.length ? initial.parties.map((p: any) => ({ ...EMPTY_PARTY, ...p })) : [{ ...EMPTY_PARTY }]);
  const [terms, setTerms] = useState<Term[]>(initial.terms?.length ? initial.terms.map(toTerm)
    : [{ valid_from: initial.contract_start || "", cold_rent: "", bk: "", hk: "", persons: "", note: "" }]);
  const [busy, setBusy] = useState(false);
  const prop = meta.properties.find((p) => p.id === propertyId);
  const [docs, setDocs] = useState<File[]>(initialFiles);
  const [reading, setReading] = useState(false);
  const [readInfo, setReadInfo] = useState<string | null>(null);

  // Angaben aus den Unterlagen auslesen: füllt nur leere Felder, überschreibt nichts, was Sie eingetragen haben.
  const readDocs = async () => {
    if (!propertyId || !docs.length) return;
    const form = new FormData();
    form.append("property_id", String(propertyId));
    docs.forEach((d) => form.append("files", d, d.name));
    setReading(true);
    setReadInfo(null);
    try {
      const res = await fetch("/api/tenancies/extract", { method: "POST", body: form, headers: { "X-Requested-With": "SmartArchive" }, credentials: "same-origin" });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new Error(data?.error || `Fehler ${res.status}`);
      const r = data.results?.[0] || {};
      const filled: string[] = [];
      // Aus dem aktuellen Stand berechnen (nicht in Updatern), damit die Rückmeldung vollständig ist
      if (!unitId && r.unit_id) { setUnitId(r.unit_id); filled.push("Wohnung"); }
      const nf = { ...f };
      for (const k of ["contract_start", "contract_end", "move_out"] as const) if (!nf[k] && r[k]) { nf[k] = r[k]; filled.push(FIELD_LABEL[k]); }
      if (!nf.move_in && (r.move_in || r.contract_start)) nf.move_in = r.move_in || r.contract_start;
      setF(nf);
      if (!parties.some((p) => p.last_name.trim()) && r.parties?.length) { setParties(r.parties.map((p: any) => ({ ...EMPTY_PARTY, ...p }))); filled.push("Vertragspartner"); }
      const t0 = { ...terms[0] };
      if (!t0.cold_rent && r.cold_rent_cents != null) { t0.cold_rent = centsToInput(r.cold_rent_cents); filled.push("Kaltmiete"); }
      if (!t0.bk && r.bk_advance_cents != null) { t0.bk = centsToInput(r.bk_advance_cents); filled.push("Betriebskosten-VZ"); }
      if (!t0.hk && r.hk_advance_cents != null) { t0.hk = centsToInput(r.hk_advance_cents); filled.push("Heizkosten-VZ"); }
      if (String(t0.persons ?? "") === "" && r.persons != null) { t0.persons = String(r.persons); filled.push("Personen"); }
      setTerms([t0, ...terms.slice(1)]);
      setReadInfo(filled.length ? `Übernommen: ${[...new Set(filled)].join(", ")} – bitte prüfen.` : "Keine zusätzlichen Angaben gefunden.");
    } catch (ex: any) {
      setReadInfo(`Auslesen nicht möglich: ${ex.message}`);
    } finally {
      setReading(false);
    }
  };

  // Die ersten Konditionen gelten immer ab Mietbeginn. Der Einzug folgt dem Mietbeginn, solange er nicht abweichend eingetragen ist.
  const lastStart = useRef(f.contract_start);
  useEffect(() => {
    setTerms((ts) => ts.map((t, i) => (i === 0 ? { ...t, valid_from: f.contract_start } : t)));
    const prev = lastStart.current; // vor dem Updater festhalten – er läuft erst beim nächsten Rendern
    setF((cur) => (cur.move_in === prev || !cur.move_in ? { ...cur, move_in: cur.contract_start } : cur));
    lastStart.current = f.contract_start;
  }, [f.contract_start]);

  const set = (k: keyof typeof f) => (e: any) => setF({ ...f, [k]: e.target.value });
  const setParty = (i: number, k: keyof Party, v: string) => setParties(parties.map((p, j) => (j === i ? { ...p, [k]: v } : p)));
  const setTerm = (i: number, k: keyof Term, v: string) => setTerms(terms.map((t, j) => (j === i ? { ...t, [k]: v } : t)));

  const [err, setErr] = useState<string | null>(null);
  const errRef = useRef<HTMLParagraphElement>(null);
  const fail = (text: string) => {
    setErr(text);
    window.setTimeout(() => errRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }), 30);
  };
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr(null);
    if (!propertyId) return fail("Bitte ein Mietobjekt wählen.");
    if (prop && prop.units.length > 0 && !unitId) return fail("Bitte die Wohnung wählen (oben unter „Mietsache“).");
    if (!f.contract_start) return fail("Bitte den Mietbeginn angeben.");
    if (!parties.some((p) => p.last_name.trim())) return fail("Bitte bei mindestens einem Vertragspartner den Nachnamen angeben.");
    if (terms.some((t, i) => i > 0 && !t.valid_from)) return fail("Bei jeder Änderung der Miete bitte „Gültig ab“ angeben.");
    const body = {
      property_id: propertyId, unit_id: unitId, contract_start: f.contract_start, move_in: f.move_in || null,
      contract_end: f.contract_end || null, move_out: f.move_out || null, umlage_notes: f.umlage_notes, notes: f.notes,
      parties: parties.filter((p) => p.last_name.trim() || p.first_name.trim()),
      terms: terms.map((t) => ({ valid_from: t.valid_from, cold_rent_cents: parseMoney(t.cold_rent) || 0, bk_advance_cents: parseMoney(t.bk) || 0,
        hk_advance_cents: parseMoney(t.hk) || 0, persons: toInt(String(t.persons)), note: t.note })),
    };
    setBusy(true);
    try {
      const saved = editing ? await put(`/api/tenancies/${initial.id}`, body) : await post("/api/tenancies", body);
      if (docs.length) {
        const up = await uploadTenancyFiles(saved.id, docs);
        toast(up.ok ? { kind: "ok", text: `Mietverhältnis gespeichert, ${up.count} Unterlage(n) archiviert.` }
          : { kind: "error", text: `Mietverhältnis gespeichert, Unterlagen aber nicht: ${up.error}` });
      } else {
        toast({ kind: "ok", text: "Mietverhältnis gespeichert." });
      }
      onSaved(saved);
    } catch (ex: any) {
      fail(ex.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form tenancy-form" onSubmit={submit} noValidate>
      {notice}
      <fieldset className="form-block">
        <legend>Mietsache</legend>
        <div className="form grid">
          <label>Objekt
            <select value={propertyId ?? ""} onChange={(e) => { setPropertyId(Number(e.target.value)); setUnitId(null); setErr(null); }}>
              {meta.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          {prop && prop.units.length > 0 && (
            <label>Wohnung / Einheit
              <select value={unitId ?? ""} onChange={(e) => { setUnitId(e.target.value ? Number(e.target.value) : null); setErr(null); }}
                className={err && !unitId ? "invalid" : ""}>
                <option value="">– wählen –</option>
                {prop.units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </label>
          )}
          {prop && prop.units.length === 0 && (
            <p className="hint span2">Dieses Objekt hat keine Wohnungen angelegt – das Mietverhältnis gilt dann für das ganze Objekt.
              Wohnungen legen Sie auf der <a href={`#/objekt/${prop.id}`}>Objektseite</a> unter „Einheiten“ an.</p>
          )}
        </div>
      </fieldset>

      <fieldset className="form-block">
        <legend>Unterlagen (Vertrag, Screenshots, Fotos)</legend>
        <FileCollector files={docs} onChange={(fs) => { setDocs(fs); setReadInfo(null); }} />
        {docs.length > 0 && (
          <div className="row-actions">
            <button type="button" className="secondary" disabled={reading || !propertyId} onClick={readDocs}>{reading ? "Wird ausgelesen …" : "Angaben aus Unterlagen auslesen"}</button>
          </div>
        )}
        {readInfo && <p className="hint">{readInfo}</p>}
        <p className="hint">Die Dateien werden beim Speichern im Archiv (Vermietung) abgelegt und diesem Mietverhältnis zugeordnet. Auslesen füllt nur leere Felder.</p>
      </fieldset>

      <fieldset className="form-block">
        <legend>Vertragspartner</legend>
        {parties.map((p, i) => (
          <div key={i} className="party-row form grid">
            <label>Vorname<input value={p.first_name} onChange={(e) => setParty(i, "first_name", e.target.value)} autoComplete="off" /></label>
            <label>Nachname<input value={p.last_name} onChange={(e) => setParty(i, "last_name", e.target.value)} required={i === 0} autoComplete="off" /></label>
            <label>Telefon<input type="tel" value={p.phone} onChange={(e) => setParty(i, "phone", e.target.value)} autoComplete="off" /></label>
            <label>E-Mail<input type="email" value={p.email} onChange={(e) => setParty(i, "email", e.target.value)} autoComplete="off" /></label>
            <label className="span2">Anschrift<input value={p.address} onChange={(e) => setParty(i, "address", e.target.value)} placeholder="leer = Anschrift der Mietsache" autoComplete="off" />
              <span className="hint">Abweichende Anschrift, z. B. nach dem Auszug für die Abrechnung.</span></label>
            {parties.length > 1 && <button type="button" className="link small span2 remove" onClick={() => setParties(parties.filter((_, j) => j !== i))}>Vertragspartner entfernen</button>}
          </div>
        ))}
        <button type="button" className="secondary small" onClick={() => setParties([...parties, { ...EMPTY_PARTY }])}><Icon name="plus" size={16} /> Weiterer Vertragspartner</button>
      </fieldset>

      <fieldset className="form-block">
        <legend>Laufzeit</legend>
        <div className="form grid">
          <label>Mietbeginn (Vertrag)<input type="date" value={f.contract_start} onChange={set("contract_start")} required /></label>
          <label>Tatsächlicher Einzug<input type="date" value={f.move_in} onChange={set("move_in")} /></label>
          <label>Mietende (Vertrag)<input type="date" value={f.contract_end} onChange={set("contract_end")} />
            <span className="hint">leer = unbefristet</span></label>
          <label>Tatsächlicher Auszug<input type="date" value={f.move_out} onChange={set("move_out")} /></label>
        </div>
        <p className="hint">Für die Abrechnung zählt die Vertragslaufzeit. Ein- und Auszug werden für Zwischenablesungen festgehalten.</p>
        {initial.predecessor_end && f.contract_start && (() => {
          const gapFrom = nextDay(initial.predecessor_end);
          if (f.contract_start < gapFrom) return <p className="notice error small">Beginnt vor dem Ende des bisherigen Mietverhältnisses ({date(initial.predecessor_end)}).</p>;
          if (f.contract_start === gapFrom) return <p className="notice ok small">Nahtloser Wechsel – kein Leerstand.</p>;
          const d = Math.round((Date.parse(f.contract_start) - Date.parse(gapFrom)) / 86400000);
          return <p className="notice warn small">Leerstand {date(gapFrom)} – {date(prevDay(f.contract_start))} ({d} Tage) wird automatisch ausgewiesen; der Kostenanteil trägt der Vermieter.</p>;
        })()}
      </fieldset>

      <fieldset className="form-block">
        <legend>Miete und Vorauszahlungen (monatlich)</legend>
        {terms.map((t, i) => (
          <div key={i} className="term-row form grid">
            <label className="span2">{i === 0 ? "Gültig ab Mietbeginn" : "Gültig ab"}
              <input type="date" value={t.valid_from} onChange={(e) => setTerm(i, "valid_from", e.target.value)} disabled={i === 0} required />
            </label>
            <label>Kaltmiete (€)<input inputMode="decimal" value={t.cold_rent} onChange={(e) => setTerm(i, "cold_rent", e.target.value)} /></label>
            <label>Personen<input inputMode="numeric" value={t.persons} onChange={(e) => setTerm(i, "persons", e.target.value)} /></label>
            <label>Betriebskosten-VZ (€)<input inputMode="decimal" value={t.bk} onChange={(e) => setTerm(i, "bk", e.target.value)} /></label>
            <label>Heizkosten-VZ (€)<input inputMode="decimal" value={t.hk} onChange={(e) => setTerm(i, "hk", e.target.value)} /></label>
            {i > 0 && <label className="span2">Anlass<input value={t.note} onChange={(e) => setTerm(i, "note", e.target.value)} placeholder="z. B. Anpassung nach Abrechnung 2025" /></label>}
            {i > 0 && <button type="button" className="link small span2 remove" onClick={() => setTerms(terms.filter((_, j) => j !== i))}>Änderung entfernen</button>}
          </div>
        ))}
        <button type="button" className="secondary small" onClick={() => setTerms([...terms, { ...terms[terms.length - 1], valid_from: "", note: "" }])}>
          <Icon name="plus" size={16} /> Änderung ab Datum
        </button>
        <p className="hint">Eine Änderung legt einen neuen Stand an; der bisherige bleibt für frühere Zeiträume erhalten.</p>
      </fieldset>

      <fieldset className="form-block">
        <legend>Vereinbarungen</legend>
        <label>Individuelle Vereinbarungen zur Betriebskostenumlage<textarea rows={3} value={f.umlage_notes} onChange={set("umlage_notes")}
          placeholder="z. B. Umlage nach Wohnfläche, Gartenpflege nicht vereinbart, Mieter trägt Wartung Therme direkt" /></label>
        <label>Notizen<textarea rows={2} value={f.notes} onChange={set("notes")} /></label>
      </fieldset>

      {err && <p ref={errRef} className="notice error form-error" role="alert"><Icon name="alert" size={18} /> {err}</p>}
      <div className="row-actions">
        <button className="primary" disabled={busy}><Icon name="check" size={18} /> {editing ? "Speichern" : "Mietverhältnis anlegen"}</button>
        {onCancel && <button type="button" className="ghost" onClick={onCancel}>Abbrechen</button>}
      </div>
    </form>
  );
}

// --- Übersicht ---------------------------------------------------------------

export function TenantsPage({ params }: { params: URLSearchParams }) {
  const { meta } = useMeta();
  const [status, setStatus] = useState(params.get("status") || "aktiv");
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const { data, error, loading, reload } = useLoad(() => get<any[]>("/api/tenancies", { status: status || undefined, q: q.trim() || undefined }), [status, q]);

  return (
    <div className="page tenants">
      <div className="page-head">
        <nav className="register small" aria-label="Ansicht">
          <button onClick={() => go("/objekte")}>Objekte</button>
          <button className="on">Mieter</button>
          <button onClick={() => go("/betriebskosten")}>Betriebskosten</button>
        </nav>
        <div className="row-actions">
          <button className="secondary" onClick={() => setImporting(true)} disabled={!meta?.properties.length}><Icon name="camera" size={18} /> Aus Vertrag/Screenshot</button>
          <button className="primary" onClick={() => setCreating(true)} disabled={!meta?.properties.length}><Icon name="plus" size={18} /> Mietverhältnis anlegen</button>
        </div>
      </div>
      <form className="search-bar" role="search" onSubmit={(e) => { e.preventDefault(); (document.activeElement as HTMLElement)?.blur(); }}>
        <Icon name="search" size={20} />
        <input type="search" placeholder="Mieter, Objekt oder Wohnung" value={q} onChange={(e) => setQ(e.target.value)} enterKeyHint="search" aria-label="Mieter suchen" />
      </form>
      <fieldset className="segmented small" aria-label="Status">
        {[["aktiv", "Aktiv"], ["kuenftig", "Künftig"], ["beendet", "Beendet"], ["", "Alle"]].map(([k, l]) => (
          <label key={k} className={status === k ? "on" : ""}><input type="radio" name="t-status" checked={status === k} onChange={() => setStatus(k)} /> {l}</label>
        ))}
      </fieldset>

      {error && <ErrorBox text={error} />}
      {!meta?.properties.length ? (
        <Empty icon="building" title="Noch kein Mietobjekt">Legen Sie zuerst unter „Objekte“ ein Mietobjekt an.</Empty>
      ) : loading && !data ? <Loading /> : !data?.length ? (
        <Empty icon="building" title="Keine Mietverhältnisse">{status === "aktiv" ? "Es gibt noch kein laufendes Mietverhältnis." : "Für diesen Filter gibt es keine Einträge."}</Empty>
      ) : (
        <div className="tenant-list">
          {data.map((t) => (
            <a key={t.id} className={`tenant-card ${t.status}`} href={`#/mieter/${t.id}`}>
              <div className="tenant-main">
                <strong>{t.names}</strong>
                <span className="muted">{objectLabel(t)}</span>
                <span className="muted">{period(t)}</span>
              </div>
              <div className="tenant-side">
                <span className={`status-pill ${STATUS_PILL[t.status]}`}>{STATUS_LABEL[t.status]}</span>
                {t.current_terms && <span className="muted small-num">BK {money(t.current_terms.bk_advance_cents)} · HK {money(t.current_terms.hk_advance_cents)}</span>}
              </div>
            </a>
          ))}
        </div>
      )}

      <Sheet open={importing} onClose={() => setImporting(false)} title="Aus Vertrag oder Screenshot übernehmen" wide>
        {importing && meta && <TenancyImport meta={meta} onCancel={() => setImporting(false)} onDone={(t) => { setImporting(false); reload(); go(`/mieter/${t.id}`); }} />}
      </Sheet>
      <Sheet open={creating} onClose={() => setCreating(false)} title="Mietverhältnis anlegen" wide>
        {creating && meta && <TenancyForm meta={meta} initial={{}} onCancel={() => setCreating(false)}
          onSaved={(t) => { setCreating(false); reload(); go(`/mieter/${t.id}`); }} />}
      </Sheet>
    </div>
  );
}

// --- Einzelnes Mietverhältnis ------------------------------------------------------

export function TenancyPage({ id }: { id: number }) {
  const { meta } = useMeta();
  const toast = useToast();
  const { data: t, error, loading, reload } = useLoad(() => get(`/api/tenancies/${id}`), [id]);
  const [mode, setMode] = useState<"" | "edit" | "end" | "successor">("");
  const [endDate, setEndDate] = useState("");
  const [moveOut, setMoveOut] = useState("");
  if (loading && !t) return <Loading />;
  if (error) return <div className="page"><BackButton fallback="/mieter" /><ErrorBox text={error} /></div>;

  const doEnd = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await post(`/api/tenancies/${id}/end`, { contract_end: endDate, move_out: moveOut || endDate });
      toast({ kind: "ok", text: "Mietverhältnis beendet." });
      reload();
      setMode("successor");
    } catch (err: any) {
      toast({ kind: "error", text: err.message });
    }
  };
  const remove = async () => {
    if (!confirm("Mietverhältnis endgültig löschen? Nur für versehentlich angelegte Einträge. Beendete Mietverhältnisse bitte nicht löschen, sondern beenden.")) return;
    try {
      await del(`/api/tenancies/${id}`);
      go("/mieter");
    } catch (err: any) {
      toast({ kind: "error", text: err.message });
    }
  };
  const end = t.contract_end || t.move_out;

  return (
    <div className="page tenancy">
      <header className="doc-head">
        <BackButton fallback="/mieter" />
        <div className="doc-head-main">
          <h1>{t.names}</h1>
          <p className="muted"><a href={`#/objekt/${t.property_id}`}>{objectLabel(t)}</a> · <span className={`status-pill ${STATUS_PILL[t.status]}`}>{STATUS_LABEL[t.status]}</span></p>
        </div>
        <button className="secondary" onClick={() => setMode("edit")}>Bearbeiten</button>
      </header>

      <div className="home-grid">
        <section className="panel">
          <h2>Laufzeit</h2>
          <dl className="facts">
            <dt>Mietbeginn</dt><dd>{date(t.contract_start)}</dd>
            <dt>Einzug</dt><dd>{t.move_in ? date(t.move_in) : "–"}</dd>
            <dt>Mietende</dt><dd>{t.contract_end ? date(t.contract_end) : "unbefristet"}</dd>
            <dt>Auszug</dt><dd>{t.move_out ? date(t.move_out) : "–"}</dd>
          </dl>
          {!end && (
            <div className="row-actions">
              <button className="secondary" onClick={() => { setEndDate(""); setMoveOut(""); setMode("end"); }}>Beenden / Mieterwechsel</button>
            </div>
          )}
          {end && !t.next && <button className="link" onClick={() => setMode("successor")}>Nachmieter anlegen</button>}
          {(t.previous || t.next) && (
            <ul className="chain">
              {t.previous && <li><span className="muted">Vormieter</span> <a href={`#/mieter/${t.previous.id}`}>{t.previous.names}</a> <span className="muted">bis {date(t.previous.end)}</span></li>}
              {t.vacancy_before && <li className="vacancy"><span>Leerstand</span> {date(t.vacancy_before.from)} – {date(t.vacancy_before.to)} · {t.vacancy_before.days} Tage</li>}
              <li className="self"><strong>{t.names}</strong> <span className="muted">{date(t.contract_start)} – {end ? date(end) : "unbefristet"}</span></li>
              {t.vacancy_after && <li className="vacancy"><span>Leerstand</span> {date(t.vacancy_after.from)} – {date(t.vacancy_after.to)} · {t.vacancy_after.days} Tage</li>}
              {t.next && <li><span className="muted">Nachmieter</span> <a href={`#/mieter/${t.next.id}`}>{t.next.names}</a> <span className="muted">ab {date(t.next.start)}</span></li>}
            </ul>
          )}

          <h3>Vertragspartner</h3>
          <ul className="plain-list">
            {t.parties.map((p: any) => (
              <li key={p.id}><span><strong>{`${p.first_name} ${p.last_name}`.trim()}</strong>
                <span className="muted sub-line">{[p.address || t.property_address, p.phone, p.email].filter(Boolean).join(" · ")}</span></span></li>
            ))}
          </ul>
        </section>

        <section className="panel">
          <h2>Miete und Vorauszahlungen</h2>
          <div className="table-wrap">
            <table className="terms-table">
              <thead><tr><th>ab</th><th>Kaltmiete</th><th>BK-VZ</th><th>HK-VZ</th><th>Pers.</th></tr></thead>
              <tbody>
                {t.terms.map((x: any) => (
                  <tr key={x.id} className={t.current_terms?.id === x.id ? "current" : ""}>
                    <td>{date(x.valid_from)}{x.note && <span className="muted sub-line">{x.note}</span>}</td>
                    <td>{money(x.cold_rent_cents)}</td><td>{money(x.bk_advance_cents)}</td><td>{money(x.hk_advance_cents)}</td><td>{x.persons ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">Vereinbarte Beträge (Soll). Tatsächlich gezahlte Vorauszahlungen kommen in der Betriebskostenabrechnung aus dem Mietkonto.</p>
          {t.umlage_notes && <><h3>Vereinbarungen zur Umlage</h3><p className="notes">{t.umlage_notes}</p></>}
          {t.notes && <><h3>Notizen</h3><p className="notes">{t.notes}</p></>}
        </section>
      </div>
      <TenancyDocuments t={t} onChanged={reload} />
      <button className="danger-link" onClick={remove}><Icon name="trash" size={18} /> Versehentlich angelegt? Löschen</button>

      <Sheet open={mode === "edit"} onClose={() => setMode((cur) => (cur === "edit" ? "" : cur))} title="Mietverhältnis bearbeiten" wide>
        {mode === "edit" && meta && <TenancyForm meta={meta} initial={t} onCancel={() => setMode("")} onSaved={() => { setMode(""); reload(); }} />}
      </Sheet>
      <Sheet open={mode === "end"} onClose={() => setMode((cur) => (cur === "end" ? "" : cur))} title="Mietverhältnis beenden">
        <form className="form" onSubmit={doEnd}>
          <label>Mietende laut Vertrag / Kündigung<input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} required /></label>
          <label>Tatsächlicher Auszug<input type="date" value={moveOut} onChange={(e) => setMoveOut(e.target.value)} />
            <span className="hint">leer = am Mietende</span></label>
          <p className="hint">Das Mietverhältnis bleibt mit allen Daten erhalten. Danach können Sie direkt den Nachmieter anlegen.</p>
          <button className="primary">Beenden</button>
        </form>
      </Sheet>
      <Sheet open={mode === "successor"} onClose={() => setMode((cur) => (cur === "successor" ? "" : cur))} title="Nachmieter anlegen" wide>
        {mode === "successor" && meta && end && (
          <TenancyForm meta={meta} initial={{ property_id: t.property_id, unit_id: t.unit_id, contract_start: nextDay(end), move_in: nextDay(end), predecessor_end: end }}
            onCancel={() => setMode("")} onSaved={(n) => { setMode(""); go(`/mieter/${n.id}`); }} />
        )}
      </Sheet>
    </div>
  );
}

// --- Belegung je Objekt (für die Objektseite) -------------------------------------------

export function OccupancyPanel({ propertyId, onChanged }: { propertyId: number; onChanged?: () => void }) {
  const { meta } = useMeta();
  const [year, setYear] = useState(new Date().getFullYear());
  const [creating, setCreating] = useState<null | { unit_id: number | null }>(null);
  const [importing, setImporting] = useState(false);
  const { data, error, reload } = useLoad(() => Promise.all([
    get(`/api/properties/${propertyId}/occupancy`, { year }), get<any[]>("/api/tenancies", { property_id: propertyId }),
  ]), [propertyId, year]);
  if (error) return <ErrorBox text={error} />;
  if (!data) return null;
  const [occ, list] = data as [any, any[]];
  const years = Array.from({ length: 6 }, (_, i) => new Date().getFullYear() + 1 - i);

  return (
    <section className="panel occupancy">
      <header className="panel-head">
        <h2>Mietverhältnisse</h2>
        <button className="secondary small" onClick={() => setImporting(true)}><Icon name="camera" size={16} /> Aus Vertrag/Screenshot</button>
        <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Jahr">{years.map((y) => <option key={y}>{y}</option>)}</select>
      </header>
      {occ.units.map((u: any) => (
        <div key={u.unit_id ?? 0} className="occ-unit">
          <div className="occ-head">
            <strong>{u.unit_name}{u.living_area ? <span className="muted"> · {String(u.living_area).replace(".", ",")} m²</span> : null}</strong>
            <span className="muted">{u.days_vacant ? `${u.days_vacant} Tage Leerstand` : "ganzjährig vermietet"}</span>
          </div>
          <div className="occ-bar" role="img" aria-label={u.segments.map((s: any) => `${s.names || "Leerstand"} ${date(s.from)} bis ${date(s.to)}`).join(", ")}>
            {u.segments.map((s: any, i: number) => (
              <span key={i} className={s.tenancy_id ? "let" : "vacant"} style={{ flexGrow: s.days }} title={`${s.names || "Leerstand"}: ${date(s.from)} – ${date(s.to)} (${s.days} Tage)`} />
            ))}
          </div>
          <ul className="occ-legend">
            {u.segments.map((s: any, i: number) => (
              <li key={i}>
                {s.tenancy_id ? <a href={`#/mieter/${s.tenancy_id}`}>{s.names}</a> : <span className="muted">Leerstand</span>}
                <span className="muted">{date(s.from)} – {date(s.to)} · {s.days} Tage</span>
              </li>
            ))}
          </ul>
          <button className="link small" onClick={() => setCreating({ unit_id: u.unit_id })}><Icon name="plus" size={14} /> Mietverhältnis für {u.unit_name}</button>
        </div>
      ))}
      {list.filter((t) => t.status === "beendet").length > 0 && (
        <details className="collapsible-inline">
          <summary>Frühere Mietverhältnisse ({list.filter((t) => t.status === "beendet").length})</summary>
          <ul className="plain-list">
            {list.filter((t) => t.status === "beendet").map((t) => (
              <li key={t.id}><a href={`#/mieter/${t.id}`}>{t.names}</a><span className="muted">{t.unit_name ? `${t.unit_name} · ` : ""}{period(t)}</span></li>
            ))}
          </ul>
        </details>
      )}
      <Sheet open={importing} onClose={() => setImporting(false)} title="Aus Vertrag oder Screenshot übernehmen" wide>
        {importing && meta && <TenancyImport meta={meta} propertyId={propertyId} onCancel={() => setImporting(false)}
          onDone={() => { setImporting(false); reload(); onChanged?.(); }} />}
      </Sheet>
      <Sheet open={creating !== null} onClose={() => setCreating(null)} title="Mietverhältnis anlegen" wide>
        {creating && meta && <TenancyForm meta={meta} initial={{ property_id: propertyId, unit_id: creating.unit_id }} onCancel={() => setCreating(null)}
          onSaved={() => { setCreating(null); reload(); onChanged?.(); }} />}
      </Sheet>
    </section>
  );
}

// --- Übernahme aus Mietvertrag oder Screenshot --------------------------------------------

const FIELD_LABEL: Record<string, string> = {
  parties: "Vertragspartner", contract_start: "Mietbeginn", cold_rent_cents: "Kaltmiete", bk_advance_cents: "Betriebskosten-VZ",
  hk_advance_cents: "Heizkosten-VZ", unit_id: "Wohnung", persons: "Personen", move_in: "Einzug", contract_end: "Mietende", move_out: "Auszug",
};

export function TenancyImport({ meta, propertyId, onDone, onCancel }: { meta: Meta; propertyId?: number; onDone: (t: any) => void; onCancel: () => void }) {
  const toast = useToast();
  const [pid, setPid] = useState<number>(propertyId ?? meta.properties[0]?.id);
  const [files, setFiles] = useState<File[]>([]);
  const [useAi, setUseAi] = useState(false);
  const [archive, setArchive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [chosen, setChosen] = useState<any>(null);
  const prop = meta.properties.find((p) => p.id === pid);
  const unitName = (id: number | null) => prop?.units.find((u) => u.id === id)?.name;
  const totalMb = files.reduce((a, f) => a + f.size, 0) / 1024 / 1024;
  const setCollected = (fs: File[]) => { setFiles(fs); setRes(null); };
  const read = async () => {
    const form = new FormData();
    form.append("property_id", String(pid));
    form.append("use_ai", useAi ? "1" : "0");
    files.forEach((f) => form.append("files", f, f.name));
    setBusy(true);
    try {
      const r = await fetch("/api/tenancies/extract", { method: "POST", body: form, headers: { "X-Requested-With": "SmartArchive" }, credentials: "same-origin" });
      const d = await r.json().catch(() => null);
      if (!r.ok) throw new Error(d?.error || `Fehler ${r.status}`);
      setRes(d);
      if (d.ai_error && useAi) toast({ kind: "error", text: `KI: ${d.ai_error} – lokale Auslese wird angezeigt.` });
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  if (chosen) {
    const r = chosen;
    const start = r.contract_start || "";
    const initial = {
      property_id: pid, unit_id: r.unit_id ?? null, contract_start: start, move_in: r.move_in || start, contract_end: r.contract_end || "",
      move_out: r.move_out || "", parties: r.parties?.length ? r.parties : undefined,
      terms: [{ valid_from: start, cold_rent_cents: r.cold_rent_cents ?? 0, bk_advance_cents: r.bk_advance_cents ?? 0, hk_advance_cents: r.hk_advance_cents ?? 0,
        persons: r.persons ?? null, note: "" }],
    };
    const notice = (
      <div className="notice warn import-notice">
        <div>
          <strong>Automatisch ausgelesen – bitte jede Angabe prüfen.</strong>
          {r.missing?.length > 0 && <p>Nicht gefunden: {r.missing.map((k: string) => FIELD_LABEL[k] || k).join(", ")}.</p>}
          {r.conflicts && Object.keys(r.conflicts).length > 0 && <p>Abweichung zwischen lokaler Auslese und KI bei: {Object.keys(r.conflicts).map((k) => FIELD_LABEL[k] || k).join(", ")} – übernommen ist die KI-Angabe.</p>}
          {r.living_area && <p>Wohnfläche laut Vorlage: {String(r.living_area).replace(".", ",")} m² – ggf. an der Wohnung eintragen.</p>}
        </div>
      </div>
    );
    return <TenancyForm meta={meta} initial={initial} notice={notice} onCancel={() => setChosen(null)}
      initialFiles={archive ? files : []} onSaved={onDone} />;
  }

  return (
    <div className="form tenancy-import">
      <p className="hint">Laden Sie den Mietvertrag (PDF oder Fotos) oder Screenshots, z. B. aus objego, hoch. Die Texterkennung läuft auf Ihrem Server;
        erkannte Angaben füllen das Formular vor. Gespeichert wird erst, wenn Sie das Formular bestätigen.</p>
      <label>Mietobjekt<select value={pid} onChange={(e) => { setPid(Number(e.target.value)); setRes(null); }}>
        {meta.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      <FileCollector files={files} onChange={setCollected} />
      {meta.ai_available && (
        <label className="check"><input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
          Zusätzlich mit KI auslesen (Mistral, EU). Dabei wird der erkannte Text mit Namen und Beträgen übermittelt; erkennt auch Mieterlisten.</label>
      )}
      <label className="check"><input type="checkbox" checked={archive} onChange={(e) => setArchive(e.target.checked)} /> Dateien beim Speichern als Unterlagen des Mietverhältnisses archivieren</label>
      <button className="primary" disabled={!files.length || busy || totalMb > FILE_MAX_MB} onClick={read}>{busy ? "Wird ausgelesen …" : "Auslesen"}</button>

      {res && (
        <div className="import-results">
          {res.chars < 30 && <p className="notice warn">Kaum Text erkannt. Bitte schärferes Foto oder Screenshot in voller Auflösung verwenden.</p>}
          {res.results.map((r: any, i: number) => (
            <div key={i} className="import-result">
              <strong>{r.parties?.length ? r.parties.map((p: any) => `${p.first_name} ${p.last_name}`.trim()).join(", ") : "Vertragspartner nicht erkannt"}</strong>
              <dl className="facts">
                <dt>Wohnung</dt><dd>{unitName(r.unit_id) || <span className="muted">nicht erkannt</span>}</dd>
                <dt>Mietbeginn</dt><dd>{r.contract_start ? date(r.contract_start) : "–"}</dd>
                {r.contract_end && <><dt>Mietende</dt><dd>{date(r.contract_end)}</dd></>}
                <dt>Kaltmiete</dt><dd>{r.cold_rent_cents != null ? money(r.cold_rent_cents) : "–"}</dd>
                <dt>BK-VZ</dt><dd>{r.bk_advance_cents != null ? money(r.bk_advance_cents) : "–"}</dd>
                <dt>HK-VZ</dt><dd>{r.hk_advance_cents != null ? money(r.hk_advance_cents) : "–"}</dd>
                {r.persons != null && <><dt>Personen</dt><dd>{r.persons}</dd></>}
              </dl>
              {r.evidence && Object.keys(r.evidence).length > 0 && (
                <details className="collapsible-inline"><summary>Fundstellen</summary>
                  <ul className="evidence">{Object.entries(r.evidence).map(([k, v]) => <li key={k}><span className="muted">{FIELD_LABEL[k] || k}:</span> „{String(v)}“</li>)}</ul>
                </details>
              )}
              <span className="muted small-num">Quelle: {r.source === "ki" ? "KI" : r.source === "lokal+ki" ? "Texterkennung + KI" : "Texterkennung"}</span>
              <button className="primary" onClick={() => setChosen(r)}>Prüfen und anlegen</button>
            </div>
          ))}
        </div>
      )}
      <button type="button" className="ghost" onClick={onCancel}>Abbrechen</button>
    </div>
  );
}

// --- Dateien sammeln (Verträge, Fotos, Screenshots) ------------------------------------------

export const FILE_MAX = 10;
export const FILE_MAX_MB = 25;

/** Mehrere Dateien in mehreren Runden sammeln (Galerie, Kamera, PDF); einzeln entfernen und sortieren. */
export function FileCollector({ files, onChange }: { files: File[]; onChange: (f: File[]) => void }) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const camRef = useRef<HTMLInputElement>(null);
  const totalMb = files.reduce((a, f) => a + f.size, 0) / 1024 / 1024;
  const add = (list: FileList | null) => {
    const incoming = Array.from(list || []);
    if (!incoming.length) return;
    const next = [...files];
    for (const f of incoming) {
      if (next.some((x) => x.name === f.name && x.size === f.size && x.lastModified === f.lastModified)) continue;
      next.push(f);
    }
    if (next.length > FILE_MAX) toast({ kind: "error", text: `Höchstens ${FILE_MAX} Dateien auf einmal – die ersten ${FILE_MAX} wurden übernommen.` });
    onChange(next.slice(0, FILE_MAX));
  };
  const remove = (i: number) => onChange(files.filter((_, j) => j !== i));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= files.length) return;
    const next = [...files];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div className="file-collector">
      <input ref={fileRef} type="file" accept="application/pdf,image/*" multiple hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      <input ref={camRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      <div className="row-actions">
        <button type="button" className="secondary" onClick={() => fileRef.current?.click()} disabled={files.length >= FILE_MAX}>
          <Icon name="file" size={18} /> {files.length ? "Weitere Dateien hinzufügen" : "Dateien oder Screenshots wählen"}</button>
        <button type="button" className="secondary" onClick={() => camRef.current?.click()} disabled={files.length >= FILE_MAX}>
          <Icon name="camera" size={18} /> {files.length ? "Weiteres Foto" : "Foto aufnehmen"}</button>
      </div>
      {files.length > 0 && (
        <>
          <ol className="file-list">
            {files.map((f, i) => (
              <li key={`${f.name}-${f.size}-${f.lastModified}`}>
                {f.type.startsWith("image/") ? <img src={URL.createObjectURL(f)} alt="" className="file-thumb" onLoad={(e) => URL.revokeObjectURL((e.target as HTMLImageElement).src)} />
                  : <span className="file-thumb pdf"><Icon name="file" size={20} /></span>}
                <span className="file-name">{f.name}<span className="muted sub-line">{f.size < 1048576 ? `${Math.round(f.size / 1024)} KB` : `${(f.size / 1048576).toFixed(1).replace(".", ",")} MB`}</span></span>
                <span className="file-actions">
                  <button type="button" className="icon-btn" aria-label="Nach oben" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                  <button type="button" className="icon-btn" aria-label="Nach unten" disabled={i === files.length - 1} onClick={() => move(i, 1)}>↓</button>
                  <button type="button" className="icon-btn" aria-label={`${f.name} entfernen`} onClick={() => remove(i)}><Icon name="trash" size={16} /></button>
                </span>
              </li>
            ))}
          </ol>
          <p className={`hint ${totalMb > FILE_MAX_MB ? "warn-text" : ""}`}>{files.length} von höchstens {FILE_MAX} Dateien · {totalMb.toFixed(1).replace(".", ",")} von {FILE_MAX_MB} MB.</p>
        </>
      )}
    </div>
  );
}

/** Unterlagen in den Archivbereich Vermietung hochladen und dem Mietverhältnis zuordnen. */
export async function uploadTenancyFiles(tenancyId: number, files: File[]): Promise<{ ok: boolean; count: number; error?: string }> {
  if (!files.length) return { ok: true, count: 0 };
  const form = new FormData();
  form.append("area", "vermietung");
  form.append("tenancy_id", String(tenancyId));
  form.append("combine", "0");
  files.forEach((f) => form.append("files", f, f.name));
  try {
    const r = await fetch("/api/upload", { method: "POST", body: form, headers: { "X-Requested-With": "SmartArchive" }, credentials: "same-origin" });
    const d = await r.json().catch(() => null);
    if (!r.ok) return { ok: false, count: 0, error: d?.error || `Fehler ${r.status}` };
    return { ok: true, count: (d.created?.length || 0) + (d.duplicates?.length || 0) };
  } catch {
    return { ok: false, count: 0, error: "Keine Verbindung zum Server." };
  }
}

/** Unterlagen auf der Seite eines Mietverhältnisses: Liste und weitere hinzufügen. */
export function TenancyDocuments({ t, onChanged }: { t: any; onChanged: () => void }) {
  const toast = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    const r = await uploadTenancyFiles(t.id, files);
    setBusy(false);
    if (!r.ok) return toast({ kind: "error", text: `Unterlagen nicht gespeichert: ${r.error}` });
    toast({ kind: "ok", text: `${r.count} Unterlage(n) archiviert.` });
    setFiles([]);
    onChanged();
  };
  return (
    <section className="panel">
      <h2>Unterlagen</h2>
      {t.documents?.length ? (
        <ul className="plain-list">
          {t.documents.map((d: any) => (
            <li key={d.id}><a href={`#/dokument/${d.id}`}>{d.title || "Dokument"}</a>
              <span className="muted">{d.status === "processing" ? "wird verarbeitet" : date(d.doc_date || d.created_at)}</span></li>
          ))}
        </ul>
      ) : <p className="muted">Noch keine Unterlagen – z. B. Mietvertrag, Übergabeprotokoll, Screenshots.</p>}
      <FileCollector files={files} onChange={setFiles} />
      {files.length > 0 && <button className="primary" disabled={busy} onClick={save}>{busy ? "Wird hochgeladen …" : `${files.length} Datei(en) archivieren`}</button>}
    </section>
  );
}
