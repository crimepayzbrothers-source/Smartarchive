import { useState } from "react";
import { date, money, patch, post } from "../api";
import { useToast } from "../lib";
import { Confidence, Icon, sourceLabel } from "./ui";

export function SuggestionBox({ docId, status, suggestion, onChanged, compact = false }: {
  docId: number; status: string; suggestion: any; onChanged: () => void; compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  if (!suggestion) return null;
  const p = suggestion.payload || {};
  const l = suggestion.labels || {};
  const applied = suggestion.status === "applied" && status === "filed";
  const open = suggestion.status === "open";
  const hasTarget = Boolean(l.category);

  const run = async (fn: () => Promise<any>, text: string, undo?: () => Promise<any>) => {
    setBusy(true);
    try {
      await fn();
      toast({ kind: "ok", text, action: undo ? { label: "Rückgängig", run: () => undo().then(onChanged) } : undefined });
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={`suggestion ${applied ? "applied" : ""} ${compact ? "compact" : ""}`}>
      <div className="suggestion-head">
        <span className="suggestion-kind">
          <Icon name={suggestion.source === "rule" ? "rule" : "spark"} size={16} />
          {applied ? "Automatisch abgelegt" : hasTarget ? "Vorschlag" : "Erkannt"} · {sourceLabel(suggestion.source)}
        </span>
        {hasTarget && <Confidence value={suggestion.confidence} />}
      </div>
      {hasTarget && <p className="suggestion-target">{l.category}</p>}
      <dl className="suggestion-facts">
        {l.property && <><dt>Objekt</dt><dd>{l.property}{p.property_assumed ? " (einziges Objekt)" : ""}</dd></>}
        {l.correspondent && <><dt>Absender</dt><dd>{l.correspondent}</dd></>}
        {p.doc_date && <><dt>Datum</dt><dd>{date(p.doc_date)}</dd></>}
        {p.amount_cents ? <><dt>Betrag</dt><dd>{money(p.amount_cents)}</dd></> : null}
        {p.doc_type && <><dt>Art</dt><dd>{p.doc_type}</dd></>}
      </dl>
      {!compact && suggestion.reason && <p className="suggestion-reason">{suggestion.reason}</p>}
      {!compact && suggestion.evidence && <blockquote className="evidence">„{suggestion.evidence}“</blockquote>}
      <div className="row-actions">
        {open && hasTarget && (
          <button className="primary" disabled={busy} onClick={() => run(() => post(`/api/documents/${docId}/accept`), "Abgelegt.")}>
            <Icon name="check" size={18} /> Übernehmen
          </button>
        )}
        {applied && (
          <>
            <button className="secondary" disabled={busy} onClick={() => run(() => patch(`/api/documents/${docId}`, { confirm: true }), "Bestätigt.")}>
              <Icon name="check" size={18} /> Passt
            </button>
            <button className="ghost" disabled={busy} onClick={() => run(() => post(`/api/documents/${docId}/undo`), "Zurück im Eingang.")}>
              <Icon name="undo" size={18} /> Rückgängig
            </button>
          </>
        )}
        {compact && <a className="ghost button" href={`#/dokument/${docId}`}>{open || applied ? "Ändern" : "Ablegen"}</a>}
      </div>
    </section>
  );
}
