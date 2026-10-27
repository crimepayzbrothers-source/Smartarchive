import { useState } from "react";
import { date, del, get, patch, post } from "../api";
import { applyTheme, storedTheme, useLoad, useMeta, useToast, type Category } from "../lib";
import { CategorySelect, Icon, Loading } from "../components/ui";

export default function Settings({ user, onLogout }: { user: any; onLogout: () => void }) {
  const { meta } = useMeta();
  if (!meta) return <Loading />;
  return (
    <div className="page settings">
      <h1 className="page-title">Einstellungen</h1>
      <AutoFiling />
      <Folders />
      <Rules />
      <Correspondents />
      <Security user={user} onLogout={onLogout} />
      <Appearance />
      <DataSection />
    </div>
  );
}

function AutoFiling() {
  const { meta, reload } = useMeta();
  const toast = useToast();
  const s = meta!.settings;
  const update = async (body: any) => {
    try { await patch("/api/settings", body); await reload(); } catch (e: any) { toast({ kind: "error", text: e.message }); }
  };
  const setAreaAi = async (id: number, on: boolean) => { await patch(`/api/areas/${id}`, { ai_enabled: on }); reload(); };
  return (
    <section className="panel">
      <h2>Automatische Ablage</h2>
      <label className="switch-row">
        <span><strong>Sichere Treffer sofort ablegen</strong><span className="muted">Sonst landet alles mit Vorschlag im Eingang.</span></span>
        <input type="checkbox" role="switch" checked={s.auto_file} onChange={(e) => update({ auto_file: e.target.checked })} />
      </label>
      <label className="range-row">
        <span>Ab welcher Sicherheit automatisch: <strong>{Math.round(s.auto_threshold * 100)} %</strong></span>
        <input type="range" min={60} max={100} step={1} value={Math.round(s.auto_threshold * 100)}
          onChange={(e) => update({ auto_threshold: Number(e.target.value) / 100 })} disabled={!s.auto_file} />
        <span className="hint">Eigene Regeln (99 %) und zweimal bestätigte Absender (92–95 %) liegen meist darüber, reine Stichwort-Treffer (bis 80 %) darunter.</span>
      </label>
      <h3>KI-Einordnung</h3>
      {meta!.ai_available ? (
        <p className="muted">Mistral ({meta!.ai_model}) ist eingerichtet. Gesendet wird nur ein Textauszug, nie die Datei.</p>
      ) : (
        <p className="notice">Keine KI eingerichtet. Die App ordnet mit Regeln, Ihrem Ablageverlauf und Stichwörtern ein. Für die KI MISTRAL_API_KEY in der .env eintragen.</p>
      )}
      <label className="switch-row">
        <span><strong>KI verwenden</strong><span className="muted">Nur wenn weder Regel noch Verlauf greift.</span></span>
        <input type="checkbox" role="switch" checked={s.ai_enabled && meta!.ai_available} disabled={!meta!.ai_available} onChange={(e) => update({ ai_enabled: e.target.checked })} />
      </label>
      {meta!.areas.map((a) => (
        <label key={a.id} className="switch-row indent">
          <span>KI für Bereich {a.name}</span>
          <input type="checkbox" role="switch" checked={a.ai_enabled} onChange={(e) => setAreaAi(a.id, e.target.checked)} />
        </label>
      ))}
      <p className="hint">Einzelne Ordner sperren Sie unten unter „Ordner“ (voreingestellt gesperrt: Gesundheit, Familie).</p>
    </section>
  );
}

