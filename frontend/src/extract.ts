// „Aus Beleg auslesen“: Vorschläge vom Server holen und leere Felder füllen.
import { centsToInput, get } from "./api";

export type Extracted = {
  text_available: boolean; amount_cents?: number | null; amount_source?: "label" | "ai" | "largest" | null;
  amount_evidence?: string | null; invoice_date?: string | null; date_evidence?: string | null; paid_on?: string | null;
  paid_assumed?: boolean; payee?: string; category_key?: string | null; category_reason?: string | null; note?: string;
};

export const fetchExtract = (docId: number) => get<Extracted>(`/api/documents/${docId}/extract`);

/** Füllt nur leere Felder (overwrite=true: alle erkannten Felder). Liefert die Namen der geänderten Felder. */
export function applyExtract<T extends Record<string, any>>(form: T, x: Extracted, overwrite = false): { form: T; changed: string[] } {
  const next: Record<string, any> = { ...form };
  const changed: string[] = [];
  const put = (key: string, value: any, label: string) => {
    if (value === null || value === undefined || value === "") return;
    if (!overwrite && next[key] !== undefined && next[key] !== null && String(next[key]).trim() !== "") return;
    if (String(next[key] ?? "") === String(value)) return;
    next[key] = value;
    changed.push(label);
  };
  put("amount", x.amount_cents ? centsToInput(x.amount_cents) : null, "Betrag");
  put("invoice_date", x.invoice_date, "Rechnungsdatum");
  if (!x.paid_assumed) put("paid_on", x.paid_on, "Zahlungsdatum");  // angenommenes Datum setzt der Server (markiert)
  put("payee", x.payee, "Aussteller");
  put("category_key", x.category_key, "Kategorie");
  put("note", x.note, "Notiz");
  return { form: next as T, changed };
}

export function extractHint(x: Extracted | null): string | null {
  if (!x) return null;
  if (!x.text_available) return "Im Beleg wurde kein Text erkannt. Bitte „Texterkennung wiederholen“ oder Werte von Hand eintragen.";
  const parts: string[] = [];
  if (x.amount_source === "largest") parts.push(`Betrag ohne Beschriftung – größter Betrag im Beleg („${x.amount_evidence}“), bitte prüfen`);
  else if (x.amount_source === "ai") parts.push("Betrag von der KI erkannt und im Beleg gefunden");
  else if (x.amount_evidence) parts.push(`Betrag aus „${x.amount_evidence}“`);
  if (!x.amount_cents) parts.push("kein Betrag gefunden");
  if (!x.invoice_date) parts.push("kein Datum gefunden");
  else if (x.paid_assumed) parts.push("Zahlungsdatum = Rechnungsdatum angenommen");
  return parts.join(" · ");
}
