// Hinweis bei möglichem Duplikat: vorhandenes überschreiben, Upload verwerfen oder behalten.
import { useState } from "react";
import { date, money, post } from "../api";
import { go, useToast } from "../lib";
import { Icon } from "./ui";

export function DuplicateCard({ docId, dup, onChanged, compact = false }: {
  docId: number; dup: any; onChanged: () => void; compact?: boolean;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const ex = dup.existing || {};
  const run = async (action: string) => {
    if (action === "replace" && !confirm(`„${ex.title}“ mit diesem Upload überschreiben? Angaben, Ordner und Ausgaben des vorhandenen Dokuments bleiben, die bisherige Datei wird als „ersetzt“ aufbewahrt.`)) return;
    if (action === "discard" && !confirm("Diesen Upload verwerfen? Er kommt in den Papierkorb und bleibt wiederherstellbar.")) return;
    setBusy(true);
    try {
      const r = await post(`/api/documents/${docId}/duplicate`, { action });
      toast({ kind: "ok", text: action === "replace" ? "Vorhandenes Dokument überschrieben." : action === "discard" ? "Upload verworfen." : "Als eigenes Dokument behalten." });
      if (action === "keep") onChanged();
      else if (compact) onChanged();
      else go(`/dokument/${r.document_id}`);
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="suggestion duplicate">
      <p className="invoice-question"><Icon name="alert" size={16} /> Mögliches Duplikat</p>
      <p className="hint">Dieser Upload gleicht einem vorhandenen Dokument ({dup.reason}). Bis zu Ihrer Entscheidung wird keine Ausgabe doppelt gezählt.</p>
      <a className="dup-existing" href={`#/dokument/${ex.id}`}>
        <strong>{ex.title || "Vorhandenes Dokument"}</strong>
        <span className="muted small">{ex.doc_date ? date(ex.doc_date) : `hochgeladen ${date(ex.created_at)}`}
          {ex.property_name ? ` · ${ex.property_name}` : ""}{ex.amount_cents ? ` · ${money(ex.amount_cents)}` : ""}</span>
      </a>
      <div className="row-actions">
        <button className="primary" disabled={busy} onClick={() => run("replace")}><Icon name="undo" size={18} /> Vorhandenes überschreiben</button>
        <button className="secondary" disabled={busy} onClick={() => run("discard")}><Icon name="trash" size={18} /> Upload verwerfen</button>
        <button className="ghost" disabled={busy} onClick={() => run("keep")}>Kein Duplikat – behalten</button>
      </div>
      {compact && <a className="ghost button small" href={`#/dokument/${docId}`}>Neuen Upload ansehen</a>}
    </section>
  );
}
