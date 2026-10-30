import type { ReactNode } from "react";
import type { Hue } from "./look";
import type { DetailId, Rect } from "./scene";

// Close-ups: a card that opens into a panel. This picture has none.
export interface Detail { frame: Rect; heading: string; line: string; notes: string; hue: Hue; Body: () => ReactNode }

export const DETAILS: Record<DetailId, Detail> = {};
