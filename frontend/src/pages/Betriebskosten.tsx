// Betriebskostenabrechnung: Übersicht je Objekt/Jahr und Schritt-für-Schritt-Assistent.
import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { centsToInput, date, del, get, money, parseMoney, patch, post, put } from "../api";
import { go, useLoad, useMeta, useToast } from "../lib";
import { BackButton, Empty, ErrorBox, Icon, Loading } from "../components/ui";
import { ConfirmDelete, SwipeRow } from "../components/SwipeRow";

type BkMeta = { cost_types: { key: string; label: string; ref: string; key_default?: string; heat?: boolean; excluded?: boolean }[]; keys: { key: string; label: string; why: string }[]; landlord: any };

const STEPS = [
  ["kosten", "Kosten"], ["schluessel", "Schlüssel"], ["werte", "Werte"], ["nutzung", "Nutzungszeiten"],
  ["vorauszahlungen", "Vorauszahlungen"], ["ergebnis", "Ergebnis"], ["pruefung", "Prüfung"], ["freigabe", "Freigabe"],
] as const;
const LEVEL: Record<string, string> = { kritisch: "Kritisch", warnung: "Warnung", info: "Hinweis" };
const tk = (unit: number | null, ten: number | null) => `${unit ?? 0}:${ten ?? 0}`;

function useBkMeta() {
  const { data } = useLoad(() => get<BkMeta>("/api/bk/meta"), []);
  return data;
}

// --- Übersicht -------------------------------------------------------------------

export function BkOverview({ params }: { params: URLSearchParams }) {
  const { meta } = useMeta();
  const toast = useToast();
  const [propertyId, setPropertyId] = useState<number | null>(params.get("objekt") ? Number(params.get("objekt")) : null);
  const pid = propertyId ?? meta?.properties[0]?.id ?? null;
  const { data, error, loading, reload } = useLoad(() => (pid ? get<any[]>("/api/bk/periods", { property_id: pid }) : Promise.resolve([])), [pid]);
  const last = data?.[0];
  const nextYear = last ? Number(last.end.slice(0, 4)) + 1 : new Date().getFullYear() - 1;
  const [year, setYear] = useState<number | null>(null);
  const y = year ?? nextYear;
  const [doomed, setDoomed] = useState<any>(null);
  const [deleting, setDeleting] = useState(false);
  const removePeriod = async () => {
    setDeleting(true);
    try {
      await del(`/api/bk/periods/${doomed.id}`);
      toast({ kind: "ok", text: `Abrechnung ${doomed.start.slice(0, 4)} gelöscht.` });
      setDoomed(null); reload();
    } catch (err: any) {
      toast({ kind: "error", text: err.message });
    } finally { setDeleting(false); }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!pid) return;
    try {
      const r = await post("/api/bk/periods", { property_id: pid, start: `${y}-01-01`, end: `${y}-12-31` });
      go(`/betriebskosten/${r.id}`);
    } catch (err: any) {
      toast({ kind: "error", text: err.message });
    }
  };

  return (
    <div className="page bk">
      <div className="page-head">
        <nav className="register small" aria-label="Ansicht">
          <button onClick={() => go("/objekte")}>Objekte</button>
          <button onClick={() => go("/mieter")}>Mieter</button>
          <button onClick={() => go("/einnahmen")}>Einnahmen</button>
          <button className="on">Betriebskosten</button>
        </nav>
      </div>
      {!meta?.properties.length ? <Empty icon="building" title="Noch kein Mietobjekt">Legen Sie zuerst ein Objekt mit Wohnungen und Mietverhältnissen an.</Empty> : (
        <>
          <section className="panel">
            <h2>Neue Abrechnung</h2>
            <form className="inline-add" onSubmit={create}>
              <select value={pid ?? ""} onChange={(e) => setPropertyId(Number(e.target.value))} aria-label="Objekt">
                {meta.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <select value={y} onChange={(e) => setYear(Number(e.target.value))} aria-label="Abrechnungsjahr">
                {Array.from({ length: 6 }, (_, i) => new Date().getFullYear() - i).map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
              <button className="primary"><Icon name="plus" size={18} /> Abrechnung {y} beginnen</button>
            </form>
            <p className="hint">Abrechnungszeitraum: Kalenderjahr (höchstens zwölf Monate, § 556 Abs. 3 BGB). Umlageschlüssel werden aus dem Vorjahr übernommen.</p>
          </section>

          <h2 className="section-title">Abrechnungen</h2>
          {error && <ErrorBox text={error} />}
          {loading && !data ? <Loading /> : !data?.length ? <Empty icon="receipt" title="Noch keine Abrechnung">Beginnen Sie oben mit dem Jahr, das Sie abrechnen möchten.</Empty> : (
            <div className="tenant-list">
              {data.map((p) => (
                <SwipeRow key={p.id} disabled={p.status === "freigegeben" || !!p.version} asking={doomed?.id === p.id} onDelete={() => setDoomed(p)}>
                <a className={`tenant-card ${p.status === "freigegeben" ? "aktiv" : "kuenftig"}`} href={`#/betriebskosten/${p.id}`}>
                  <div className="tenant-main">
                    <strong>{p.property_name} · {p.start.slice(0, 4)}</strong>
                    <span className="muted">{date(p.start)} – {date(p.end)}</span>
                    <span className="muted">Umlagefähige Kosten {money(p.costs)}</span>
                  </div>
                  <div className="tenant-side">
                    <span className={`status-pill ${p.status === "freigegeben" ? "bestaetigt" : "entwurf"}`}>{p.status === "freigegeben" ? `freigegeben${p.version ? ` · Fassung ${p.version}` : ""}` : "Entwurf"}</span>
                  </div>
                </a>
                </SwipeRow>
              ))}
            </div>
          )}
          {data?.some((p) => p.status !== "freigegeben" && !p.version) && <p className="hint swipe-hint">Entwürfe zum Löschen nach links wischen. Freigegebene Abrechnungen bleiben zur Nachvollziehbarkeit erhalten.</p>}
          <ConfirmDelete open={!!doomed} title="Abrechnung löschen?" busy={deleting} onClose={() => setDoomed(null)} onConfirm={removePeriod}
            confirmLabel="Abrechnung löschen"
            text={doomed && <>
              <p>Den Entwurf <strong>{doomed.property_name} · {doomed.start.slice(0, 4)}</strong> mit allen erfassten Kosten, Schlüsseln, Werten und Vorauszahlungen löschen?</p>
              <p className="muted">Das lässt sich nicht rückgängig machen. Belege und Ausgaben im Archiv bleiben unverändert.</p></>} />
          <LandlordPanel onSaved={reload} />
        </>
      )}
    </div>
  );
}

function LandlordPanel({ onSaved }: { onSaved?: () => void }) {
  const toast = useToast();
  const { data, reload } = useLoad(() => get<BkMeta>("/api/bk/meta"), []);
  const [f, setF] = useState<any>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (data && !f) setF({ name: "", address: "", phone: "", email: "", iban: "", bank: "", place: "", ...data.landlord }); }, [data, f]);
  if (!f) return null;
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const { logo, ...rest } = f;
      await put("/api/bk/landlord", rest);
      toast({ kind: "ok", text: "Vermieterangaben gespeichert." });
      reload(); onSaved?.();
    } catch (err: any) { toast({ kind: "error", text: err.message }); }
  };
  const uploadLogo = async (file?: File) => {
    if (!file) return;
    const form = new FormData();
    form.append("file", file, file.name);
    const res = await fetch("/api/bk/landlord/logo", { method: "POST", body: form, headers: { "X-Requested-With": "SmartArchive" }, credentials: "same-origin" });
    const d = await res.json().catch(() => null);
    if (!res.ok) return toast({ kind: "error", text: d?.error || "Logo konnte nicht gespeichert werden." });
    setF({ ...f, logo: d.logo });
    toast({ kind: "ok", text: "Logo gespeichert." });
  };
  return (
    <details className="panel collapsible">
      <summary><h2>Vermieterangaben für die Abrechnung</h2></summary>
      <form className="form grid" onSubmit={save}>
        <label>Name<input value={f.name} onChange={set("name")} /></label>
        <label>Ort (für Datumszeile)<input value={f.place} onChange={set("place")} placeholder="Bad Sobernheim" /></label>
        <label className="span2">Anschrift<input value={f.address} onChange={set("address")} placeholder="Straße Nr., PLZ Ort" /></label>
        <label>Telefon<input value={f.phone} onChange={set("phone")} /></label>
        <label>E-Mail<input type="email" value={f.email} onChange={set("email")} /></label>
        <label>IBAN für Nachzahlungen<input value={f.iban} onChange={set("iban")} /></label>
        <label>Bank<input value={f.bank} onChange={set("bank")} /></label>
        <div className="span2 row-actions">
          <input ref={fileRef} type="file" accept="image/png,image/jpeg" hidden onChange={(e) => { uploadLogo(e.target.files?.[0]); e.target.value = ""; }} />
          <button type="button" className="secondary" onClick={() => fileRef.current?.click()}>{f.logo ? "Logo ersetzen" : "Logo hochladen (optional)"}</button>
          {f.logo && <button type="button" className="ghost" onClick={async () => { await del("/api/bk/landlord/logo"); setF({ ...f, logo: undefined }); }}>Logo entfernen</button>}
          <button className="primary">Speichern</button>
        </div>
      </form>
    </details>
  );
}

