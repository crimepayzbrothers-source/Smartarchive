// Wischen zum Löschen: Zeile nach links wischen legt „Löschen“ frei; gelöscht wird erst nach Bestätigung.
import { useEffect, useRef, useState, type MouseEvent, type PointerEvent, type ReactNode } from "react";
import { Icon, Sheet } from "./ui";

const OPEN = 96; // Breite der freigelegten Schaltfläche (px)
const SKIP = "input, select, textarea, button, label";

export function SwipeRow({ children, onDelete, disabled, className = "", label = "Löschen", asking }: {
  children: ReactNode; onDelete: () => void; disabled?: boolean; className?: string; label?: string; asking?: boolean;
}) {
  const [dx, setDx] = useState(0);
  useEffect(() => { if (!asking) setDx(0); }, [asking]); // Rückfrage abgebrochen → Zeile schließt sich wieder
  const [drag, setDrag] = useState(false);
  const st = useRef<{ x: number; y: number; base: number; mode: "" | "h" | "v"; moved: boolean; width: number } | null>(null);
  const swallowClick = useRef(false);

  const down = (e: PointerEvent<HTMLDivElement>) => {
    swallowClick.current = false;
    if (disabled || (e.pointerType === "mouse" && e.button !== 0) || (e.target as HTMLElement).closest(SKIP)) return;
    st.current = { x: e.clientX, y: e.clientY, base: dx, mode: "", moved: false, width: e.currentTarget.offsetWidth };
  };
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const s = st.current;
    if (!s) return;
    const ddx = e.clientX - s.x, ddy = e.clientY - s.y;
    if (!s.mode) {
      if (Math.abs(ddx) < 8 && Math.abs(ddy) < 8) return;
      s.mode = Math.abs(ddx) > Math.abs(ddy) ? "h" : "v";
      if (s.mode === "v") { st.current = null; return; }
      e.currentTarget.setPointerCapture(e.pointerId);
      setDrag(true);
    }
    s.moved = true;
    setDx(Math.max(-s.width * 0.85, Math.min(0, s.base + ddx)));
  };
  const up = (e: PointerEvent<HTMLDivElement>) => {
    const s = st.current;
    st.current = null;
    setDrag(false);
    if (!s || !s.moved) return;
    swallowClick.current = true;
    const pos = Math.min(0, s.base + (e.clientX - s.x));
    if (-pos > s.width * 0.5) { setDx(-OPEN); onDelete(); }   // weit gewischt → direkt Rückfrage
    else setDx(-pos > OPEN / 2 ? -OPEN : 0);
  };
  const click = (e: MouseEvent) => {
    if (swallowClick.current) { swallowClick.current = false; e.preventDefault(); e.stopPropagation(); return; }
    if (dx !== 0 && !(e.target as HTMLElement).closest(SKIP)) { e.preventDefault(); e.stopPropagation(); setDx(0); }
  };

  return (
    <div className={`swipe-row ${dx ? "open" : ""}`}>
      {!disabled && (
        <button type="button" className="swipe-action" tabIndex={dx ? 0 : -1} aria-hidden={!dx} onClick={onDelete}>
          <Icon name="trash" size={18} /><span>{label}</span>
        </button>
      )}
      <div className={`swipe-front ${className}`} style={{ transform: dx ? `translateX(${dx}px)` : undefined, transition: drag ? "none" : undefined }}
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onClickCapture={click}>
        {children}
      </div>
    </div>
  );
}

export function ConfirmDelete({ open, title, text, confirmLabel = "Endgültig löschen", busy, onConfirm, onClose }: {
  open: boolean; title: string; text: ReactNode; confirmLabel?: string; busy?: boolean; onConfirm: () => void; onClose: () => void;
}) {
  return (
    <Sheet open={open} onClose={onClose} title={title}>
      <div className="confirm-delete">
        <div className="confirm-text">{text}</div>
        <div className="row-actions">
          <button type="button" className="secondary" onClick={onClose} autoFocus>Abbrechen</button>
          <button type="button" className="danger-btn" disabled={busy} onClick={onConfirm}><Icon name="trash" size={16} /> {confirmLabel}</button>
        </div>
      </div>
    </Sheet>
  );
}
