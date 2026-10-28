// Rechnungs-Abfrage: erkannte Daten zeigen, Objekt (oder Privat) zuordnen, Ausgabe bestätigen.
import { useMemo, useState } from "react";
import { centsToInput, date, money, parseMoney, post } from "../api";
import { useMeta, useToast, type Meta } from "../lib";
import { CategorySelect, Icon } from "./ui";

const INVOICE_TYPES = ["Rechnung", "Kassenbon", "Quittung"];

/** Ist das Dokument eine Rechnung, die noch bestätigt werden muss? */
export function needsInvoiceCard(doc: any): boolean {
  const p = doc?.suggestion?.payload;
  if (!p || doc.deleted_at) return false;
  if (doc.status === "processing" || doc.status === "error") return false;
  if (doc.status === "filed" && doc.confirmed === 1) return false;
  return Boolean(p.invoice) || INVOICE_TYPES.includes(p.doc_type);
}

type CardProps = { doc: any; onChanged: () => void; compact?: boolean };

export function InvoiceCard(props: CardProps) {
  const { meta } = useMeta();
  if (!meta) return null;
  // neu aufbauen, sobald sich das Dokument ändert (Vorbelegung aus frischen Daten)
  return <InvoiceCardInner key={`${props.doc.id}-${props.doc.updated_at}-${(props.doc.expenses || []).length}`} {...props} meta={meta} />;
}

