export type BBox = [number, number, number, number];

export interface PolygonGeometry {
  type: "Polygon";
  coordinates: number[][][];
}

export interface AoiMetrics {
  status: string;
  geometry_type: string;
  parts: number;
  vertices: number;
  holes: number;
  area_km2: number;
  perimeter_km: number;
  compactness: number | null;
  centroid: [number, number];
  bbox: BBox;
  projected: {
    crs: string;
    area_km2: number;
    perimeter_km: number;
    relative_area_difference: number | null;
  };
  audit: { input_type: string; repaired: boolean; valid: boolean };
}

export interface Scene {
  id: string;
  collection: string | null;
  datetime: string | null;
  cloud_cover: number | null;
  platform: string | null;
  bbox: number[] | null;
  tile: string | null;
  epsg: number | null;
  source_url: string | null;
}

export interface HistogramBin {
  low: number;
  high: number;
  count: number;
}

export interface IndexStats {
  mean: number;
  median: number;
  min: number;
  max: number;
  std: number;
  p10: number;
  p90: number;
  count: number;
}

export interface IndexResult {
  status: string;
  index: string;
  label: string;
  formula: string;
  stats: IndexStats | null;
  histogram: { bins: HistogramBin[]; range: [number, number] } | null;
  pixels: {
    inside_aoi: number;
    valid: number;
    cloud_masked: number;
    cloud_mask_applied: boolean;
  };
  grid: { crs: string; width: number; height: number; res_m: number[] };
  scene: Scene;
  area_km2: number;
  read_seconds: number;
  evidence_id: string | null;
  generated_at: string;
  citation: string;
  reflectance: {
    scale: number;
    offset: number;
    source: string;
    declared_asset_offset: number | null;
    note?: string;
  };
  cloud_mask: { applied: boolean; classes: number[] };
  preview_url?: string;
  preview_bytes?: number;
}

export interface PopulationResult {
  status: string;
  population: number;
  density_per_km2: number | null;
  area_km2: number;
  pixels_used: number;
  notes: string[];
  source: {
    dataset: string;
    iso3: string;
    year: number;
    url: string;
    resolution_deg: number[];
  };
}

export interface Capabilities {
  stack: string;
  python: string;
  installed: Record<string, string | null>;
  missing: string[];
  vector_ready: boolean;
  raster_ready: boolean;
  population_ready: boolean;
  map_ready: boolean;
  earth_engine: { configured: boolean; project: string | null };
  worldpop_cached: { name: string; bytes: number }[];
  maps: { name: string; bytes: number }[];
  notes: string[];
}

export interface LandcoverClass {
  id: number;
  name: string;
  color: string;
  km2: number;
  percent: number;
}

export interface LandcoverResult {
  status: string;
  classes: LandcoverClass[];
  total_km2: number;
  structures: {
    count: number | null;
    confidence: number;
    source: string;
    note?: string;
  } | null;
  scene_count: number;
  window: { from: string; to: string };
  scale_m: number;
  elapsed_seconds: number;
  source: string;
  notes: string[];
}

export interface MapResult {
  map_id: string;
  url: string;
  bytes: number;
  title: string;
  layers: string[];
  centre: [number, number];
  note: string | null;
}
