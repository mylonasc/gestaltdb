// Deterministic, colorblind-safe coloring (VIZ-04).
//
// Okabe-Ito palette (black removed to keep dark-mode contrast); keys hash
// with FNV-1a so a label/type always maps to the same swatch.

const PALETTE = [
  "#0072B2", // blue
  "#E69F00", // orange
  "#009E73", // bluish green
  "#CC79A7", // reddish purple
  "#56B4E9", // sky blue
  "#D55E00", // vermillion
  "#F0E442", // yellow
  "#999999", // grey
];

export function colorFor(key: string): string {
  let hash = 2166136261;
  for (let i = 0; i < key.length; i++) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return PALETTE[(hash >>> 0) % PALETTE.length];
}
