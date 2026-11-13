import type {
  AoiMetrics,
  Capabilities,
  IndexResult,
  MapResult,
  PolygonGeometry,
  PopulationResult,
} from "./types";

/** Unwrap SAGE's error envelope so the UI shows the code, not "HTTP 422". */
async function read<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const error = body?.error;
    throw new Error(
      error ? `${error.code}: ${error.message}` : `request failed with HTTP ${response.status}`,
    );
  }
  return body as T;
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  return read<T>(
    await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export const getCapabilities = () => fetch("/api/geo/capabilities").then(read<Capabilities>);

export const aoiMetrics = (geometry: PolygonGeometry) =>
  post<AoiMetrics>("/api/geo/aoi", { geometry });

export const runIndex = (options: {
  geometry: PolygonGeometry;
  index: string;
  dateFrom: string;
  dateTo: string;
  maxCloud: number;
  cloudMask: boolean;
  preview: boolean;
}) =>
  post<IndexResult>("/api/geo/index", {
    geometry: options.geometry,
    index: options.index,
    date_from: options.dateFrom,
    date_to: options.dateTo,
    max_cloud: options.maxCloud,
    cloud_mask: options.cloudMask,
    preview: options.preview,
  });

export const runPopulation = (geometry: PolygonGeometry, iso3: string, year: number) =>
  post<PopulationResult>("/api/geo/population", { geometry, iso3, year });

export const exportMap = (geometry: PolygonGeometry, title: string) =>
  post<MapResult>("/api/geo/map", { geometry, title });
