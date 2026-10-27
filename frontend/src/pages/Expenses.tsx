import { useState } from "react";
import { date, get, money } from "../api";
import { useLoad, useMeta } from "../lib";
import { Empty, ErrorBox, Icon, Loading } from "../components/ui";

export default function Expenses({ params }: { params: URLSearchParams }) {
  const { meta } = useMeta();
  const [year, setYear] = useState(Number(params.get("jahr")) || new Date().getFullYear());
  const [property, setProperty] = useState<number | null>(params.get("objekt") ? Number(params.get("objekt")) : null);
  const { data, error, loading } = useLoad(() => Promise.all([
    get("/api/expenses/summary", { year, property_id: property }),
    get("/api/expenses", { year, property_id: property }),
    get("/api/expenses", { year, property_id: property, open: 1 }),
  ]), [year, property]);
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox text={error} />;
  const [s, items, openItems] = data as any[];
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
        <div><span className="kpi-label">Bestätigt</span><strong>{money(s.confirmed)}</strong><span className="muted">fließt in den Export</span></div>
        <div><span className="kpi-label">Noch offen</span><strong>{money(s.unconfirmed)}</strong><span className="muted">Entwurf oder ungeklärt</span></div>
        <div><span className="kpi-label">Nicht sofort abziehbar</span><strong>{money(s.non_deductible)}</strong><span className="muted">gesondert ausgewiesen</span></div>
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

      <section className="panel export">
        <h2>Export für die Steuererklärung {year}</h2>
        <p className="muted">Enthält nur bestätigte Positionen; offene stehen in Excel auf einem eigenen Blatt. Jede Zeile verweist auf ihren Beleg.</p>
        <div className="row-actions">
          <a className="primary button" href={`/api/export/expenses.xlsx?${qs}`}><Icon name="down" size={18} /> Excel</a>
          <a className="secondary button" href={`/api/export/expenses.csv?${qs}`}>CSV</a>
          <a className="secondary button" href={`/api/export/expenses.pdf?${qs}`}>PDF mit Belegen</a>
        </div>
      </section>

      {s.properties.length === 0 ? (
        <Empty icon="euro" title={`Keine bezahlten Ausgaben in ${year}`}>Positionen erfassen Sie am Beleg. Maßgeblich für das Jahr ist das Zahlungsdatum.</Empty>
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
          <p className="muted">Diese Positionen fehlen im Jahr, bis ein Zahlungsdatum eingetragen ist.</p>
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
              <td>{e.paid_on ? date(e.paid_on) : <span className="muted">fehlt</span>}</td>
              <td>{e.payee || e.document_title}</td>
              <td>{labels[e.category_key] || e.category_key}</td>
              <td>{e.property_name || "–"}</td>
              <td className="num">{money(e.amount_cents)}</td>
              <td><span className={`status-pill ${e.status}`}>{e.status === "bestaetigt" ? "Bestätigt" : e.status === "ungeklaert" ? "Ungeklärt" : "Entwurf"}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
