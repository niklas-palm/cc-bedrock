import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { CARDS, CONNECTORS, REGIONS, STEPS, WORLD, changedAt, look, rectAt, shown, type Card, type Connector, type DetailId } from "./scene";
import { DETAILS } from "./details";
import { IconGlyph } from "./icons";
import { CAMERA_MS, EASE_CAMERA, EASE_OUT, HUES, PAGE, type Hue } from "./look";

const LAST = STEPS.length - 1;
const MOVE = `left ${CAMERA_MS}ms ${EASE_CAMERA}, top ${CAMERA_MS}ms ${EASE_CAMERA}, width ${CAMERA_MS}ms ${EASE_CAMERA}, height ${CAMERA_MS}ms ${EASE_CAMERA}`;

function appear(visible: boolean, delay = 150): CSSProperties {
  return {
    opacity: visible ? 1 : 0,
    transform: visible ? "none" : "translateY(10px) scale(0.98)",
    pointerEvents: visible ? "auto" : "none",
    transition: `opacity 700ms ${EASE_OUT} ${visible ? delay : 0}ms, transform 700ms ${EASE_OUT} ${visible ? delay : 0}ms, ${MOVE}, box-shadow 600ms ${EASE_OUT}`,
  };
}

function halo(hue: Hue): string {
  const h = HUES[hue];
  return `0 0 0 4px ${h.tint}, 0 0 0 5.5px ${h.line}, 0 10px 34px -6px ${h.line}`;
}

// --- connectors -------------------------------------------------------------------------------

function pathOf(points: [number, number][], r = 12): string {
  let d = `M ${points[0][0]} ${points[0][1]}`;
  for (let i = 1; i < points.length; i++) {
    const [x, y] = points[i];
    if (i === points.length - 1) { d += ` L ${x} ${y}`; break; }
    const [px, py] = points[i - 1];
    const [nx, ny] = points[i + 1];
    const inLen = Math.hypot(x - px, y - py), outLen = Math.hypot(nx - x, ny - y);
    const k = Math.min(r, inLen / 2, outLen / 2);
    const ax = x - ((x - px) / inLen) * k, ay = y - ((y - py) / inLen) * k;
    const bx = x + ((nx - x) / outLen) * k, by = y + ((ny - y) / outLen) * k;
    d += ` L ${ax} ${ay} Q ${x} ${y} ${bx} ${by}`;
  }
  return d;
}

function Wire({ c, step }: { c: Connector; step: number }) {
  const visible = shown(c, step);
  const h = HUES[c.hue];
  const d = pathOf(c.points);
  const arriving = visible && c.step === step && step !== LAST && c.flows !== false;
  const mid = c.labelAt ?? [(c.points[0][0] + c.points[c.points.length - 1][0]) / 2, (c.points[0][1] + c.points[c.points.length - 1][1]) / 2 - 8];
  return (
    <g style={{ opacity: visible ? 1 : 0, transition: `opacity ${visible ? 400 : 500}ms ease ${visible ? 450 : 0}ms` }}>
      <path d={d} fill="none" stroke={h.line} strokeWidth={1.8} strokeLinecap="round"
        markerEnd={`url(#arrow-${c.hue})`} markerStart={c.both ? `url(#arrow-${c.hue})` : undefined}
        {...(c.dashed
          ? { strokeDasharray: "5 5" }
          : { pathLength: 1, strokeDasharray: "1 1", style: { strokeDashoffset: visible ? 0 : 1, transition: `stroke-dashoffset 900ms ${EASE_OUT} 450ms` } })} />
      {arriving && (
        <circle r={3.6} fill={h.ink}>
          <animateMotion dur="1.8s" begin="1.2s" repeatCount="indefinite" path={d} keyPoints={c.both ? "0;1;0" : "0;1"} keyTimes={c.both ? "0;0.5;1" : "0;1"} calcMode="linear" />
        </circle>
      )}
      {c.label && (
        <text x={mid[0]} y={mid[1]} textAnchor={c.labelAnchor ?? "middle"} fontSize={12.5} fontWeight={600} fill={h.ink}
          stroke={PAGE} strokeWidth={5} paintOrder="stroke" style={{ opacity: visible ? 1 : 0, transition: `opacity 500ms ease ${visible ? 1100 : 0}ms` }}>
          {c.label}
        </text>
      )}
    </g>
  );
}