// --- Assistent -------------------------------------------------------------------

export function BkWizard({ id, params }: { id: number; params: URLSearchParams }) {
  const bkMeta = useBkMeta();
  const { data, error, loading, reload } = useLoad(() => get(`/api/bk/periods/${id}`), [id]);
  const [step, setStepState] = useState<string>(params.get("schritt") || "kosten");
  const [d, setD] = useState<any>(null);
  useEffect(() => { if (data) setD(data); }, [data]);
  const setStep = (s: string) => { setStepState(s); history.replaceState(null, "", `#/betriebskosten/${id}?schritt=${s}`); window.scrollTo(0, 0); };
  if (loading && !d) return <Loading />;
  if (error) return <div className="page"><BackButton fallback="/betriebskosten" /><ErrorBox text={error} /></div>;
  if (!d || !bkMeta) return <Loading />;
  const p = d.period;
  const crit = d.issues.filter((i: any) => i.level === "kritisch").length;
  const warn = d.issues.filter((i: any) => i.level === "warnung" && !d.acknowledged.includes(i.code)).length;
  const sums = d.results.reduce((a: any, r: any) => ({ cost: a.cost + r.total, paid: a.paid + r.paid, nach: a.nach + Math.max(r.balance, 0), gut: a.gut + Math.max(-r.balance, 0) }),
    { cost: 0, paid: 0, nach: 0, gut: 0 });
  const update = (next: any) => setD(next);
  const idx = STEPS.findIndex(([k]) => k === step);

  return (
    <div className="page bk-wizard">
      <header className="doc-head">
        <BackButton fallback="/betriebskosten" />
        <div className="doc-head-main">
          <h1>Betriebskosten {p.start.slice(0, 4)}</h1>
          <p className="muted">{p.property_name} · {date(p.start)} – {date(p.end)} · <span className={`status-pill ${p.status === "freigegeben" ? "bestaetigt" : "entwurf"}`}>{p.status === "freigegeben" ? "freigegeben" : "Entwurf"}</span></p>
        </div>
      </header>

      <div className="bk-kpis">
        <div><span>Erfasste Kosten</span><strong>{money(d.totals.costs)}</strong></div>
        <div><span>Umlagefähig</span><strong>{money(d.totals.distributable)}</strong></div>
        <div><span>Vorauszahlungen (Ist)</span><strong>{money(sums.paid)}</strong></div>
        <div><span>Nachzahlungen</span><strong className="neg">{money(sums.nach)}</strong></div>
        <div><span>Guthaben</span><strong className="pos">{money(sums.gut)}</strong></div>
        <div><span>Vermieter (Leerstand)</span><strong>{money(d.landlord.vacancy)}</strong></div>
        <button className="bk-check" onClick={() => setStep("pruefung")}>
          <span>Prüfstatus</span>
          <strong className={crit ? "neg" : warn ? "warn" : "pos"}>{crit ? `${crit} kritisch` : warn ? `${warn} Warnungen offen` : "bereit"}</strong>
        </button>
      </div>

      <nav className="stepper" aria-label="Schritte">
        {STEPS.map(([k, label], i) => (
          <button key={k} className={k === step ? "on" : i < idx ? "done" : ""} onClick={() => setStep(k)}><span className="num">{i + 1}</span>{label}</button>
        ))}
      </nav>

      {step === "kosten" && <CostsStep d={d} meta={bkMeta} onChange={update} />}
      {step === "schluessel" && <KeysStep d={d} meta={bkMeta} onChange={update} />}
      {step === "werte" && <ValuesStep d={d} onChange={update} />}
      {step === "nutzung" && <UsageStep d={d} />}
      {step === "vorauszahlungen" && <AdvancesStep d={d} onChange={update} />}
      {step === "ergebnis" && <ResultStep d={d} />}
      {step === "pruefung" && <CheckStep d={d} onChange={update} />}
      {step === "freigabe" && <ReleaseStep d={d} onChange={(n) => { update(n); reload(); }} />}

      <div className="row-actions step-nav">
        {idx > 0 && <button className="secondary" onClick={() => setStep(STEPS[idx - 1][0])}>Zurück</button>}
        {idx < STEPS.length - 1 && <button className="primary" onClick={() => setStep(STEPS[idx + 1][0])}>Weiter: {STEPS[idx + 1][1]}</button>}
      </div>
    </div>
  );
}

