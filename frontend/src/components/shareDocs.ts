// Dokumente als Dateien laden und über das System-Menü teilen (Web Share API, auch mehrere Dateien auf einmal).

function nameFrom(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*=(?:UTF-8|utf-8)''([^;]+)/.exec(header);
  if (star) { try { return decodeURIComponent(star[1].trim()); } catch { /* weiter */ } }
  const plain = /filename="?([^";]+)"?/.exec(header);
  return plain ? plain[1].trim() : fallback;
}

export async function fetchDocFile(id: number, title?: string): Promise<File> {
  const res = await fetch(`/api/documents/${id}/file`, { credentials: "same-origin" });
  if (!res.ok) throw new Error(res.status === 401 ? "Bitte neu anmelden." : `Datei nicht abrufbar (Fehler ${res.status}).`);
  const blob = await res.blob();
  const name = nameFrom(res.headers.get("content-disposition"), `${(title || `Dokument ${id}`).replace(/[\\/:*?"<>|]+/g, " ").trim()}.pdf`);
  return new File([blob], name, { type: blob.type || "application/pdf" });
}

export const canShareFiles = (files?: File[]) =>
  typeof navigator !== "undefined" && typeof navigator.canShare === "function" &&
  (files ? navigator.canShare({ files }) : true);

/** Teilt die Dateien. "aborted" = im Menü abgebrochen; "again" = Browser verlangt einen neuen Fingertipp (Laden dauerte zu lange). */
export async function shareFiles(files: File[], title: string): Promise<"ok" | "aborted" | "again"> {
  try {
    await navigator.share({ files, title });
    return "ok";
  } catch (e: any) {
    if (e?.name === "AbortError") return "aborted";
    if (e?.name === "NotAllowedError") return "again";
    throw e;
  }
}
