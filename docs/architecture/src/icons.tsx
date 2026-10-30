import type { Icon } from "./scene";

// Small line icons, 16 × 16, drawn in the card's ink colour.
const PATHS: Record<Icon, string> = {
  lambda: "M3.6 2.6 C5.3 2.4 6.3 3.3 7.1 4.9 L12.4 13.4 M8.5 7.4 L4 13.4",
  choice: "M8 2 L14 8 L8 14 L2 8 Z",
  ai: "M8 2 L9.3 6.7 L14 8 L9.3 9.3 L8 14 L6.7 9.3 L2 8 L6.7 6.7 Z",
  person: "M8 7.5 A2.6 2.6 0 1 0 8 2.3 A2.6 2.6 0 1 0 8 7.5 M3 14 C3 10.8 5.2 9.3 8 9.3 C10.8 9.3 13 10.8 13 14",
  machine: "M2.5 3.5 H13.5 V12.5 H2.5 Z M5 6.5 H8 M5 9.5 H11 M10 6.5 H11",
  file: "M4 2 H9.5 L12.5 5 V14 H4 Z M9.5 2 V5 H12.5 M6 8.5 H10.5 M6 11 H10.5",
  db: "M3 4 C3 2.3 13 2.3 13 4 V12 C13 13.7 3 13.7 3 12 Z M3 4 C3 5.7 13 5.7 13 4 M3 8 C3 9.7 13 9.7 13 8",
  agent: "M8 1.8 L9.2 5.6 L13 6.6 L9.9 9 L10.9 13 L8 10.8 L5.1 13 L6.1 9 L3 6.6 L6.8 5.6 Z",
  tool: "M2.5 4 H13.5 V12 H2.5 Z M4.5 7 L6.5 8.5 L4.5 10 M8 10 H11",
  brain: "M6 3 C3.8 3 2.8 4.8 3.2 6.4 C2 7.4 2.2 9.6 3.6 10.3 C3.8 12.4 5.6 13.4 7.6 12.8 V3.4 C7.2 3.1 6.6 3 6 3 Z M10 3 C12.2 3 13.2 4.8 12.8 6.4 C14 7.4 13.8 9.6 12.4 10.3 C12.2 12.4 10.4 13.4 8.4 12.8 V3.4 C8.8 3.1 9.4 3 10 3 Z",
  queue: "M2.5 4 H13.5 M2.5 8 H13.5 M2.5 12 H13.5 M11 2.5 L13.5 4 L11 5.5",
  test: "M5.5 2 H10.5 M6.5 2 V6 L3 13 C2.6 13.8 3.1 14 3.8 14 H12.2 C12.9 14 13.4 13.8 13 13 L9.5 6 V2 M5 10 H11",
  pr: "M4.5 4.5 V11.5 M4.5 2.6 A1.9 1.9 0 1 0 4.5 6.4 M11.5 11.5 V7 C11.5 5.5 10.5 4.5 9 4.5 H7 M8.5 3 L7 4.5 L8.5 6 M4.5 13.4 A1.9 1.9 0 1 0 4.5 9.6 M11.5 13.4 A1.9 1.9 0 1 0 11.5 9.6",
  image: "M8 1.8 L13.5 4.8 V11.2 L8 14.2 L2.5 11.2 V4.8 Z M2.5 4.8 L8 7.8 L13.5 4.8 M8 7.8 V14.2",
  vm: "M2.5 3 H13.5 V10.5 H2.5 Z M6 13.5 H10 M8 10.5 V13.5",
  check: "M3 8.5 L6.5 12 L13 4.5",
  wrench: "M10.5 2.2 A3.6 3.6 0 0 0 8.4 7 L2.6 12.8 L3.2 13.4 L9 7.6 A3.6 3.6 0 0 0 13.8 5.5 L11.6 7 L9.8 6.2 L9 4.4 Z",
  globe: "M8 1.8 A6.2 6.2 0 1 0 8 14.2 A6.2 6.2 0 1 0 8 1.8 M1.8 8 H14.2 M8 1.8 C5.8 4 5.8 12 8 14.2 M8 1.8 C10.2 4 10.2 12 8 14.2",
};

export function IconGlyph({ icon, color, size = 15 }: { icon: Icon; color: string; size?: number }) {
  const filled = icon === "agent" || icon === "ai";
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" style={{ flex: "none" }} aria-hidden>
      <path d={PATHS[icon]} fill={filled ? color : "none"} fillOpacity={filled ? 0.18 : 0} stroke={color}
        strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
