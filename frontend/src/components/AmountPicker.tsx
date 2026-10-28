// Auswahlliste aller im Beleg erkannten Beträge – wenn kein Endbetrag eindeutig ist oder Sie einen anderen wählen möchten.
import { useState } from "react";
import { money, parseMoney } from "../api";
import type { Extracted } from "../extract";

export function AmountPicker({ x, value, onPick }: { x: Extracted | null; value: string; onPick: (cents: number) => void }) {
  const [open, setOpen] = useState(false);
  const list = x?.candidates || [];
  if (!x || !list.length) return null;
  const show = x.needs_choice || open;
  const current = parseMoney(value || "");
  if (!show) {
    return <button type="button" className="link small-link" onClick={() => setOpen(true)}>Anderen Betrag aus dem Beleg wählen ({list.length})</button>;
  }
  return (
    <div className="amount-picker" role="radiogroup" aria-label="Betrag aus dem Beleg wählen">
      <p className="small-title">{x.needs_choice ? "Welcher Betrag ist der Rechnungsbetrag?" : "Beträge im Beleg"}</p>
      {list.map((c) => (
        <button key={c.amount_cents} type="button" role="radio" aria-checked={current === c.amount_cents}
          className={`amount-option ${current === c.amount_cents ? "on" : ""}`} onClick={() => onPick(c.amount_cents)}>
          <strong>{money(c.amount_cents)}</strong>
          <span className="amount-line">„{c.line}“</span>
          {c.tag && <span className={`amount-tag ${c.tag === "Endbetrag" ? "good" : ""}`}>{c.tag}</span>}
          {x.suggested_cents === c.amount_cents && <span className="amount-tag good">KI-Vorschlag</span>}
        </button>
      ))}
      {!x.needs_choice && <button type="button" className="link small-link" onClick={() => setOpen(false)}>Liste schließen</button>}
    </div>
  );
}
