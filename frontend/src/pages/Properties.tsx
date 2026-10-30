import { useState } from "react";
import { centsToInput, date, del, get, money, parseMoney, patch, post } from "../api";
import { go, useLoad, useMeta, useToast } from "../lib";
import { BackButton, Empty, ErrorBox, Icon, Loading, Sheet } from "../components/ui";
import { OccupancyPanel } from "./Tenants";

// Bezeichnungen der Wohnungen (Hauptstraße 15); weitere über das Eingabefeld.
const UNIT_PRESETS = ["EG", "OG links", "OG Mitte", "OG rechts", "Galerie"];

const EMPTY = { name: "", address: "", kind: "Eigentumswohnung", living_area: "", ownership_share: "", rental_status: "vermietet",
  acquired_on: "", acquisition_cost: "", keywords: "", notes: "" };

function PropertyForm({ initial, onSave, submitLabel }: { initial: any; onSave: (body: any) => Promise<void>; submitLabel: string }) {
  const [f, setF] = useState(initial);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onSave({
        name: f.name.trim(), address: f.address.trim(), kind: f.kind, rental_status: f.rental_status,
        living_area: f.living_area ? Number(String(f.living_area).replace(",", ".")) : null,
        ownership_share: f.ownership_share, acquired_on: f.acquired_on || null,
        acquisition_cost_cents: f.acquisition_cost ? parseMoney(f.acquisition_cost) : null,
        keywords: f.keywords, notes: f.notes,
      });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="form grid" onSubmit={submit}>
      <label className="span2">Objektname<input value={f.name} onChange={set("name")} required placeholder="z. B. Hauptstraße 15" /></label>
      <label className="span2">Anschrift<input value={f.address} onChange={set("address")} placeholder="Straße Nr., PLZ Ort" />
        <span className="hint">Die Adresse wird in Belegen gesucht, um sie diesem Objekt zuzuordnen.</span></label>
      <label>Art
        <select value={f.kind} onChange={set("kind")}>
          {["Eigentumswohnung", "Einfamilienhaus", "Mehrfamilienhaus", "Gewerbeeinheit", "Garage/Stellplatz", "Sonstiges"].map((k) => <option key={k}>{k}</option>)}
        </select>
      </label>
      <label>Vermietungsstatus
        <select value={f.rental_status} onChange={set("rental_status")}>
          {["vermietet", "leerstehend", "teilweise vermietet", "selbst genutzt"].map((k) => <option key={k}>{k}</option>)}
        </select>
      </label>
      <label>Wohnfläche (m²)<input inputMode="decimal" value={f.living_area} onChange={set("living_area")} /></label>
      <label>Eigentumsanteil<input value={f.ownership_share} onChange={set("ownership_share")} placeholder="z. B. 100 % oder 85/1000 MEA" /></label>
      <label>Anschaffungsdatum<input type="date" value={f.acquired_on} onChange={set("acquired_on")} /></label>
      <label>Anschaffungskosten (€)<input inputMode="decimal" value={f.acquisition_cost} onChange={set("acquisition_cost")} /></label>
      <label className="span2">Weitere Suchbegriffe<input value={f.keywords} onChange={set("keywords")} placeholder="z. B. WEG Hauptstraße, Objekt-Nr. 4711" />
        <span className="hint">Kommagetrennt. Hilft bei Belegen ohne Anschrift (Hausverwaltung, Versicherung).</span></label>
      <label className="span2">Notizen<textarea value={f.notes} onChange={set("notes")} rows={3} /></label>
      <button className="primary span2" disabled={busy}>{submitLabel}</button>
    </form>
  );
}

