import { useEffect, useMemo, useState } from "react";
import { centsToInput, date, del, get, money, parseMoney, patch, post, relative } from "../api";
import { go, useLoad, useMeta, usePoll, useToast } from "../lib";
import { SuggestionBox } from "../components/Suggestion";
import { InvoiceCard, needsInvoiceCard } from "../components/InvoiceCard";
import { DateQuestion } from "../components/DateQuestion";
import { applyExtract, extractHint, fetchExtract, type Extracted } from "../extract";
import { AmountPicker } from "../components/AmountPicker";
import { AreaBadge, BackButton, CategorySelect, ErrorBox, Icon, Loading, sourceLabel } from "../components/ui";

const ACTION_LABEL: Record<string, string> = {
  uploaded: "Hochgeladen", auto_filed: "Automatisch abgelegt", suggested: "Vorschlag erstellt", filed: "Abgelegt",
  updated: "Geändert", area_changed: "Bereich gewechselt", auto_filing_undone: "Automatische Ablage zurückgenommen",
  deleted: "In den Papierkorb gelegt", restored: "Wiederhergestellt", expense_added: "Ausgabe erfasst",
  file_viewed: "Angesehen", file_downloaded: "Heruntergeladen", imported: "Importiert", import_filed: "Beim Import abgelegt", reprocess_requested: "Neu verarbeitet", processing_failed: "Verarbeitung fehlgeschlagen",
};

