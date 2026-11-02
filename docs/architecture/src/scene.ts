// Claude Code on Amazon Bedrock, for an organisation: how it works, as one picture.
//
// The final picture (step 2: everything at rest; step 1 adds it all):
//
//   [Identity Center]                 .--------------------- The Bedrock account ---------------------.
//         | sign in                   |                                       [IAM denies] <------.    |
//   [Claude Code] ---- every call, as you ----> [Amazon Bedrock] --- cost per person ---> [The bill] |  |
//         |                           |              ^ refused                              | daily   |  |
//         '<-- telemetry, tags ----->|  [Ingest λ] --> [Firehose] --- Parquet ---> [S3]    v         |  |
//                                     |                                              |              |  |
//   [Browser] -- usage, controls ---> |  [Dashboard] ------- SQL -----------------> [Athena]          |  |
//         | sign in                   |        '---- budgets, models ----> [Enforcer λ] <-- spend     |  |
//   [Your IdP]                        |                                          '--------------------'  |
//                                     '-------------------------------------------------------------------'
//
// Hues: people sky · identity indigo · inference violet · money amber · telemetry teal · queries emerald ·
// controls rose.

import type { Hue } from "./look";

export const META = {
  title: "Claude Code on Amazon Bedrock",
  brand: "cc-bedrock · how it works",
};

export const WORLD = { w: 1640, h: 840 };

export type Icon = "lambda" | "choice" | "ai" | "person" | "machine" | "file" | "db" | "agent" | "tool"
  | "brain" | "queue" | "test" | "pr" | "image" | "vm" | "check" | "wrench" | "globe";

export type DetailId = string;  // no close-ups in this picture

export interface Rect { x: number; y: number; w: number; h: number }

interface Placed extends Rect {
  id: string;
  step: number;
  until?: number;
  moves?: ({ step: number } & Rect)[];
}

export interface Variant { step: number; label?: string; sub?: string; hue?: Hue; icon?: Icon; tool?: boolean }

export interface Card extends Placed {
  label: string;
  sub?: string;
  hue: Hue;
  icon?: Icon;
  items?: string[];
  columns?: 2;
  code?: string;
  container?: boolean;
  labelBottom?: boolean;
  small?: boolean;
  tool?: boolean;
  big?: boolean;
  chip?: boolean;
  detail?: DetailId;
  detailFrom?: number;
  variants?: Variant[];
}

export interface Region extends Placed { label: string; hue: Hue }

export interface Connector {
  id: string;
  step: number;
  until?: number;
  points: [number, number][];
  label?: string;
  labelAt?: [number, number];
  labelAnchor?: "start" | "middle" | "end";
  hue: Hue;
  dashed?: boolean;
  both?: boolean;
  flows?: boolean;
}

export interface Step {
  heading: string;
  line?: string;
  notes: string;
  camera: Rect;
}

// ---------------------------------------------------------------------------------------------
// One frame: the whole world, about 2:1.

const WHOLE: Rect = { x: 20, y: 40, w: 1600, h: 760 };

export const TURN = 99; // no turn: this picture only grows

// Three lanes (the y of their connectors) and three columns inside the Bedrock account (where their cards start)
const CARD = { h: 70, w: 250 };
const HALF = CARD.h / 2;
const LANE = { calls: 260, telemetry: 420, dashboard: 580 };
const COL = { a: 660, b: 940, c: 1260 };
const TOP = 90;                                   // Identity Center and the IAM denies
const BOTTOM = LANE.dashboard + HALF + 60;        // your IdP and the enforcer
const LEFT = 185;                                 // the middle of the left column

export const STEPS: Step[] = [
  {
    heading: "Claude Code on Amazon Bedrock, for an organisation",
    line: "How it works, in one picture.",
    notes: "The title.",
    camera: WHOLE,
  },
  {
    heading: "How it works",
    line: "Nothing sits between Claude Code and Bedrock; everything else is on the side.",
    notes: "Everything at once.",
    camera: WHOLE,
  },
  {
    heading: "How it works",
    line: "Nothing sits between Claude Code and Bedrock; everything else is on the side.",
    notes: "People sign in with Identity Center and call Bedrock as themselves, so the bill shows cost by person. "
      + "Telemetry, signed with the same identity, lands in S3 as Parquet, and the same identity asks which tags "
      + "the admins require. The dashboard queries telemetry and the bill "
      + "with Athena. The enforcer turns budgets and allowed models into IAM denies that Bedrock applies.",
    camera: WHOLE,
  },
];

