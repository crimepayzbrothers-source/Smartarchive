// Rückfrage zum Belegdatum: fehlt es, eintragen; ist es unsicher, Vorschlag bestätigen oder anderes wählen.
import { useState } from "react";
import { date, money, post } from "../api";
import { useToast } from "../lib";
import { Icon } from "./ui";

type Candidate = { date: string; line: string; labeled: boolean; due: boolean };

export function DateQuestion({ docId, amount, onChanged, compact = false, suggestion, candidates = [] }: {
  docId: number; amount?: number | null; onChanged: () => void; compact?: boolean; suggestion?: string | null; candidates?: Candidate[];
}) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async (d: string) => {
    if (!d) return toast({ kind: "error", text: "Bitte ein Datum wählen." });
    setBusy(true);
    try {
      await post(`/api/documents/${docId}/date`, { date: d });
      toast({ kind: "ok", text: `Datum ${date(d)} gespeichert.` });
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  const sugg = suggestion ? candidates.find((c) => c.date === suggestion) : null;
  const others = candidates.filter((c) => c.date !== suggestion);
  return (
    <section className={`suggestion date-question ${compact ? "compact" : ""}`}>
      {suggestion ? (
        <>
          <p className="invoice-question"><Icon name="alert" size={16} /> Datum bitte bestätigen</p>
          <p className="hint">Im Beleg ist das Datum nicht eindeutig{amount ? ` (Betrag ${money(amount)})` : ""}. Vorschlag:</p>
          <button className="primary date-confirm" disabled={busy} onClick={() => save(suggestion)}>
            <Icon name="check" size={18} /> {date(suggestion)} stimmt
          </button>
          {sugg && <span className="amount-line">aus „{sugg.line}“</span>}
          {others.length > 0 && (
            <div className="date-others">
              <p className="small-title">Oder ein anderes Datum aus dem Beleg:</p>
              {others.map((c) => (
                <button key={c.date} type="button" className="amount-option" disabled={busy} onClick={() => save(c.date)}>
                  <strong>{date(c.date)}</strong>
                  {c.due && <span className="amount-tag">Fälligkeit</span>}
                  {c.labeled && !c.due && <span className="amount-tag good">beschriftet</span>}
                  <span className="amount-line">„{c.line}“</span>
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <p className="invoice-question"><Icon name="alert" size={16} /> Kein Datum im Beleg erkannt</p>
          <p className="hint">Bitte das Rechnungs- bzw. Belegdatum eintragen{amount ? ` (Betrag ${money(amount)})` : ""}. Ohne Datum zählt die
            Ausgabe zu keinem Jahr.</p>
        </>
      )}
      <div className="date-row">
        <input type="date" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Belegdatum" />
        <button className={suggestion ? "secondary" : "primary"} disabled={busy || !value} onClick={() => save(value)}>
          <Icon name="check" size={18} /> {suggestion ? "Anderes Datum" : "Übernehmen"}
        </button>
      </div>
      {compact && <a className="ghost button small" href={`#/dokument/${docId}`}>Beleg ansehen</a>}
    </section>
  );
}
