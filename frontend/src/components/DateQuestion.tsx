// Rückfrage, wenn im Beleg kein Datum lesbar war.
import { useState } from "react";
import { money, post } from "../api";
import { useToast } from "../lib";
import { Icon } from "./ui";

export function DateQuestion({ docId, amount, onChanged, compact = false }: {
  docId: number; amount?: number | null; onChanged: () => void; compact?: boolean;
}) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!value) return toast({ kind: "error", text: "Bitte ein Datum wählen." });
    setBusy(true);
    try {
      await post(`/api/documents/${docId}/date`, { date: value });
      toast({ kind: "ok", text: "Datum gespeichert." });
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className={`suggestion date-question ${compact ? "compact" : ""}`}>
      <p className="invoice-question"><Icon name="alert" size={16} /> Kein Datum im Beleg erkannt</p>
      <p className="hint">Bitte das Rechnungs- bzw. Belegdatum eintragen{amount ? ` (Betrag ${money(amount)})` : ""}. Ohne Datum zählt die
        Ausgabe zu keinem Jahr.</p>
      <div className="date-row">
        <input type="date" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Belegdatum" />
        <button className="primary" disabled={busy || !value} onClick={save}><Icon name="check" size={18} /> Übernehmen</button>
      </div>
      {compact && <a className="ghost button small" href={`#/dokument/${docId}`}>Beleg ansehen</a>}
    </section>
  );
}