export function Properties() {
  const { data, error, loading, reload } = useLoad(() => get("/api/properties"), []);
  const { reload: reloadMeta } = useMeta();
  const [creating, setCreating] = useState(false);
  const toast = useToast();
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox text={error} />;

  const create = async (body: any) => {
    try {
      const r = await post("/api/properties", body);
      setCreating(false);
      reloadMeta();
      reload();
      go(`/objekt/${r.id}`);
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    }
  };

  return (
    <div className="page properties">
      <div className="page-head">
        <nav className="register small" aria-label="Ansicht">
          <button className="on">Objekte</button>
          <button onClick={() => go("/mieter")}>Mieter</button>
          <button onClick={() => go("/einnahmen")}>Einnahmen</button>
          <button onClick={() => go("/betriebskosten")}>Betriebskosten</button>
        </nav>
        <button className="primary" onClick={() => setCreating(true)}><Icon name="plus" size={18} /> Objekt anlegen</button>
      </div>
      {data.properties.length === 0 ? (
        <Empty icon="building" title="Noch kein Mietobjekt">Legen Sie Ihr erstes Objekt an. Belege mit dessen Anschrift werden danach automatisch zugeordnet.</Empty>
      ) : (
        <div className="property-grid">
          {data.properties.map((p: any) => (
            <a key={p.id} className="property-card" href={`#/objekt/${p.id}`}>
              <h2>{p.name}</h2>
              <p className="muted">{p.address || "Keine Anschrift"}</p>
              <dl>
                <div><dt>Ausgaben {data.year}</dt><dd>{money(p.expenses_year)}</dd></div>
                <div><dt>Dokumente</dt><dd>{p.document_count}</dd></div>
                <div><dt>Einheiten</dt><dd>{p.units.length || "–"}</dd></div>
              </dl>
              <span className={`status-pill ${p.rental_status === "vermietet" ? "bestaetigt" : "entwurf"}`}>{p.rental_status}</span>
            </a>
          ))}
        </div>
      )}
      <Sheet open={creating} onClose={() => setCreating(false)} title="Mietobjekt anlegen">
        <PropertyForm initial={EMPTY} onSave={create} submitLabel="Objekt anlegen" />
      </Sheet>
    </div>
  );
}

