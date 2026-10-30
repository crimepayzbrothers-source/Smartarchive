// Mahnwesen je Mietverhältnis: offene Monate aus den Einnahmen, Zahlungserinnerung und Mahnung als PDF.
import { useState, type FormEvent } from "react";
import { date, del, get, money, post } from "../api";
import { useLoad, useToast } from "../lib";
import { ErrorBox, Icon, Sheet } from "../components/ui";
import { ConfirmDelete, SwipeRow } from "../components/SwipeRow";

const KIND: Record<string, string> = { erinnerung: "Zahlungserinnerung", mahnung: "Mahnung" };
const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
export const monthLabel = (key: string) => `${MONTHS[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`;

type Form = { kind: string; letter_date: string; due_date: string; months: string[]; salutation: string; extra_text: string };

async function openPdf(path: string, body: any) {
  // Fenster sofort öffnen (sonst blockiert der Browser das spätere Öffnen nach dem Laden)
  const w = window.open("", "_blank");
  try {
    const res = await fetch(path, { method: "POST", body: JSON.stringify(body), credentials: "same-origin",
      headers: { "X-Requested-With": "SmartArchive", "Content-Type": "application/json" } });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.error || `Fehler ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    if (w) w.location.href = url; else window.location.href = url;
  } catch (e) {
    w?.close();
    throw e;
  }
}

export function DunningPanel({ tenancyId, onChanged }: { tenancyId: number; onChanged?: () => void }) {
  const toast = useToast();
  const { data: d, error, reload } = useLoad(() => get(`/api/tenancies/${tenancyId}/dunning`), [tenancyId]);
  const [form, setForm] = useState<Form | null>(null);
  const [busy, setBusy] = useState(false);
  const [doomed, setDoomed] = useState<any>(null);
  if (error) return <ErrorBox text={error} />;
  if (!d) return null;

  const start = (kind: string) => setForm({ kind, ...d.defaults, months: d.open.map((i: any) => i.month), extra_text: "" });
  const chosenTotal = form ? d.open.filter((i: any) => form.months.includes(i.month)).reduce((a: number, i: any) => a + i.open, 0) : 0;
  const toggle = (m: string) => form && setForm({ ...form, months: form.months.includes(m) ? form.months.filter((x) => x !== m) : [...form.months, m] });

  const preview = async () => {
    try { await openPdf(`/api/tenancies/${tenancyId}/dunning/preview`, form); }
    catch (e: any) { toast({ kind: "error", text: e.message }); }
  };
  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await post(`/api/tenancies/${tenancyId}/dunning`, form);
      toast({ kind: "ok", text: `${KIND[form!.kind]} erstellt und beim Mieter abgelegt.` });
      setForm(null); reload(); onChanged?.();
    } catch (err: any) { toast({ kind: "error", text: err.message }); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    setBusy(true);
    try {
      await del(`/api/dunning/${doomed.id}`);
      toast({ kind: "ok", text: "Eintrag gelöscht." });
      setDoomed(null); reload();
    } catch (err: any) { toast({ kind: "error", text: err.message }); }
    finally { setBusy(false); }
  };
  const th = d.threshold;

  return (
    <section className="panel dunning">
      <h2>Mahnungen</h2>
      {d.open.length === 0 ? (
        <p className="muted">Kein Rückstand.{d.assume_paid && <> Fällige Mieten gelten als eingegangen – eine ausgebliebene Zahlung hinterlegen Sie
          unter <a href="#/einnahmen">Einnahmen</a> („Ausgeblieben?“).</>}</p>
      ) : (
        <>
          <ul className="plain-list">
            {d.open.map((i: any) => (
              <li key={i.month}><span>{monthLabel(i.month)}<span className="muted sub-line">Soll {money(i.soll)} · gezahlt {money(i.ist + (i.credited || 0))}</span></span>
                <strong className="num neg">{money(i.open)}</strong></li>
            ))}
            <li><strong>Offen insgesamt</strong><strong className="num neg">{money(d.total)}</strong></li>
          </ul>
          {th.reached && (
            <p className="notice warn block">Rechnerischer Hinweis: {th.consecutive ? "Für zwei aufeinanderfolgende Monate ist zusammen mehr als eine Monatsmiete offen" : ""}
              {th.consecutive && th.two_rents ? "; " : ""}{th.two_rents ? `der Rückstand erreicht zwei Monatsmieten (${money(2 * th.monthly_rent)})` : ""}.
              Das entspricht einer Schwelle in § 543 Abs. 2 Satz 1 Nr. 3 BGB. Ob eine Kündigung möglich ist, prüft die App nicht – lassen Sie sich dazu bitte beraten.</p>
          )}
          <div className="row-actions">
            <button className="secondary" onClick={() => start("erinnerung")}>Zahlungserinnerung</button>
            <button className="primary" onClick={() => start("mahnung")}>Mahnung</button>
          </div>
        </>
      )}

      {d.letters.length > 0 && (
        <>
          <h3>Versandte Schreiben</h3>
          <ul className="plain-list letter-list">
            {d.letters.map((x: any) => (
              <li key={x.id} className="swipe-li">
                <SwipeRow className="letter-item" asking={doomed?.id === x.id} onDelete={() => setDoomed(x)}>
                  <span><strong>{x.label}</strong> vom {date(x.letter_date)}
                    <span className="muted sub-line">{x.months.map((m: any) => monthLabel(m.month)).join(", ")} · Frist {date(x.due_date)}</span></span>
                  <span className="num">{money(x.total_cents)}</span>
                  <a className="secondary small btn-link" href={`/api/dunning/${x.id}/pdf`} target="_blank" rel="noopener">PDF</a>
                  <button className="icon-btn" aria-label="Eintrag löschen" onClick={() => setDoomed(x)}><Icon name="trash" size={16} /></button>
                </SwipeRow>
              </li>
            ))}
          </ul>
        </>
      )}

      <Sheet open={!!form} onClose={() => setForm(null)} title={form ? `${KIND[form.kind]} erstellen` : ""} wide>
        {form && (
          <form className="form" onSubmit={create} noValidate>
            {d.landlord_missing.length > 0 && (
              <p className="notice warn block">Für den Briefkopf fehlen: {d.landlord_missing.join(", ")}. Eintragen unter
                {" "}<a href="#/betriebskosten">Betriebskosten › Vermieterangaben</a>.</p>
            )}
            <fieldset className="segmented" aria-label="Art des Schreibens">
              {Object.entries(KIND).map(([k, label]) => (
                <label key={k} className={form.kind === k ? "on" : ""}>
                  <input type="radio" name="dunning-kind" checked={form.kind === k} onChange={() => setForm({ ...form, kind: k })} />{label}</label>
              ))}
            </fieldset>
            <div className="form grid">
              <label>Briefdatum<input type="date" value={form.letter_date} onChange={(e) => setForm({ ...form, letter_date: e.target.value })} required /></label>
              <label>Zahlungsfrist bis<input type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} required /></label>
            </div>
            <fieldset className="month-pick">
              <legend>Offene Monate</legend>
              {d.open.map((i: any) => (
                <label key={i.month} className="check"><input type="checkbox" checked={form.months.includes(i.month)} onChange={() => toggle(i.month)} />
                  <span>{monthLabel(i.month)}</span><span className="num">{money(i.open)}</span></label>
              ))}
              <p className="month-pick-total"><span>Gefordert</span><strong>{money(chosenTotal)}</strong></p>
            </fieldset>
            <label>Anrede<input value={form.salutation} onChange={(e) => setForm({ ...form, salutation: e.target.value })} /></label>
            <label>Zusätzlicher Text (optional)<textarea rows={3} value={form.extra_text} onChange={(e) => setForm({ ...form, extra_text: e.target.value })}
              placeholder="z. B. Hinweis auf eine vereinbarte Ratenzahlung" /></label>
            <p className="hint">Die Beträge stammen aus den Einnahmen und lassen sich hier nicht ändern. Das Schreiben wird als PDF beim Mieter
              abgelegt; versenden Sie es selbst (Brief oder E-Mail). Verzugszinsen und Mahngebühren werden nicht berechnet.</p>
            <div className="row-actions">
              <button type="button" className="secondary" onClick={preview} disabled={!form.months.length}>Vorschau</button>
              <button className="primary" disabled={busy || !form.months.length}>Erstellen und ablegen</button>
            </div>
          </form>
        )}
      </Sheet>
      <ConfirmDelete open={!!doomed} title="Eintrag löschen?" busy={busy} onClose={() => setDoomed(null)} onConfirm={remove} confirmLabel="Eintrag löschen"
        text={doomed && <>
          <p><strong>{doomed.label}</strong> vom {date(doomed.letter_date)} über {money(doomed.total_cents)} aus der Liste entfernen?</p>
          <p className="muted">Nur für versehentlich erstellte Schreiben. Das abgelegte PDF bleibt im Archiv beim Mieter; löschen Sie es dort, falls es nie versandt wurde.</p></>} />
    </section>
  );
}
