/**
 * A small, deterministic intent parser for the chat box.
 *
 * It is intentionally not a language model: the answers must be reproducible and
 * every request has to map onto something the API can actually do. Unknown text
 * gets the capability list rather than an invented promise.
 */

export type Intent =
  | { kind: "landcover"; focus: number | null }
  | { kind: "buildings" }
  | { kind: "features"; category: "roads" | "monuments" }
  | { kind: "index"; index: string }
  | { kind: "population" }
  | { kind: "help" };

const INDEX_WORDS: [RegExp, string][] = [
  [/\bndvi\b|\bvegetation index\b|\bgreenness\b/, "ndvi"],
  [/\bndwi\b|\bwater index\b/, "ndwi"],
  [/\bndbi\b|\bbuilt[- ]up index\b/, "ndbi"],
  [/\bnbr\b|\bburn\b|\bfire severity\b/, "nbr"],
  [/\bndmi\b|\bmoisture index\b/, "ndmi"],
];

export const SUGGESTIONS = [
  "Mark all buildings",
  "Mark all roads",
  "Mark all monuments",
  "Show water and trees",
  "Run NDVI",
  "How many people live here?",
];

export function parseCommand(input: string): Intent {
  const text = input.toLowerCase();

  for (const [pattern, index] of INDEX_WORDS) {
    if (pattern.test(text)) return { kind: "index", index };
  }
  if (/\b(road|roads|street|streets|highway|highways)\b/.test(text)) {
    return { kind: "features", category: "roads" };
  }
  if (/\b(monument|monuments|heritage|historic|landmark|landmarks|memorial|memorials)\b/.test(text)) {
    return { kind: "features", category: "monuments" };
  }
  if (/\b(building|buildings|structure|structures|house|houses|roof|roofs)\b/.test(text)) {
    return { kind: "buildings" };
  }
  if (/\b(water|lake|river|pond|reservoir|waterbody|water body|water bodies)\b/.test(text)) {
    return { kind: "landcover", focus: 0 };
  }
  if (/\b(tree|trees|forest|forests|vegetation|green cover|greenery)\b/.test(text)) {
    return { kind: "landcover", focus: 1 };
  }
  if (/\b(built|built-up|urban|concrete|settlement)\b/.test(text)) {
    return { kind: "landcover", focus: 6 };
  }
  if (/\b(crop|crops|farm|farming|agriculture)\b/.test(text)) {
    return { kind: "landcover", focus: 4 };
  }
  if (/\b(population|people|residents|inhabitants|population density)\b/.test(text)) {
    return { kind: "population" };
  }
  if (/\b(land ?cover|land ?use|classify|classification)\b/.test(text)) {
    return { kind: "landcover", focus: null };
  }
  return { kind: "help" };
}

export const HELP_TEXT =
  "I can mark buildings (Open Buildings footprints), roads and monuments (OpenStreetMap), " +
  "show modelled water, trees, built-up, crop and other Dynamic World classes, run an index " +
  "such as NDVI or NDWI, and estimate population. Draw an area on the map first. " +
  "Marking is limited by its data: OSM completeness varies, and land cover is 10 m modelled " +
  "cover rather than object outlines.";
