import type {
  BBox,
  CacheStats,
  CompareResponse,
  DownloadParams,
  IndexResponse,
  Meta,
  Plan,
  Place,
  QueryResponse,
  ReplayResponse,
  SearchResponse,
  TimeseriesResponse,
} from "../types";

/** A coded failure from the API, mirroring alephgeo's error envelope. */
export class ApiError extends Error {
  code: string;
  details: Record<string, unknown>;

  constructor(code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.details = details;
  }

  /** Candidates attached to an `ambiguous_place` failure. */
  get candidates(): Place[] {
    const value = this.details.candidates;
    return Array.isArray(value) ? (value as Place[]) : [];
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const envelope = (body as { error?: Record<string, unknown> } | null)?.error;
    if (envelope && typeof envelope.code === "string") {
      const { code, message, ...details } = envelope as { code: string; message?: string };
      throw new ApiError(code, String(message ?? code), details);
    }
    throw new ApiError("http_" + res.status, `HTTP ${res.status}`);
  }

  return (await res.json()) as T;
}

export const api = {
  meta: () => request<Meta>("/api/meta"),

  plan: (text: string, bbox?: BBox | null) =>
    request<{ plan: Plan; candidates: Place[]; warnings: string[]; area_km2: number | null; bbox: BBox | null }>(
      "/api/plan",
      {
        method: "POST",
        body: JSON.stringify(bbox ? { bbox } : { text }),
      },
    ),

  query: (text: string, opts: { placeId?: string; bestMatch?: boolean; limit?: number } = {}) =>
    request<QueryResponse>("/api/query", {
      method: "POST",
      body: JSON.stringify({
        text,
        limit: opts.limit ?? 40,
        run_search: true,
        place_id: opts.placeId ?? null,
        best_match: opts.bestMatch ?? false,
      }),
    }),

  search: (params: {
    bbox: BBox;
    date_from?: string | null;
    date_to?: string | null;
    max_cloud?: number | null;
    limit?: number;
  }) =>
    request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify({
        bbox: params.bbox,
        date_from: params.date_from ?? null,
        date_to: params.date_to ?? null,
        max_cloud: params.max_cloud ?? null,
        limit: params.limit ?? 40,
      }),
    }),

  index: (index: string, bbox: BBox) =>
    request<IndexResponse>("/api/index", {
      method: "POST",
      body: JSON.stringify({ index, bbox }),
    }),

  timeseries: (params: {
    bbox: BBox;
    index: string;
    date_from?: string | null;
    date_to?: string | null;
    max_points?: number;
  }) =>
    request<TimeseriesResponse>("/api/timeseries", {
      method: "POST",
      body: JSON.stringify({
        bbox: params.bbox,
        index: params.index,
        date_from: params.date_from ?? null,
        date_to: params.date_to ?? null,
        max_points: params.max_points ?? 10,
      }),
    }),

  compare: (params: { bbox: BBox; index: string; date_a: string; date_b: string }) =>
    request<CompareResponse>("/api/compare", {
      method: "POST",
      body: JSON.stringify(params),
    }),

  /** A cached fixture, for demonstrating without network access. */
  demo: () => request<IndexResponse & { bbox: BBox }>("/api/demo"),

  /**
   * Binary endpoint: success is a zip, failure is the usual JSON envelope, so the
   * error path is parsed separately instead of going through request<T>().
   */
  download: async (
    params: DownloadParams,
  ): Promise<{ blob: Blob; filename: string; images: number }> => {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });

    if (!res.ok) {
      let body: unknown = null;
      try {
        body = await res.json();
      } catch {
        /* non-JSON error body; the status is still meaningful */
      }
      const envelope = (body as { error?: Record<string, unknown> } | null)?.error;
      if (envelope && typeof envelope.code === "string") {
        const { code, message, ...details } = envelope as { code: string; message?: string };
        throw new ApiError(code, String(message ?? code), details);
      }
      throw new ApiError("http_" + res.status, `HTTP ${res.status}`);
    }

    const disposition = res.headers.get("Content-Disposition") ?? "";
    const match = /filename="([^"]+)"/.exec(disposition);
    return {
      blob: await res.blob(),
      filename: match?.[1] ?? "sage-download.zip",
      images: Number(res.headers.get("X-SAGE-Images") ?? 0),
    };
  },

  replay: (evidenceId: string) =>
    request<ReplayResponse & { cache: CacheStats }>(`/api/evidence/${evidenceId}/replay`, {
      method: "POST",
    }),
};

export const providerLabel = (name: string): string =>
  ({
    "earth-search": "Earth Search",
    "planetary-computer": "Planetary Computer",
    "arcgis-sentinel2": "ArcGIS Sentinel-2",
  })[name] ?? name;
