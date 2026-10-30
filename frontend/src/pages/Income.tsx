// Einnahmen: Soll laut Mietverhältnissen gegen Ist aus Mietkonto und Barzahlungen, je Monat.
import { useState, type FormEvent } from "react";
import { centsToInput, date, del, get, money, parseMoney, post } from "../api";
import { go, useLoad, useMeta, useToast } from "../lib";
import { Empty, ErrorBox, Icon, Loading } from "../components/ui";

const MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
const STATUS: Record<string, string> = { bezahlt: "bezahlt", teilweise: "teilweise", offen: "offen", frei: "kein Mietverhältnis", kuenftig: "noch nicht fällig" };

export default function Income({ params }: { params: URLSearchParams }) {
  const { meta } = useMeta();
  const [year, setYear] = useState(Number(params.get("jahr")) || new Date().getFullYear());
  const [property, setProperty] = useState<number | null>(params.get("objekt") ? Number(params.get("objekt")) : null);
  const [open, setOpen] = useState<number | null>(null);
  const { data, error, loading, reload } = useLoad(() => get("/api/income", { year, property_id: property }), [year, property]);
  const years = Array.from({ length: 6 }, (_, i) => new Date().getFullYear() + 1 - i);

  return (
    <div className="page income">
      <div className="page-head">
        <nav className="register small" aria-label="Ansicht">
          <button onClick={() => go("/objekte")}>Objekte</button>
          <button onClick={() => go("/mieter")}>Mieter</button>
          <button className="on">Einnahmen</button>
          <button onClick={() => go("/betriebskosten")}>Betriebskosten</button>
        </nav>
      </div>
      <div className="filters">
        <select value={year} onChange={(e) => setYear(Number(e.target.value))} aria-label="Jahr">{years.map((y) => <option key={y}>{y}</option>)}</select>
        {(meta?.properties.length || 0) > 1 && (
          <select value={property ?? ""} onChange={(e) => setProperty(e.target.value ? Number(e.target.value) : null)} aria-label="Objekt">
            <option value="">Alle Objekte</option>
            {meta!.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        )}
      </div>
      {error && <ErrorBox text={error} />}
      {loading && !data ? <Loading /> : data && (
        <>
          <div className="bk-kpis">
            <div><span>Soll {year}</span><strong>{money(data.totals.soll_year)}</strong></div>
            <div><span>Soll bis heute</span><strong>{money(data.totals.soll_due)}</strong></div>
            <div><span>Eingegangen</span><strong className="pos">{money(data.totals.ist)}</strong></div>
            <div><span>Rückstände</span><strong className={data.totals.arrears ? "neg" : ""}>{money(data.totals.arrears)}</strong></div>
            <div><span>Überzahlungen</span><strong>{money(data.totals.credit)}</strong></div>
          </div>
          {data.open_incoming > 0 && (
            <p className="notice warn"><span>{data.open_incoming} Zahlungseingänge im Mietkonto sind noch nicht zugeordnet.</span>
              <button className="secondary small" onClick={() => go("/eingang")}>Zum Eingang</button></p>
          )}
          {data.unassigned.length > 0 && (
            <section className="panel">
              <h2>Eingänge ohne Mietverhältnis ({data.unassigned.length})</h2>
              <p className="hint">Als Einnahme zugeordnet, aber keinem Mietverhältnis – z. B. weil die Wohnung fehlt oder es zu dem Zeitpunkt kein Mietverhältnis gab.</p>
              <ul className="plain-list">
                {data.unassigned.map((u: any) => (
                  <li key={u.id}><span>{u.counterparty}<span className="muted sub-line">{date(u.booking_date)} · {u.purpose.slice(0, 60)}</span></span>
                    <span className="num">{money(u.amount_cents)}</span></li>
                ))}
              </ul>
              <button className="secondary small" onClick={() => go("/umsaetze?status=done")}>In den Kontoumsätzen korrigieren</button>
            </section>
          )}
          {data.items.length === 0 ? (
            <Empty icon="euro" title={`Keine Mietverhältnisse ${year}`}>Legen Sie unter „Mieter“ die Mietverhältnisse an; die Sollmiete entsteht daraus automatisch.</Empty>
          ) : (
            <div className="income-list">
              {data.items.map((it: any) => (
                <IncomeCard key={it.tenancy_id} it={it} year={year} open={open === it.tenancy_id}
                  onToggle={() => setOpen(open === it.tenancy_id ? null : it.tenancy_id)} onChanged={reload} />
              ))}
            </div>
          )}
          <p className="hint">Soll aus Kaltmiete und Vorauszahlungen der Mietverhältnisse (bei Ein-/Auszug im Monat anteilig). Ist aus zugeordneten Mieteingängen
            des Mietkontos und von Hand erfassten Zahlungen. Eingänge ab dem 25. zählen für den Folgemonat. Der Saldo läuft innerhalb des Jahres auf.</p>
        </>
      )}
    </div>
  );
}

function IncomeCard({ it, year, open, onToggle, onChanged }: { it: any; year: number; open: boolean; onToggle: () => void; onChanged: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ paid_on: new Date().toISOString().slice(0, 10), amount: "", for_month: "", note: "" });
  const [busy, setBusy] = useState(false);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    const cents = parseMoney(f.amount);
    if (!cents || cents <= 0) return toast({ kind: "error", text: "Bitte einen Betrag angeben." });
    setBusy(true);
    try {
      await post("/api/income/receipts", { tenancy_id: it.tenancy_id, paid_on: f.paid_on, amount_cents: cents, for_month: f.for_month || null, note: f.note });
      toast({ kind: "ok", text: "Zahlung erfasst." });
      setF({ ...f, amount: "", note: "" });
      onChanged();
    } catch (ex: any) {
      toast({ kind: "error", text: ex.message });
    } finally {
      setBusy(false);
    }
  };
  const removeReceipt = async (id: number) => {
    if (!confirm("Diese von Hand erfasste Zahlung löschen?")) return;
    await del(`/api/income/receipts/${id}`);
    onChanged();
  };
  const monthsDue = it.months.filter((m: any) => m.soll.total > 0);
  const nextSoll = monthsDue.length ? monthsDue[monthsDue.length - 1].soll.total : 0;

  return (
    <article className={`income-card ${it.arrears ? "arrears" : ""}`}>
      <button className="income-head" onClick={onToggle} aria-expanded={open}>
        <span className="tenant-main">
          <strong>{it.names}</strong>
          <span className="muted">{it.property_name}{it.unit_name ? ` · ${it.unit_name}` : ""}</span>
        </span>
        <span className="tenant-side">
          <span className="num">{money(it.ist)} <span className="muted">von {money(it.soll_due)}</span></span>
          {it.arrears > 0 ? <span className="status-pill ungeklaert">Rückstand {money(it.arrears)}</span>
            : it.credit > 0 ? <span className="status-pill">Überzahlung {money(it.credit)}</span>
              : <span className="status-pill bestaetigt">ausgeglichen</span>}
        </span>
      </button>
      <ol className="month-strip" aria-label="Monate">
        {it.months.map((m: any) => (
          <li key={m.month} className={m.status} title={`${MONTHS[m.month - 1]}: Soll ${money(m.soll.total)}, Ist ${money(m.ist)} – ${STATUS[m.status]}`}>
            <span>{MONTHS[m.month - 1].slice(0, 1)}</span>
          </li>
        ))}
      </ol>
      {open && (
        <div className="income-detail">
          <div className="table-wrap">
            <table className="terms-table">
              <thead><tr><th>Monat</th><th>Soll</th><th>Ist</th><th>Saldo</th></tr></thead>
              <tbody>
                {it.months.filter((m: any) => m.soll.total || m.ist).map((m: any) => (
                  <tr key={m.month} className={`m-${m.status}`}>
                    <td>{MONTHS[m.month - 1]}<span className="muted sub-line">{STATUS[m.status]}</span></td>
                    <td>{money(m.soll.total)}{m.soll.days && m.soll.days < 28 ? <span className="muted sub-line">{m.soll.days} Tage</span> : null}</td>
                    <td>{money(m.ist)}</td>
                    <td className={m.saldo < 0 ? "neg" : ""}>{money(m.saldo)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint small-num">Jahressoll: Kaltmiete {money(it.soll_parts.cold)} · BK-VZ {money(it.soll_parts.bk)} · HK-VZ {money(it.soll_parts.hk)}</p>
          <h3>Zahlungen {year}</h3>
          {it.payments.length === 0 ? <p className="muted">Keine Zahlungen erfasst.</p> : (
            <ul className="plain-list">
              {it.payments.map((p: any) => (
                <li key={`${p.source}${p.id}`}>
                  <span>{date(p.date)} · für {MONTHS[p.month - 1]}{p.year !== year ? ` ${p.year}` : ""}
                    <span className="muted sub-line">{p.source === "konto" ? "Mietkonto" : "von Hand"}{p.unconfirmed ? " · automatisch zugeordnet, noch nicht bestätigt" : ""} · {p.text}</span></span>
                  <span className="num">{money(p.amount)}</span>
                  {p.source === "hand" && <button className="icon-btn" aria-label="Löschen" onClick={() => removeReceipt(p.id)}><Icon name="trash" size={16} /></button>}
                </li>
              ))}
            </ul>
          )}
          <form className="form grid add-receipt" onSubmit={add}>
            <h3 className="span2">Zahlung von Hand erfassen (z. B. bar)</h3>
            <label>Eingegangen am<input type="date" value={f.paid_on} onChange={(e) => setF({ ...f, paid_on: e.target.value })} required /></label>
            <label>Betrag (€)<input inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} placeholder={centsToInput(nextSoll)} /></label>
            <label>Für Monat<input type="month" value={f.for_month} onChange={(e) => setF({ ...f, for_month: e.target.value })} />
              <span className="hint">leer = nach Eingangsdatum</span></label>
            <label>Notiz<input value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="z. B. bar, Quittung Nr. 12" /></label>
            <button className="secondary span2" disabled={busy}><Icon name="plus" size={16} /> Zahlung erfassen</button>
          </form>
        </div>
      )}
    </article>
  );
}
