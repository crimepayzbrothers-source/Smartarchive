// Mietkaution je Mietverhältnis: Art, Höhe (2 oder 3 Kaltmieten oder eigener Betrag), Raten, Einzahlungen, Urkunde, Rückgabe.
import { useState, type FormEvent } from "react";
import { centsToInput, date, del, get, money, parseMoney, post, put } from "../api";
import { useLoad, useToast } from "../lib";
import { ErrorBox, Icon, Sheet } from "../components/ui";
import { ConfirmDelete } from "../components/SwipeRow";

const PILL: Record<string, string> = { offen: "ungeklaert", teilweise: "entwurf", vollstaendig: "bestaetigt", liegt_vor: "bestaetigt",
  zurueck: "", abgerechnet: "", keine: "", fehlt: "entwurf" };
const DOC_LABEL: Record<string, string> = { sparbuch: "Verpfändungserklärung erhalten am", buergschaft: "Bürgschaftsurkunde erhalten am",
  versicherung: "Urkunde/Police erhalten am" };
const PROVIDER_LABEL: Record<string, string> = { sparbuch: "Bank des Sparbuchs", buergschaft: "Bürgende Bank", versicherung: "Versicherer" };

export function DepositPill({ d }: { d: any }) {
  if (!d || ["keine", "vollstaendig", "liegt_vor", "zurueck", "abgerechnet"].includes(d.status)) return null;
  return <span className={`status-pill ${d.overdue ? "ungeklaert" : "entwurf"}`}>Kaution {d.status === "fehlt" ? "nicht erfasst" : d.status_label}</span>;
}

type Form = Record<string, any>;

