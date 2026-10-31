// Schlanker API-Client. Alle schreibenden Anfragen tragen den CSRF-Header.

export class ApiError extends Error {
  status: number;
  data: any;
  constructor(status: number, message: string, data: any) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

type Opts = { method?: string; body?: any; form?: FormData; signal?: AbortSignal };

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

// Laufende Anfragen zählen: die Scroll-Wiederherstellung wartet, bis alle Inhalte geladen sind
let inflight = 0;
export const pendingRequests = () => inflight;

export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
  inflight++;
  try { return await apiRaw<T>(path, opts); } finally { inflight--; }
}

async function apiRaw<T>(path: string, opts: Opts): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "SmartArchive" };
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  let res: Response;
  try {
    res = await fetch(path, { method: opts.method || (body ? "POST" : "GET"), headers, body, credentials: "same-origin", signal: opts.signal });
  } catch (e: any) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, "Keine Verbindung zum Server. Ist das VPN aktiv?", null);
  }
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : null;
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized?.();
    throw new ApiError(res.status, data?.error || `Fehler ${res.status}`, data);
  }
  return data as T;
}

export const get = <T = any>(path: string, params?: Record<string, any>) => {
  const q = params
    ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => [k, String(v)])).toString()
    : "";
  return api<T>(path + (q === "?" ? "" : q));
};
export const post = <T = any>(path: string, body?: any) => api<T>(path, { method: "POST", body: body ?? {} });
export const patch = <T = any>(path: string, body: any) => api<T>(path, { method: "PATCH", body });
export const del = <T = any>(path: string) => api<T>(path, { method: "DELETE" });
export const put = <T = any>(path: string, body: any) => api<T>(path, { method: "PUT", body });

// Upload mit Fortschrittsanzeige (fetch kennt keinen Upload-Fortschritt)
export function upload(form: FormData, onProgress: (p: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("X-Requested-With", "SmartArchive");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data: any = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* leer */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, data?.error || `Fehler ${xhr.status}`, data));
    };
    xhr.onerror = () => reject(new ApiError(0, "Upload abgebrochen – Verbindung prüfen.", null));
    xhr.send(form);
  });
}

// --- Formatierung ---------------------------------------------------------

const eur = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
export const money = (cents: number | null | undefined) => (cents === null || cents === undefined ? "–" : eur.format(cents / 100));

export function parseMoney(input: string): number | null {
  const s = input.trim().replace(/€|eur/gi, "").replace(/\s/g, "");
  if (!s) return null;
  const normalized = s.includes(",") ? s.replace(/\./g, "").replace(",", ".") : s;
  const n = Number(normalized);
  return Number.isFinite(n) ? Math.round(n * 100) : null;
}

export const centsToInput = (c: number | null | undefined) => (c === null || c === undefined ? "" : (c / 100).toFixed(2).replace(".", ","));

export function date(iso?: string | null) {
  if (!iso) return "–";
  const [y, m, d] = iso.slice(0, 10).split("-");
  return `${d}.${m}.${y}`;
}

export function relative(iso?: string | null) {
  if (!iso) return "";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "gerade eben";
  if (diff < 3600) return `vor ${Math.floor(diff / 60)} Min.`;
  if (diff < 86400) return `vor ${Math.floor(diff / 3600)} Std.`;
  if (diff < 86400 * 7) return `vor ${Math.floor(diff / 86400)} Tagen`;
  return date(iso);
}