export function PropertyPage({ id }: { id: number }) {
  const { data: p, error, loading, reload } = useLoad(() => get(`/api/properties/${id}`), [id]);
  const { reload: reloadMeta } = useMeta();
  const [editing, setEditing] = useState(false);
  const [unitName, setUnitName] = useState("");
  const toast = useToast();
  if (loading && !p) return <Loading />;
  if (error) return <div className="page"><BackButton to="/objekte" /><ErrorBox text={error} /></div>;

  const save = async (body: any) => {
    try {
      await patch(`/api/properties/${id}`, body);
      setEditing(false);
      reloadMeta();
      reload();
      toast({ kind: "ok", text: "Gespeichert." });
    } catch (e: any) {
      toast({ kind: "error", text: e.message });
    }
  };
  const addUnit = async (name?: string) => {
    const n = (name ?? unitName).trim();
    if (!n) return;
    await post(`/api/properties/${id}/units`, { name: n });
    setUnitName("");
    reloadMeta();
    reload();
  };
  const removeUnit = async (uid: number) => {
    if (!confirm("Einheit löschen? Zuordnungen von Dokumenten und Ausgaben werden entfernt.")) return;
    try {
      await del(`/api/units/${uid}`);
    } catch (e: any) {
      return toast({ kind: "error", text: e.message });
    }
    reloadMeta();
    reload();
  };
  const setArea = async (uid: number, raw: string) => {
    const v = raw.trim() ? Number(raw.replace(",", ".")) : null;
    if (v !== null && (Number.isNaN(v) || v < 0)) return toast({ kind: "error", text: "Wohnfläche ist ungültig." });
    await patch(`/api/units/${uid}`, { living_area: v });
    reloadMeta();
    reload();
  };
  const archive = async () => {
    if (!confirm("Objekt archivieren? Dokumente und Ausgaben bleiben erhalten.")) return;
    await patch(`/api/properties/${id}`, { archived: true });
    reloadMeta();
    go("/objekte");
  };

  return (
    <div className="page property">
      <header className="doc-head">
        <BackButton to="/objekte" />
        <div className="doc-head-main">
          <h1>{p.name}</h1>
          <p className="muted">{p.address || "Keine Anschrift hinterlegt"}</p>
        </div>
        <button className="secondary" onClick={() => setEditing(true)}>Bearbeiten</button>
      </header>

      <div className="home-grid">
        <section className="panel">
          <h2>Stammdaten</h2>
          <dl className="facts">
            <dt>Art</dt><dd>{p.kind || "–"}</dd>
            <dt>Status</dt><dd>{p.rental_status}</dd>
            <dt>Wohnfläche</dt><dd>{p.living_area ? `${String(p.living_area).replace(".", ",")} m²` : "–"}</dd>
            <dt>Eigentumsanteil</dt><dd>{p.ownership_share || "–"}</dd>
            <dt>Angeschafft</dt><dd>{date(p.acquired_on)}</dd>
            <dt>Anschaffungskosten</dt><dd>{money(p.acquisition_cost_cents)}</dd>
          </dl>
          {p.notes && <p className="notes">{p.notes}</p>}
          <h3>Einheiten</h3>
          {p.units.length === 0 && <p className="muted">Keine Einheiten – Ausgaben gelten für das ganze Objekt.</p>}
          <ul className="unit-list">
            {p.units.map((u: any) => (
              <li key={u.id}><span>{u.name}</span>
                <label className="unit-area"><input inputMode="decimal" defaultValue={u.living_area != null ? String(u.living_area).replace(".", ",") : ""}
                  onBlur={(e) => e.target.value !== (u.living_area != null ? String(u.living_area).replace(".", ",") : "") && setArea(u.id, e.target.value)}
                  aria-label={`Wohnfläche ${u.name}`} placeholder="–" /> m²</label>
                <button className="icon-btn" aria-label="Löschen" onClick={() => removeUnit(u.id)}><Icon name="trash" size={16} /></button></li>
            ))}
          </ul>
          <div className="inline-add">
            <input value={unitName} onChange={(e) => setUnitName(e.target.value)} placeholder="z. B. Wohnung EG links" onKeyDown={(e) => e.key === "Enter" && addUnit()} />
            <button className="secondary" onClick={() => addUnit()}>Hinzufügen</button>
          </div>
          {UNIT_PRESETS.some((n) => !p.units.some((u: any) => u.name.toLowerCase() === n.toLowerCase())) && (
            <div className="unit-presets">
              <span className="muted">Schnell anlegen:</span>
              {UNIT_PRESETS.filter((n) => !p.units.some((u: any) => u.name.toLowerCase() === n.toLowerCase())).map((n) => (
                <button key={n} className="choice small" onClick={() => addUnit(n)}><Icon name="plus" size={14} /> {n}</button>
              ))}
            </div>
          )}
        </section>

        <section className="panel">
          <header className="panel-head"><h2>Ausgaben nach Jahr</h2><a href={`#/ausgaben?objekt=${id}`}>Auswertung</a></header>
          {p.years.length === 0 ? <p className="muted">Noch keine bezahlten Ausgaben erfasst.</p> : (
            <ul className="prop-totals">
              {p.years.map((y: any) => <li key={y.year}><span>{y.year} ({y.count} Positionen)</span><strong>{money(y.total)}</strong></li>)}
            </ul>
          )}
          <header className="panel-head"><h2>Dokumente</h2><a href={`#/archiv?bereich=vermietung&objekt=${id}`}>Alle anzeigen</a></header>
          {p.recent_documents.length === 0 && <p className="muted">Noch keine Dokumente zugeordnet.</p>}
          <ul className="plain-list">
            {p.recent_documents.map((d: any) => <li key={d.id}><a href={`#/dokument/${d.id}`}>{d.title}</a><span className="muted">{date(d.doc_date || d.created_at)}</span></li>)}
          </ul>
        </section>
      </div>
      <OccupancyPanel key={p.units.map((u: any) => `${u.id}:${u.living_area ?? ""}`).join(",")} propertyId={id} />
      <button className="danger-link" onClick={archive}><Icon name="archive" size={18} /> Objekt archivieren</button>

      <Sheet open={editing} onClose={() => setEditing(false)} title="Objekt bearbeiten">
        <PropertyForm submitLabel="Speichern" onSave={save} initial={{
          ...p, living_area: p.living_area ?? "", acquired_on: p.acquired_on || "", acquisition_cost: centsToInput(p.acquisition_cost_cents),
        }} />
      </Sheet>
    </div>
  );
}