function Folders() {
  const { meta, reload } = useMeta();
  const toast = useToast();
  const [area, setArea] = useState("privat");
  const [newName, setNewName] = useState("");
  const [parent, setParent] = useState<number | null>(null);
  const [edit, setEdit] = useState<Category | null>(null);
  const areaObj = meta!.areas.find((a) => a.key === area)!;
  const cats = meta!.categories.filter((c) => c.area_key === area);
  const run = async (fn: () => Promise<any>) => {
    try { await fn(); await reload(); } catch (e: any) { toast({ kind: "error", text: e.message }); }
  };
  return (
    <section className="panel">
      <h2>Ordner</h2>
      <nav className="register small" aria-label="Bereich">
        <button className={`privat ${area === "privat" ? "on" : ""}`} onClick={() => setArea("privat")}>Privat</button>
        <button className={`vermietung ${area === "vermietung" ? "on" : ""}`} onClick={() => setArea("vermietung")}>Vermietung</button>
      </nav>
      <ul className="folder-tree">
        {cats.map((c) => (
          <li key={c.id} className={c.parent_id ? "sub" : ""}>
            {edit?.id === c.id ? (
              <div className="form grid">
                <label>Name<input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
                <label className="span2">Stichwörter für die Erkennung<textarea rows={2} value={edit.keywords} onChange={(e) => setEdit({ ...edit, keywords: e.target.value })} /></label>
                <div className="row-actions span2">
                  <button className="primary" onClick={() => run(() => patch(`/api/categories/${c.id}`, { name: edit.name, keywords: edit.keywords })).then(() => setEdit(null))}>Speichern</button>
                  <button className="ghost" onClick={() => setEdit(null)}>Abbrechen</button>
                </div>
              </div>
            ) : (
              <>
                <button className="folder-name" onClick={() => setEdit(c)}>{c.name}<span className="muted"> {c.count || ""}</span></button>
                <label className={`ai-chip ${c.ai_enabled ? "on" : ""}`} title="KI für diesen Ordner">
                  <input type="checkbox" checked={c.ai_enabled} onChange={(e) => run(() => patch(`/api/categories/${c.id}`, { ai_enabled: e.target.checked }))} />
                  {c.ai_enabled ? "KI erlaubt" : "KI gesperrt"}
                </label>
                <button className="icon-btn" aria-label="Löschen" onClick={() => confirm(`Ordner „${c.name}“ löschen?`) && run(() => del(`/api/categories/${c.id}`))}><Icon name="trash" size={16} /></button>
              </>
            )}
          </li>
        ))}
      </ul>
      <div className="inline-add">
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Neuer Ordner" />
        <select value={parent ?? ""} onChange={(e) => setParent(e.target.value ? Number(e.target.value) : null)} aria-label="Übergeordneter Ordner">
          <option value="">als Hauptordner</option>
          {cats.filter((c) => !c.parent_id).map((c) => <option key={c.id} value={c.id}>in {c.name}</option>)}
        </select>
        <button className="secondary" disabled={!newName.trim()} onClick={() => run(() => post("/api/categories", { area_id: areaObj.id, parent_id: parent, name: newName.trim() })).then(() => setNewName(""))}>Anlegen</button>
      </div>
    </section>
  );
}