export function DepositPanel({ tenancyId }: { tenancyId: number }) {
  const toast = useToast();
  const { data: d, error, reload } = useLoad(() => get(`/api/tenancies/${tenancyId}/deposit`), [tenancyId]);
  const [form, setForm] = useState<Form | null>(null);
  const [pay, setPay] = useState({ paid_on: new Date().toISOString().slice(0, 10), amount: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [doomed, setDoomed] = useState<any>(null);
  if (error) return <ErrorBox text={error} />;
  if (!d) return null;

  const edit = () => setForm({
    kind: d.status === "fehlt" ? "bar" : d.kind,
    mode: d.months === 2 ? "2" : d.months === 3 || d.status === "fehlt" ? "3" : "eigen",
    amount: centsToInput(d.amount_cents || null), installments: !!d.installments, account: d.account || "", provider: d.provider || "",
    reference: d.reference || "", document_received_on: d.document_received_on || "", has_return: !!d.returned_on,
    returned_on: d.returned_on || "", returned_amount: centsToInput(d.returned_cents ?? null), retained: centsToInput(d.retained_cents ?? null),
    note: d.note || "",
  });
  const set = (p: any) => setForm({ ...form, ...p });

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const months = form.mode === "eigen" ? null : Number(form.mode);
    const body: any = { kind: form.kind, months, amount_cents: months ? 0 : parseMoney(form.amount) || 0, installments: form.installments,
      account: form.account, provider: form.provider, reference: form.reference, document_received_on: form.document_received_on || null,
      returned_on: form.has_return ? form.returned_on || null : null,
      returned_cents: form.has_return ? parseMoney(form.returned_amount) ?? 0 : null,
      retained_cents: form.has_return ? parseMoney(form.retained) ?? 0 : null, note: form.note };
    if (form.has_return && !form.returned_on) return toast({ kind: "error", text: "Bitte das Datum der Rückgabe angeben." });
    setBusy(true);
    try {
      await put(`/api/tenancies/${tenancyId}/deposit`, body);
      toast({ kind: "ok", text: "Kaution gespeichert." });
      setForm(null); reload();
    } catch (err: any) { toast({ kind: "error", text: err.message }); }
    finally { setBusy(false); }
  };
  const addPayment = async (e: FormEvent) => {
    e.preventDefault();
    const cents = parseMoney(pay.amount);
    if (!cents || cents <= 0) return toast({ kind: "error", text: "Bitte einen Betrag angeben." });
    setBusy(true);
    try {
      await post(`/api/tenancies/${tenancyId}/deposit/payments`, { paid_on: pay.paid_on, amount_cents: cents, note: pay.note });
      toast({ kind: "ok", text: "Einzahlung erfasst." });
      setPay({ ...pay, amount: "", note: "" }); reload();
    } catch (err: any) { toast({ kind: "error", text: err.message }); }
    finally { setBusy(false); }
  };
  const removePayment = async () => {
    setBusy(true);
    try { await del(`/api/deposit-payments/${doomed.id}`); setDoomed(null); reload(); }
    catch (err: any) { toast({ kind: "error", text: err.message }); }
    finally { setBusy(false); }
  };

  const cash = d.kind === "bar";
  const doc = ["sparbuch", "buergschaft", "versicherung"].includes(d.kind);
  const pct = d.amount_cents ? Math.min(100, Math.round((d.paid / d.amount_cents) * 100)) : 0;
  const preview = (m: number) => money(Math.round(d.cold_rent * m));

  return (
    <section className="panel deposit">
      <header className="panel-head">
        <h2>Kaution {d.status !== "fehlt" && <span className={`status-pill ${PILL[d.status]}`}>{d.status_label}</span>}</h2>
        <button className="secondary" onClick={edit}>{d.status === "fehlt" ? "Erfassen" : "Bearbeiten"}</button>
      </header>
      {d.status === "fehlt" ? <p className="muted">Noch nicht erfasst. Höchstens drei Nettokaltmieten ({money(3 * d.cold_rent)}), § 551 BGB.</p>
        : d.status === "keine" ? <p className="muted">Keine Kaution vereinbart.</p> : (
          <>
            <dl className="facts">
              <div><dt>Art</dt><dd>{d.kind_label}</dd></div>
              <div><dt>Höhe</dt><dd>{money(d.amount_cents)}{d.months ? <span className="muted"> · {String(d.months).replace(".", ",")} Kaltmieten</span> : null}</dd></div>
              {cash && d.account && <div><dt>Kautionskonto</dt><dd>{d.account}</dd></div>}
              {doc && d.provider && <div><dt>{PROVIDER_LABEL[d.kind]}</dt><dd>{d.provider}{d.reference ? ` · Nr. ${d.reference}` : ""}</dd></div>}
              {doc && <div><dt>Urkunde</dt><dd>{d.document_received_on ? `liegt vor seit ${date(d.document_received_on)}` : <span className="neg">noch nicht erhalten</span>}</dd></div>}
            </dl>
            {d.over_cap && <p className="notice warn block">Der Betrag übersteigt drei Nettokaltmieten ({money(d.cap)}, § 551 Abs. 1 BGB). Bitte die Vereinbarung prüfen.</p>}
            {cash && (
              <>
                <div className="deposit-progress" aria-label={`${pct} % eingezahlt`}><span style={{ width: `${pct}%` }} /></div>
                <p className="small-num">{money(d.paid)} von {money(d.amount_cents)} eingezahlt{d.amount_cents > d.paid ? ` · offen ${money(d.amount_cents - d.paid)}` : ""}</p>
                {d.schedule.length > 1 && (
                  <ul className="plain-list">
                    {d.schedule.map((r: any) => (
                      <li key={r.no}><span>{r.no}. Rate<span className="muted sub-line">fällig {date(r.due)}</span></span>
                        <span className="num">{money(r.amount)}
                          <span className={`sub-line ${r.paid >= r.amount ? "pos" : r.overdue ? "neg" : "muted"}`}>
                            {r.paid >= r.amount ? "bezahlt" : r.overdue ? "überfällig" : r.paid ? `${money(r.paid)} bezahlt` : "offen"}</span></span></li>
                    ))}
                  </ul>
                )}
                <h3>Einzahlungen</h3>
                {d.payments.length === 0 ? <p className="muted">Noch keine Einzahlung erfasst.</p> : (
                  <ul className="plain-list">
                    {d.payments.map((p: any) => (
                      <li key={p.id}><span>{date(p.paid_on)}{p.note && <span className="muted sub-line">{p.note}</span>}</span>
                        <span className="num">{money(p.amount_cents)}</span>
                        <button className="icon-btn" aria-label="Einzahlung löschen" onClick={() => setDoomed(p)}><Icon name="trash" size={16} /></button></li>
                    ))}
                  </ul>
                )}
                {!d.returned_on && (
                  <form className="inline-add deposit-pay" onSubmit={addPayment}>
                    <label>Datum<input type="date" value={pay.paid_on} onChange={(e) => setPay({ ...pay, paid_on: e.target.value })} required /></label>
                    <label>Betrag (€)<input inputMode="decimal" value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} placeholder="0,00" /></label>
                    <label>Notiz<input value={pay.note} onChange={(e) => setPay({ ...pay, note: e.target.value })} placeholder="z. B. 1. Rate" /></label>
                    <button className="primary small" disabled={busy}>Einzahlung erfassen</button>
                  </form>
                )}
                <p className="hint">Kaution getrennt vom eigenen Vermögen anlegen (§ 551 Abs. 3 BGB). Einzahlungen hier erfassen – im Mietkonto
                  zählt eine Kautionszahlung sonst als Miete.</p>
              </>
            )}
            {d.returned_on && (
              <p className="notice block">Zurückgegeben am {date(d.returned_on)}: ausgezahlt {money(d.returned_cents)}
                {d.retained_cents ? `, einbehalten ${money(d.retained_cents)}` : ""}{d.note ? ` – ${d.note}` : ""}.</p>
            )}
            {!d.returned_on && d.note && <p className="notes">{d.note}</p>}
          </>
        )}

      <Sheet open={!!form} onClose={() => setForm(null)} title="Kaution" wide>
        {form && (
          <form className="form" onSubmit={save} noValidate>
            <label>Art der Kaution<select value={form.kind} onChange={(e) => set({ kind: e.target.value })}>
              {d.kinds.map((k: any) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select></label>
            {form.kind !== "keine" && (
              <>
                <fieldset className="segmented" aria-label="Höhe">
                  {[["2", `2 Kaltmieten`], ["3", `3 Kaltmieten`], ["eigen", "Eigener Betrag"]].map(([k, l]) => (
                    <label key={k} className={form.mode === k ? "on" : ""}>
                      <input type="radio" name="dep-mode" checked={form.mode === k} onChange={() => set({ mode: k })} />{l}</label>
                  ))}
                </fieldset>
                {form.mode === "eigen" ? (
                  <label>Betrag (€)<input inputMode="decimal" value={form.amount} onChange={(e) => set({ amount: e.target.value })} placeholder="0,00" /></label>
                ) : (
                  <p className="muted">{form.mode} × Kaltmiete zu Mietbeginn ({money(d.cold_rent)}) = <strong>{preview(Number(form.mode))}</strong></p>
                )}
                <p className="hint">Höchstens drei Nettokaltmieten ({money(3 * d.cold_rent)}) – ohne Betriebs- und Heizkostenvorauszahlungen (§ 551 Abs. 1 BGB).</p>
                {form.kind === "bar" && (
                  <>
                    <label className="check"><input type="checkbox" checked={form.installments} onChange={(e) => set({ installments: e.target.checked })} />
                      <span>In drei Monatsraten<span className="muted sub-line">1. Rate zu Mietbeginn, 2. und 3. mit den folgenden Mieten (§ 551 Abs. 2 BGB)</span></span></label>
                    <label>Kautionskonto (Bank, IBAN)<input value={form.account} onChange={(e) => set({ account: e.target.value })} /></label>
                  </>
                )}
                {form.kind !== "bar" && (
                  <div className="form grid">
                    <label>{PROVIDER_LABEL[form.kind]}<input value={form.provider} onChange={(e) => set({ provider: e.target.value })} /></label>
                    <label>{form.kind === "sparbuch" ? "Sparbuch-/Konto-Nr." : "Urkunden-/Policen-Nr."}<input value={form.reference} onChange={(e) => set({ reference: e.target.value })} /></label>
                    <label className="span2">{DOC_LABEL[form.kind]}<input type="date" value={form.document_received_on} onChange={(e) => set({ document_received_on: e.target.value })} />
                      <span className="hint">leer = liegt noch nicht vor</span></label>
                  </div>
                )}
                <label className="check"><input type="checkbox" checked={form.has_return} onChange={(e) => set({ has_return: e.target.checked })} />
                  <span>Kaution zurückgegeben bzw. abgerechnet</span></label>
                {form.has_return && (
                  <div className="form grid">
                    <label>Am<input type="date" value={form.returned_on} onChange={(e) => set({ returned_on: e.target.value })} /></label>
                    <label>{form.kind === "bar" ? "Ausgezahlt (€)" : "Freigegeben (€)"}<input inputMode="decimal" value={form.returned_amount} onChange={(e) => set({ returned_amount: e.target.value })} /></label>
                    <label>Einbehalten (€)<input inputMode="decimal" value={form.retained} onChange={(e) => set({ retained: e.target.value })} placeholder="0,00" /></label>
                  </div>
                )}
              </>
            )}
            <label>Notiz<textarea rows={2} value={form.note} onChange={(e) => set({ note: e.target.value })} /></label>
            <div className="row-actions">
              <button type="button" className="secondary" onClick={() => setForm(null)}>Abbrechen</button>
              <button className="primary" disabled={busy}>Speichern</button>
            </div>
          </form>
        )}
      </Sheet>
      <ConfirmDelete open={!!doomed} title="Einzahlung löschen?" busy={busy} onClose={() => setDoomed(null)} onConfirm={removePayment}
        confirmLabel="Einzahlung löschen" text={doomed && <p>Einzahlung vom {date(doomed.paid_on)} über {money(doomed.amount_cents)} löschen?</p>} />
    </section>
  );
}