function useAction(onChange: (d: any) => void) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<any>, ok?: string) => {
    setBusy(true);
    try {
      const r = await fn();
      if (r && r.period) onChange(r);
      if (ok) toast({ kind: "ok", text: ok });
      return r;
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

// Belegprüfung: Archivbelege des Objekts per Volltext auswerten (Abgabenbescheide zeilenweise zerlegen)
function ReceiptScan({ periodId, meta, onClose, onImported }: { periodId: number; meta: BkMeta; onClose: () => void; onImported: (r: any) => void }) {
  const toast = useToast();
  const { data, error } = useLoad(() => get<any[]>(`/api/bk/periods/${periodId}/receipt-scan`), [periodId]);
  const [sel, setSel] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!data) return;
    const init: Record<string, any> = {};
    data.forEach((doc) => doc.positions.forEach((p: any, i: number) => {
      init[`${doc.document_id}:${i}`] = { on: p.amount_cents != null, cost_type: p.cost_type, amount: p.amount_cents != null ? centsToInput(p.amount_cents) : "" };
    }));
    setSel(init);
  }, [data]);
  const upd = (k: string, patchv: any) => setSel({ ...sel, [k]: { ...sel[k], ...patchv } });
  const chosen = data ? data.flatMap((doc) => doc.positions.map((p: any, i: number) => ({ doc, p, k: `${doc.document_id}:${i}` })))
    .filter((x) => sel[x.k]?.on) : [];
  const doImport = async () => {
    const items = chosen.map(({ doc, p, k }) => ({ document_id: doc.document_id, cost_type: sel[k].cost_type, amount_cents: parseMoney(sel[k].amount),
      evidence: p.evidence }));
    if (items.some((i) => !i.amount_cents)) return toast({ kind: "error", text: "Bitte für jede ausgewählte Position einen Betrag eintragen." });
    setBusy(true);
    try {
      const r = await post(`/api/bk/periods/${periodId}/receipt-scan/import`, { items });
      toast({ kind: "ok", text: `${r.imported} Position${r.imported === 1 ? "" : "en"} übernommen – bitte mit dem Beleg abgleichen.` });
      onImported(r);
    } catch (e: any) { toast({ kind: "error", text: e.message }); }
    finally { setBusy(false); }
  };
  return (
    <div className="import-box receipt-scan">
      <h3>Belegprüfung im Archiv</h3>
      <p className="hint">Belege dieses Objekts aus dem Bereich Vermietung, die zum Zeitraum gehören und noch nicht übernommen sind. Abgabenbescheide
        werden per Volltext Zeile für Zeile in Kostenarten zerlegt; jede Position zeigt die Zeile, aus der sie stammt. Rechnungen, die schon als Ausgabe
        erfasst sind, übernehmen Sie über „Aus Ausgaben übernehmen“.</p>
      {error && <ErrorBox text={error} />}
      {!data ? <Loading /> : data.length === 0 ? <p className="muted">Keine weiteren passenden Belege gefunden.</p> : data.map((doc) => (
        <div key={doc.document_id} className="scan-doc">
          <div className="scan-doc-head">
            <span><strong>{doc.correspondent || doc.title}</strong><span className="muted sub-line">{doc.title}{doc.doc_date ? ` · ${date(doc.doc_date)}` : ""} · {doc.why}</span></span>
            <a className="secondary small btn-link" href={`#/dokument/${doc.document_id}`} target="_blank" rel="noopener">Beleg öffnen</a>
          </div>
          {doc.kind === "bescheid" && doc.total != null && (
            <p className={`small-num ${doc.total_matches ? "pos" : "warn-text"}`}>{doc.total_matches
              ? `Summe der Positionen = Gesamtbetrag laut Bescheid (${money(doc.total)})`
              : `Summe der Positionen ${money(doc.sum)} weicht vom erkannten Gesamtbetrag ${money(doc.total)} ab – bitte prüfen`}</p>
          )}
          <ul className="plain-list">
            {doc.positions.map((p: any, i: number) => {
              const k = `${doc.document_id}:${i}`;
              const v = sel[k] || {};
              return (
                <li key={k} className={`scan-pos ${v.on ? "" : "off"}`}>
                  <label className="check"><input type="checkbox" checked={!!v.on} onChange={(e) => upd(k, { on: e.target.checked })} aria-label="übernehmen" />
                    <span className="bk-evidence">„{p.evidence}“{p.note && <span className="muted sub-line">{p.note}</span>}</span></label>
                  <select value={v.cost_type || p.cost_type} onChange={(e) => upd(k, { cost_type: e.target.value })} aria-label="Kostenart">
                    {meta.cost_types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                  </select>
                  <input className="amount" inputMode="decimal" value={v.amount ?? ""} onChange={(e) => upd(k, { amount: e.target.value })} placeholder="Betrag" aria-label="Betrag" />
                  {p.confidence === "hoch" ? <span className="status-pill bestaetigt">passt zur Summe</span> : <span className="status-pill entwurf">prüfen</span>}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      <div className="row-actions">
        {data && data.length > 0 && <button className="primary" disabled={busy || !chosen.length} onClick={doImport}>{chosen.length} Position{chosen.length === 1 ? "" : "en"} übernehmen</button>}
        <button className="ghost" onClick={onClose}>Schließen</button>
      </div>
    </div>
  );
}

// 1 · Kosten
function CostsStep({ d, meta, onChange }: { d: any; meta: BkMeta; onChange: (d: any) => void }) {
  const { busy, run } = useAction(onChange);
  const [importing, setImporting] = useState<any[] | null>(null);
  const [choice, setChoice] = useState<Record<number, string>>({});
  const [doomed, setDoomed] = useState<any>(null);
  const [scanning, setScanning] = useState(false);
  const [form, setForm] = useState({ cost_type: "grundsteuer", description: "", amount: "", invoice_date: "", unit_id: "" });
  const types = meta.cost_types;
  const label = (k: string) => types.find((t) => t.key === k)?.label || k;
  const loadImport = async () => {
    const items = await get<any[]>(`/api/bk/periods/${d.period.id}/importable`);
    setImporting(items);
    setChoice(Object.fromEntries(items.map((e) => [e.id, e.suggested_type])));
  };
  const add = async (e: FormEvent) => {
    e.preventDefault();
    const cents = parseMoney(form.amount);
    if (!cents) return;
    const r = await run(() => post(`/api/bk/periods/${d.period.id}/costs`, { cost_type: form.cost_type, description: form.description, amount_cents: cents,
      invoice_date: form.invoice_date || null, unit_id: form.unit_id ? Number(form.unit_id) : null }), "Kosten erfasst.");
    if (r) setForm({ ...form, description: "", amount: "" });
  };
  const doImport = async () => {
    const items = Object.entries(choice).filter(([, v]) => v !== "__skip").map(([k, v]) => ({ expense_id: Number(k), cost_type: v }));
    const r = await run(() => post(`/api/bk/periods/${d.period.id}/costs/import`, { items }));
    if (r) { setImporting(null); }
  };
  const units = d.units.filter((u: any) => u.id);

  return (
    <section className="panel">
      <header className="panel-head"><h2>Kosten des Hauses</h2></header>
      <div className="row-actions">
        <button className="secondary" onClick={() => setScanning(true)}><Icon name="search" size={16} /> Belege prüfen</button>
        <button className="secondary" onClick={loadImport}><Icon name="down" size={16} /> Aus Ausgaben übernehmen{d.importable ? ` (${d.importable})` : ""}</button>
      </div>
      {d.receipts?.unverified > 0 && (
        <p className="notice warn block">{d.receipts.unverified} automatisch erfasste Position{d.receipts.unverified === 1 ? "" : "en"} noch ungeprüft:
          Beleg öffnen, Betrag und Kostenart vergleichen, dann „geprüft“ antippen.</p>
      )}
      {scanning && <ReceiptScan periodId={d.period.id} meta={meta} onClose={() => setScanning(false)} onImported={(r) => { onChange(r); setScanning(false); }} />}
      <p className="hint">Erfassen Sie die Gesamtkosten des Hauses für den Zeitraum. Bezahlte Ausgaben des Objekts (Abflussprinzip) können Sie direkt übernehmen;
        Verwaltung, Instandhaltung und Zinsen werden dabei als „nicht umlagefähig“ eingeordnet.</p>

      {importing && (
        <div className="import-box">
          <h3>Ausgaben im Zeitraum ({importing.length})</h3>
          {!importing.length ? <p className="muted">Keine weiteren Ausgaben dieses Objekts im Zeitraum.</p> : (
            <ul className="import-list">
              {importing.map((e) => (
                <li key={e.id}>
                  <span><strong>{e.payee || e.document_title || "Ausgabe"}</strong> <span className="muted">{date(e.paid_on || e.invoice_date)} · {money(e.amount_cents)}</span>
                    <span className="muted sub-line">{e.suggested_reason}</span></span>
                  <select value={choice[e.id]} onChange={(ev) => setChoice({ ...choice, [e.id]: ev.target.value })} aria-label="Kostenart">
                    <option value="__skip">nicht übernehmen</option>
                    {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                  </select>
                </li>
              ))}
            </ul>
          )}
          <div className="row-actions">
            {importing.length > 0 && <button className="primary" disabled={busy} onClick={doImport}>Übernehmen</button>}
            <button className="ghost" onClick={() => setImporting(null)}>Schließen</button>
          </div>
        </div>
      )}

      {d.lines.length === 0 ? <p className="muted">Noch keine Kosten erfasst.</p> : (
        <div className="cost-groups">
          {d.lines.map((l: any) => (
            <div key={l.cost_type} className={`cost-group ${l.excluded ? "excluded" : ""}`}>
              <div className="cost-group-head"><strong>{l.label}</strong><span className="muted">{l.ref}</span><strong className="num">{money(l.total)}</strong></div>
              <ul className="plain-list">
                {l.items.map((c: any) => (
                  <li key={c.id} className="swipe-li"><SwipeRow className="cost-item" asking={doomed?.id === c.id} onDelete={() => setDoomed(c)}>
                    <span>{c.description || "ohne Beschreibung"}<span className="muted sub-line">{c.invoice_date ? date(c.invoice_date) : "ohne Datum"}
                      {c.unit_name ? ` · nur ${c.unit_name}` : ""}{c.origin === "ausgabe" ? " · aus Ausgaben" : ""}
                      {c.origin === "beleg" ? " · aus Beleg" : ""}
                      {c.document_id ? <> · <a href={`#/dokument/${c.document_id}`}>Beleg</a></> : ""}</span>
                      {c.evidence && <span className="bk-evidence">„{c.evidence}“</span>}
                      {c.origin !== "manuell" && (c.verified_at
                        ? <span className="verify done"><Icon name="check" size={14} /> geprüft <button className="link small" onClick={() => run(() => patch(`/api/bk/costs/${c.id}`, { verified: false }))}>zurücksetzen</button></span>
                        : <span className="verify"><span className="status-pill entwurf">ungeprüft</span>
                          <button className="secondary small" disabled={busy} onClick={() => run(() => patch(`/api/bk/costs/${c.id}`, { verified: true }), "Als geprüft markiert.")}>geprüft</button></span>)}
                    </span>
                    <select value={l.cost_type} onChange={(e) => run(() => patch(`/api/bk/costs/${c.id}`, { cost_type: e.target.value }))} aria-label="Kostenart ändern">
                      {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                    </select>
                    <span className="num">{money(c.amount_cents)}</span>
                    <button className="icon-btn" aria-label="Entfernen" onClick={() => setDoomed(c)}><Icon name="trash" size={16} /></button>
                  </SwipeRow></li>
                ))}
              </ul>
            </div>
          ))}
          <p className="hint swipe-hint">Zum Löschen eine Position nach links wischen oder den Papierkorb antippen.</p>
        </div>
      )}
      <ConfirmDelete open={!!doomed} title="Kostenposition löschen?" busy={busy} onClose={() => setDoomed(null)}
        confirmLabel="Position löschen"
        onConfirm={async () => { const r = await run(() => del(`/api/bk/costs/${doomed.id}`), "Kostenposition gelöscht."); if (r) setDoomed(null); }}
        text={doomed && <>
          <p><strong>{doomed.description || "ohne Beschreibung"}</strong> · {money(doomed.amount_cents)}{doomed.invoice_date ? ` · ${date(doomed.invoice_date)}` : ""}</p>
          <p className="muted">Die Position wird aus dieser Abrechnung entfernt.{doomed.origin === "ausgabe" ? " Die Ausgabe selbst und ihr Beleg bleiben erhalten und können erneut übernommen werden." : ""}</p></>} />

      <form className="form grid add-cost" onSubmit={add}>
        <h3 className="span2">Kosten manuell erfassen</h3>
        <label>Kostenart<select value={form.cost_type} onChange={(e) => setForm({ ...form, cost_type: e.target.value })}>
          {types.map((t) => <option key={t.key} value={t.key}>{t.label}{t.ref ? ` (${t.ref.replace(" BetrKV", "")})` : ""}</option>)}
        </select></label>
        <label>Betrag (€)<input inputMode="decimal" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} required placeholder="Gutschrift mit Minus" /></label>
        <label>Beschreibung<input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="z. B. Grundsteuer B 2026" /></label>
        <label>Rechnungsdatum<input type="date" value={form.invoice_date} onChange={(e) => setForm({ ...form, invoice_date: e.target.value })} /></label>
        {units.length > 0 && <label className="span2">Betrifft nur Wohnung<select value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })}>
          <option value="">– ganzes Haus –</option>{units.map((u: any) => <option key={u.id} value={u.id}>{u.name}</option>)}</select>
          <span className="hint">Nur für Kosten mit Schlüssel „Direkt zugeordnet“.</span></label>}
        <button className="primary span2" disabled={busy}><Icon name="plus" size={18} /> Hinzufügen</button>
      </form>
      {d.lines.some((l: any) => l.excluded) && <p className="hint">Nicht umlagefähige Kosten ({money(d.landlord.excluded)}) bleiben sichtbar, werden aber nicht verteilt.</p>}
    </section>
  );
}

// 2 · Schlüssel
function KeysStep({ d, meta, onChange }: { d: any; meta: BkMeta; onChange: (d: any) => void }) {
  const { busy, run } = useAction(onChange);
  const present = d.lines.filter((l: any) => !l.excluded);
  const [vp, setVp] = useState(String(d.period.vacancy_persons).replace(".", ","));
  return (
    <section className="panel">
      <h2>Umlageschlüssel je Kostenart</h2>
      <p className="hint">Maßgeblich ist, was im Mietvertrag vereinbart ist. Ohne Vereinbarung gilt die Wohnfläche; verbrauchsabhängig erfasste Kosten werden nach Verbrauch verteilt (§ 556a BGB).
        Heiz- und Warmwasserkosten übernehmen Sie aus der Abrechnung der Ablesefirma.</p>
      {present.length === 0 && <p className="muted">Erfassen Sie zuerst Kosten.</p>}
      <div className="key-list">
        {present.map((l: any) => (
          <KeyRow key={l.cost_type} line={l} current={d.keys[l.cost_type]} meta={meta} busy={busy}
            onSave={(key, reason) => run(() => put(`/api/bk/periods/${d.period.id}/keys/${l.cost_type}`, { key, reason }), "Schlüssel gespeichert.")} />
        ))}
      </div>
      {present.some((l: any) => l.key === "personen") && (
        <div className="inline-add">
          <label>Personen je leerstehender Wohnung<input inputMode="decimal" value={vp} onChange={(e) => setVp(e.target.value)} /></label>
          <button className="secondary" disabled={busy} onClick={() => run(() => patch(`/api/bk/periods/${d.period.id}`, { vacancy_persons: Number(vp.replace(",", ".")) }), "Gespeichert.")}>Speichern</button>
        </div>
      )}
    </section>
  );
}

function KeyRow({ line, current, meta, busy, onSave }: { line: any; current: any; meta: BkMeta; busy: boolean; onSave: (k: string, r: string) => void }) {
  const [key, setKey] = useState(current?.key || line.key);
  const [reason, setReason] = useState(current?.reason || "");
  const k = meta.keys.find((x) => x.key === key);
  return (
    <div className="key-row">
      <div><strong>{line.label}</strong><span className="muted sub-line">{line.ref} · {money(line.total)}</span></div>
      <select value={key} onChange={(e) => setKey(e.target.value)} aria-label={`Schlüssel ${line.label}`}>
        {meta.keys.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
      </select>
      <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder={k?.why} aria-label="Begründung" />
      <button className="secondary small" disabled={busy || (key === (current?.key || line.key) && reason === (current?.reason || ""))} onClick={() => onSave(key, reason)}>Speichern</button>
    </div>
  );
}

// 3 · Werte (Verbrauch, Ablesefirma, Anteile) und CO₂
function ValuesStep({ d, onChange }: { d: any; onChange: (d: any) => void }) {
  const { busy, run } = useAction(onChange);
  const lines = d.lines.filter((l: any) => ["verbrauch", "extern", "individuell"].includes(l.key));
  const targets = useMemo(() => {
    const m = new Map<string, any>();
    for (const s of d.segments) {
      const k = tk(s.unit_id, s.tenancy_id);
      if (!m.has(k)) m.set(k, { unit_id: s.unit_id, tenancy_id: s.tenancy_id, label: s.tenancy_id ? s.names : `Leerstand ${s.unit_name}`, unit_name: s.unit_name, days: 0 });
      m.get(k).days += s.days;
    }
    return [...m.values()];
  }, [d.segments]);
  const heat = d.lines.some((l: any) => ["heizung", "warmwasser", "heizung_warmwasser"].includes(l.cost_type));
  return (
    <>
      {lines.length === 0 && !heat && <section className="panel"><p className="muted">Für die gewählten Schlüssel sind keine Einzelwerte nötig.</p></section>}
      {lines.map((l: any) => (
        <section key={l.cost_type} className="panel">
          <h2>{l.label} <span className="muted small-num">· {l.key_label} · {money(l.total)}</span></h2>
          {l.key === "extern" && <p className="hint">Beträge je Nutzer laut Heizkostenabrechnung der Ablesefirma (bei Mieterwechsel mit Zwischenablesung, § 9b HeizkostenV). Der Leerstandsanteil trägt der Vermieter.</p>}
          {l.key === "verbrauch" && <p className="hint">Abgelesene Verbräuche je Mieter. Fehlt ein Wert, nur ein zulässiges Ersatzverfahren verwenden und als „geschätzt“ mit Begründung kennzeichnen.</p>}
          <div className="value-rows">
            {(l.key === "individuell" ? d.units.filter((u: any) => u.id !== undefined).map((u: any) => ({ unit_id: u.id, tenancy_id: null, label: u.name })) : targets).map((t: any) => (
              <ValueRow key={tk(t.unit_id, t.tenancy_id)} line={l} target={t} busy={busy}
                current={d.values.find((v: any) => v.cost_type === l.cost_type && (v.unit_id ?? null) === (t.unit_id ?? null) && (v.tenancy_id ?? null) === (t.tenancy_id ?? null))}
                onSave={(body) => run(() => put(`/api/bk/periods/${d.period.id}/values`, { cost_type: l.cost_type, unit_id: t.unit_id, tenancy_id: t.tenancy_id, ...body }), "Gespeichert.")} />
            ))}
          </div>
          {l.key === "extern" && (() => {
            const sum = d.values.filter((v: any) => v.cost_type === l.cost_type).reduce((a: number, v: any) => a + (v.amount_cents || 0), 0);
            return <p className={`hint ${sum !== l.total ? "warn-text" : ""}`}>Summe der Einzelbeträge: {money(sum)} von {money(l.total)}</p>;
          })()}
        </section>
      ))}
      {heat && <Co2Panel d={d} onChange={onChange} />}
    </>
  );
}

function ValueRow({ line, target, current, busy, onSave }: { line: any; target: any; current: any; busy: boolean; onSave: (b: any) => void }) {
  const isAmount = line.key === "extern";
  const [v, setV] = useState(current ? (isAmount ? centsToInput(current.amount_cents) : String(current.value ?? "").replace(".", ",")) : "");
  const [est, setEst] = useState(Boolean(current?.estimated));
  const [note, setNote] = useState(current?.note || "");
  const save = () => {
    const raw = v.trim();
    if (!raw) return onSave({ value: null, amount_cents: null });
    if (isAmount) return onSave({ amount_cents: parseMoney(raw) });
    onSave({ value: Number(raw.replace(/\./g, "").replace(",", ".")), estimated: est, note });
  };
  return (
    <div className="value-row">
      <span>{target.label}{target.unit_name && target.tenancy_id ? <span className="muted"> · {target.unit_name}</span> : null}</span>
      <input inputMode="decimal" value={v} onChange={(e) => setV(e.target.value)} placeholder={isAmount ? "Betrag €" : "Wert"} aria-label={`Wert ${target.label}`} />
      {line.key === "verbrauch" && (
        <>
          <label className="chip-toggle small-check"><input type="checkbox" checked={est} onChange={(e) => setEst(e.target.checked)} /> geschätzt</label>
          {est && <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Grund und Verfahren" aria-label="Begründung der Schätzung" />}
        </>
      )}
      <button className="secondary small" disabled={busy} onClick={save}>Speichern</button>
    </div>
  );
}

function Co2Panel({ d, onChange }: { d: any; onChange: (d: any) => void }) {
  const { busy, run } = useAction(onChange);
  const c = d.period.co2 || {};
  const [f, setF] = useState({ stage: c.stage || "", kg_m2: c.kg_m2 != null ? String(c.kg_m2).replace(".", ",") : "", landlord_percent: c.landlord_percent ?? "",
    tenant_share: centsToInput(c.tenant_share_cents), not_applicable: Boolean(c.not_applicable) });
  const save = () => run(() => patch(`/api/bk/periods/${d.period.id}`, { co2: {
    stage: f.stage, kg_m2: f.kg_m2 ? Number(String(f.kg_m2).replace(",", ".")) : null, landlord_percent: f.landlord_percent === "" ? null : Number(f.landlord_percent),
    tenant_share_cents: f.tenant_share ? parseMoney(f.tenant_share) : null, not_applicable: f.not_applicable } }), "CO₂-Angaben gespeichert.");
  return (
    <section className="panel">
      <h2>CO₂-Kostenaufteilung</h2>
      <p className="hint">Nach § 7 CO2KostAufG gehören Einstufung, Mieteranteil und Berechnungsgrundlagen in die Heizkostenabrechnung, sonst darf der Mieter um 3 % kürzen.
        Die Werte stehen in der Regel in der Abrechnung der Ablesefirma.</p>
      <div className="form grid">
        <label>Einstufung (Stufe)<input value={f.stage} onChange={(e) => setF({ ...f, stage: e.target.value })} placeholder="z. B. Stufe 5" /></label>
        <label>kg CO₂ je m² und Jahr<input inputMode="decimal" value={f.kg_m2} onChange={(e) => setF({ ...f, kg_m2: e.target.value })} /></label>
        <label>Vermieteranteil (%)<input inputMode="numeric" value={f.landlord_percent} onChange={(e) => setF({ ...f, landlord_percent: e.target.value })} /></label>
        <label>Mieteranteil gesamt (€)<input inputMode="decimal" value={f.tenant_share} onChange={(e) => setF({ ...f, tenant_share: e.target.value })} /></label>
        <label className="span2 check"><input type="checkbox" checked={f.not_applicable} onChange={(e) => setF({ ...f, not_applicable: e.target.checked })} /> Nicht anwendbar (z. B. Heizung ohne BEHG-Brennstoff)</label>
        <button className="primary span2" disabled={busy} onClick={save}>Speichern</button>
      </div>
    </section>
  );
}

// 4 · Nutzungszeiten
function UsageStep({ d }: { d: any }) {
  const byUnit = d.units.map((u: any) => ({ ...u, segs: d.segments.filter((s: any) => (s.unit_id ?? null) === (u.id ?? null)) }));
  return (
    <section className="panel occupancy">
      <h2>Mietverhältnisse und Nutzungszeiten</h2>
      <p className="hint">Grundlage für zeitanteilige Kosten. Abgerechnet wird nach der Vertragslaufzeit; Leerstand trägt der Vermieter. Änderungen nehmen Sie beim jeweiligen Mietverhältnis vor.</p>
      {byUnit.map((u: any) => (
        <div key={u.id ?? 0} className="occ-unit">
          <div className="occ-head"><strong>{u.name}{u.living_area ? <span className="muted"> · {String(u.living_area).replace(".", ",")} m²</span> : <span className="neg"> · Wohnfläche fehlt</span>}</strong></div>
          <div className="occ-bar">{u.segs.map((s: any, i: number) => <span key={i} className={s.tenancy_id ? "let" : "vacant"} style={{ flexGrow: s.days }} />)}</div>
          <ul className="occ-legend">
            {u.segs.map((s: any, i: number) => (
              <li key={i}>{s.tenancy_id ? <a href={`#/mieter/${s.tenancy_id}`}>{s.names}</a> : <span className="muted">Leerstand</span>}
                <span className="muted">{date(s.from)} – {date(s.to)} · {s.days} von {d.period_days} Tagen</span></li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

// 5 · Vorauszahlungen
function AdvancesStep({ d, onChange }: { d: any; onChange: (d: any) => void }) {
  return (
    <section className="panel">
      <h2>Vorauszahlungen</h2>
      <p className="hint">Abgezogen werden nur tatsächlich geleistete Vorauszahlungen (Ist), nicht die vereinbarten (Soll). Der Vorschlag aus dem Mietkonto wird nie automatisch übernommen –
        bitte prüfen und bestätigen.</p>
      <div className="adv-list">{d.results.map((r: any) => <AdvanceRow key={r.tenancy_id} d={d} r={r} onChange={onChange} />)}</div>
    </section>
  );
}

function AdvanceRow({ d, r, onChange }: { d: any; r: any; onChange: (d: any) => void }) {
  const { busy, run } = useAction(onChange);
  const cur = d.advances.find((a: any) => a.tenancy_id === r.tenancy_id);
  const [bk, setBk] = useState(cur ? centsToInput(cur.bk_cents) : "");
  const [hk, setHk] = useState(cur ? centsToInput(cur.hk_cents) : "");
  const [note, setNote] = useState(cur?.note || "");
  const [sug, setSug] = useState<any>(null);
  useEffect(() => { get(`/api/bk/periods/${d.period.id}/advances/${r.tenancy_id}/suggestion`).then(setSug).catch(() => {}); }, [d.period.id, r.tenancy_id]);
  return (
    <div className={`adv-row ${cur?.confirmed ? "ok" : ""}`}>
      <div className="adv-head"><strong>{r.names}</strong><span className="muted">{r.unit_name ? `${r.unit_name} · ` : ""}{date(r.from)} – {date(r.to)}</span>
        {cur?.confirmed ? <span className="status-pill bestaetigt">bestätigt</span> : <span className="status-pill entwurf">offen</span>}</div>
      <p className="small-num muted">Soll (vereinbart): BK {money(r.advances_soll.bk)} · HK {money(r.advances_soll.hk)}</p>
      {sug && <p className="hint">{sug.text}{sug.suggestion && <> <button className="link small" onClick={() => { setBk(centsToInput(sug.suggestion.bk)); setHk(centsToInput(sug.suggestion.hk)); }}>Vorschlag einsetzen</button></>}</p>}
      <div className="inline-add">
        <label>BK geleistet (€)<input inputMode="decimal" value={bk} onChange={(e) => setBk(e.target.value)} /></label>
        <label>HK geleistet (€)<input inputMode="decimal" value={hk} onChange={(e) => setHk(e.target.value)} /></label>
        <label>Notiz<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="z. B. Juni nicht gezahlt" /></label>
        <button className="primary" disabled={busy || bk === "" || hk === ""} onClick={() => run(() => put(`/api/bk/periods/${d.period.id}/advances/${r.tenancy_id}`,
          { bk_cents: parseMoney(bk) || 0, hk_cents: parseMoney(hk) || 0, note }), "Vorauszahlungen bestätigt.")}>Bestätigen</button>
      </div>
    </div>
  );
}

// 6 · Ergebnis
function ResultStep({ d }: { d: any }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <section className="panel">
      <h2>Ergebnis je Mietverhältnis</h2>
      <div className="table-wrap">
        <table className="terms-table result-table">
          <thead><tr><th>Mieter</th><th>Kosten</th><th>Vorausz.</th><th>Ergebnis</th></tr></thead>
          <tbody>
            {d.results.map((r: any) => (
              <Fragment key={r.tenancy_id}>
                <tr className="clickable" onClick={() => setOpen(open === r.tenancy_id ? null : r.tenancy_id)}>
                  <td>{r.names}<span className="muted sub-line">{r.unit_name ? `${r.unit_name} · ` : ""}{r.days} Tage</span></td><td>{money(r.total)}</td><td>{money(r.paid)}</td>
                  <td className={r.balance > 0 ? "neg" : r.balance < 0 ? "pos" : ""}>{r.balance > 0 ? "Nachzahlung " : r.balance < 0 ? "Guthaben " : ""}{money(Math.abs(r.balance))}</td>
                </tr>
                {open === r.tenancy_id && (
                  <tr className="detail"><td colSpan={4}>
                    <table className="terms-table">
                      <thead><tr><th>Kostenart</th><th>Gesamt</th><th>Schlüssel</th><th>Ihre Einheiten</th><th>Anteil</th></tr></thead>
                      <tbody>{r.positions.map((x: any) => (
                        <tr key={x.cost_type}><td>{x.label}</td><td>{money(x.total)}</td><td>{x.key_label}<span className="muted sub-line">{x.basis.total}</span></td><td>{x.basis.mine}</td><td>{money(x.amount)}</td></tr>
                      ))}</tbody>
                    </table>
                  </td></tr>
                )}
              </Fragment>
            ))}
            {d.vacancy.filter((v: any) => v.days).map((v: any) => (
              <tr key={`v${v.unit_id}`} className="muted-row"><td>Leerstand {v.unit_name}<span className="muted sub-line">{v.days} Tage · trägt Vermieter</span></td><td>{money(v.amount)}</td><td>–</td><td>–</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Verteilt: {money(d.totals.distributed)} von {money(d.totals.distributable)} umlagefähigen Kosten (Rundung nach der Methode des größten Rests – Summe stimmt auf den Cent).</p>
    </section>
  );
}

// 7 · Prüfung
function CheckStep({ d, onChange }: { d: any; onChange: (d: any) => void }) {
  const { busy, run } = useAction(onChange);
  const [ack, setAck] = useState<string[]>(d.acknowledged);
  useEffect(() => setAck(d.acknowledged), [d.acknowledged]);
  const groups = ["kritisch", "warnung", "info"].map((lv) => [lv, d.issues.filter((i: any) => i.level === lv)] as const);
  const warnings = d.issues.filter((i: any) => i.level === "warnung");
  return (
    <section className="panel">
      <h2>Plausibilitätsprüfung</h2>
      <p className="hint">Kritische Fehler verhindern die Freigabe. Warnungen müssen Sie nach Prüfung einzeln bestätigen. Hinweise haben keine unmittelbare Auswirkung.</p>
      {d.issues.length === 0 && <p className="notice ok">Keine Auffälligkeiten.</p>}
      {groups.map(([lv, list]) => list.length > 0 && (
        <div key={lv} className={`issue-group ${lv}`}>
          <h3>{LEVEL[lv]} ({list.length})</h3>
          <ul>
            {list.map((i: any) => (
              <li key={i.code}>
                {lv === "warnung" ? (
                  <label className="check"><input type="checkbox" checked={ack.includes(i.code)} onChange={(e) => setAck(e.target.checked ? [...ack, i.code] : ack.filter((c) => c !== i.code))} /> {i.text}</label>
                ) : i.text}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {warnings.length > 0 && <button className="primary" disabled={busy} onClick={() => run(() => post(`/api/bk/periods/${d.period.id}/acknowledge`, { codes: ack }), "Bestätigungen gespeichert.")}>Geprüfte Warnungen bestätigen</button>}
    </section>
  );
}

// 8 · Vorschau und Freigabe
function ReleaseStep({ d, onChange }: { d: any; onChange: (d: any) => void }) {
  const { busy, run } = useAction(onChange);
  const [note, setNote] = useState("");
  const id = d.period.id;
  return (
    <>
      <section className="panel">
        <h2>Vorschau</h2>
        <p className="hint">Entwurf mit Wasserzeichen – zum Prüfen, nicht zum Versand.</p>
        <ul className="plain-list">
          {d.results.map((r: any) => (
            <li key={r.tenancy_id}><span>{r.names}</span><a className="secondary small btn-link" href={`/api/bk/periods/${id}/preview.pdf?tenancy_id=${r.tenancy_id}`} target="_blank" rel="noopener">PDF ansehen</a></li>
          ))}
          <li><span>Alle Mieter + interne Übersicht</span><a className="secondary small btn-link" href={`/api/bk/periods/${id}/preview.pdf`} target="_blank" rel="noopener">PDF ansehen</a></li>
          <li><span>Belege zur Abrechnung (Belegeinsicht)</span><a className="secondary small btn-link" href={`/api/bk/periods/${id}/receipts.zip`}>ZIP laden</a></li>
        </ul>
      </section>
      <section className="panel">
        <h2>Freigabe</h2>
        {!d.can_release ? (
          <p className="notice warn">Freigabe noch nicht möglich: {d.issues.some((i: any) => i.level === "kritisch") ? "kritische Fehler beheben" : "Warnungen in der Prüfung bestätigen"}.</p>
        ) : (
          <>
            <p className="hint">Die Freigabe speichert eine unveränderliche Fassung und legt je Mieter ein PDF im Archiv ab (Vermietung › Nebenkosten). Spätere Änderungen erzeugen eine neue Fassung; frühere bleiben erhalten.</p>
            <label>Anmerkung zur Fassung<input value={note} onChange={(e) => setNote(e.target.value)} placeholder={d.statements.length ? "z. B. Korrektur Wasser" : "z. B. Erstfassung"} /></label>
            <button className="primary block" disabled={busy} onClick={() => run(() => post(`/api/bk/periods/${id}/release`, { note }), "Abrechnung freigegeben und archiviert.")}>
              <Icon name="check" size={18} /> Abrechnung freigeben
            </button>
          </>
        )}
        {d.statements.length > 0 && (
          <>
            <h3>Freigegebene Fassungen</h3>
            <ul className="plain-list">
              {d.statements.map((s: any) => (
                <li key={s.id}><span>Fassung {s.version} · {date(s.created_at)}{s.note ? <span className="muted"> · {s.note}</span> : null}</span>
                  <a className="secondary small btn-link" href={`/api/bk/statements/${s.id}/pdf`} target="_blank" rel="noopener">PDF</a></li>
              ))}
            </ul>
          </>
        )}
      </section>
      <LandlordPanel />
    </>
  );
}
