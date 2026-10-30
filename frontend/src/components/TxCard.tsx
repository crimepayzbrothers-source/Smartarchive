// Rückfrage zu einem Kontoumsatz: Ausgabe/Einnahme/Ignorieren, Objekt, Kategorie oder passende Rechnung.
import { useState } from "react";
import { centsToInput, date, money, parseMoney, post } from "../api";
import { useMeta, useToast, type Meta } from "../lib";
import { Icon, sourceLabel } from "./ui";

const KIND_LABEL: Record<string, string> = { ausgabe: "Ausgabe", einnahme: "Einnahme", ignoriert: "Ignorieren" };
const SOURCE: Record<string, string> = { beleg: "Rechnung gefunden", history: "Bisherige Zuordnung", zweck: "Verwendungszweck", ai: "KI", keyword: "Stichwörter", user: "Von Ihnen" };

export function TxCard(props: { tx: any; onChanged: () => void }) {
  const { meta } = useMeta();
  if (!meta) return null;
  return <TxCardInner key={`${props.tx.id}-${props.tx.updated_at}`} {...props} meta={meta} />;
}

function TxCardInner({ tx, onChanged, meta }: { tx: any; onChanged: () => void; meta: Meta }) {
  const toast = useToast();
  const s = tx.suggestion || {};
  const auto = tx.status === "auto";
  const done = tx.status === "done";
  const applied = auto || done; // schon zugeordnet: Zusammenfassung, Ändern auf Wunsch
  const out = tx.amount_cents < 0;
  const candidates: any[] = tx.candidates || [];
  const properties = meta.properties;

  const [kind, setKind] = useState<string>(tx.kind || s.kind || (out ? "ausgabe" : "einnahme"));
  const [link, setLink] = useState<number | null>(tx.expense_origin && tx.expense_origin !== "bank" ? tx.expense_id : s.expense_id ?? null);
  const [propertyId, setPropertyId] = useState<number | null>(tx.property_id ?? s.property_id ?? null);
  const [unitId, setUnitId] = useState<number | null>(tx.unit_id ?? s.unit_id ?? null);
  const [cat, setCat] = useState<string>(tx.category_key ?? s.category_key ?? "");
  // Vorschlag „nur Zinsanteil“ (aus dem Verwendungszweck) vorbelegen, sonst der volle Umsatz.
  const [amount, setAmount] = useState(centsToInput(tx.expense_origin === "bank" && tx.expense_amount ? tx.expense_amount
    : s.amount_cents && s.amount_cents <= Math.abs(tx.amount_cents) ? s.amount_cents : Math.abs(tx.amount_cents)));
  const [editing, setEditing] = useState(!applied);
  const [showText, setShowText] = useState(false);
  const [busy, setBusy] = useState(false);
  const prop = properties.find((p) => p.id === propertyId);

  const submit = async () => {
    const body: any = { kind };
    if (kind === "ausgabe" && link) body.expense_id = link;
    else if (kind === "ausgabe") {
      if (properties.length && !propertyId) return toast({ kind: "error", text: "Bitte ein Mietobjekt wählen." });
      if (!cat) return toast({ kind: "error", text: "Bitte eine Ausgabenkategorie wählen." });
      const cents = parseMoney(amount);
      if (!cents || cents <= 0) return toast({ kind: "error", text: "Betrag ist ungültig." });
      Object.assign(body, { property_id: propertyId, unit_id: unitId, category_key: cat, amount_cents: cents });
    } else if (kind === "einnahme") {
      Object.assign(body, { property_id: propertyId, unit_id: unitId });
    }
    setBusy(true);
    try {
      await post(`/api/transactions/${tx.id}/decide`, body);
      toast({ kind: "ok", text: kind === "ignoriert" ? "Ignoriert." : done ? "Zuordnung geändert." : "Zugeordnet." });
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  const undo = async () => {
    setBusy(true);
    try { await post(`/api/transactions/${tx.id}/undo`); onChanged(); } catch (e: any) { toast({ kind: "error", text: e.message }); } finally { setBusy(false); }
  };

  const catLabel = meta.expense_categories.find((c) => c.key === (tx.category_key || cat))?.label;

  return (
    <article className={`tx-card ${applied ? "applied" : ""} ${done ? "done" : ""}`}>
      <header className="tx-head">
        <div className="tx-main">
          <strong className="tx-party">{tx.counterparty || "Ohne Namen"}</strong>
          <span className="muted">{date(tx.booking_date)}{tx.booking_kind ? ` · ${tx.booking_kind}` : ""}</span>
        </div>
        <strong className={`tx-amount ${out ? "out" : "in"}`}>{money(tx.amount_cents)}</strong>
      </header>
      <button className="tx-purpose link" onClick={() => setShowText(!showText)} aria-expanded={showText}>
        {showText ? tx.purpose : tx.purpose.length > 90 ? tx.purpose.slice(0, 90) + " …" : tx.purpose}
      </button>
      {s.source && (
        <p className="tx-reason"><Icon name={s.source === "beleg" ? "receipt" : "spark"} size={15} /> {SOURCE[s.source] || sourceLabel(s.source)}: {tx.reason || s.reason}</p>
      )}
      {s.hint && <p className="notice warn small">{s.hint}</p>}

      {applied && !editing ? (
        <>
          <p className="suggestion-target">
            {done && <span className="tx-state">{tx.kind === "ignoriert" ? "Ignoriert" : "Bestätigt"}</span>}
            {KIND_LABEL[tx.kind]}{tx.property_name ? ` · ${tx.property_name}` : ""}{tx.kind === "ausgabe" && catLabel ? ` · ${catLabel}` : ""}
            {tx.kind === "ausgabe" && tx.expense_amount && tx.expense_amount !== Math.abs(tx.amount_cents) ? ` · ${money(tx.expense_amount)} erfasst` : ""}
          </p>
          {tx.document_id && <a className="hint" href={`#/dokument/${tx.document_id}`}>Verknüpfte Rechnung: {tx.document_title || "ansehen"}</a>}
          {tx.expense_status === "entwurf" && <p className="hint">Ausgabe als Entwurf angelegt – „Passt“ bestätigt sie.</p>}
          <div className="row-actions">
            {auto && <button className="primary" disabled={busy} onClick={submit}><Icon name="check" size={18} /> Passt</button>}
            <button className="secondary" disabled={busy} onClick={() => setEditing(true)}>Ändern</button>
            <button className="ghost" disabled={busy} onClick={undo}><Icon name="undo" size={18} /> {done ? "Zurück zur Klärung" : "Rückgängig"}</button>
          </div>
        </>
      ) : (
        <>
          <fieldset className="segmented small" aria-label="Art">
            {(out ? ["ausgabe", "ignoriert"] : ["einnahme", "ignoriert"]).map((k) => (
              <label key={k} className={kind === k ? "on" : ""}>
                <input type="radio" name={`kind-${tx.id}`} checked={kind === k} onChange={() => setKind(k)} /> {KIND_LABEL[k]}
              </label>
            ))}
          </fieldset>

          {kind === "ausgabe" && candidates.length > 0 && (
            <div className="tx-candidates" role="radiogroup" aria-label="Passende Rechnung">
              <p className="small-title">Passende Rechnung?</p>
              {candidates.map((c) => (
                <label key={c.id} className={`candidate ${link === c.id ? "on" : ""}`}>
                  <input type="radio" name={`link-${tx.id}`} checked={link === c.id} onChange={() => setLink(c.id)} />
                  <span><strong>{c.payee || c.document_title || "Ausgabe"}</strong> · {money(c.amount_cents)}
                    <span className="muted"> · {c.invoice_date ? date(c.invoice_date) : "ohne Datum"}{c.property_name ? ` · ${c.property_name}` : ""}</span></span>
                </label>
              ))}
              <label className={`candidate ${link === null ? "on" : ""}`}>
                <input type="radio" name={`link-${tx.id}`} checked={link === null} onChange={() => setLink(null)} />
                <span>Keine davon – neue Ausgabe aus dem Umsatz</span>
              </label>
            </div>
          )}

          {(kind === "einnahme" || (kind === "ausgabe" && !link)) && properties.length > 0 && (
            <div className="choice-chips" role="radiogroup" aria-label="Mietobjekt">
              {properties.map((p) => (
                <button key={p.id} type="button" role="radio" aria-checked={propertyId === p.id}
                  className={`choice vermietung ${propertyId === p.id ? "on" : ""}`} onClick={() => { setPropertyId(p.id); setUnitId(null); }}>
                  <Icon name="building" size={16} /> {p.name}
                </button>
              ))}
            </div>
          )}

          {(kind === "einnahme" || (kind === "ausgabe" && !link)) && (
            <div className="form grid">
              {prop && prop.units.length > 0 && (
                <label className={kind === "einnahme" ? "span2" : ""}>Einheit
                  <select value={unitId ?? ""} onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">– ganzes Objekt –</option>
                    {prop.units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </label>
              )}
              {kind === "ausgabe" && (
                <>
                  <label>Ausgabenkategorie
                    <select value={cat} onChange={(e) => setCat(e.target.value)}>
                      <option value="">– wählen –</option>
                      {meta.expense_categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                    </select>
                  </label>
                  <label>Betrag (€)<input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
                    <span className="hint">{s.partial && parseMoney(amount) === s.amount_cents ? `Zinsanteil von ${money(Math.abs(tx.amount_cents))}` : "anpassbar, z. B. nur Zinsanteil"}</span></label>
                </>
              )}
            </div>
          )}
          {kind === "einnahme" && <p className="hint">Einnahmen werden gespeichert; die Auswertung für die Anlage V folgt später.</p>}

          <div className="row-actions">
            <button className="primary" disabled={busy} onClick={submit}><Icon name="check" size={18} /> {kind === "ignoriert" ? "Ignorieren" : done ? "Änderung speichern" : "Zuordnen"}</button>
            {applied && <button className="ghost" onClick={() => setEditing(false)}>Abbrechen</button>}
          </div>
        </>
      )}
    </article>
  );
}
