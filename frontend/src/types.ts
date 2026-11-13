export type BBox = [number, number, number, number];

export interface Scene {
  id: string;
  provider: string;
  mission: string;
  datetime: string | null;
  cloud_cover: number | null;
  bbox: BBox | null;
  resolution_m: number | null;
  thumbnail: string | null;
  index_support: boolean;
  assets: string[];
  score: number | null;
  score_parts: Record<string, number>;
  source_url: string | null;
}

export interface ProviderStatus {
  name: string;
  ok: boolean;
  count: number;
  error: string | null;
  error_code: string | null;
  elapsed_ms: number | null;
}

export interface CacheStats {
  hits: number;
  misses: number;
}

export interface Place {
  id: string;
  name: string | null;
  label: string;
  type: string | null;
  klass: string | null;
  importance: number | null;
  bbox: BBox | null;
  centre: [number, number] | null;
  source: string;
  source_url: string | null;
}

export interface Plan {
  providers: string[];
  requests: number;
  estimated_seconds: number;
  basis: string;
  notes: string[];
}

export interface SearchResponse {
  count: number;
  scenes: Scene[];
  providers: ProviderStatus[];
  warnings: string[];
  plan: Plan | null;
  cache: CacheStats;
}

export interface Intent {
  place: string | null;
  date_from: string | null;
  date_to: string | null;
  max_cloud: number | null;
  mission: string | null;
  index: string | null;
  notes: string[];
}

export interface QueryResponse {
  intent: Intent;
  bbox: BBox | null;
  place: Place | null;
  candidates: Place[];
  plan: Plan | null;
  search: SearchResponse | null;
}

export interface Stats {
  min: number;
  max: number;
  mean: number;
  std: number | null;
  median: number | null;
  count: number;
  source: string;
  raster_function: string | null;
}

export interface Histogram {
  counts: number[];
  min: number | null;
  max: number | null;
}

export interface AreaClass {
  name: string;
  label: string;
  threshold: number;
  above: boolean;
  basis: string;
  fraction: number;
  area_km2: number;
  uncertainty_km2: number;
  pixels: number;
}

export interface AreasEstimate {
  aoi_km2: number;
  observed_pixels: number;
  bin_width: number;
  classes: AreaClass[];
  method: string;
  notes: string[];
}

export interface AreaChange {
  name: string;
  label: string;
  threshold: number;
  from_km2: number;
  to_km2: number;
  change_km2: number;
  direction: string;
  uncertainty_km2: number;
}

export interface IndexResponse {
  index: string;
  label: string;
  formula: string;
  stats: Stats | null;
  histogram: Histogram | null;
  areas?: AreasEstimate | null;
  visual_png_url: string | null;
  status: "ok" | "insufficient_data" | "unsupported" | "error";
  message: string | null;
  evidence_id: string | null;
  generated_at?: string | null;
  citation?: string | null;
  cache: CacheStats | null;
}

export interface CompareSide {
  date: string;
  requested_date: string;
  substituted: boolean;
  objectid: string;
  cloud_cover_percent: number | null;
  mean: number | null;
  min: number | null;
  max: number | null;
  count: number;
  areas?: AreasEstimate | null;
  image_url: string;
  width: number;
  height: number;
}

export interface CompareResponse {
  index: string;
  label: string;
  formula: string;
  bbox: BBox;
  before: CompareSide;
  after: CompareSide;
  delta: {
    mean: number;
    absolute: number;
    direction: string;
    areas?: AreaChange[];
  } | null;
  notes: string[];
  generated_at: string;
  citation: string;
}

export interface ReplayResponse {
  evidence_id: string;
  replay_status: string;
  original_stats: Stats | null;
  replay_stats: Stats | null;
  comparison: { matched: boolean; tolerance: number; absolute_differences: Record<string, number> };
  interpretation: string;
}

export interface Meta {
  version: string;
  indices: Record<
    string,
    { label: string; description: string; formula: string; bands: Record<string, string>; supported: boolean }
  >;
  providers: { name: string; indices: boolean; note: string }[];
  rank_weights: Record<string, number>;
  limits: { max_aoi_km2: number; min_aoi_km2: number; min_valid_pixel_fraction: number; max_limit: number };
  area_classes?: Record<string, { name: string; label: string; threshold: number; above: boolean; basis: string }[]>;
  thermal?: {
    variable: string;
    unit: string;
    note: string;
    years: number;
    scenes_per_year: number;
    plausible_range_c: number[];
  };
  network: { request_delay_seconds: number; retries: number; cache_ttl_seconds: number };
}

export interface TimeseriesPoint {
  date: string;
  objectid: string;
  cloud_cover_percent: number | null;
  mean: number | null;
  min: number | null;
  max: number | null;
  count: number;
  areas?: AreasEstimate | null;
}

export interface SeasonalYear {
  year: number;
  month: number;
  acquisitions: number;
  used: { date: string; objectid: string; cloud_cover_percent: number | null; mean: number | null; pixels: number }[];
  median: number | null;
  min: number | null;
  max: number | null;
  spread: number | null;
  areas: { aoi_km2: number; classes: { name: string; label: string; threshold: number; area_km2: number; min_km2: number; max_km2: number; samples: number }[]; aggregation: string } | null;
}

export interface SeasonalResponse {
  index: string;
  label: string;
  formula: string;
  month: number;
  bbox: BBox;
  aoi_km2: number;
  count: number;
  series: SeasonalYear[];
  skipped_years: { year: number; reason: string }[];
  change: {
    first_year: number;
    last_year: number;
    first: number;
    last: number;
    delta: number;
    interpretation: string;
    areas?: AreaChange[];
  } | null;
  notes: string[];
  generated_at?: string;
  citation?: string;
}

export interface ThermalYear {
  year: number;
  month: number;
  acquisitions: number;
  clearest: { day: string; cloud_cover_percent: number | null; value_c: number; name: string | null };
  median_c: number | null;
  min_c: number | null;
  max_c: number | null;
  spread_c: number | null;
  rejected: { day: string; reason: string; value_c?: number }[];
}

export interface ThermalResponse {
  variable: string;
  label: string;
  unit: string;
  month: number;
  bbox: BBox;
  aoi_km2: number;
  count: number;
  series: ThermalYear[];
  skipped_years: { year: number; reason: string }[];
  change: {
    first_year: number;
    last_year: number;
    first_c: number;
    last_c: number;
    delta_c: number;
    basis: string;
    interpretation: string;
  } | null;
  notes: string[];
  generated_at?: string;
  citation?: string;
}

export interface TimeseriesResponse {
  index: string;
  label: string;
  formula: string;
  bbox: BBox;
  date_from: string;
  date_to: string;
  count: number;
  points: TimeseriesPoint[];
  skipped: { date: string; objectid: string; status: string; reason: string }[];
  change: {
    first: number;
    last: number;
    delta: number;
    between: [string, string];
    interpretation: string;
    areas?: AreaChange[];
  } | null;
  notes: string[];
}

export interface DownloadParams {
  bbox: BBox;
  date_from: string;
  date_to: string;
  kind: "truecolor" | "index";
  index?: string;
  max_images?: number;
}
