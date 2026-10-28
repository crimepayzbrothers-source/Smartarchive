import { useEffect, useRef, type ReactNode } from "react";
import { date, relative } from "../api";
import { go, type Meta } from "../lib";

// --- Symbole (inline, keine externen Dateien) -------------------------------

const P: Record<string, ReactNode> = {
  home: <path d="M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-4.5v-5.5h-5V21H5a1 1 0 0 1-1-1z" />,
  inbox: <><path d="M4 13.5 6.2 6h11.6L20 13.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z" /><path d="M4 13.5h4.5l1 2h5l1-2H20" /></>,
  archive: <><rect x="3.5" y="4" width="17" height="5" rx="1" /><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5" /></>,
  building: <><path d="M5 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16M15 10h3a1 1 0 0 1 1 1v10M3 21h18" /><path d="M8 8h1M11 8h1M8 12h1M11 12h1M8 16h1M11 16h1" /></>,
  euro: <><path d="M17.5 6.5A7 7 0 1 0 17.5 17.5" /><path d="M4 10.5h9M4 13.5h9" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" /></>,
  camera: <><path d="M4 8.5a1.5 1.5 0 0 1 1.5-1.5h2L9 5h6l1.5 2h2A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5z" /><circle cx="12" cy="13" r="3.5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  file: <><path d="M14 3.5H7a1 1 0 0 0-1 1v15a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7.5z" /><path d="M14 3.5v4h4" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  undo: <><path d="M9 14 4.5 9.5 9 5" /><path d="M5 9.5h9.5a5 5 0 0 1 0 10H11" /></>,
  star: <path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.4l-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z" />,
  trash: <><path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13" /></>,
  back: <path d="M15 5 8 12l7 7" />,
  down: <path d="M12 4v11M7 10.5 12 15.5l5-5M5 20h14" />,
  spark: <><path d="M12 4v4M12 16v4M4 12h4M16 12h4" /><path d="m7 7 2 2M15 15l2 2M17 7l-2 2M9 15l-2 2" /></>,
  rule: <><path d="M4 6h10M4 12h16M4 18h8" /><circle cx="17" cy="6" r="2" /><circle cx="15" cy="18" r="2" /></>,
  alert: <><path d="M12 4 2.8 19.5h18.4z" /><path d="M12 10v4M12 17h.01" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  rotate: <><path d="M15 5l4.5 4.5L15 14" /><path d="M19.5 9.5H10a5 5 0 0 0 0 10h3" /></>,
  crop: <><path d="M7 3v14h14" /><path d="M3 7h14v14" /></>,
  share: <><circle cx="17.5" cy="5.5" r="2.5" /><circle cx="6.5" cy="12" r="2.5" /><circle cx="17.5" cy="18.5" r="2.5" /><path d="m8.7 10.8 6.6-4M8.7 13.2l6.6 4" /></>,
  open: <><path d="M13 4h7v7M20 4l-9 9" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>,
  receipt: <><path d="M6 3.5h12v17l-2-1.3-2 1.3-2-1.3-2 1.3-2-1.3-2 1.3z" /><path d="M9 8h6M9 11.5h6M9 15h3.5" /></>,
  logout: <><path d="M14 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4" /><path d="M10 16l-4-4 4-4M6 12h9" /></>,
};

export function Icon({ name, size = 22, filled = false }: { name: keyof typeof P | string; size?: number; filled?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={filled ? "currentColor" : "none"} stroke="currentColor"
      strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="icon">
      {P[name]}
    </svg>
  );
}

// --- Bausteine ----------------------------------------------------------------

export function Sheet({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className={`sheet ${wide ? "wide" : ""}`} onClose={(e) => e.target === ref.current && onClose()}
      onCancel={(e) => { if (e.target !== ref.current) e.preventDefault(); }} onClick={(e) => e.target === ref.current && onClose()}>
      <div className="sheet-body">
        <header className="sheet-head">
          <h2>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Schließen"><Icon name="close" /></button>
        </header>
        {open && children}
      </div>
    </dialog>
  );
}

export function Empty({ icon, title, children }: { icon: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} size={30} />
      <p className="empty-title">{title}</p>
      {children && <div className="empty-text">{children}</div>}
    </div>
  );
}

export function Loading() {
  return <div className="loading" aria-label="Lädt"><span /><span /><span /></div>;
}

export function ErrorBox({ text }: { text: string }) {
  return <div className="notice error"><Icon name="alert" size={18} /> {text}</div>;
}