// --- cards ------------------------------------------------------------------------------------

function CardBox({ card, step, onOpen }: { card: Card; step: number; onOpen: (d: DetailId) => void }) {
  const visible = shown(card, step);
  const r = rectAt(card, step);
  const v = look(card, step);
  const h = HUES[v.hue];
  const isNew = visible && changedAt(card, step) === step;
  const opens = card.detail && visible && step >= (card.detailFrom ?? card.step) ? card.detail : undefined;

  if (card.container) {
    return (
      <div style={{
        position: "absolute", left: r.x, top: r.y, width: r.w, height: r.h, borderRadius: 18,
        border: `1.5px solid ${h.line}`, background: `${h.tint}cc`, ...appear(visible, 0),
      }}>
        <div style={{ position: "absolute", left: 16, [card.labelBottom ? "bottom" : "top"]: 10, fontSize: 13, fontWeight: 650, color: h.ink, letterSpacing: 0.1 }}>
          {card.label}
        </div>
      </div>
    );
  }

  return (
    <div
      data-id={card.id}
      onClick={opens ? (e) => { e.stopPropagation(); onOpen(opens); } : undefined}
      className={opens ? "card opens" : "card"}
      style={{
        position: "absolute", left: r.x, top: r.y, width: r.w, height: r.h, borderRadius: card.small ? 10 : 14,
        background: "#fff", border: `1px solid ${h.line}`, overflow: "hidden",
        boxShadow: isNew ? halo(v.hue) : "0 1px 2px rgba(28,25,23,0.06), 0 4px 14px -6px rgba(28,25,23,0.12)",
        cursor: opens ? "pointer" : "default", ...appear(visible),
      }}>
      <div style={{ height: 3, background: h.ink, opacity: 0.85 }} />
      {card.chip ? (
        <div style={{ height: "calc(100% - 3px)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--mono)", fontSize: 11.5, fontWeight: 600, color: "#1c1917" }}>{v.label}</div>
      ) : card.big ? (
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, padding: "12px 18px" }}>
          <div style={{ fontSize: 40, fontWeight: 750, color: h.ink, letterSpacing: -1 }}>{v.label}</div>
          <div style={{ fontSize: 16, fontWeight: 550, color: "#44403c" }}>{v.sub}</div>
        </div>
      ) : (
        <div style={{ padding: card.small ? "6px 10px" : "10px 14px", height: "calc(100% - 3px)", display: "flex", flexDirection: "column", justifyContent: card.small ? "center" : "flex-start", gap: 3 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0 }}>
            {v.icon && <IconGlyph icon={v.icon} color={h.ink} size={card.small ? 14 : 16} />}
            <div style={{
              fontSize: card.small ? (v.tool ? 12.5 : 13) : 15.5, fontWeight: card.small && v.tool ? 600 : 650, color: "#1c1917",
              fontFamily: v.tool ? "var(--mono)" : undefined, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}>{v.label}</div>
          </div>
          {v.sub && <div style={{ fontSize: card.small ? 11.5 : 12.5, color: "#57534e", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", paddingLeft: v.icon ? 22 : 0 }}>{v.sub}</div>}
          {card.items && (
            <div style={{ display: "grid", gridTemplateColumns: card.columns === 2 ? "1fr 1fr" : "1fr", gap: "2px 14px", marginTop: 4, paddingLeft: v.icon ? 22 : 0 }}>
              {card.items.map((it) => (
                <div key={it} style={{ fontSize: 12.5, color: "#44403c", display: "flex", gap: 6, alignItems: "baseline" }}>
                  <span style={{ width: 4, height: 4, borderRadius: 2, background: h.line, flex: "none", transform: "translateY(-2px)" }} />{it}
                </div>
              ))}
            </div>
          )}
          {card.code && <code style={{ fontSize: 11.5, color: h.ink }}>{card.code}</code>}
          {opens && <div className={card.small ? "opens-hint small" : "opens-hint"} style={{ color: h.ink }}>{card.small ? "↗" : "open ↗"}</div>}
        </div>
      )}
    </div>
  );
}

// --- the close-up panel -----------------------------------------------------------------------

function Panel({ id, step, onClose }: { id: DetailId; step: number; onClose: () => void }) {
  const detail = DETAILS[id];
  const card = CARDS.find((c) => c.detail === id)!;
  const from = rectAt(card, step);
  const h = HUES[detail.hue];
  return (
    <PanelGrow from={from} to={detail.frame} hueLine={h.line} onClose={onClose}>
      <div style={{ height: 4, background: h.ink }} />
      <div className="panel-body">
        <detail.Body />
      </div>
    </PanelGrow>
  );
}

function PanelGrow({ from, to, hueLine, onClose, children }: { from: { x: number; y: number; w: number; h: number }; to: { x: number; y: number; w: number; h: number }; hueLine: string; onClose: () => void; children: ReactNode }) {
  const [grown, setGrown] = useState(false);
  useEffect(() => { const t = requestAnimationFrame(() => requestAnimationFrame(() => setGrown(true))); return () => cancelAnimationFrame(t); }, []);
  const r = grown ? to : from;
  return (
    <div onClick={(e) => e.stopPropagation()} style={{
      position: "absolute", left: r.x, top: r.y, width: r.w, height: r.h, background: "#fff", borderRadius: 20,
      border: `1px solid ${hueLine}`, boxShadow: "0 30px 80px -20px rgba(28,25,23,0.35)", overflow: "hidden", zIndex: 20,
      transition: `left 900ms ${EASE_CAMERA}, top 900ms ${EASE_CAMERA}, width 900ms ${EASE_CAMERA}, height 900ms ${EASE_CAMERA}`,
    }}>
      <button className="panel-close" onClick={onClose} aria-label="Close">×</button>
      {children}
    </div>
  );
}

// --- the world --------------------------------------------------------------------------------

export function Stage({ step, open, onOpen, onClose }: { step: number; open: DetailId | null; onOpen: (d: DetailId) => void; onClose: () => void }) {
  const hues = Object.keys(HUES) as Hue[];
  return (
    <div style={{ position: "relative", width: WORLD.w, height: WORLD.h }} onClick={open ? onClose : undefined}>
      {REGIONS.map((g) => {
        const visible = shown(g, step);
        const r = rectAt(g, step);
        const h = HUES[g.hue];
        return (
          <div key={g.id} style={{
            position: "absolute", left: r.x, top: r.y, width: r.w, height: r.h, borderRadius: 26,
            border: `1.6px dashed ${h.line}`, background: `${h.tint}80`, ...appear(visible, 0),
          }}>
            <div style={{ position: "absolute", left: 18, top: 10, fontSize: 13.5, fontWeight: 650, color: h.ink }}>{g.label}</div>
          </div>
        );
      })}
      {CARDS.filter((c) => c.container).map((c) => <CardBox key={c.id} card={c} step={step} onOpen={onOpen} />)}
      <svg width={WORLD.w} height={WORLD.h} style={{ position: "absolute", inset: 0, overflow: "visible", pointerEvents: "none" }}>
        <defs>
          {hues.map((hue) => (
            <marker key={hue} id={`arrow-${hue}`} viewBox="0 0 10 10" refX={8.6} refY={5} markerWidth={7.5} markerHeight={7.5} orient="auto-start-reverse">
              <path d="M 0 1 L 9 5 L 0 9 z" fill={HUES[hue].line} />
            </marker>
          ))}
        </defs>
        {CONNECTORS.map((c) => <Wire key={c.id} c={c} step={step} />)}
      </svg>
      {CARDS.filter((c) => !c.container).map((c) => <CardBox key={c.id} card={c} step={step} onOpen={onOpen} />)}
      <div style={{
        position: "absolute", left: -4000, top: -4000, width: WORLD.w + 8000, height: WORLD.h + 8000, background: PAGE,
        opacity: open ? 0.84 : 0, pointerEvents: open ? "auto" : "none", transition: `opacity 700ms ${EASE_OUT}`, zIndex: 10,
      }} />
      {open && <Panel key={open} id={open} step={step} onClose={onClose} />}
    </div>
  );
}