// ---------------------------------------------------------------------------------------------

export const REGIONS: Region[] = [
  { id: "account", step: 1, x: 580, y: 50, w: 1030, h: BOTTOM + CARD.h + 40 - 50, label: "The Bedrock account",
    hue: "slate" },
];

const card = (id: string, x: number, y: number, label: string, sub: string, hue: Hue, icon: Icon, w = CARD.w): Card =>
  ({ id, step: 1, x, y, w, h: CARD.h, label, sub, hue, icon });

export const CARDS: Card[] = [
  // People, on the left
  card("idc", 60, TOP, "Identity Center", "its users, or your IdP's", "indigo", "check"),
  card("claude", 60, LANE.calls - HALF, "Claude Code", "on a laptop", "sky", "tool"),
  card("browser", 60, LANE.dashboard - HALF, "Browser", "admins and people", "sky", "globe"),
  card("idp", 60, BOTTOM, "Your IdP", "Entra, Keycloak, Okta…", "indigo", "person"),

  // Calls and money, along the top
  card("iam", COL.c, TOP, "IAM denies", "over budget · models off", "rose", "wrench"),
  card("bedrock", COL.a, LANE.calls - HALF, "Amazon Bedrock", "Claude models", "violet", "ai"),
  card("bill", COL.c, LANE.calls - HALF, "The bill", "CUR 2.0, by person", "amber", "file"),

  // Telemetry, through the middle
  card("ingest", COL.a, LANE.telemetry - HALF, "Ingest", "checks who sent it", "teal", "lambda", 220),
  card("firehose", COL.b, LANE.telemetry - HALF, "Firehose", "batches, 5 minutes", "teal", "queue", 220),
  card("s3", COL.c, LANE.telemetry - HALF, "S3", "telemetry and the bill", "teal", "db"),

  // The dashboard and the controls, along the bottom
  card("dashboard", COL.a, LANE.dashboard - HALF, "Dashboard", "CloudFront, API λ", "sky", "vm"),
  card("athena", COL.c, LANE.dashboard - HALF, "Athena", "SQL over S3", "emerald", "choice"),
  card("enforcer", COL.c, BOTTOM, "Enforcer", "every 10 minutes", "rose", "lambda"),
];

const right = (c: string) => { const k = CARDS.find((x) => x.id === c)!; return k.x + k.w; };
const mid = (c: string) => { const k = CARDS.find((x) => x.id === c)!; return k.x + k.w / 2; };
const between = (a: number, b: number) => (a + b) / 2 + 4;   // a label beside a vertical run
const TOP_MID = TOP + HALF, BOTTOM_MID = BOTTOM + HALF;