export function AreaBadge({ area }: { area?: string | null }) {
  if (!area) return null;
  return <span className={`area-badge ${area}`}>{area === "vermietung" ? "Vermietung" : "Privat"}</span>;
}

export function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const level = pct >= 90 ? "high" : pct >= 60 ? "mid" : "low";
  return (
    <span className={`confidence ${level}`} title={`Sicherheit ${pct} %`}>
      <span className="bar"><span style={{ width: `${pct}%` }} /></span>
      {pct} %
    </span>
  );
}

const SOURCE: Record<string, string> = { rule: "Regel", history: "Bisherige Ablage", ai: "KI", keyword: "Stichwörter", user: "Von Ihnen", import: "Import" };
export const sourceLabel = (s?: string | null) => (s ? SOURCE[s] || s : "");

// --- Dokumentliste --------------------------------------------------------------

export type DocItem = {
  id: number; title: string; doc_date: string | null; created_at: string; status: string; area_key: string | null;
  category_path: string | null; property_name: string | null; correspondent_name: string | null; has_thumb: boolean;
  favorite: number; tags: string[]; filed_by?: string | null; confirmed?: number; page_count?: number | null;
  snippet?: { t: string; m: boolean }[]; title_hl?: { t: string; m: boolean }[]; suggestion?: any; error?: string | null;
};

export function Highlight({ parts, fallback }: { parts?: { t: string; m: boolean }[]; fallback?: string }) {
  if (!parts || !parts.length) return <>{fallback}</>;
  return <>{parts.map((p, i) => (p.m ? <mark key={i}>{p.t}</mark> : <span key={i}>{p.t}</span>))}</>;
}

export function shortPath(path?: string | null) {
  if (!path) return "";
  return path.split(" › ").slice(1).join(" › ");
}

export function DocRow({ doc, showArea = true, children }: { doc: DocItem; showArea?: boolean; children?: ReactNode }) {
  return (
    <article className={`doc-row ${doc.area_key || "none"}`}>
      <a className="doc-link" href={`#/dokument/${doc.id}`}>
        <div className="thumb">
          {doc.has_thumb ? <img src={`/api/documents/${doc.id}/thumb`} alt="" loading="lazy" /> : <Icon name="file" size={26} />}
        </div>
        <div className="doc-main">
          <h3 className="doc-title"><Highlight parts={doc.title_hl} fallback={doc.title || "Ohne Titel"} /></h3>
          <p className="doc-meta">
            {doc.status === "processing" ? <span className="processing-dot">Wird verarbeitet</span> : <span>{date(doc.doc_date || doc.created_at)}</span>}
            {doc.correspondent_name && <span>{doc.correspondent_name}</span>}
            {doc.property_name && <span className="prop">{doc.property_name}</span>}
          </p>
          {doc.category_path && <p className="doc-path">{showArea ? doc.category_path : shortPath(doc.category_path)}</p>}
          {doc.snippet && doc.snippet.length > 0 && <p className="snippet"><Highlight parts={doc.snippet} /></p>}
          {doc.tags?.length > 0 && <p className="tags">{doc.tags.map((t) => <span key={t} className="tag">{t}</span>)}</p>}
        </div>
        {doc.favorite ? <span className="fav" aria-label="Favorit"><Icon name="star" size={16} filled /></span> : null}
      </a>
      {children}
    </article>
  );
}

export function CategorySelect({ meta, area, value, onChange, id, allowEmpty = true }: {
  meta: Meta; area?: string | null; value: number | null; onChange: (id: number | null) => void; id?: string; allowEmpty?: boolean;
}) {
  const cats = meta.categories.filter((c) => !area || c.area_key === area);
  const groups = meta.areas.filter((a) => !area || a.key === area);
  return (
    <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}>
      {allowEmpty && <option value="">– Kategorie wählen –</option>}
      {groups.map((a) => (
        <optgroup key={a.key} label={a.name}>
          {cats.filter((c) => c.area_key === a.key).map((c) => (
            <option key={c.id} value={c.id}>{"  ".repeat(c.depth)}{c.depth ? "↳ " : ""}{c.name}</option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

export function BackButton({ to }: { to?: string }) {
  return (
    <button className="icon-btn back" onClick={() => (to ? go(to) : history.length > 1 ? history.back() : go("/"))} aria-label="Zurück">
      <Icon name="back" />
    </button>
  );
}

export { relative };
