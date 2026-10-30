// Mieterverwaltung: Übersicht, Mietverhältnis anlegen/ändern, Mieterwechsel, Konditionen-Verlauf, Belegung je Objekt.
import { useEffect, useRef, useState, type FormEvent } from "react";
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

export function TenancyForm({ meta, initial, onSaved, onCancel }: { meta: Meta; initial: any; onSaved: (t: any) => void; onCancel?: () => void }) {
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

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!propertyId) return toast({ kind: "error", text: "Bitte ein Mietobjekt wählen." });
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
      toast({ kind: "ok", text: "Mietverhältnis gespeichert." });
      onSaved(saved);
    } catch (err: any) {
      toast({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form tenancy-form" onSubmit={submit}>
      <fieldset className="form-block">
        <legend>Mietsache</legend>
        <div className="form grid">
          <label>Objekt
            <select value={propertyId ?? ""} onChange={(e) => { setPropertyId(Number(e.target.value)); setUnitId(null); }} disabled={editing}>
              {meta.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          {prop && prop.units.length > 0 && (
            <label>Wohnung / Einheit
              <select value={unitId ?? ""} onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : null)} required disabled={editing}>
                <option value="">– wählen –</option>
                {prop.units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </label>
          )}
        </div>
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
  const { data, error, loading, reload } = useLoad(() => get<any[]>("/api/tenancies", { status: status || undefined, q: q.trim() || undefined }), [status, q]);

  return (
    <div className="page tenants">
      <div className="page-head">
        <nav className="register small" aria-label="Ansicht">
          <button onClick={() => go("/objekte")}>Objekte</button>
          <button className="on">Mieter</button>
          <button onClick={() => go("/betriebskosten")}>Betriebskosten</button>
        </nav>
        <button className="primary" onClick={() => setCreating(true)} disabled={!meta?.properties.length}><Icon name="plus" size={18} /> Mietverhältnis anlegen</button>
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
      <Sheet open={creating !== null} onClose={() => setCreating(null)} title="Mietverhältnis anlegen" wide>
        {creating && meta && <TenancyForm meta={meta} initial={{ property_id: propertyId, unit_id: creating.unit_id }} onCancel={() => setCreating(null)}
          onSaved={() => { setCreating(null); reload(); onChanged?.(); }} />}
      </Sheet>
    </section>
  );
}