export default function DocumentPage({ id }: { id: number }) {
  const { meta } = useMeta();
  const toast = useToast();
  const { data: doc, error, loading, reload } = useLoad(() => get(`/api/documents/${id}`), [id]);
  usePoll(doc?.status === "processing", reload);
  const [page, setPage] = useState(1);
  const [showText, setShowText] = useState(false);
  const [text, setText] = useState<string | null>(null);

  useEffect(() => { setPage(1); setText(null); setShowText(false); }, [id]);
  useEffect(() => {
    if (showText && text === null) get(`/api/documents/${id}/text`).then((t) => setText(t.content || "")).catch(() => setText(""));
  }, [showText, text, id]);

  if (loading && !doc) return <Loading />;
  if (error) return <div className="page"><BackButton /><ErrorBox text={error} /></div>;
  if (!doc || !meta) return <Loading />;

  const pages = doc.page_count || 1;
  const invoiceCard = needsInvoiceCard(doc);
  const toggleFav = async () => { await patch(`/api/documents/${id}`, { favorite: !doc.favorite }); reload(); };
  const remove = async () => {
    if (!confirm("Dokument in den Papierkorb legen? Es bleibt wiederherstellbar.")) return;
    await del(`/api/documents/${id}`);
    toast({ kind: "ok", text: "Im Papierkorb.", action: { label: "Wiederherstellen", run: () => post(`/api/documents/${id}/restore`).then(() => go(`/dokument/${id}`)) } });
    go("/archiv");
  };
  const rerun = async (kind: string) => {
    await post(`/api/documents/${id}/run/${kind}`);
    toast({ kind: "ok", text: kind === "process" ? "Texterkennung läuft erneut." : "Wird neu eingeordnet." });
    window.setTimeout(reload, 1500);
  };

  return (
    <div className={`page document ${doc.area_key || ""}`}>
      <header className="doc-head">
        <BackButton />
        <div className="doc-head-main">
          <h1>{doc.title || "Ohne Titel"}</h1>
          <p className="muted">
            <AreaBadge area={doc.area_key} /> {doc.category_path ? doc.category_path.split(" › ").slice(1).join(" › ") : "noch nicht abgelegt"}
          </p>
        </div>
        <button className={`icon-btn ${doc.favorite ? "fav-on" : ""}`} onClick={toggleFav} aria-label="Favorit"><Icon name="star" filled={!!doc.favorite} /></button>
      </header>

      {doc.deleted_at && (
        <div className="notice warn">Liegt im Papierkorb. <button className="link" onClick={() => post(`/api/documents/${id}/restore`).then(reload)}>Wiederherstellen</button></div>
      )}

      <div className="doc-layout">
        <section className="preview">
          {doc.status === "processing" ? (
            <div className="preview-placeholder"><Loading /><p>Texterkennung läuft …</p></div>
          ) : doc.has_pdf ? (
            <>
              <a href={`/api/documents/${id}/file`} target="_blank" rel="noopener" className="page-img">
                <img src={`/api/documents/${id}/pages/${page}?dpi=110`} alt={`Seite ${page} von ${pages}`} />
              </a>
              {pages > 1 && (
                <div className="pager">
                  <button className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Zurück</button>
                  <span>Seite {page} von {pages}</span>
                  <button className="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Weiter</button>
                </div>
              )}
            </>
          ) : <div className="preview-placeholder"><Icon name="file" size={32} /><p>Keine Vorschau</p></div>}
          {doc.has_pdf && doc.status !== "processing" && <FileActions doc={doc} />}
          <div className="file-actions">
            {doc.files.filter((f: any) => f.role === "capture").map((f: any) => (
              <a key={f.id} className="ghost button small" href={`/api/documents/${id}/file?variant=capture&position=${f.position}&download=1`}>
                Unbearbeitetes Foto{doc.files.filter((x: any) => x.role === "capture").length > 1 ? ` ${f.position + 1}` : ""} ({Math.round(f.size / 1024)} KB)
              </a>
            ))}
            {doc.files.filter((f: any) => f.role === "original").map((f: any) => (
              <a key={f.id} className="ghost button small" href={`/api/documents/${id}/file?variant=original&position=${f.position}&download=1`}>
                Original {f.original_name ? `„${f.original_name}“` : ""} ({Math.round(f.size / 1024)} KB)
              </a>
            ))}
          </div>
        </section>

        <div className="details">
          {doc.status === "error" && (
            <div className="notice error">
              Verarbeitung fehlgeschlagen: {doc.error}
              <button className="secondary" onClick={() => rerun("process")}>Erneut versuchen</button>
            </div>
          )}
          {doc.status === "inbox" && !invoiceCard && !doc.suggestion?.labels?.category && <div className="notice attention">Im Eingang – bitte unten eine Ablage wählen.</div>}
          {!doc.doc_date && !invoiceCard && doc.expenses.some((e: any) => !e.invoice_date && !e.paid_on) && (
            <DateQuestion docId={id} amount={doc.expenses[0]?.amount_cents} onChanged={reload} />
          )}
          {invoiceCard && <InvoiceCard doc={doc} onChanged={reload} />}
          {!invoiceCard && doc.suggestion && doc.status !== "processing" && (doc.suggestion.status === "open" || doc.suggestion.status === "applied" || doc.suggestion.status === "info") && !(doc.confirmed && doc.status === "filed") && (
            <SuggestionBox docId={id} status={doc.status} suggestion={doc.suggestion} onChanged={reload} />
          )}
          {doc.source_path && <p className="hint">Aus Dropbox übernommen: /{doc.source_path}</p>}
          {doc.status === "filed" && doc.confirmed === 1 && doc.filed_by && doc.filed_by !== "user" && doc.filed_by !== "import" && (
            <p className="hint">Abgelegt durch {sourceLabel(doc.filed_by)}, von Ihnen bestätigt.</p>
          )}

          {invoiceCard ? (
            <details className="panel collapsible">
              <summary>Weitere Angaben (Titel, Absender, Tags)</summary>
              <FilingForm key={doc.updated_at} doc={doc} onSaved={reload} />
            </details>
          ) : <FilingForm key={doc.updated_at} doc={doc} onSaved={reload} />}

          {doc.area_key === "vermietung" && !invoiceCard && <Expenses doc={doc} onChanged={reload} />}

          <details className="panel collapsible" open={showText} onToggle={(e) => setShowText((e.target as HTMLDetailsElement).open)}>
            <summary>Erkannter Text {doc.text_source === "ocr" ? "(Texterkennung)" : doc.text_source === "digital" ? "(digital)" : ""}</summary>
            {text === null ? <Loading /> : text ? <pre className="ocr-text">{text}</pre> : <p className="muted">Kein Text erkannt.</p>}
            <div className="row-actions">
              <button className="ghost small" onClick={() => rerun("process")}>Texterkennung wiederholen</button>
              <button className="ghost small" onClick={() => rerun("reclassify")}>Neu einordnen</button>
            </div>
          </details>

          <details className="panel collapsible">
            <summary>Verlauf</summary>
            <ol className="history">
              {doc.history.map((h: any, i: number) => (
                <li key={i}>
                  <span className="when">{relative(h.ts)}</span>
                  <span>{ACTION_LABEL[h.action] || h.action}{h.actor === "system" ? " (automatisch)" : ""}</span>
                  {h.details?.source && <span className="muted"> – {sourceLabel(h.details.source)}, {Math.round((h.details.confidence || 0) * 100)} %</span>}
                  {h.details?.blockers?.length > 0 && <span className="muted"> – {h.details.blockers.join("; ")}</span>}
                </li>
              ))}
            </ol>
          </details>

          <button className="danger-link" onClick={remove}><Icon name="trash" size={18} /> In den Papierkorb</button>
        </div>
      </div>
    </div>
  );
}

