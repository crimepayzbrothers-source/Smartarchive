import { useEffect, useRef, useState } from "react";
import { upload } from "../api";
import { go, useToast } from "../lib";
import { Icon, Sheet } from "./ui";

type Picked = { file: File; url: string | null };

export default function UploadSheet({ open, onClose, defaultArea = "auto", onDone, initialFiles }: {
  open: boolean; onClose: () => void; defaultArea?: string; onDone?: () => void; initialFiles?: File[];
}) {
  const [area, setArea] = useState(defaultArea);
  const [items, setItems] = useState<Picked[]>([]);
  const [combine, setCombine] = useState(true);
  const [progress, setProgress] = useState<number | null>(null);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const cameraRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  useEffect(() => {
    if (open) {
      setArea(defaultArea); setResult(null); setError(null); setProgress(null);
      setItems((initialFiles || []).map((file) => ({ file, url: file.type.startsWith("image/") ? URL.createObjectURL(file) : null })));
    }
  }, [open, defaultArea, initialFiles]);

  const add = (files: FileList | File[] | null) => {
    if (!files) return;
    const next = Array.from(files).map((file) => ({ file, url: file.type.startsWith("image/") ? URL.createObjectURL(file) : null }));
    setItems((cur) => [...cur, ...next]);
    setResult(null);
  };
  const images = items.filter((i) => i.file.type.startsWith("image/"));

  const send = async () => {
    if (!items.length) return;
    const form = new FormData();
    form.append("area", area);
    form.append("combine", combine && images.length > 1 ? "1" : "0");
    items.forEach((i) => form.append("files", i.file, i.file.name || "scan.jpg"));
    setProgress(0);
    setError(null);
    try {
      const res = await upload(form, setProgress);
      setResult(res);
      setItems([]);
      onDone?.();
      if (res.created.length && !res.duplicates.length && !res.rejected.length) {
        toast({ kind: "ok", text: res.created.length === 1 ? "Hochgeladen – Texterkennung läuft …" : `${res.created.length} Dokumente hochgeladen.` });
        onClose();
        go(res.created.length === 1 ? `/dokument/${res.created[0]}` : "/eingang");
      }
    } catch (e: any) {
      setError(e.message);
    } finally {
      setProgress(null);
    }
  };

  return (
    <Sheet open={open} onClose={onClose} title="Dokument erfassen">
      <div className="upload">
        <fieldset className="segmented" aria-label="Bereich">
          {[["auto", "Automatisch"], ["privat", "Privat"], ["vermietung", "Vermietung"]].map(([key, label]) => (
            <label key={key} className={area === key ? "on" : ""}>
              <input type="radio" name="area" value={key} checked={area === key} onChange={() => setArea(key)} />
              {label}
            </label>
          ))}
        </fieldset>
        <p className="hint">
          {area === "auto"
            ? "Die App schlägt den Bereich vor; Sie bestätigen ihn im Eingang."
            : `Sichere Treffer werden direkt in „${area === "privat" ? "Privat" : "Vermietung"}“ abgelegt.`}
        </p>

        <div className={`drop ${drag ? "drag" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}>
          <button className="big-action" onClick={() => cameraRef.current?.click()}>
            <Icon name="camera" size={28} /> <span>Foto aufnehmen</span>
          </button>
          <button className="big-action secondary" onClick={() => fileRef.current?.click()}>
            <Icon name="file" size={26} /> <span>Dateien wählen</span>
          </button>
          <p className="drop-hint">oder Dateien hierher ziehen</p>
          <input ref={cameraRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
          <input ref={fileRef} type="file" accept="application/pdf,image/*,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp,.rtf,.csv,.txt" multiple hidden onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        </div>

        {items.length > 0 && (
          <>
            <ul className="picked">
              {items.map((i, idx) => (
                <li key={idx}>
                  {i.url ? <img src={i.url} alt="" /> : <span className="pdf-chip"><Icon name="file" size={20} /></span>}
                  <span className="picked-name">{i.file.name || `Foto ${idx + 1}`}</span>
                  <button className="icon-btn" aria-label="Entfernen" onClick={() => setItems(items.filter((_, j) => j !== idx))}><Icon name="close" size={18} /></button>
                </li>
              ))}
            </ul>
            {images.length > 1 && (
              <label className="check">
                <input type="checkbox" checked={combine} onChange={(e) => setCombine(e.target.checked)} />
                {images.length} Fotos als ein Dokument ablegen (mehrseitiger Beleg)
              </label>
            )}
            {images.length === 1 && <p className="hint">Weitere Seite? Einfach noch ein Foto aufnehmen.</p>}
          </>
        )}

        {error && <p className="notice error">{error}</p>}
        {result && (result.duplicates.length > 0 || result.rejected.length > 0) && (
          <div className="notice">
            {result.created.length > 0 && <p>{result.created.length} Dokument(e) hochgeladen.</p>}
            {result.duplicates.map((d: any) => (
              <p key={d.document_id}>„{d.name}“ ist schon vorhanden: <a href={`#/dokument/${d.document_id}`} onClick={onClose}>{d.title}</a></p>
            ))}
            {result.rejected.map((r: any) => <p key={r.name}>„{r.name}“: {r.reason}</p>)}
          </div>
        )}

        <button className="primary block" disabled={!items.length || progress !== null} onClick={send}>
          {progress !== null ? `Lädt hoch … ${Math.round(progress * 100)} %` : items.length ? `${items.length} Datei${items.length > 1 ? "en" : ""} hochladen` : "Hochladen"}
        </button>
      </div>
    </Sheet>
  );
}
