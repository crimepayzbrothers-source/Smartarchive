// Abmahnungen je Mietverhältnis: Anlass, konkrete Vorfälle, Aufforderung, Hinweis auf Folgen, Zustellweg, Ablage.
import { useState, type FormEvent } from "react";
import { date, del, get, post } from "../api";
import { useLoad, useToast } from "../lib";
import { ErrorBox, Icon, Sheet } from "../components/ui";
import { ConfirmDelete, SwipeRow } from "../components/SwipeRow";
import { openPdf } from "./Dunning";

type Incident = { date: string; time: string; text: string };
type Form = { reason: string; letter_date: string; deadline: string; delivery: string; incidents: Incident[]; demand: string;
  consequence: string; salutation: string; extra_text: string };
const EMPTY: Incident = { date: "", time: "", text: "" };

export function WarningPanel({ tenancyId, onChanged }: { tenancyId: number; onChanged?: () => void }) {
  const toast = useToast();
  const { data: d, error, reload } = useLoad(() => get(`/api/tenancies/${tenancyId}/warnings`), [tenancyId]);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [doomed, setDoomed] = useState<any>(null);
  if (error) return <ErrorBox text={error} />;
  if (!d) return null;

  const reasonOf = (k: string) => d.reasons.find((r: any) => r.key === k);
  const start = () => {
    const r = d.reasons[0];
    setForm({ reason: r.key, letter_date: d.defaults.letter_date, deadline: d.defaults.deadline, delivery: d.defaults.delivery,
      incidents: [{ ...EMPTY }], demand: r.demand, consequence: r.consequence, salutation: d.defaults.salutation, extra_text: "" });
  };
  const set = (patch: Partial<Form>) => form && setForm({ ...form, ...patch });
  const setReason = (k: string) => { const r = reasonOf(k); set({ reason: k, demand: r.demand, consequence: r.consequence }); };
  const setInc = (i: number, k: keyof Incident, v: string) =>
    form && set({ incidents: form.incidents.map((x, j) => (j === i ? { ...x, [k]: v } : x)) });
  const addLate = (p: any) => {
    if (!form || form.incidents.some((x) => x.text === p.text)) return;
    const kept = form.incidents.filter((x) => x.date || x.text);
    set({ incidents: [...kept, { date: p.date, time: "", text: p.text }] });
  };
  const needsDeadline = form ? (form.demand + form.consequence + form.extra_text).includes("{frist}") : false;
  const payload = () => ({ ...form, deadline: needsDeadline ? form!.deadline : null });

  const preview = async () => {
    try { await openPdf(`/api/tenancies/${tenancyId}/warnings/preview`, payload()); }
    catch (e: any) { toast({ kind: "error", text: e.message }); }
  };
  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await post(`/api/tenancies/${tenancyId}/warnings`, payload());
      toast({ kind: "ok", text: "Abmahnung erstellt und beim Mieter abgelegt." });
      setForm(null); reload(); onChanged?.();
    } catch (err: any) { toast({ kind: "error", text: err.message }); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    setBusy(true);
    try {
      await del(`/api/warnings/${doomed.id}`);
      toast({ kind: "ok", text: "Eintrag gelöscht." });
      setDoomed(null); reload();
    } catch (err: any) { toast({ kind: "error", text: err.message }); }
    finally { setBusy(false); }
  };

  return (
    <section className="panel warnings">
      <header className="panel-head"><h2>Abmahnungen</h2>
        <button className="secondary" onClick={start}><Icon name="plus" size={16} /> Abmahnung</button>
      </header>
      {d.letters.length === 0 ? <p className="muted">Keine Abmahnungen.</p> : (
        <ul className="plain-list letter-list">
          {d.letters.map((x: any) => (
            <li key={x.id} className="swipe-li">
              <SwipeRow className="letter-item" asking={doomed?.id === x.id} onDelete={() => setDoomed(x)}>
                <span><strong>{x.label}</strong> vom {date(x.letter_date)}
                  <span className="muted sub-line">{x.incidents.length} {x.incidents.length === 1 ? "Vorfall" : "Vorfälle"}
                    {" "}({x.incidents.map((i: any) => date(i.date)).join(", ")}){x.deadline ? ` · Frist ${date(x.deadline)}` : ""}
                    {x.delivery_label ? ` · ${x.delivery_label}` : ""}</span></span>
                <a className="secondary small btn-link" href={`/api/warnings/${x.id}/pdf`} target="_blank" rel="noopener">PDF</a>
                <button className="icon-btn" aria-label="Eintrag löschen" onClick={() => setDoomed(x)}><Icon name="trash" size={16} /></button>
              </SwipeRow>
            </li>
          ))}
        </ul>
      )}

      <Sheet open={!!form} onClose={() => setForm(null)} title="Abmahnung erstellen" wide>
        {form && (
          <form className="form" onSubmit={create} noValidate>
            {d.landlord_missing.length > 0 && (
              <p className="notice warn block">Für den Briefkopf fehlen: {d.landlord_missing.join(", ")}. Eintragen unter
                {" "}<a href="#/betriebskosten">Betriebskosten › Vermieterangaben</a>.</p>
            )}
            <label>Anlass<select value={form.reason} onChange={(e) => setReason(e.target.value)}>
              {d.reasons.map((r: any) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select></label>
            <div className="form grid">
              <label>Briefdatum<input type="date" value={form.letter_date} onChange={(e) => set({ letter_date: e.target.value })} /></label>
              <label>Zustellung<select value={form.delivery} onChange={(e) => set({ delivery: e.target.value })}>
                {d.delivery.map((x: any) => <option key={x.key} value={x.key}>{x.label}</option>)}
              </select></label>
              {needsDeadline && <label>Frist bis<input type="date" value={form.deadline} onChange={(e) => set({ deadline: e.target.value })} /></label>}
            </div>
            <p className="hint">Zustellung: Nachweisbar sind vor allem Einwurf-Einschreiben oder Übergabe durch einen Boten (mit Notiz zu Datum und Uhrzeit).</p>

            <fieldset className="incidents">
              <legend>Vorfälle</legend>
              <p className="hint">So konkret wie möglich: wann, was genau, wer war betroffen oder Zeuge. Pauschale Vorwürfe reichen für eine Abmahnung nicht.</p>
              {form.incidents.map((x, i) => (
                <div key={i} className="incident">
                  <div className="incident-when">
                    <label>Datum<input type="date" value={x.date} onChange={(e) => setInc(i, "date", e.target.value)} /></label>
                    <label>Uhrzeit<input value={x.time} onChange={(e) => setInc(i, "time", e.target.value)} placeholder="z. B. 23:30–01:00" /></label>
                    {form.incidents.length > 1 && (
                      <button type="button" className="icon-btn" aria-label="Vorfall entfernen"
                        onClick={() => set({ incidents: form.incidents.filter((_, j) => j !== i) })}><Icon name="close" size={16} /></button>
                    )}
                  </div>
                  <label>Was ist geschehen?<textarea rows={2} value={x.text} onChange={(e) => setInc(i, "text", e.target.value)} /></label>
                </div>
              ))}
              <button type="button" className="ghost small" onClick={() => set({ incidents: [...form.incidents, { ...EMPTY }] })}>
                <Icon name="plus" size={14} /> Weiterer Vorfall</button>
              {form.reason === "unpuenktlich" && (
                <div className="late-box">
                  <h4>Verspätete Eingänge im Mietkonto</h4>
                  {d.late_payments.length === 0 ? <p className="muted">Keine deutlich verspäteten Eingänge in den letzten 24 Monaten gefunden.</p> : (
                    <ul className="plain-list">
                      {d.late_payments.map((p: any) => (
                        <li key={p.date + p.month}><span>{p.text}</span>
                          <button type="button" className="secondary small" disabled={form.incidents.some((x) => x.text === p.text)}
                            onClick={() => addLate(p)}>Übernehmen</button></li>
                      ))}
                    </ul>
                  )}
                  <p className="hint">Gezeigt werden nur Eingänge mehr als zwei Werktage nach Fälligkeit (dritter Werktag, ohne Samstage und Feiertage in RLP).
                    Maßgeblich ist, wann der Mieter überwiesen hat – nicht die Gutschrift bei Ihnen (BGH VIII ZR 222/15). Bitte prüfen.</p>
                </div>
              )}
            </fieldset>

            <label>Aufforderung<textarea rows={3} value={form.demand} onChange={(e) => set({ demand: e.target.value })} /></label>
            <label>Hinweis auf Folgen<textarea rows={3} value={form.consequence} onChange={(e) => set({ consequence: e.target.value })} /></label>
            {needsDeadline && <p className="hint">„{"{frist}"}“ wird im Schreiben durch das Fristdatum ersetzt.</p>}
            <label>Anrede<input value={form.salutation} onChange={(e) => set({ salutation: e.target.value })} /></label>
            <label>Zusätzlicher Text (optional)<textarea rows={2} value={form.extra_text} onChange={(e) => set({ extra_text: e.target.value })} /></label>
            <p className="hint">Die App liefert Textbausteine und dokumentiert das Schreiben. Ob die Abmahnung berechtigt ist oder eine Kündigung trägt, bewertet sie nicht.</p>
            <div className="row-actions">
              <button type="button" className="secondary" onClick={preview}>Vorschau</button>
              <button className="primary" disabled={busy}>Erstellen und ablegen</button>
            </div>
          </form>
        )}
      </Sheet>
      <ConfirmDelete open={!!doomed} title="Eintrag löschen?" busy={busy} onClose={() => setDoomed(null)} onConfirm={remove} confirmLabel="Eintrag löschen"
        text={doomed && <>
          <p><strong>Abmahnung – {doomed.label}</strong> vom {date(doomed.letter_date)} aus der Liste entfernen?</p>
          <p className="muted">Nur für versehentlich erstellte Schreiben. Das abgelegte PDF bleibt im Archiv beim Mieter; löschen Sie es dort, falls es nie versandt wurde.</p></>} />
    </section>
  );
}
