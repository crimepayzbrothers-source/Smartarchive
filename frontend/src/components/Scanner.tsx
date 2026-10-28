// Scanner nach der Aufnahme: Ecken prüfen/verschieben, entzerren, Filter wählen, drehen.
import { useEffect, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { applyFilter, detectQuad, loadBitmap, rotate, toJpeg, warp, type Filter, type Quad } from "../scan";
import { Icon, Loading } from "./ui";

const FILTERS: [Filter, string][] = [["dokument", "Dokument"], ["farbe", "Farbe"], ["original", "Original"]];

function stamp() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}.${p(d.getMinutes())}.${p(d.getSeconds())}`;
}

export default function Scanner({ file, onDone, onSkip, onCancel }: {
  file: File | null; onDone: (scan: File, raw: File) => void; onSkip: (raw: File) => void; onCancel: () => void;
}) {
  const dlg = useRef<HTMLDialogElement>(null);
  const view = useRef<HTMLCanvasElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const warped = useRef<HTMLCanvasElement | null>(null);
  const [bmp, setBmp] = useState<ImageBitmap | null>(null);
  const [quad, setQuad] = useState<Quad | null>(null);
  const [found, setFound] = useState(false);
  const [step, setStep] = useState<"ecken" | "ergebnis">("ecken");
  const [filter, setFilter] = useState<Filter>("dokument");
  const [turns, setTurns] = useState(0);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const drag = useRef<number | null>(null);

  // öffnen/schließen
  useEffect(() => {
    const d = dlg.current;
    if (!d) return;
    if (file && !d.open) d.showModal();
    if (!file && d.open) d.close();
  }, [file]);

  // Bild laden und Kanten suchen
  useEffect(() => {
    setBmp(null); setQuad(null); setStep("ecken"); setFilter("dokument"); setTurns(0); setPreview(null); setError(null);
    warped.current = null;
    if (!file) return;
    let alive = true;
    loadBitmap(file).then((b) => {
      if (!alive) return;
      const r = detectQuad(b);
      setBmp(b); setQuad(r.quad); setFound(r.found);
    }).catch(() => alive && setError("Das Foto konnte nicht geöffnet werden."));
    return () => { alive = false; };
  }, [file]);

  // Vorschau des Fotos zeichnen
  useEffect(() => {
    const c = view.current;
    if (!c || !bmp || step !== "ecken") return;
    const s = Math.min(1, 1400 / Math.max(bmp.width, bmp.height));
    c.width = Math.round(bmp.width * s);
    c.height = Math.round(bmp.height * s);
    c.getContext("2d")!.drawImage(bmp, 0, 0, c.width, c.height);
  }, [bmp, step]);

  // Ergebnis berechnen (Filter/Drehung aus dem zwischengespeicherten Zuschnitt)
  useEffect(() => {
    if (step !== "ergebnis" || !bmp || !quad) return;
    let alive = true;
    setBusy(true);
    const t = window.setTimeout(() => {
      try {
        if (!warped.current) warped.current = warp(bmp, quad);
        const out = rotate(applyFilter(warped.current, filter), turns);
        out.toBlob((b) => {
          if (!alive || !b) return;
          setPreview((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(b); });
          setBusy(false);
        }, "image/jpeg", 0.8);
      } catch {
        if (alive) { setError("Zuschnitt fehlgeschlagen."); setBusy(false); }
      }
    }, 30);
    return () => { alive = false; window.clearTimeout(t); };
  }, [step, filter, turns, bmp, quad]);

  const toImage = (e: RPointerEvent) => {
    const r = svg.current!.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(bmp!.width, ((e.clientX - r.left) / r.width) * bmp!.width)),
      y: Math.max(0, Math.min(bmp!.height, ((e.clientY - r.top) / r.height) * bmp!.height)),
    };
  };
  const onDown = (i: number) => (e: RPointerEvent<SVGCircleElement>) => {
    drag.current = i;
    (e.target as Element).setPointerCapture(e.pointerId);
    e.preventDefault();
  };
  const onMove = (e: RPointerEvent) => {
    if (drag.current === null || !quad) return;
    const q = [...quad] as Quad;
    q[drag.current] = toImage(e);
    setQuad(q);
  };
  const onUp = () => { drag.current = null; };

  const toResult = () => { warped.current = null; setStep("ergebnis"); };
  const whole = () => bmp && setQuad([{ x: 0, y: 0 }, { x: bmp.width, y: 0 }, { x: bmp.width, y: bmp.height }, { x: 0, y: bmp.height }]);

  const finish = async () => {
    if (!bmp || !quad || !file) return;
    setBusy(true);
    try {
      if (!warped.current) warped.current = warp(bmp, quad);
      const out = rotate(applyFilter(warped.current, filter), turns);
      const ts = stamp();
      const scan = await toJpeg(out, `Scan ${ts}.jpg`);
      const raw = new File([file], `Foto ${ts}${/\.\w+$/.exec(file.name)?.[0] || ".jpg"}`, { type: file.type || "image/jpeg" });
      onDone(scan, raw);
    } catch (e: any) {
      setError(e?.message || "Speichern fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  };

  const r = bmp ? Math.max(bmp.width, bmp.height) * 0.022 : 10;
  const pts = quad?.map((p) => `${p.x},${p.y}`).join(" ");

  return (
    <dialog ref={dlg} className="scanner" onClose={(e) => e.stopPropagation()}
      onCancel={(e) => { e.preventDefault(); e.stopPropagation(); onCancel(); }}>
      <header className="scanner-head">
        <button className="icon-btn" onClick={onCancel} aria-label="Abbrechen"><Icon name="close" /></button>
        <h2>{step === "ecken" ? "Ecken prüfen" : "Scan"}</h2>
        <span className="scanner-spacer" />
      </header>

      {error && <p className="notice error">{error}</p>}

      {step === "ecken" && (
        <>
          <div className="scanner-stage">
            {!bmp ? <Loading /> : (
              <div className="scanner-frame">
                <canvas ref={view} />
                <svg ref={svg} viewBox={`0 0 ${bmp.width} ${bmp.height}`} preserveAspectRatio="none"
                  onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
                  {quad && (
                    <>
                      <path className="scan-shade" fillRule="evenodd"
                        d={`M0 0H${bmp.width}V${bmp.height}H0Z M${quad.map((p) => `${p.x} ${p.y}`).join(" L")}Z`} />
                      <polygon className="scan-edge" points={pts} style={{ strokeWidth: r * 0.25 }} />
                      {quad.map((p, i) => (
                        <g key={i}>
                          <circle className="scan-handle-hit" cx={p.x} cy={p.y} r={r * 2.4} onPointerDown={onDown(i)} />
                          <circle className="scan-handle" cx={p.x} cy={p.y} r={r} style={{ strokeWidth: r * 0.3 }} />
                        </g>
                      ))}
                    </>
                  )}
                </svg>
              </div>
            )}
          </div>
          <p className="hint scanner-hint">
            {found ? "Dokument erkannt. Ecken bei Bedarf mit dem Finger verschieben." : "Keine klaren Kanten gefunden – bitte die Ecken auf das Blatt ziehen."}
          </p>
          <div className="scanner-actions">
            <button className="ghost" onClick={() => file && onSkip(file)}>Ohne Zuschnitt</button>
            <button className="secondary" onClick={whole} disabled={!bmp}>Ganzes Foto</button>
            <button className="primary" onClick={toResult} disabled={!quad}>Weiter</button>
          </div>
        </>
      )}

      {step === "ergebnis" && (
        <>
          <div className="scanner-stage">
            {preview ? <img className="scanner-result" src={preview} alt="Zugeschnittener Scan" /> : <Loading />}
            {busy && preview && <div className="scanner-busy"><Loading /></div>}
          </div>
          <fieldset className="segmented small scanner-filters" aria-label="Darstellung">
            {FILTERS.map(([k, label]) => (
              <label key={k} className={filter === k ? "on" : ""}>
                <input type="radio" name="scan-filter" checked={filter === k} onChange={() => setFilter(k)} /> {label}
              </label>
            ))}
          </fieldset>
          <div className="scanner-actions">
            <button className="ghost" onClick={() => setStep("ecken")}>Ecken</button>
            <button className="secondary" onClick={() => setTurns((t) => t + 1)} aria-label="Um 90 Grad drehen"><Icon name="rotate" size={18} /> Drehen</button>
            <button className="primary" onClick={finish} disabled={busy}><Icon name="check" size={18} /> Fertig</button>
          </div>
        </>
      )}
    </dialog>
  );
}