function InvoiceCardInner({ doc, onChanged, compact = false, meta }: CardProps & { meta: Meta }) {
  const toast = useToast();
  const p = doc.suggestion?.payload || {};
  const inv = p.invoice || {};
  const expenses: any[] = doc.expenses || [];
  const draft = expenses.find((e) => e.origin === "auto") || expenses[0];
  const properties = meta.properties;

  const suggestedArea = useMemo(() => {
    if (doc.status === "filed" && doc.area_key) return doc.area_key;
    const cat = meta.categories.find((c) => c.id === p.category_id);
    if (cat) return cat.area_key;
    if (p.property_id) return "vermietung";
    if (doc.upload_area && doc.upload_area !== "auto") return doc.upload_area;
    return null;
  }, [doc, meta, p.category_id, p.property_id]);

  const [area, setArea] = useState<string | null>(suggestedArea);
  const [propertyId, setPropertyId] = useState<number | null>(doc.property_id ?? p.property_id ?? null);
  const [unitId, setUnitId] = useState<number | null>(doc.unit_id ?? null);
  const [categoryId, setCategoryId] = useState<number | null>(() => {
    const id = doc.category_id ?? p.category_id ?? null;
    return meta.categories.find((c) => c.id === id)?.area_key === suggestedArea ? id : null;
  });
  const [amount, setAmount] = useState(centsToInput(draft?.amount_cents ?? inv.amount_cents ?? p.amount_cents));
  const [expCat, setExpCat] = useState<string>(draft?.category_key ?? inv.expense_category ?? "");
  const [invoiceDate, setInvoiceDate] = useState<string>(draft?.invoice_date ?? doc.doc_date ?? p.doc_date ?? "");
  const [paidOn, setPaidOn] = useState<string>(draft?.paid_on ?? inv.paid_on ?? "");
  const [payee, setPayee] = useState<string>(draft?.payee || doc.correspondent_name || doc.suggestion?.labels?.correspondent || inv.payee || "");
  const [note, setNote] = useState<string>(draft?.note ?? (inv.number ? `Rechnung Nr. ${inv.number}` : ""));
  const [busy, setBusy] = useState(false);

  const known = area === "privat" || (area === "vermietung" && (propertyId !== null || properties.length === 0));
  const [editing, setEditing] = useState(!known);

  const prop = properties.find((x) => x.id === propertyId);
  const expLabel = meta.expense_categories.find((c) => c.key === expCat)?.label;
  const catPath = meta.categories.find((c) => c.id === categoryId)?.path;
  const applied = doc.status === "filed";

  const choose = (nextArea: string, pid: number | null) => {
    setArea(nextArea);
    setPropertyId(pid);
    setUnitId(null);
    if (meta.categories.find((c) => c.id === categoryId)?.area_key !== nextArea) setCategoryId(null);
    setEditing(true);
  };

  const submit = async () => {
    if (!area) return toast({ kind: "error", text: "Bitte zuerst wählen, wofür die Rechnung ist." });
    if (area === "vermietung" && properties.length > 0 && !propertyId) return toast({ kind: "error", text: "Bitte ein Mietobjekt wählen." });
    let cents: number | null = null;
    if (area === "vermietung" && !invoiceDate && !paidOn) {
      setEditing(true);
      return toast({ kind: "error", text: "Kein Datum im Beleg erkannt – bitte das Rechnungsdatum eintragen." });
    }
    if (area === "vermietung") {
      cents = amount.trim() ? parseMoney(amount) : null;
      if (amount.trim() && (cents === null || cents <= 0)) { setEditing(true); return toast({ kind: "error", text: "Betrag ist ungültig (z. B. 245,00)." }); }
      if (cents && !expCat) { setEditing(true); return toast({ kind: "error", text: "Bitte eine Ausgabenkategorie wählen." }); }
      if (!cents && !confirm("Ohne Betrag ablegen? Dann wird keine Ausgabe erfasst.")) return;
    }
    setBusy(true);
    try {
      await post(`/api/documents/${doc.id}/invoice`, {
        area, property_id: area === "vermietung" ? propertyId : null, unit_id: area === "vermietung" ? unitId : null,
        category_id: categoryId, amount_cents: cents, expense_category: cents ? expCat : null,
        invoice_date: invoiceDate || null, paid_on: paidOn || null, payee: payee.trim(), note: note.trim(),
      });
      toast({ kind: "ok", text: area === "vermietung" && cents ? "Abgelegt, Ausgabe erfasst." : "Abgelegt." });
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const target = area === "privat"
    ? (catPath || "Privat › Sonstiges")
    : area === "vermietung" ? `Vermietung › ${prop ? prop.name : "ohne Objekt"}${prop && unitId ? ` › ${prop.units.find((u) => u.id === unitId)?.name}` : ""}` : null;

  return (
    <section className={`suggestion invoice ${applied ? "applied" : ""} ${compact ? "compact" : ""}`}>
      <div className="suggestion-head">
        <span className="suggestion-kind"><Icon name="receipt" size={16} /> {p.doc_type || "Rechnung"} erkannt{applied ? " · automatisch abgelegt" : ""}</span>
        {inv.number && <span className="muted small">Nr. {inv.number}</span>}
      </div>

      {!known || editing ? (
        <>
          <p className="invoice-question">{area ? "Zuordnung" : "Wofür ist diese Rechnung?"}</p>
          {draft && !draft.property_id && (
            <p className="hint">Betrag {money(draft.amount_cents)} ist bereits erfasst – bitte nur noch das Objekt wählen.</p>
          )}
          <div className="choice-chips" role="radiogroup" aria-label="Zuordnung">
            {properties.map((x) => (
              <button key={x.id} type="button" role="radio" aria-checked={area === "vermietung" && propertyId === x.id}
                className={`choice vermietung ${area === "vermietung" && propertyId === x.id ? "on" : ""}`} onClick={() => choose("vermietung", x.id)}>
                <Icon name="building" size={16} /> {x.name}
              </button>
            ))}
            {properties.length === 0 && (
              <button type="button" role="radio" aria-checked={area === "vermietung"} className={`choice vermietung ${area === "vermietung" ? "on" : ""}`}
                onClick={() => choose("vermietung", null)}><Icon name="building" size={16} /> Vermietung</button>
            )}
            <button type="button" role="radio" aria-checked={area === "privat"} className={`choice privat ${area === "privat" ? "on" : ""}`}
              onClick={() => choose("privat", null)}><Icon name="home" size={16} /> Privat</button>
          </div>
          {!area && p.property_id === undefined && properties.length > 1 && (
            <p className="hint">Im Beleg steht keine Adresse eines Ihrer Objekte.</p>
          )}

          {area && (
            <div className="form grid invoice-form">
              {area === "vermietung" && prop && prop.units.length > 0 && (
                <label className="span2">Einheit
                  <select value={unitId ?? ""} onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">– ganzes Objekt –</option>
                    {prop.units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </label>
              )}
              {area === "vermietung" && (
                <>
                  <label>Betrag (€)<input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0,00" /></label>
                  <label>Ausgabenkategorie
                    <select value={expCat} onChange={(e) => setExpCat(e.target.value)}>
                      <option value="">– wählen –</option>
                      {meta.expense_categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                    </select>
                    {inv.expense_reason && expCat === inv.expense_category && <span className="hint">Vorschlag ({inv.expense_reason})</span>}
                  </label>
                  <label>Rechnungsdatum<input type="date" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} />
                    {!invoiceDate && <span className="hint warn-text">Kein Datum im Beleg erkannt – bitte eintragen.</span>}</label>
                  <label>Bezahlt am<input type="date" value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
                    <span className="hint">leer = Rechnungsdatum (angenommen, später änderbar)</span></label>
                  <label>Aussteller<input value={payee} onChange={(e) => setPayee(e.target.value)} maxLength={120} /></label>
                  <label>Notiz<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={200} /></label>
                </>
              )}
              <label className="span2">Ordner
                <CategorySelect meta={meta} area={area} value={categoryId} onChange={setCategoryId} />
                {!categoryId && <span className="hint">leer = {area === "vermietung" ? "Rechnungen und Belege" : "Sonstiges"}</span>}
              </label>
              {area === "privat" && <p className="hint span2">Privat: Es wird keine Ausgabe für die Anlage V erfasst.</p>}
            </div>
          )}
          <div className="row-actions">
            <button className="primary" disabled={busy || !area} onClick={submit}><Icon name="check" size={18} /> {area === "vermietung" && amount.trim() ? "Ablegen und Ausgabe erfassen" : "Ablegen"}</button>
            {known && <button className="ghost" onClick={() => setEditing(false)}>Abbrechen</button>}
            {compact && <a className="ghost button" href={`#/dokument/${doc.id}`}>Beleg ansehen</a>}
          </div>
        </>
      ) : (
        <>
          <p className="suggestion-target">{target}</p>
          <dl className="suggestion-facts">
            {payee && <><dt>Aussteller</dt><dd>{payee}</dd></>}
            {invoiceDate && <><dt>Datum</dt><dd>{date(invoiceDate)}</dd></>}
            {area === "vermietung" && (
              <>
                <dt>Betrag</dt><dd>{amount ? money(parseMoney(amount)) : "nicht erkannt"}</dd>
                <dt>Kategorie</dt><dd>{expLabel || "bitte wählen"}</dd>
                <dt>Bezahlt</dt><dd>{paidOn ? date(paidOn) : invoiceDate ? `${date(invoiceDate)} (angenommen)` : "–"}</dd>
              </>
            )}
          </dl>
          {area === "vermietung" && draft?.status === "entwurf" && <p className="hint">Betrag ist bereits erfasst und zählt, sobald Sie bestätigen.</p>}
          <div className="row-actions">
            <button className="primary" disabled={busy} onClick={submit}><Icon name="check" size={18} /> {area === "vermietung" && amount ? "Bestätigen" : "Passt"}</button>
            <button className="secondary" disabled={busy} onClick={() => setEditing(true)}>Ändern</button>
            {applied && (
              <button className="ghost" disabled={busy} onClick={() => post(`/api/documents/${doc.id}/undo`).then(onChanged)}><Icon name="undo" size={18} /> Rückgängig</button>
            )}
            {compact && <a className="ghost button" href={`#/dokument/${doc.id}`}>Beleg ansehen</a>}
          </div>
        </>
      )}
    </section>
  );
}