function Rules() {
  const { meta } = useMeta();
  const toast = useToast();
  const { data, reload } = useLoad(() => get("/api/rules"), []);
  const [form, setForm] = useState({ name: "", match_field: "text", pattern: "", category_id: null as number | null, property_id: null as number | null });
  const create = async (body: any) => {
    try { await post("/api/rules", body); reload(); toast({ kind: "ok", text: "Regel angelegt." }); } catch (e: any) { toast({ kind: "error", text: e.message }); }
  };
  if (!data) return null;
  return (
    <section className="panel">
      <h2>Regeln</h2>
      <p className="muted">Regeln greifen vor der KI und legen sicher ab. Beispiel: Text enthält „Gebäudeversicherung“ → Vermietung › Versicherungen › Hauptstraße 15.</p>
      {data.proposals.length > 0 && (
        <div className="proposals">
          <h3>Aus Ihren Korrekturen</h3>
          {data.proposals.map((p: any) => (
            <div key={`${p.correspondent_id}-${p.category_id}`} className="proposal">
              <span>Dokumente von <strong>{p.correspondent_name}</strong> haben Sie {p.n}× nach {p.category_path} gelegt.</span>
              <button className="secondary" onClick={() => create({ name: `Absender ${p.correspondent_name}`, match_field: "correspondent", pattern: p.correspondent_name,
                category_id: p.category_id, property_id: p.property_id, origin: "learned" })}>Als Regel übernehmen</button>
            </div>
          ))}
        </div>
      )}
      <ul className="rule-list">
        {data.rules.map((r: any) => (
          <li key={r.id} className={r.enabled ? "" : "off"}>
            <div>
              <strong>{r.name}</strong>
              <p className="muted">{r.match_field === "correspondent" ? "Absender" : r.match_field === "filename" ? "Dateiname" : "Text"} enthält „{r.pattern}“ → {r.category_path}{r.property_name ? `, ${r.property_name}` : ""}. {r.hits}× angewendet.</p>
            </div>
            <input type="checkbox" role="switch" checked={r.enabled} aria-label="Regel aktiv" onChange={async (e) => { await patch(`/api/rules/${r.id}`, { enabled: e.target.checked }); reload(); }} />
            <button className="icon-btn" aria-label="Regel löschen" onClick={async () => { if (confirm("Regel löschen?")) { await del(`/api/rules/${r.id}`); reload(); } }}><Icon name="trash" size={16} /></button>
          </li>
        ))}
      </ul>
      <details className="collapsible">
        <summary>Neue Regel</summary>
        <div className="form grid">
          <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="z. B. Grundsteuer Schmidt-Hachenbach" /></label>
          <label>Wo suchen
            <select value={form.match_field} onChange={(e) => setForm({ ...form, match_field: e.target.value })}>
              <option value="text">im Text</option><option value="correspondent">beim Absender</option><option value="filename">im Dateinamen</option>
            </select>
          </label>
          <label className="span2">Enthält<input value={form.pattern} onChange={(e) => setForm({ ...form, pattern: e.target.value })} placeholder="z. B. Kassenzeichen 123/456" />
            <span className="hint">Groß-/Kleinschreibung egal. Für Fortgeschrittene: /regulärer Ausdruck/</span></label>
          <label>Ablegen in<CategorySelect meta={meta!} value={form.category_id} onChange={(v) => setForm({ ...form, category_id: v })} /></label>
          <label>Objekt (optional)
            <select value={form.property_id ?? ""} onChange={(e) => setForm({ ...form, property_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">–</option>
              {meta!.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <button className="primary span2" disabled={!form.name || !form.pattern || !form.category_id}
            onClick={() => create(form).then(() => setForm({ name: "", match_field: "text", pattern: "", category_id: null, property_id: null }))}>Regel anlegen</button>
        </div>
      </details>
    </section>
  );
}

function Correspondents() {
  const { meta, reload } = useMeta();
  const [editing, setEditing] = useState<any>(null);
  if (!meta!.correspondents.length) return null;
  return (
    <section className="panel">
      <details className="collapsible">
        <summary>Absender ({meta!.correspondents.length})</summary>
        <p className="muted">Alternative Schreibweisen helfen bei der Erkennung, z. B. „VG Kirner Land“ für die Verbandsgemeinde.</p>
        <ul className="plain-list">
          {meta!.correspondents.map((c) => (
            <li key={c.id}>
              {editing?.id === c.id ? (
                <div className="inline-add">
                  <input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} aria-label="Name" />
                  <input value={editing.aliases} onChange={(e) => setEditing({ ...editing, aliases: e.target.value })} placeholder="weitere Schreibweisen, kommagetrennt" aria-label="Schreibweisen" />
                  <button className="primary" onClick={async () => { await patch(`/api/correspondents/${c.id}`, { name: editing.name, aliases: editing.aliases }); setEditing(null); reload(); }}>Speichern</button>
                </div>
              ) : (
                <>
                  <button className="link" onClick={() => setEditing({ ...c })}>{c.name}</button>
                  <span className="muted">{c.aliases}</span>
                  <button className="icon-btn" aria-label="Löschen" onClick={async () => { if (confirm(`„${c.name}“ löschen?`)) { await del(`/api/correspondents/${c.id}`); reload(); } }}><Icon name="trash" size={16} /></button>
                </>
              )}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function Security({ user, onLogout }: { user: any; onLogout: () => void }) {
  const toast = useToast();
  const [totp, setTotp] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [enabled, setEnabled] = useState<boolean>(user.totp_enabled);
  const [pw, setPw] = useState({ old_password: "", new_password: "" });
  const run = async (fn: () => Promise<any>, ok: string) => {
    try { await fn(); toast({ kind: "ok", text: ok }); return true; } catch (e: any) { toast({ kind: "error", text: e.message }); return false; }
  };
  return (
    <section className="panel">
      <h2>Konto und Sicherheit</h2>
      <p className="muted">Angemeldet als <strong>{user.username}</strong>.</p>
      <h3>Zwei-Faktor-Anmeldung</h3>
      {enabled ? (
        <div className="row-actions">
          <span className="status-pill bestaetigt">aktiv</span>
          <button className="ghost" onClick={async () => {
            const password = prompt("Zum Abschalten Passwort eingeben:");
            if (password && await run(() => post("/api/auth/totp/disable", { password }), "Zwei-Faktor ausgeschaltet.")) setEnabled(false);
          }}>Ausschalten</button>
        </div>
      ) : totp ? (
        <div className="totp-setup">
          <ol>
            <li>Authenticator-App öffnen (z. B. Aegis, Google Authenticator) und „Schlüssel eingeben“ wählen.</li>
            <li>Diesen Schlüssel eintragen: <code className="secret">{totp.secret.match(/.{1,4}/g)?.join(" ")}</code> <a href={totp.uri}>oder direkt in der App öffnen</a></li>
            <li>Den angezeigten 6-stelligen Code hier bestätigen:</li>
          </ol>
          <div className="inline-add">
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" maxLength={7} placeholder="123 456" aria-label="Code" />
            <button className="primary" onClick={async () => { if (await run(() => post("/api/auth/totp/confirm", { code }), "Zwei-Faktor ist aktiv.")) { setEnabled(true); setTotp(null); } }}>Bestätigen</button>
          </div>
        </div>
      ) : (
        <button className="secondary" onClick={async () => setTotp(await post("/api/auth/totp/start"))}>Zwei-Faktor einrichten</button>
      )}
      <h3>Passwort ändern</h3>
      <div className="form grid">
        <label>Bisheriges Passwort<input type="password" autoComplete="current-password" value={pw.old_password} onChange={(e) => setPw({ ...pw, old_password: e.target.value })} /></label>
        <label>Neues Passwort<input type="password" autoComplete="new-password" minLength={10} value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></label>
        <button className="secondary span2" disabled={pw.new_password.length < 10 || !pw.old_password}
          onClick={async () => { if (await run(() => post("/api/auth/password", pw), "Passwort geändert. Andere Geräte sind abgemeldet.")) setPw({ old_password: "", new_password: "" }); }}>Passwort ändern</button>
      </div>
      <button className="ghost" onClick={onLogout}><Icon name="logout" size={18} /> Abmelden</button>
    </section>
  );
}

function Appearance() {
  const [theme, setTheme] = useState(storedTheme());
  return (
    <section className="panel">
      <h2>Darstellung</h2>
      <fieldset className="segmented" aria-label="Farbschema">
        {[["system", "Wie Gerät"], ["light", "Hell"], ["dark", "Dunkel"]].map(([k, l]) => (
          <label key={k} className={theme === k ? "on" : ""}>
            <input type="radio" name="theme" checked={theme === k} onChange={() => { setTheme(k); applyTheme(k); }} /> {l}
          </label>
        ))}
      </fieldset>
    </section>
  );
}

function DataSection() {
  const { data, reload } = useLoad(() => get("/api/documents", { trash: 1, limit: 100 }), []);
  return (
    <section className="panel">
      <h2>Daten</h2>
      <p className="muted">Gesamtexport aller Originale, durchsuchbaren PDFs und Metadaten als ZIP – Ihr Archiv bleibt jederzeit mitnehmbar.</p>
      <a className="secondary button" href="/api/export/archive.zip"><Icon name="down" size={18} /> Gesamtexport herunterladen</a>
      <h3>Papierkorb</h3>
      {!data || data.results.length === 0 ? <p className="muted">Leer.</p> : (
        <ul className="plain-list">
          {data.results.map((d: any) => (
            <li key={d.id}>
              <a href={`#/dokument/${d.id}`}>{d.title}</a>
              <span className="muted">{date(d.doc_date || d.created_at)}</span>
              <button className="link" onClick={async () => { await post(`/api/documents/${d.id}/restore`); reload(); }}>Wiederherstellen</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