// Öffnen in der PDF-App des Geräts und Teilen über das System-Menü (Web Share API).
function FileActions({ doc }: { doc: any }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState<File | null>(null);
  const url = `/api/documents/${doc.id}/file`;
  const shareSupported = typeof navigator !== "undefined" && typeof navigator.canShare === "function";

  const loadFile = async () => {
    const res = await fetch(url, { credentials: "same-origin" });
    if (!res.ok) throw new Error(res.status === 401 ? "Bitte neu anmelden." : `Datei nicht abrufbar (Fehler ${res.status}).`);
    return new File([await res.blob()], doc.display_filename, { type: "application/pdf" });
  };

  const share = async () => {
    setBusy(true);
    try {
      const file = ready || await loadFile();
      if (!navigator.canShare({ files: [file] })) {
        toast({ kind: "error", text: "Dieses Gerät kann die Datei nicht teilen. Bitte „Öffnen“ nutzen." });
        return;
      }
      try {
        await navigator.share({ files: [file], title: doc.title || doc.display_filename });
        setReady(null);
      } catch (e: any) {
        if (e?.name === "AbortError") return;          // Teilen abgebrochen
        if (e?.name === "NotAllowedError") {           // Laden hat zu lange gedauert
          setReady(file);
          toast({ kind: "ok", text: "Datei ist bereit – bitte noch einmal auf „Teilen“ tippen." });
          return;
        }
        throw e;
      }
    } catch (e: any) {
      toast({ kind: "error", text: e?.message || "Teilen fehlgeschlagen." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="open-share">
      <a className="primary button" href={`${url}?download=1`} download={doc.display_filename}>
        <Icon name="open" size={18} /> Öffnen
      </a>
      {shareSupported && (
        <button className={ready ? "primary" : "secondary"} disabled={busy} onClick={share}>
          <Icon name="share" size={18} /> {busy ? "Wird geladen …" : ready ? "Jetzt teilen" : "Teilen"}
        </button>
      )}
      <p className="hint">„Öffnen“ lädt die PDF herunter; Android öffnet sie dann mit Ihrer PDF-App.</p>
    </div>
  );
}

function FilingForm({ doc, onSaved }: { doc: any; onSaved: () => void }) {
  const { meta, reload: reloadMeta } = useMeta();
  const toast = useToast();
  const sug = doc.suggestion?.payload || {};
  const initialArea = doc.area_key || (doc.upload_area !== "auto" ? doc.upload_area : null) ||
    meta!.categories.find((c) => c.id === sug.category_id)?.area_key || "privat";
  const [area, setArea] = useState<string>(initialArea);
  const [categoryId, setCategoryId] = useState<number | null>(doc.category_id ?? (doc.status !== "filed" ? sug.category_id ?? null : null));
  const [propertyId, setPropertyId] = useState<number | null>(doc.property_id ?? sug.property_id ?? null);
  const [unitId, setUnitId] = useState<number | null>(doc.unit_id ?? null);
  const [title, setTitle] = useState(doc.title || "");
  const [docDate, setDocDate] = useState(doc.doc_date || "");
  const [docType, setDocType] = useState(doc.doc_type || "");
  const [correspondent, setCorrespondent] = useState(doc.correspondent_name || "");
  const [tags, setTags] = useState((doc.tags || []).join(", "));
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (categoryId && meta!.categories.find((c) => c.id === categoryId)?.area_key !== area) setCategoryId(null);
  }, [area]); // eslint-disable-line react-hooks/exhaustive-deps

  const prop = meta!.properties.find((p) => p.id === propertyId);
  const isInbox = doc.status !== "filed";

  const save = async () => {
    if (isInbox && !categoryId) return toast({ kind: "error", text: "Bitte eine Kategorie wählen." });
    if (doc.status === "filed" && doc.area_key && doc.area_key !== area &&
      !confirm(`Dokument wirklich von „${doc.area_key === "privat" ? "Privat" : "Vermietung"}“ nach „${area === "privat" ? "Privat" : "Vermietung"}“ verschieben?`)) return;
    setBusy(true);
    try {
      const known = meta!.correspondents.find((c) => c.name.toLowerCase() === correspondent.trim().toLowerCase());
      await patch(`/api/documents/${doc.id}`, {
        title, doc_date: docDate || null, doc_type: docType, category_id: categoryId,
        property_id: area === "vermietung" ? propertyId : null, unit_id: area === "vermietung" ? unitId : null,
        ...(correspondent.trim() ? (known ? { correspondent_id: known.id } : { correspondent_name: correspondent.trim() }) : { correspondent_id: null }),
        tags: tags.split(",").map((t: string) => t.trim()).filter(Boolean),
        file: isInbox,
      });
      toast({ kind: "ok", text: isInbox ? "Abgelegt." : "Gespeichert." });
      reloadMeta();
      onSaved();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel filing">
      <h2>{isInbox ? "Ablage" : "Angaben"}</h2>
      <fieldset className="segmented" aria-label="Bereich">
        {[["privat", "Privat"], ["vermietung", "Vermietung"]].map(([key, label]) => (
          <label key={key} className={`${key} ${area === key ? "on" : ""}`}>
            <input type="radio" name={`area-${doc.id}`} checked={area === key} onChange={() => setArea(key)} /> {label}
          </label>
        ))}
      </fieldset>
      <div className="form grid">
        <label className="span2">Kategorie<CategorySelect meta={meta!} area={area} value={categoryId} onChange={setCategoryId} /></label>
        {area === "vermietung" && (
          <>
            <label>Mietobjekt
              <select value={propertyId ?? ""} onChange={(e) => { setPropertyId(e.target.value ? Number(e.target.value) : null); setUnitId(null); }}>
                <option value="">– kein Objekt –</option>
                {meta!.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              {meta!.properties.length === 0 && <a className="hint" href="#/objekte">Erst ein Objekt anlegen</a>}
            </label>
            <label>Einheit
              <select value={unitId ?? ""} onChange={(e) => setUnitId(e.target.value ? Number(e.target.value) : null)} disabled={!prop?.units.length}>
                <option value="">{prop?.units.length ? "– ganzes Objekt –" : "keine Einheiten"}</option>
                {prop?.units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </label>
          </>
        )}
        <label className="span2">Titel<input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} /></label>
        <label>Absender
          <input value={correspondent} onChange={(e) => setCorrespondent(e.target.value)} list="correspondents" maxLength={80} />
          <datalist id="correspondents">{meta!.correspondents.map((c) => <option key={c.id} value={c.name} />)}</datalist>
        </label>
        <label>Dokumentdatum<input type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></label>
        <label>Art<input value={docType} onChange={(e) => setDocType(e.target.value)} placeholder="z. B. Rechnung" list="doctypes" maxLength={60} />
          <datalist id="doctypes">{["Rechnung", "Kassenbon", "Bescheid", "Vertrag", "Jahresabrechnung", "Nebenkostenabrechnung", "Kontoauszug", "Versicherungsschein", "Mahnung", "Angebot"].map((t) => <option key={t} value={t} />)}</datalist>
        </label>
        <label>Tags<input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="kommagetrennt" /></label>
      </div>
      <button className="primary block" disabled={busy} onClick={save}>{isInbox ? "Ablegen" : "Änderungen speichern"}</button>
    </section>
  );
}

function Expenses({ doc, onChanged }: { doc: any; onChanged: () => void }) {
  const { meta } = useMeta();
  const toast = useToast();
  const [editing, setEditing] = useState<any | null>(null);
  const sug = doc.suggestion?.payload || {};
  const labels = useMemo(() => Object.fromEntries(meta!.expense_categories.map((c) => [c.key, c.label])), [meta]);
  const sum = doc.expenses.reduce((s: number, e: any) => s + e.amount_cents, 0);

  const [extracted, setExtracted] = useState<Extracted | null>(null);
  const [reading, setReading] = useState(false);
  const readFromDoc = async (form: any, overwrite = false) => {
    setReading(true);
    try {
      const x = await fetchExtract(doc.id);
      setExtracted(x);
      const { form: next, changed } = applyExtract(form, x, overwrite);
      setEditing(next);
      if (!x.text_available) return null;
      if (changed.length) toast({ kind: "ok", text: `Ausgefüllt: ${changed.join(", ")}.` });
      return changed;
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
      return null;
    } finally {
      setReading(false);
    }
  };
  const readButton = async () => {
    const changed = await readFromDoc(editing);
    if (changed && changed.length === 0) {
      if (confirm("Alle Felder sind schon ausgefüllt. Mit den Werten aus dem Beleg überschreiben?")) await readFromDoc(editing, true);
    }
  };
  const startNew = () => {
    const form = {
      amount: doc.expenses.length ? "" : centsToInput(sug.amount_cents), category_key: "", invoice_date: doc.doc_date || sug.doc_date || "",
      paid_on: "", payee: doc.correspondent_name || "", note: "", status: "bestaetigt", property_id: doc.property_id,
    };
    setEditing(form);
    setExtracted(null);
    if (!doc.expenses.length) readFromDoc(form);  // neue Position: gleich aus dem Beleg vorbelegen
  };
  const startEdit = (e: any) => setEditing({ ...e, amount: centsToInput(e.amount_cents) });

  const save = async () => {
    const cents = parseMoney(editing.amount);
    if (cents === null) return toast({ kind: "error", text: "Betrag fehlt oder ist ungültig (z. B. 245,00)." });
    if (!editing.category_key) return toast({ kind: "error", text: "Bitte eine Ausgabenkategorie wählen." });
    const body = {
      document_id: doc.id, property_id: editing.property_id || doc.property_id || null, amount_cents: cents,
      category_key: editing.category_key, invoice_date: editing.invoice_date || null, paid_on: editing.paid_on || null,
      payee: editing.payee, note: editing.note, status: editing.status,
    };
    try {
      if (editing.id) await patch(`/api/expenses/${editing.id}`, body);
      else await post("/api/expenses", body);
      setEditing(null);
      onChanged();
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    }
  };
  const check = async (eid: number) => {
    await patch(`/api/expenses/${eid}`, {});
    toast({ kind: "ok", text: "Als geprüft markiert." });
    onChanged();
  };
  const remove = async (eid: number) => {
    if (!confirm("Position löschen?")) return;
    await del(`/api/expenses/${eid}`);
    onChanged();
  };

  return (
    <section className="panel expenses">
      <header className="panel-head">
        <h2>Ausgaben</h2>
        {doc.expenses.length > 0 && <strong className="sum">{money(sum)}</strong>}
      </header>
      {!doc.property_id && <p className="notice warn small">Kein Mietobjekt gewählt – bitte oben zuordnen.</p>}
      {doc.expenses.some((e: any) => e.origin === "auto") && (
        <p className="hint">Betrag automatisch aus dem Beleg ausgelesen – bitte kurz vergleichen und „Stimmt“ tippen oder korrigieren.</p>
      )}
      {doc.expenses.some((e: any) => e.paid_assumed) && (
        <p className="hint">Zahlungsdatum angenommen (= Rechnungsdatum). Weicht es ab, Position antippen und „Zahlungsdatum“ ändern.</p>
      )}
      {doc.expenses.length === 0 && !editing && (
        <p className="muted">Noch keine Position. {sug.amount_cents ? `Erkannter Betrag: ${money(sug.amount_cents)}.` : ""}</p>
      )}
      <ul className="expense-list">
        {doc.expenses.map((e: any) => (
          <li key={e.id} className={`expense ${e.status}`}>
            <button className="expense-main" onClick={() => startEdit(e)}>
              <span className="expense-cat">{labels[e.category_key] || e.category_key}</span>
              <span className="expense-meta">{e.paid_on ? `bezahlt ${date(e.paid_on)}${e.paid_assumed ? " (angenommen)" : ""}` : "Zahlungsdatum fehlt"}{e.note ? `, ${e.note}` : ""}</span>
            </button>
            <span className="expense-amount">{money(e.amount_cents)}</span>
            <span className={`status-pill ${e.origin === "auto" && e.status === "bestaetigt" ? "auto" : e.status}`}>
              {e.status !== "bestaetigt" ? (e.status === "ungeklaert" ? "Ungeklärt" : "Entwurf") : e.origin === "auto" ? "Automatisch" : "Geprüft"}</span>
            {(e.origin === "auto" || e.paid_assumed) && <button className="secondary small" onClick={() => check(e.id)}>Stimmt</button>}
            <button className="icon-btn" aria-label="Löschen" onClick={() => remove(e.id)}><Icon name="trash" size={18} /></button>
          </li>
        ))}
      </ul>
      {editing ? (
        <div className="form grid expense-form">
          <div className="span2 read-row">
            <button type="button" className="secondary small" disabled={reading} onClick={readButton}>
              <Icon name="spark" size={16} /> {reading ? "Wird ausgelesen …" : "Aus Beleg auslesen"}
            </button>
            {extractHint(extracted) && <span className="hint">{extractHint(extracted)}</span>}
          </div>
          <label>Betrag (€)<input inputMode="decimal" value={editing.amount} onChange={(e) => setEditing({ ...editing, amount: e.target.value })} placeholder="0,00" autoFocus /></label>
          <div className="span2"><AmountPicker x={extracted} value={editing.amount} onPick={(c) => setEditing({ ...editing, amount: centsToInput(c) })} /></div>
          <label>Kategorie
            <select value={editing.category_key} onChange={(e) => setEditing({ ...editing, category_key: e.target.value })}>
              <option value="">– wählen –</option>
              {meta!.expense_categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </label>
          <label>Rechnungsdatum<input type="date" value={editing.invoice_date || ""} onChange={(e) => setEditing({ ...editing, invoice_date: e.target.value })} /></label>
          <label>Zahlungsdatum<input type="date" value={editing.paid_on || ""} onChange={(e) => setEditing({ ...editing, paid_on: e.target.value })} />
            <span className="hint">{editing.paid_assumed ? "angenommen (= Rechnungsdatum) – bei Abweichung ändern" : editing.paid_on ? "bestimmt das Jahr" : "leer = Rechnungsdatum (angenommen)"}</span></label>
          <label>Aussteller<input value={editing.payee} onChange={(e) => setEditing({ ...editing, payee: e.target.value })} /></label>
          <label>Status
            <select value={editing.status} onChange={(e) => setEditing({ ...editing, status: e.target.value })}>
              <option value="entwurf">Entwurf</option><option value="bestaetigt">Bestätigt</option><option value="ungeklaert">Ungeklärt</option>
            </select>
          </label>
          <label className="span2">Notiz<input value={editing.note} onChange={(e) => setEditing({ ...editing, note: e.target.value })} placeholder="z. B. Austausch Wasserzähler Whg. 2" /></label>
          <div className="row-actions span2">
            <button className="primary" onClick={save}>{editing.id ? "Position speichern" : "Position hinzufügen"}</button>
            <button className="ghost" onClick={() => setEditing(null)}>Abbrechen</button>
          </div>
        </div>
      ) : (
        <button className="secondary block" onClick={startNew}><Icon name="plus" size={18} /> Ausgabe erfassen</button>
      )}
    </section>
  );
}
