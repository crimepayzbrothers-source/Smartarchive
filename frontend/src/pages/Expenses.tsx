import { useRef, useState } from "react";
import { ApiError, centsToInput, date, get, money, parseMoney, post } from "../api";
import { go, useLoad, useMeta, useToast } from "../lib";
import { Empty, ErrorBox, Icon, Loading } from "../components/ui";

export default function Expenses({ params }: { params: URLSearchParams }) {
  const { meta } = useMeta();
  const [year, setYear] = useState(Number(params.get("jahr")) || new Date().getFullYear());
  const [property, setProperty] = useState<number | null>(params.get("objekt") ? Number(params.get("objekt")) : null);
  const { data, error, loading, reload } = useLoad(() => Promise.all([
    get("/api/expenses/summary", { year, property_id: property }),
    get("/api/expenses", { year, property_id: property }),
    get("/api/expenses", { year, property_id: property, open: 1 }),
    get("/api/expenses/missing", { year }),
  ]), [year, property]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox text={error} />;
  const [s, items, openItems, missing] = data as any[];
  const labels = Object.fromEntries((meta?.expense_categories || []).map((c) => [c.key, c.label]));
  const qs = `year=${year}${property ? `&property_id=${property}` : ""}`;
  const maxCat = Math.max(1, ...s.properties.flatMap((p: any) => p.categories.map((c: any) => c.total)));
  const change = s.previous_year_total ? Math.round(((s.total - s.previous_year_total) / s.previous_year_total) * 100) : null;

  return (
    <div className="page expenses-page">
      <div className="page-head">
        <h1 className="page-title">Ausgaben Vermietung</h1>
        <div className="filters">
          <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Steuerjahr">
            {s.years.map((y: number) => <option key={y} value={y}>{y}</option>)}
          </select>
          <select value={property ?? ""} onChange={(e) => setProperty(e.target.value ? Number(e.target.value) : null)} aria-label="Objekt">
            <option value="">Alle Objekte</option>
            {meta?.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </div>

      <section className="kpis">
        <div><span className="kpi-label">Erfasst {year}</span><strong>{money(s.total)}</strong>
          {change !== null && <span className="muted">{change >= 0 ? "+" : ""}{change} % zum Vorjahr</span>}</div>
        <div><span className="kpi-label">Im Export</span><strong>{money(s.confirmed)}</strong><span className="muted">erfasste Aufwendungen</span></div>
        <div><span className="kpi-label">Automatisch ausgelesen</span><strong>{money(s.auto_total)}</strong>
          <span className="muted">{s.auto_count} Beleg{s.auto_count === 1 ? "" : "e"}, noch nicht geprüft</span></div>
        <div><span className="kpi-label">Noch offen</span><strong>{money(s.unconfirmed)}</strong><span className="muted">Entwurf oder ungeklärt</span></div>
      </section>

      {s.issues.length > 0 && (
        <section className="panel issues">
          <h2><Icon name="alert" size={18} /> Zu prüfen</h2>
          <ul>
            {s.issues.map((i: any, idx: number) => (
              <li key={idx}>{i.document_id ? <a href={`#/dokument/${i.document_id}`}>{i.text}</a> : i.text}</li>
            ))}
          </ul>
          <p className="hint">Hinweise sind keine steuerliche Bewertung, sondern Auffälligkeiten zum Nachsehen.</p>
        </section>
      )}

      {missing.length > 0 && <MissingPanel items={missing} year={year} onChanged={reload} />}

      {s.undated.length > 0 && (
        <section className="panel missing">
          <h2><Icon name="alert" size={18} /> Ohne Datum ({s.undated.length})</h2>
          <p className="muted">Im Beleg war kein Datum lesbar. Diese Positionen zählen zu keinem Jahr, bis Sie am Beleg ein
            Rechnungs- oder Zahlungsdatum eintragen (Position antippen).</p>
          <PositionTable items={s.undated} labels={labels} />
        </section>
      )}

      <BankPanel />

      <section className="panel export">
        <h2>Export für die Steuererklärung {year}</h2>
        <p className="muted">Alle Positionen chronologisch mit Datum, Monat und Jahr, Monatssummen je Objekt und einer Belegnummer, die in
          Excel, CSV und PDF gleich ist. Im PDF folgen die Belege mit aufgestempelter Nummer. Spalte „Erfassung“: automatisch ausgelesen
          oder geprüft.</p>
        <div className="row-actions">
          <a className="primary button" href={`/api/export/expenses.xlsx?${qs}`}><Icon name="down" size={18} /> Excel</a>
          <a className="secondary button" href={`/api/export/expenses.csv?${qs}`}>CSV</a>
          <a className="secondary button" href={`/api/export/expenses.pdf?${qs}`}>PDF mit Belegen</a>
          <a className="secondary button" href={`/api/export/expenses.pdf?${qs}&belege=0`}>PDF ohne Belege</a>
        </div>
      </section>

      {s.properties.length === 0 ? (
        <Empty icon="euro" title={`Keine Ausgaben in ${year}`}>Rechnungen im Bereich Vermietung werden automatisch erfasst. Das Jahr richtet sich nach dem Zahlungsdatum, ersatzweise nach dem Rechnungsdatum.</Empty>
      ) : s.properties.map((p: any) => (
        <section key={p.property} className="panel">
          <header className="panel-head"><h2>{p.property}</h2><strong>{money(p.total)}</strong></header>
          <ul className="cat-bars">
            {p.categories.map((c: any) => (
              <li key={c.key}>
                <span className="cat-name">{c.label}<span className="muted"> ({c.count})</span></span>
                <span className="cat-bar"><span style={{ width: `${(c.total / maxCat) * 100}%` }} /></span>
                <span className="cat-total">{money(c.total)}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {openItems.length > 0 && (
        <section className="panel">
          <h2>Ohne Zahlungsdatum ({openItems.length})</h2>
          <p className="muted">Diese Positionen sind dem Jahr nach Rechnungsdatum zugeordnet. Am einfachsten ergänzen Sie das Zahlungsdatum,
            indem Sie die Umsätze des Mietkontos einlesen – passende Zahlungen tragen es automatisch ein.</p>
          <PositionTable items={openItems} labels={labels} />
        </section>
      )}

      {items.length > 0 && (
        <details className="panel collapsible">
          <summary>Alle Positionen {year} ({items.length})</summary>
          <PositionTable items={items} labels={labels} />
        </details>
      )}
    </div>
  );
}

function PositionTable({ items, labels }: { items: any[]; labels: Record<string, string> }) {
  return (
    <div className="table-wrap">
      <table className="positions">
        <thead><tr><th>Bezahlt</th><th>Aussteller</th><th>Kategorie</th><th>Objekt</th><th className="num">Betrag</th><th>Status</th></tr></thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.id} onClick={() => e.document_id && (window.location.hash = `/dokument/${e.document_id}`)} className={e.document_id ? "clickable" : ""}>
              <td>{e.paid_on ? <>{date(e.paid_on)}{e.paid_assumed ? <span className="muted"> (angen.)</span> : null}</> : <span className="muted">fehlt</span>}</td>
              <td>{e.payee || e.document_title}</td>
              <td>{labels[e.category_key] || e.category_key}</td>
              <td>{e.property_name || "–"}</td>
              <td className="num">{money(e.amount_cents)}</td>
              <td><span className={`status-pill ${e.origin === "auto" && e.status === "bestaetigt" ? "auto" : e.status}`}>
                {e.status !== "bestaetigt" ? (e.status === "ungeklaert" ? "Ungeklärt" : "Entwurf") : e.origin === "auto" ? "Automatisch" : "Geprüft"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Mietkonto: Umsätze als CSV aus dem Online-Banking einlesen.
function BankPanel() {
  const toast = useToast();
  const { data: accounts, reload } = useLoad(() => get("/api/bank/accounts"), []);
  const [accountId, setAccountId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [name, setName] = useState("Mietkonto Commerzbank");
  const fileRef = useRef<HTMLInputElement>(null);
  if (!accounts) return null;
  const selected = accountId ?? accounts[0]?.id ?? null;
  const acc = accounts.find((a: any) => a.id === selected);

  const create = async () => {
    try {
      await post("/api/bank/accounts", { name });
      reload();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    }
  };

  const upload = async (file: File | undefined) => {
    if (!file || !selected) return;
    const form = new FormData();
    form.append("account_id", String(selected));
    form.append("file", file, file.name);
    setBusy(true);
    setResult(null);
    try {
      const res = await fetch("/api/bank/import", { method: "POST", body: form, headers: { "X-Requested-With": "SmartArchive" }, credentials: "same-origin" });
      const data = await res.json().catch(() => null);
      if (!res.ok) throw new ApiError(res.status, data?.error || `Fehler ${res.status}`, data);
      setResult(data);
      reload();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel bank">
      <h2><Icon name="euro" size={18} /> Mietkonto</h2>
      {accounts.length === 0 ? (
        <div className="form">
          <p className="muted">Legen Sie Ihr Mietkonto an. Danach können Sie die Umsätze als CSV aus dem Online-Banking einlesen.</p>
          <label>Bezeichnung<input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} /></label>
          <button className="primary" onClick={create}>Konto anlegen</button>
        </div>
      ) : (
        <>
          {accounts.length > 1 && (
            <select value={selected ?? ""} onChange={(e) => setAccountId(Number(e.target.value))} aria-label="Konto">
              {accounts.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
          <p className="muted">
            {acc?.name}: {acc?.range?.count ? `${acc.range.count} Umsätze vom ${date(acc.range.first)} bis ${date(acc.range.last)}` : "noch keine Umsätze"}
          </p>
          <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          <button className="primary block" disabled={busy} onClick={() => fileRef.current?.click()}>
            <Icon name="down" size={18} /> {busy ? "Wird eingelesen …" : "Umsätze einlesen (CSV)"}
          </button>
          {result && (
            <div className="notice">
              <span>{result.new} neue Umsätze ({date(result.from)} – {date(result.to)}){result.duplicates ? `, ${result.duplicates} schon vorhanden` : ""}.
                {" "}{result.auto} automatisch zugeordnet, {result.open} zum Klären.</span>
              {(result.auto + result.open) > 0 && <button className="secondary small" onClick={() => go("/eingang")}>Zum Eingang</button>}
            </div>
          )}
          <details className="collapsible-inline">
            <summary>So exportieren Sie die Umsätze bei der Commerzbank</summary>
            <ol className="steps">
              <li>Im Online-Banking (Browser) die Umsatzübersicht des Mietkontos öffnen.</li>
              <li>Rechts über den Umsätzen auf „PDF / CSV“ tippen und den Umsatz-Export wählen.</li>
              <li>Zeitraum wählen, Format <strong>CSV</strong>, „Exportieren“, Datei speichern.</li>
              <li>Hier „Umsätze einlesen“ tippen und die Datei wählen. Doppelte Umsätze werden erkannt.</li>
            </ol>
            <p className="hint">Online-Banking zeigt bis zu 12 Monate. Am besten einmal im Monat einlesen.</p>
          </details>
        </>
      )}
    </section>
  );
}

// Rechnungen im Bereich Vermietung ohne Ausgabe (z. B. aus dem Dropbox-Import) nachträglich erfassen.
function MissingPanel({ items, year, onChanged }: { items: any[]; year: number; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const ready = items.filter((i) => i.amount_cents && i.property_id).length;
  const bulk = async () => {
    if (!confirm(`${ready} Rechnung(en) mit dem erkannten Betrag erfassen?`)) return;
    setBusy(true);
    try {
      const r = await post(`/api/expenses/missing/drafts?year=${year}`);
      toast({ kind: "ok", text: `${r.created} erfasst${r.skipped ? `, ${r.skipped} brauchen noch Angaben` : ""}.` });
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel missing">
      <h2><Icon name="receipt" size={18} /> Rechnungen ohne Ausgabe ({items.length})</h2>
      <p className="muted">Belege aus {year}, die nicht automatisch erfasst werden konnten – meist weil kein Endbetrag im Text
        erkennbar war oder kein Mietobjekt zugeordnet ist. Bitte Betrag ergänzen oder „Keine Ausgabe“ wählen.</p>
      {ready > 0 && (
        <button className="secondary block" disabled={busy} onClick={bulk}>Alle {ready} mit erkanntem Betrag erfassen</button>
      )}
      <ul className="missing-list">{items.map((i) => <MissingRow key={i.id} item={i} onChanged={onChanged} />)}</ul>
    </section>
  );
}

function MissingRow({ item, onChanged }: { item: any; onChanged: () => void }) {
  const { meta } = useMeta();
  const toast = useToast();
  const [amount, setAmount] = useState(centsToInput(item.amount_cents));
  const [cat, setCat] = useState<string>(item.category_key || "");
  const [date, setDate] = useState<string>(item.date || "");
  const [paid, setPaid] = useState<string>(item.paid_on || "");
  const [prop, setProp] = useState<number | null>(item.property_id);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const cents = parseMoney(amount);
    if (!cents || cents <= 0) return toast({ kind: "error", text: "Betrag fehlt oder ist ungültig." });
    if (!cat) return toast({ kind: "error", text: "Bitte eine Ausgabenkategorie wählen." });
    if (!prop && meta?.properties.length) return toast({ kind: "error", text: "Bitte ein Mietobjekt wählen." });
    setBusy(true);
    try {
      await post("/api/expenses", { document_id: item.id, property_id: prop, unit_id: item.unit_id, amount_cents: cents,
        category_key: cat, invoice_date: date || null, paid_on: paid || null, payee: item.payee, note: item.note, status: "bestaetigt" });
      toast({ kind: "ok", text: "Ausgabe erfasst." });
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  const dismiss = async () => {
    await post(`/api/documents/${item.id}/no-expense`);
    toast({ kind: "ok", text: "Ausgeblendet – keine Ausgabe." });
    onChanged();
  };

  return (
    <li className="missing-row">
      <a href={`#/dokument/${item.id}`} className="missing-title">{item.title || "Ohne Titel"}</a>
      <span className="muted small">{item.payee || "Aussteller unbekannt"}{item.property_name ? ` · ${item.property_name}` : ""}
        {item.amount_evidence ? ` · erkannt: „${item.amount_evidence}“` : ""}</span>
      <div className="form grid">
        <label>Betrag (€)<input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" /></label>
        <label>Kategorie
          <select value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">– wählen –</option>
            {meta?.expense_categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
          </select>
        </label>
        <label>Rechnungsdatum<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label>Bezahlt am<input type="date" value={paid} onChange={(e) => setPaid(e.target.value)} /></label>
        {!item.property_id && (
          <label className="span2">Mietobjekt
            <select value={prop ?? ""} onChange={(e) => setProp(e.target.value ? Number(e.target.value) : null)}>
              <option value="">– wählen –</option>
              {meta?.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
        )}
      </div>
      <div className="row-actions">
        <button className="primary" disabled={busy} onClick={save}><Icon name="check" size={18} /> Erfassen</button>
        <button className="ghost" disabled={busy} onClick={dismiss}>Keine Ausgabe</button>
      </div>
    </li>
  );
}