export const CONNECTORS: Connector[] = [
  // People sign in
  { id: "signin", step: 1, points: [[LEFT, TOP + CARD.h], [LEFT, LANE.calls - HALF]], hue: "indigo", both: true,
    label: "sign in", labelAt: [LEFT + 12, between(TOP + CARD.h, LANE.calls - HALF)], labelAnchor: "start" },
  { id: "signin2", step: 1, points: [[LEFT, LANE.dashboard + HALF], [LEFT, BOTTOM]], hue: "indigo", both: true,
    label: "sign in", labelAt: [LEFT + 12, between(LANE.dashboard + HALF, BOTTOM)], labelAnchor: "start" },

  // Claude Code calls Bedrock as the person; the bill knows who
  { id: "calls", step: 1, points: [[right("claude"), LANE.calls], [COL.a, LANE.calls]], hue: "violet",
    label: "every call, as you", labelAt: [460, LANE.calls - 10] },
  { id: "cost", step: 1, points: [[right("bedrock"), LANE.calls], [COL.c, LANE.calls]], hue: "amber",
    label: "cost per person", labelAt: [1085, LANE.calls - 10] },
  { id: "export", step: 1, points: [[mid("bill"), LANE.calls + HALF], [mid("bill"), LANE.telemetry - HALF]],
    hue: "amber", label: "daily", labelAt: [mid("bill") + 12, between(LANE.calls + HALF, LANE.telemetry - HALF)],
    labelAnchor: "start" },

  // Telemetry
  { id: "telemetry", step: 1, points: [[LEFT, LANE.calls + HALF], [LEFT, LANE.telemetry], [COL.a, LANE.telemetry]],
    hue: "teal", both: true, label: "telemetry · required tags", labelAt: [430, LANE.telemetry - 10] },
  { id: "rows", step: 1, points: [[right("ingest"), LANE.telemetry], [COL.b, LANE.telemetry]], hue: "teal" },
  { id: "parquet", step: 1, points: [[right("firehose"), LANE.telemetry], [COL.c, LANE.telemetry]], hue: "teal",
    label: "Parquet", labelAt: [1210, LANE.telemetry - 10] },

  // The dashboard
  { id: "usage", step: 1, points: [[right("browser"), LANE.dashboard], [COL.a, LANE.dashboard]], hue: "sky",
    both: true, label: "usage, controls", labelAt: [460, LANE.dashboard - 10] },
  { id: "query", step: 1, points: [[mid("s3"), LANE.telemetry + HALF], [mid("s3"), LANE.dashboard - HALF]],
    hue: "emerald" },
  { id: "sql", step: 1, points: [[right("dashboard"), LANE.dashboard], [COL.c, LANE.dashboard]], hue: "emerald",
    both: true, label: "SQL", labelAt: [1085, LANE.dashboard - 10] },

  // The controls: budgets and models become IAM denies that Bedrock applies
  { id: "controls", step: 1, points: [[mid("dashboard"), LANE.dashboard + HALF], [mid("dashboard"), BOTTOM_MID],
    [COL.c, BOTTOM_MID]], hue: "rose", label: "budgets, models", labelAt: [1020, BOTTOM_MID - 10] },
  { id: "spend", step: 1, points: [[mid("athena"), LANE.dashboard + HALF], [mid("athena"), BOTTOM]], hue: "emerald",
    label: "spend", labelAt: [mid("athena") + 12, between(LANE.dashboard + HALF, BOTTOM)], labelAnchor: "start" },
  { id: "deny", step: 1, points: [[right("enforcer"), BOTTOM_MID], [1575, BOTTOM_MID], [1575, TOP_MID],
    [right("iam"), TOP_MID]], hue: "rose" },
  { id: "refuse", step: 1, points: [[COL.c, TOP_MID], [mid("bedrock"), TOP_MID], [mid("bedrock"), LANE.calls - HALF]],
    hue: "rose", label: "refused", labelAt: [mid("bedrock") + 12, between(TOP_MID, LANE.calls - HALF)],
    labelAnchor: "start" },
];

// ---------------------------------------------------------------------------------------------
// Helpers the renderer uses. No need to change these.

export function shown(el: { step: number; until?: number }, step: number): boolean {
  return step >= el.step && (el.until === undefined || step < el.until);
}

export function rectAt(el: Placed, step: number): Rect {
  let r: Rect = { x: el.x, y: el.y, w: el.w, h: el.h };
  for (const m of el.moves ?? []) if (step >= m.step) r = { x: m.x, y: m.y, w: m.w, h: m.h };
  return r;
}

export function look(card: Card, step: number) {
  let v = { label: card.label, sub: card.sub, hue: card.hue, icon: card.icon, tool: card.tool };
  for (const x of card.variants ?? []) if (step >= x.step) v = { ...v, ...x };
  return v;
}

export function changedAt(card: Card, step: number): number {
  let at = card.step;
  for (const m of card.moves ?? []) if (step >= m.step) at = Math.max(at, m.step);
  for (const v of card.variants ?? []) if (step >= v.step) at = Math.max(at, v.step);
  return at;
}
