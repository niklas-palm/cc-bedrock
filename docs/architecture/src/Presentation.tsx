import { useCallback, useEffect, useRef, useState, type TouchEvent } from "react";
import { DETAILS } from "./details";
import { CAMERA_MS, EASE_CAMERA, EASE_OUT } from "./look";
import { CARDS, META, STEPS, type DetailId, type Rect } from "./scene";
import { Stage } from "./Stage";

const LAST = STEPS.length - 1;

// for scripts/shoot.mjs: how many steps, and which card opens which close-up from which step
(window as unknown as { __unfold: unknown }).__unfold = {
  last: LAST,
  closeups: CARDS.filter((c) => c.detail).map((c) => ({ detail: c.detail, card: c.id, step: c.detailFrom ?? c.step })),
};

function stepFromHash(): number {
  const n = parseInt(location.hash.replace("#", ""), 10);
  return Number.isFinite(n) ? Math.max(0, Math.min(LAST, n)) : 0;
}

export function Presentation() {
  const root = useRef<HTMLDivElement>(null);
  const [step, setStep] = useState(stepFromHash);
  const [open, setOpen] = useState<DetailId | null>(null);
  const [notes, setNotes] = useState(false);
  const [size, setSize] = useState({ w: window.innerWidth, h: window.innerHeight });

  useEffect(() => {
    const el = root.current!;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const go = useCallback((n: number) => {
    setOpen(null);
    setStep((s) => {
      const next = Math.max(0, Math.min(LAST, typeof n === "number" ? n : s));
      history.replaceState(null, "", `#${next}`);
      return next;
    });
  }, []);
  const next = useCallback(() => go(step + 1), [go, step]);
  const prev = useCallback(() => go(step - 1), [go, step]);

  useEffect(() => {
    const onHash = () => setStep(stepFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key;
      if (open && (k === "Escape" || k.startsWith("Arrow") || k === " " || k === "PageDown" || k === "PageUp")) { e.preventDefault(); setOpen(null); return; }
      if (k === "ArrowRight" || k === "ArrowDown" || k === " " || k === "PageDown") { e.preventDefault(); next(); }
      else if (k === "ArrowLeft" || k === "ArrowUp" || k === "PageUp") { e.preventDefault(); prev(); }
      else if (k === "Home") go(0);
      else if (k === "End") go(LAST);
      else if (k === "n" || k === "N") setNotes((v) => !v);
      else if (k === "f" || k === "F") {
        if (document.fullscreenElement) document.exitFullscreen(); else root.current?.requestFullscreen?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [next, prev, go, open]);

  // swipe
  const touch = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart = (e: TouchEvent) => { touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY }; };
  const onTouchEnd = (e: TouchEvent) => {
    const t = touch.current; touch.current = null;
    if (!t) return;
    const dx = e.changedTouches[0].clientX - t.x, dy = e.changedTouches[0].clientY - t.y;
    if (Math.abs(dx) < 50 || Math.abs(dx) < Math.abs(dy)) return;
    if (open) { setOpen(null); return; }
    if (dx < 0) next(); else prev();
  };

  // the camera
  const compact = size.w < 760;
  const INSET = compact ? { top: 112, right: 14, bottom: 58, left: 14 } : { top: 124, right: 48, bottom: 74, left: 48 };
  const frame: Rect = open ? grow(DETAILS[open].frame, 26) : STEPS[step].camera;
  const room = { w: Math.max(100, size.w - INSET.left - INSET.right), h: Math.max(100, size.h - INSET.top - INSET.bottom) };
  const scale = Math.min(room.w / frame.w, room.h / frame.h);
  const x = INSET.left + room.w / 2 - (frame.x + frame.w / 2) * scale;
  const y = INSET.top + room.h / 2 - (frame.y + frame.h / 2) * scale;

  const s = STEPS[step];
  const title = step === 0;
  const heading = open ? DETAILS[open].heading : s.heading;
  const line = open ? DETAILS[open].line : s.line;
  const noteText = open ? DETAILS[open].notes : s.notes;
  const portrait = size.h > size.w;

  return (
    <div ref={root} className="root" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
      <div className="world" style={{
        transform: `translate(${x}px, ${y}px) scale(${scale})`,
        transition: `transform ${CAMERA_MS}ms ${EASE_CAMERA}, opacity 800ms ${EASE_OUT}`,
        opacity: title ? 0 : 1,
      }}>
        <Stage step={step} open={open} onOpen={setOpen} onClose={() => setOpen(null)} />
      </div>

      <div className="edges" />

      {title ? (
        <div className="title">
          <div className="brand">{META.brand}</div>
          <h1>{s.heading}</h1>
          <p>{s.line}</p>
          <button className="start" onClick={next}>Start →</button>
          {portrait && compact && <div className="turn">Turn your phone for the full view</div>}
        </div>
      ) : (
        <header className="band" key={`${step}-${open ?? ""}`}>
          <h2>{heading}</h2>
          {line && <p>{line}</p>}
        </header>
      )}

      {notes && (
        <aside className="notes">
          <div className="notes-h">Notes · step {step} of {LAST}</div>
          <p>{noteText}</p>
        </aside>
      )}

      <nav className="bar">
        <a className="back" href="#0" onClick={(e) => { e.preventDefault(); go(0); }}>{META.title}</a>
        <div className="progress"><div style={{ width: `${(step / LAST) * 100}%` }} /></div>
        <div className="buttons">
          <button onClick={prev} disabled={step === 0} aria-label="Previous">←</button>
          <span className="count">{step} / {LAST}</span>
          <button onClick={next} disabled={step === LAST} aria-label="Next">→</button>
          <button onClick={() => setNotes((v) => !v)} className={notes ? "on" : ""} title="Notes (N)">N</button>
          <button onClick={() => (document.fullscreenElement ? document.exitFullscreen() : root.current?.requestFullscreen?.())} title="Full screen (F)">⤢</button>
        </div>
      </nav>
    </div>
  );
}

function grow(r: Rect, m: number): Rect {
  return { x: r.x - m, y: r.y - m, w: r.w + 2 * m, h: r.h + 2 * m };
}
