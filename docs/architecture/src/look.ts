// The palette: one hue per kind of thing, used everywhere (decide which hue means what before you
// lay out the scene, and write it next to each). ink for text, tint for fills, line for borders and
// connectors.

export type Hue = "slate" | "orange" | "rose" | "amber" | "indigo" | "teal" | "violet" | "sky" | "emerald";

export const HUES: Record<Hue, { ink: string; tint: string; line: string }> = {
  slate: { ink: "#334155", tint: "#f1f5f9", line: "#a8b3c2" },     //
  orange: { ink: "#c2410c", tint: "#fff7ed", line: "#f6a96b" },    //
  rose: { ink: "#be123c", tint: "#fff1f2", line: "#f39aab" },      //
  amber: { ink: "#a16207", tint: "#fefce8", line: "#e5c35a" },     //
  indigo: { ink: "#4338ca", tint: "#eef2ff", line: "#9aa4f5" },    //
  teal: { ink: "#0f766e", tint: "#f0fdfa", line: "#5fd3c2" },      //
  violet: { ink: "#6d28d9", tint: "#f5f3ff", line: "#b9a3f5" },    //
  sky: { ink: "#0369a1", tint: "#f0f9ff", line: "#7cc6ec" },       //
  emerald: { ink: "#047857", tint: "#ecfdf5", line: "#5fcf9f" },   //
};

export const PAGE = "#fafaf9";
export const INK = "#1c1917";
export const MUTED = "#57534e";

export const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";
export const EASE_CAMERA = "cubic-bezier(0.65, 0, 0.35, 1)";
export const CAMERA_MS = 1200;
