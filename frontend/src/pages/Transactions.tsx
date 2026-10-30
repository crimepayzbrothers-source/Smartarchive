// Alle Kontoumsätze: suchen, filtern, Zuordnung jederzeit prüfen und korrigieren.
import { useEffect, useState } from "react";
import { date, get, money } from "../api";
import { useLoad, useMeta } from "../lib";
import { BackButton, Empty, ErrorBox, Icon, Loading } from "../components/ui";
import { TxCard } from "../components/TxCard";

const STATUS: [string, string][] = [["", "Alle"], ["open", "Zu klären"], ["auto", "Automatisch"], ["done", "Bestätigt"]];
const KIND: Record<string, string> = { ausgabe: "Ausgabe", einnahme: "Einnahme", ignoriert: "Ignoriert" };

export default function Transactions({ params }: { params: URLSearchParams }) {
  const { meta } = useMeta();
  const [year, setYear] = useState<number | null>(params.get("jahr") ? Number(params.get("jahr")) : new Date().getFullYear());
  const [status, setStatus] = useState(params.get("status") || "");
  const [kind, setKind] = useState("");
  const [property, setProperty] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 250); return () => clearTimeout(t); }, [q]);

  const { data, error, loading, reload } = useLoad(() => get<any[]>("/api/transactions", {
    year, status: status || undefined, kind: kind || undefined, property_id: property, q: debounced || undefined, limit: 1000,
  }), [year, status, kind, property, debounced]);
  const years = Array.from({ length: 6 }, (_, i) => new Date().getFullYear() - i);
  const labels = Object.fromEntries((meta?.expense_categories || []).map((c) => [c.key, c.label]));
  const sum = (data || []).reduce((a, t) => a + t.amount_cents, 0);

  return (
    <div className="page transactions">
      <header className="doc-head">
        <BackButton fallback="/ausgaben" />
        <div className="doc-head-main"><h1>Kontoumsätze</h1><p className="muted">Zuordnungen prüfen und bei Bedarf ändern</p></div>
      </header>

      <form className="search-bar" role="search" onSubmit={(e) => { e.preventDefault(); setDebounced(q.trim()); (document.activeElement as HTMLElement)?.blur(); }}>
        <Icon name="search" size={20} />
        <input type="search" placeholder="Zahlungspartner oder Verwendungszweck" value={q} onChange={(e) => setQ(e.target.value)} enterKeyHint="search" aria-label="Umsätze durchsuchen" />
      </form>

      <fieldset className="segmented small" aria-label="Status">
        {STATUS.map(([k, l]) => (
          <label key={k} className={status === k ? "on" : ""}><input type="radio" name="tx-status" checked={status === k} onChange={() => setStatus(k)} /> {l}</label>
        ))}
      </fieldset>
      <div className="filters">
        <select value={year ?? ""} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : null)} aria-label="Jahr">
          <option value="">Alle Jahre</option>
          {years.map((y) => <option key={y} value={y}>{y}</option>)}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Art">
          <option value="">Alle Arten</option>
          {Object.entries(KIND).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {(meta?.properties.length || 0) > 0 && (
          <select value={property ?? ""} onChange={(e) => setProperty(e.target.value ? Number(e.target.value) : null)} aria-label="Objekt">
            <option value="">Alle Objekte</option>
            {meta!.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        )}
      </div>

      {error && <ErrorBox text={error} />}
      {loading && !data ? <Loading /> : !data?.length ? (
        <Empty icon="euro" title="Keine Umsätze">Filter anpassen oder Umsätze unter „Ausgaben → Mietkonto“ einlesen.</Empty>
      ) : (
        <>
          <p className="result-count">{data.length} Umsätze · Saldo {money(sum)}</p>
          <div className="tx-rows">
            {data.map((t) => openId === t.id ? (
              <div key={t.id} className="tx-open">
                <TxCard tx={t} onChanged={() => { setOpenId(null); reload(); }} />
                <button className="link small" onClick={() => setOpenId(null)}>Schließen</button>
              </div>
            ) : (
              <button key={t.id} className={`tx-row ${t.status}`} onClick={() => setOpenId(t.id)}>
                <span className="tx-row-main">
                  <strong>{t.counterparty || "Ohne Namen"}</strong>
                  <span className="muted">{date(t.booking_date)} · {t.status === "open" ? "zu klären" : [
                    KIND[t.kind] || "", t.property_name, t.kind === "ausgabe" ? labels[t.category_key] : null,
                    t.kind === "ausgabe" && t.expense_amount && t.expense_amount !== Math.abs(t.amount_cents) ? `${money(t.expense_amount)} erfasst` : null,
                  ].filter(Boolean).join(" · ")}{t.status === "auto" ? " · automatisch" : ""}</span>
                </span>
                <span className={`tx-amount ${t.amount_cents < 0 ? "out" : "in"}`}>{money(t.amount_cents)}</span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
