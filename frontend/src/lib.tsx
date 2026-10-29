import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { get } from "./api";

// --- Routing (Hash, funktioniert ohne Server-Konfiguration) ----------------

// Scrollposition je Adresse, damit die Rückkehr zu einer Liste an derselben Stelle landet.
const scrollMemo = new Map<string, number>();
const hashOf = (url: string) => { const i = url.indexOf("#"); return i < 0 ? "#/" : url.slice(i); };
export const takeScroll = (hash = window.location.hash): number | null => {
  const y = scrollMemo.get(hash || "#/");
  scrollMemo.delete(hash || "#/");
  return y ?? null;
};

// Letzte Seite außerhalb eines Dokuments: Ziel für „Zurück“, wenn kein Browser-Verlauf da ist
// (z. B. nachdem Android die App beim Öffnen einer PDF neu geladen hat).
const RETURN_KEY = "sa-return";
function rememberReturn(hash: string) {
  if (hash.startsWith("#/dokument/")) return;
  try { sessionStorage.setItem(RETURN_KEY, hash.replace(/^#/, "") || "/"); } catch { /* privat */ }
}
export function returnTarget(): string {
  try { return sessionStorage.getItem(RETURN_KEY) || "/"; } catch { return "/"; }
}

export function useRoute(): [string, URLSearchParams] {
  const read = () => {
    const raw = window.location.hash.replace(/^#/, "") || "/";
    const [path, query = ""] = raw.split("?");
    return [path, new URLSearchParams(query)] as [string, URLSearchParams];
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    rememberReturn(window.location.hash);
    const fn = (e: HashChangeEvent) => {
      scrollMemo.set(hashOf(e.oldURL), window.scrollY);
      rememberReturn(window.location.hash);
      setRoute(read());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", fn);
    return () => window.removeEventListener("hashchange", fn);
  }, []);
  return route;
}

export function go(path: string) {
  window.location.hash = path;
}

/** Ersetzt die Adresse der aktuellen Seite (z. B. Suchbegriff und Filter), ohne neuen Verlaufseintrag und ohne Neuladen. */
export function replaceRoute(path: string, params: URLSearchParams) {
  const qs = params.toString();
  const hash = `#${path}${qs ? `?${qs}` : ""}`;
  if (hash !== window.location.hash) history.replaceState(history.state, "", hash);
  rememberReturn(hash);
}

// --- Stammdaten -------------------------------------------------------------

export type Category = { id: number; area_id: number; area_key: string; parent_id: number | null; name: string; path: string; keywords: string; ai_enabled: boolean; ai_blocked: boolean; count: number; depth: number };
export type Property = { id: number; name: string; address: string; kind: string; living_area: number | null; ownership_share: string; rental_status: string; acquired_on: string | null; acquisition_cost_cents: number | null; keywords: string; notes: string; units: { id: number; name: string; living_area: number | null; rental_status: string }[] };
export type Meta = {
  areas: { id: number; key: string; name: string; ai_enabled: boolean }[];
  categories: Category[];
  properties: Property[];
  correspondents: { id: number; name: string; aliases: string }[];
  tags: { id: number; name: string; count: number }[];
  expense_categories: { key: string; label: string; deductible: boolean }[];
  settings: { auto_file: boolean; auto_threshold: number; suggest_threshold: number; ai_enabled: boolean };
  ai_available: boolean;
  ai_model: string | null;
};

const MetaCtx = createContext<{ meta: Meta | null; reload: () => Promise<void> }>({ meta: null, reload: async () => {} });

export function MetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const reload = useCallback(async () => setMeta(await get<Meta>("/api/meta")), []);
  useEffect(() => { reload().catch(() => {}); }, [reload]);
  return <MetaCtx.Provider value={{ meta, reload }}>{children}</MetaCtx.Provider>;
}

export const useMeta = () => useContext(MetaCtx);

export function areaOf(meta: Meta | null, key: string) {
  return meta?.areas.find((a) => a.key === key);
}

// --- Daten laden ------------------------------------------------------------

export function useLoad<T>(fn: () => Promise<T>, deps: any[]): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn().then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(e.message || String(e)); })
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1) };
}

export function usePoll(active: boolean, fn: () => void, ms = 2500) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => ref.current(), ms);
    return () => window.clearInterval(id);
  }, [active, ms]);
}

// --- Hinweise (Toast) -------------------------------------------------------

type Toast = { id: number; text: string; kind: "ok" | "error"; action?: { label: string; run: () => void } };
const ToastCtx = createContext<(t: Omit<Toast, "id">) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((ts) => [...ts, { ...t, id }]);
    window.setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), t.action ? 7000 : 3800);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            <span>{t.text}</span>
            {t.action && <button className="link" onClick={() => { t.action!.run(); setToasts((ts) => ts.filter((x) => x.id !== t.id)); }}>{t.action.label}</button>}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

// --- Theme ------------------------------------------------------------------

export function applyTheme(theme: string) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
  try { localStorage.setItem("sa-theme", theme); } catch { /* privat */ }
}

export function storedTheme() {
  try { return localStorage.getItem("sa-theme") || "system"; } catch { return "system"; }
}
