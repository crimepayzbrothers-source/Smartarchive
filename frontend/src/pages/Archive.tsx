import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from "react";
import { get } from "../api";
import { replaceRoute, takeScroll, useMeta, useToast } from "../lib";
import { canShareFiles, fetchDocFile, shareFiles } from "../components/shareDocs";
import { DocRow, Empty, ErrorBox, Icon, Loading, type DocItem } from "../components/ui";

const YEARS = (() => {
  const y = new Date().getFullYear();
  return Array.from({ length: 12 }, (_, i) => y - i);
})();

// Zuletzt angezeigte Liste. Bei der Rückkehr aus einem Dokument erscheint sie sofort wieder,
// statt leer neu zu laden; danach wird sie still aktualisiert.
type ListCache = { key: string; items: DocItem[]; total: number; hint: string | null };
let listCache: ListCache | null = null;
const MAX_PICK = 50;
const num = (v: string | null) => (v && /^\d+$/.test(v) ? Number(v) : null);

export default function Archive({ params, searchMode = false }: { params: URLSearchParams; searchMode?: boolean }) {
  const { meta } = useMeta();
  const [area, setArea] = useState(params.get("bereich") || (searchMode ? "" : "privat"));
  const [category, setCategory] = useState<number | null>(num(params.get("kategorie")));
  const [property, setProperty] = useState<number | null>(num(params.get("objekt")));
  const [year, setYear] = useState<number | null>(num(params.get("jahr")));
  const [tag, setTag] = useState<string>(params.get("tag") || "");
  const [favorite, setFavorite] = useState(params.get("favorit") === "1");
  const [q, setQ] = useState(params.get("q") || "");
  const [debounced, setDebounced] = useState(q);

  const query = useMemo(() => ({
    q: debounced.trim(), area: area || undefined, category_id: category, property_id: area === "vermietung" ? property : null,
    year, tag: tag || undefined, favorite: favorite ? 1 : undefined,
  }), [debounced, area, category, property, year, tag, favorite]);
  const cacheKey = JSON.stringify([searchMode, query]);

  // Nur übernehmen, wenn die Liste zu genau diesen Such- und Filterangaben gehört.
  const [restored] = useState(() => (listCache && listCache.key === cacheKey ? listCache : null));
  const [items, setItems] = useState<DocItem[]>(restored?.items || []);
  const [total, setTotal] = useState(restored?.total || 0);
  const [hint, setHint] = useState<string | null>(restored?.hint || null);
  const [loading, setLoading] = useState(!restored);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  // Mehrfachauswahl: mehrere Dokumente gemeinsam teilen (E-Mail, WhatsApp …) oder als ZIP laden
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const [sharing, setSharing] = useState(false);
  const [ready, setReady] = useState<File[] | null>(null);
  const pressTimer = useRef<number | null>(null);
  const pressed = useRef(false);
  const toggle = (id: number) => { setReady(null); setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= MAX_PICK ? cur : [...cur, id])); };
  const stopSelect = () => { setSelecting(false); setPicked([]); setReady(null); };
  const startPress = (id: number) => {
    pressed.current = false;
    pressTimer.current = window.setTimeout(() => { pressed.current = true; pressTimer.current = null; setSelecting(true); toggle(id); navigator.vibrate?.(15); }, 500);
  };
  const cancelPress = () => { if (pressTimer.current) { clearTimeout(pressTimer.current); pressTimer.current = null; } };
  const onPickClick = (e: MouseEvent, id: number) => {
    if (pressed.current) { pressed.current = false; e.preventDefault(); e.stopPropagation(); return; }  // langes Drücken: kein Öffnen
    if (!selecting) return;
    e.preventDefault(); e.stopPropagation();
    toggle(id);
  };
  const shareSelected = async () => {
    setSharing(true);
    try {
      let files = ready;
      if (!files) {
        files = [];
        for (const id of picked) files.push(await fetchDocFile(id, items.find((d) => d.id === id)?.title));
      }
      if (!canShareFiles(files)) {
        toast({ kind: "error", text: "Dieses Gerät kann mehrere Dateien nicht direkt teilen – bitte „ZIP“ verwenden." });
        return;
      }
      const r = await shareFiles(files, `${files.length} Dokumente`);
      if (r === "again") { setReady(files); toast({ kind: "ok", text: "Dateien sind bereit – bitte noch einmal auf „Jetzt teilen“ tippen." }); return; }
      setReady(null);
      if (r === "ok") stopSelect();
    } catch (e: any) {
      toast({ kind: "error", text: e.message || "Teilen fehlgeschlagen." });
    } finally {
      setSharing(false);
    }
  };
  const first = useRef(true);

  // Tastatur nur bei leerer Suche öffnen, nicht bei der Rückkehr zu Treffern.
  useEffect(() => { if (searchMode && !q) inputRef.current?.focus(); }, [searchMode]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const t = setTimeout(() => setDebounced(q), 250); return () => clearTimeout(t); }, [q]);
  // Bei der Rückkehr an dieselbe Stelle der Liste springen.
  useLayoutEffect(() => { if (restored) { const y = takeScroll(); if (y !== null) window.scrollTo(0, y); } }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Suchbegriff und Filter in der Adresse festhalten (ohne neuen Verlaufseintrag), damit „Zurück“ sie wiederfindet.
  useEffect(() => {
    const p = new URLSearchParams();
    if (debounced.trim()) p.set("q", debounced.trim());
    if (area) p.set("bereich", area);
    if (category !== null) p.set("kategorie", String(category));
    if (area === "vermietung" && property !== null) p.set("objekt", String(property));
    if (year !== null) p.set("jahr", String(year));
    if (tag) p.set("tag", tag);
    if (favorite) p.set("favorit", "1");
    replaceRoute(searchMode ? "/suche" : "/archiv", p);
  }, [debounced, area, category, property, year, tag, favorite, searchMode]);

  useEffect(() => { listCache = { key: cacheKey, items, total, hint }; }, [cacheKey, items, total, hint]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setDebounced(q);
    inputRef.current?.blur(); // schließt die Bildschirmtastatur
  };

  const load = async (offset = 0, limit = 60) => {
    setLoading(true);
    try {
      const res = await get("/api/documents", { ...query, offset, limit });
      setItems((cur) => (offset ? [...cur, ...res.results] : res.results));
      setTotal(res.total);
      setHint(res.hint || null);
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    // Wiederhergestellte Liste still in derselben Länge aktualisieren, damit die Scrollposition stimmt.
    if (first.current && restored) load(0, Math.max(60, restored.items.length));
    else load(0);
    first.current = false;
  }, [query]); // eslint-disable-line react-hooks/exhaustive-deps

  const roots = (meta?.categories || []).filter((c) => c.area_key === area && !c.parent_id);
  const children = (meta?.categories || []).filter((c) => category && c.parent_id === category);
  const selected = meta?.categories.find((c) => c.id === category);
  const parentOfSelected = selected?.parent_id ? meta?.categories.find((c) => c.id === selected.parent_id) : null;
  const switchArea = (key: string) => { setArea(key); setCategory(null); setProperty(null); };

  return (
    <div className={`page archive ${area || "alle"}`}>
      <form className="search-bar" role="search" action="" onSubmit={submit}>
        <Icon name="search" size={20} />
        <input ref={inputRef} type="search" placeholder={searchMode ? "Suchen, z. B. Heizungswartung, Grundsteuer 2025 …" : "Im Archiv suchen"}
          value={q} onChange={(e) => setQ(e.target.value)} enterKeyHint="search" aria-label="Suche" />
      </form>

      <nav className="register" aria-label="Bereich">
        {searchMode && <button className={area === "" ? "on" : ""} onClick={() => switchArea("")}>Alle</button>}
        <button className={`privat ${area === "privat" ? "on" : ""}`} onClick={() => switchArea("privat")}>Privat</button>
        <button className={`vermietung ${area === "vermietung" ? "on" : ""}`} onClick={() => switchArea("vermietung")}>Vermietung</button>
      </nav>

      <div className="register-body">
        {area && (
          <div className="folders" role="list">
            {parentOfSelected && (
              <button className="folder up" onClick={() => setCategory(parentOfSelected.id)}>↰ {parentOfSelected.name}</button>
            )}
            <button className={`folder ${category === null ? "on" : ""}`} onClick={() => setCategory(null)}>Alle Ordner</button>
            {(selected && !children.length && selected.parent_id ? (meta?.categories || []).filter((c) => c.parent_id === selected.parent_id) : category && children.length ? [selected!, ...children] : roots)
              .map((c) => (
                <button key={c.id} className={`folder ${category === c.id ? "on" : ""} ${c.parent_id ? "sub" : ""}`} onClick={() => setCategory(c.id)}>
                  {c.name}{c.count ? <span className="count">{c.count}</span> : null}
                </button>
              ))}
          </div>
        )}

        <div className="filters">
          <select value={year ?? ""} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : null)} aria-label="Jahr">
            <option value="">Alle Jahre</option>
            {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          {area === "vermietung" && (meta?.properties.length || 0) > 0 && (
            <select value={property ?? ""} onChange={(e) => setProperty(e.target.value ? Number(e.target.value) : null)} aria-label="Objekt">
              <option value="">Alle Objekte</option>
              {meta!.properties.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          )}
          {(meta?.tags.length || 0) > 0 && (
            <select value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Tag">
              <option value="">Alle Tags</option>
              {meta!.tags.map((t) => <option key={t.id} value={t.name}>{t.name}</option>)}
            </select>
          )}
          <label className={`chip-toggle ${favorite ? "on" : ""}`}>
            <input type="checkbox" checked={favorite} onChange={(e) => setFavorite(e.target.checked)} /> <Icon name="star" size={15} filled={favorite} /> Favoriten
          </label>
        </div>

        {hint && <p className="hint">{hint}</p>}
        {error && <ErrorBox text={error} />}
        {loading && !items.length ? <Loading /> : items.length === 0 ? (
          debounced ? <Empty icon="search" title={`Nichts gefunden für „${debounced}“`}>Tipp: Wortteile reichen, z. B. „wartung“ statt „Heizungswartung“. Filter oben zurücksetzen.</Empty>
            : <Empty icon="archive" title="Dieser Ordner ist leer">Dokumente landen hier, sobald sie abgelegt sind.</Empty>
        ) : (
          <>
            <div className="result-head">
              <p className="result-count">{total} {total === 1 ? "Dokument" : "Dokumente"}</p>
              <button className="ghost small" onClick={() => (selecting ? stopSelect() : setSelecting(true))}>{selecting ? "Fertig" : "Auswählen"}</button>
            </div>
            {selecting && !picked.length && <p className="hint">Dokumente antippen, um sie auszuwählen. Tipp: Langes Drücken startet die Auswahl direkt.</p>}
            <div className={`doc-list ${selecting ? "selecting" : ""}`}>{items.map((d) => (
              <div key={d.id} className={`pick-wrap ${picked.includes(d.id) ? "picked" : ""}`} onClickCapture={(e) => onPickClick(e, d.id)}
                onPointerDown={() => startPress(d.id)} onPointerUp={cancelPress} onPointerLeave={cancelPress} onPointerCancel={cancelPress}
                onContextMenu={(e) => { if (pressTimer.current || selecting) e.preventDefault(); }}>
                {selecting && <span className="pick-box" aria-hidden="true">{picked.includes(d.id) && <Icon name="check" size={16} />}</span>}
                <DocRow doc={d} showArea={!area} />
              </div>
            ))}</div>
            {items.length < total && <button className="secondary block" disabled={loading} onClick={() => load(items.length)}>Weitere laden</button>}
          </>
        )}
      </div>
      {selecting && picked.length > 0 && (
        <div className="pick-bar" role="toolbar" aria-label="Auswahl">
          <span className="pick-count">{picked.length} ausgewählt</span>
          <button className="ghost small" onClick={() => setPicked(picked.length === items.length ? [] : items.map((d) => d.id).slice(0, MAX_PICK))}>
            {picked.length === items.length ? "Keine" : "Alle"}</button>
          <a className="secondary small btn-link" href={`/api/documents/bundle.zip?ids=${picked.join(",")}`}>ZIP</a>
          <button className="primary small" disabled={sharing} onClick={shareSelected}>
            <Icon name="share" size={16} /> {sharing ? "Lädt …" : ready ? "Jetzt teilen" : "Teilen"}</button>
        </div>
      )}
    </div>
  );
}
