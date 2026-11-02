import { useCallback, useEffect, useState } from "react";
import MapView from "./components/MapView";
import { ApiError, api, providerLabel } from "./lib/api";
import type {
  BBox,
  CacheStats,
  IndexResponse,
  Intent,
  Meta,
  Plan,
  Place,
  ProviderStatus,
  ReplayResponse,
  Scene,
  TimeseriesResponse,
} from "./types";

const DEFAULT_QUERY = "cloud free NDVI of Hyderabad over the last 3 months";

const fmt = (v: number | null | undefined, digits = 3): string =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);

const areaKm2 = (b: BBox): number => {
  const lat = (((b[1] + b[3]) / 2) * Math.PI) / 180;
  const w = Math.abs(b[2] - b[0]) * 111.32 * Math.cos(lat);
  const h = Math.abs(b[3] - b[1]) * 111.32;
  return w * h;
};

const dday = (iso: string | null): string => (iso ? iso.slice(0, 10) : "—");

// Download defaults: the last 30 days is the window most users actually mean.
const TODAY = new Date().toISOString().slice(0, 10);
const MONTH_AGO = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);

// One click each, for someone who does not yet know what to ask for.
const EXAMPLES = [
  "vegetation of Hyderabad last 30 days",
  "Godavari river near Nanded",
  "Hussain Sagar water last month",
  "Charminar, Hyderabad",
];

// This catalogue often reports well under 1% cloud; one decimal would render
// those scenes as "0.0%" and misrepresent them as no-data.
const pct = (v: number | null): string =>
  v === null ? "n/a" : `${v < 1 ? v.toFixed(2) : v.toFixed(1)}%`;

/* ------------------------------------------------------------------ pieces */

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-edge bg-ink/70 px-2.5 py-1 text-[11px]">
      <span className="text-slate-500">{label}</span>
      <span className="font-mono text-sky-300">{value}</span>
    </span>
  );
}

function StatRow({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between border-b border-edge/60 py-1.5 last:border-0">
      <span className="text-xs text-slate-400">
        {label}
        {hint ? <span className="ml-1 text-[10px] text-slate-600">{hint}</span> : null}
      </span>
      <span className="font-mono text-sm text-slate-100">{value}</span>
    </div>
  );
}

function Histogram({ counts, min, max }: { counts: number[]; min: number | null; max: number | null }) {
  const peak = Math.max(...counts, 1);
  return (
    <div className="mt-2">
      <div className="flex h-16 items-end gap-[1px]">
        {counts.map((c, i) => (
          <div
            key={i}
            className="flex-1 rounded-t-[1px] bg-gradient-to-t from-sky-700/30 to-sky-400"
            style={{ height: `${Math.max(2, (c / peak) * 100)}%` }}
            title={`${c} px`}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-slate-500">
        <span>{fmt(min, 2)}</span>
        <span>{fmt(max, 2)}</span>
      </div>
    </div>
  );
}

function Series({ series }: { series: TimeseriesResponse }) {
  const points = series.points;
  const values = points.map((p) => p.mean).filter((v): v is number => v !== null);
  if (values.length < 2) return null;

  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const w = 320;
  const h = 80;
  const coords = points.map((p, i) => ({
    x: points.length === 1 ? w / 2 : (i / (points.length - 1)) * w,
    y: h - (((p.mean ?? min) - min) / span) * h,
    p,
  }));

  return (
    <div className="mt-2">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" role="img" aria-label={`${series.label} over time`}>
        <polyline
          fill="none"
          stroke="#38bdf8"
          strokeWidth="1.5"
          points={coords.map((c) => `${c.x},${c.y}`).join(" ")}
        />
        {coords.map((c) => (
          <circle key={c.p.date} cx={c.x} cy={c.y} r="2.6" fill="#38bdf8">
            <title>{`${c.p.date} · mean ${fmt(c.p.mean, 3)} · cloud ${pct(c.p.cloud_cover_percent)}`}</title>
          </circle>
        ))}
      </svg>
      <div className="flex justify-between font-mono text-[10px] text-slate-500">
        <span>{points[0].date}</span>
        <span>
          {fmt(min, 3)} – {fmt(max, 3)}
        </span>
        <span>{points[points.length - 1].date}</span>
      </div>
    </div>
  );
}

function Report({
  result,
  bbox,
  intent,
  chosen,
}: {
  result: IndexResponse | null;
  bbox: BBox | null;
  intent: Intent | null;
  chosen: Place | null;
}) {
  if (!bbox) return null;
  const rows: [string, string][] = result?.stats
    ? [
        ["mean", fmt(result.stats.mean, 4)],
        ["min", fmt(result.stats.min, 4)],
        ["max", fmt(result.stats.max, 4)],
        ["std dev", fmt(result.stats.std, 4)],
        ["valid pixels", result.stats.count.toLocaleString()],
        ["source", result.stats.source],
        ["raster function", result.stats.raster_function ?? "—"],
      ]
    : [];

  return (
    <div className="hidden bg-white p-10 text-black print:block">
      <h1 className="text-xl font-semibold">SAGE — satellite index report</h1>
      <p className="mt-1 text-xs text-gray-600">
        Generated {new Date().toISOString().slice(0, 19).replace("T", " ")} UTC
      </p>

      <h2 className="mt-6 text-sm font-semibold uppercase tracking-wide">Area of interest</h2>
      <p className="mt-1 font-mono text-xs">
        W {bbox[0].toFixed(4)} · S {bbox[1].toFixed(4)} · E {bbox[2].toFixed(4)} · N {bbox[3].toFixed(4)}
        {"  "}(≈ {areaKm2(bbox).toLocaleString(undefined, { maximumFractionDigits: 0 })} km²)
      </p>
      {chosen && (
        <p className="mt-1 text-xs">
          {chosen.name} — {chosen.label} [{chosen.id}]
        </p>
      )}
      {intent && (
        <p className="mt-1 text-xs">
          Window {intent.date_from ?? "any"} → {intent.date_to ?? "any"}
          {intent.max_cloud !== null ? ` · cloud ≤ ${intent.max_cloud}%` : ""}
        </p>
      )}

      {result?.stats ? (
        <>
          <h2 className="mt-6 text-sm font-semibold uppercase tracking-wide">{result.label}</h2>
          {result.formula && <p className="mt-1 font-mono text-[11px] text-gray-600">{result.formula}</p>}
          <table className="mt-2 w-full border-collapse text-xs">
            <tbody>
              {rows.map(([label, value]) => (
                <tr key={label}>
                  <td className="border-b border-gray-300 py-1 pr-4 text-gray-600">{label}</td>
                  <td className="border-b border-gray-300 py-1 font-mono">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.visual_png_url && (
            <img src={result.visual_png_url} alt={`${result.label} over the AOI`} className="mt-3 w-full border border-gray-300" />
          )}
          {result.evidence_id && (
            <p className="mt-2 font-mono text-xs">evidence id: {result.evidence_id}</p>
          )}
        </>
      ) : (
        <p className="mt-6 text-xs">No index result in this report.</p>
      )}

      <p className="mt-6 text-[10px] leading-relaxed text-gray-600">
        Contains modified Copernicus Sentinel data, processed by Esri. Rendered with the ArcGIS
        Sentinel-2 ImageServer (10 m, rolling ~14-month window). Statistics are computed
        server-side over the AOI, not estimated. The window rolls forward daily, so a replay
        after coverage changes will differ by design.
      </p>
    </div>
  );
}

function SceneCard({ scene, onPick }: { scene: Scene; onPick: () => void }) {
  const score = scene.score ?? 0;
  return (
    <button
      onClick={onPick}
      className="group w-56 shrink-0 rounded-lg border border-edge bg-panel/80 p-2.5 text-left transition hover:border-sky-500/60 hover:bg-panel"
    >
      <div className="flex items-center justify-between">
        <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-[10px] font-medium text-sky-300">
          {providerLabel(scene.provider)}
        </span>
        {scene.index_support && (
          <span className="rounded bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-300">
            indices
          </span>
        )}
      </div>
      <div className="mt-1.5 font-mono text-[11px] text-slate-300">{dday(scene.datetime)}</div>
      <div className="mt-0.5 truncate text-[10px] text-slate-500" title={scene.id}>
        {scene.id}
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-edge">
          <div className="h-full rounded-full bg-sky-400" style={{ width: `${score * 100}%` }} />
        </div>
        <span className="font-mono text-[10px] text-slate-400">{score.toFixed(2)}</span>
      </div>
      <div className="mt-1 text-[10px] text-slate-500">
        cloud {pct(scene.cloud_cover)}
      </div>
    </button>
  );
}

/* -------------------------------------------------------------------- app */

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [text, setText] = useState(DEFAULT_QUERY);
  const [intent, setIntent] = useState<Intent | null>(null);
  const [bbox, setBbox] = useState<BBox | null>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [candidates, setCandidates] = useState<Place[]>([]);
  const [chosen, setChosen] = useState<Place | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [cache, setCache] = useState<CacheStats | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [indexKey, setIndexKey] = useState("ndvi");
  const [result, setResult] = useState<IndexResponse | null>(null);
  const [replay, setReplay] = useState<ReplayResponse | null>(null);
  const [busy, setBusy] = useState<null | "interpret" | "search" | "analyze">(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [dlMode, setDlMode] = useState<"single" | "range">("range");
  const [dlDay, setDlDay] = useState(TODAY);
  const [dlFrom, setDlFrom] = useState(MONTH_AGO);
  const [dlTo, setDlTo] = useState(TODAY);
  const [dlKind, setDlKind] = useState<"truecolor" | "index">("truecolor");
  const [dlBusy, setDlBusy] = useState(false);
  const [dlNote, setDlNote] = useState<string | null>(null);
  const [series, setSeries] = useState<TimeseriesResponse | null>(null);
  const [tsBusy, setTsBusy] = useState(false);

  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
  }, []);

  // The download follows the parsed intent when the query supplied dates,
  // rather than silently using the 30-day default while the user asked for March.
  useEffect(() => {
    if (!intent) return;
    if (intent.date_to) {
      const day = intent.date_to.slice(0, 10);
      setDlDay(day);
      setDlTo(day);
    }
    if (intent.date_from) setDlFrom(intent.date_from.slice(0, 10));
  }, [intent]);

  const runInterpret = useCallback(
    async (opts: { placeId?: string; bestMatch?: boolean; query?: string } = {}) => {
      const q = (opts.query ?? text).trim();
      if (!q) return;
      setText(q);
      setBusy("interpret");
      setError(null);
      setErrorCode(null);
      setCandidates([]);
      setResult(null);
      setReplay(null);
      try {
        const res = await api.query(q, opts);
        setIntent(res.intent);
        setChosen(res.place);
        setCandidates(res.candidates ?? []);
        setPlan(res.plan);
        if (res.bbox) setBbox(res.bbox as BBox);
        setScenes(res.search?.scenes ?? []);
        setProviders(res.search?.providers ?? []);
        setWarnings(res.search?.warnings ?? []);
        if (res.search?.cache) setCache(res.search.cache);
        if (res.intent.index && meta?.indices[res.intent.index]?.supported) {
          setIndexKey(res.intent.index);
        }
      } catch (e) {
        // An ambiguous name is not a failure to hide: show the choices.
        if (e instanceof ApiError) {
          setErrorCode(e.code);
          setError(e.message);
          if (e.code === "ambiguous_place") {
            setCandidates(e.candidates);
            return;
          }
        } else {
          setError((e as Error).message);
        }
        setScenes([]);
        setProviders([]);
        setWarnings([]);
      } finally {
        setBusy(null);
      }
    },
    [text, meta],
  );

  const runSearch = useCallback(
    async (box: BBox) => {
      setBusy("search");
      setError(null);
      try {
        const res = await api.search({
          bbox: box,
          date_from: intent?.date_from ?? null,
          date_to: intent?.date_to ?? null,
          max_cloud: intent?.max_cloud ?? null,
        });
        setScenes(res.scenes);
        setProviders(res.providers);
        setWarnings(res.warnings);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [intent],
  );

  const handleBbox = useCallback(
    (box: BBox) => {
      setBbox(box);
      setResult(null);
      setReplay(null);
      if (box) void runSearch(box);
    },
    [runSearch],
  );

  const runAnalyze = useCallback(async () => {
    if (!bbox) return;
    setBusy("analyze");
    setError(null);
    setReplay(null);
    try {
      setResult(await api.index(indexKey, bbox));
    } catch (e) {
      setError((e as Error).message);
      setResult(null);
    } finally {
      setBusy(null);
    }
  }, [bbox, indexKey]);

  const runSeries = useCallback(async () => {
    if (!bbox) return;
    setTsBusy(true);
    setError(null);
    setErrorCode(null);
    try {
      setSeries(
        await api.timeseries({
          bbox,
          index: indexKey,
          date_from: intent?.date_from ?? null,
          date_to: intent?.date_to ?? null,
        }),
      );
    } catch (e) {
      if (e instanceof ApiError) {
        setErrorCode(e.code);
        setError(e.message);
      } else {
        setError((e as Error).message);
      }
      setSeries(null);
    } finally {
      setTsBusy(false);
    }
  }, [bbox, indexKey, intent]);

  const loadDemo = useCallback(async () => {
    try {
      const demo = await api.demo();
      setBbox(demo.bbox);
      setResult(demo);
      setSeries(null);
      setReplay(null);
      setError(null);
      setErrorCode(null);
      setScenes([]);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const downloadSeriesCsv = useCallback(() => {
    if (!series) return;
    const header = ["date", "objectid", "mean", "min", "max", "count", "cloud_percent"];
    const rows = series.points.map((p) => [
      p.date,
      p.objectid,
      p.mean ?? "",
      p.min ?? "",
      p.max ?? "",
      p.count,
      p.cloud_cover_percent ?? "",
    ]);
    const csv = [header, ...rows].map((r) => r.join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `sage_${series.index}_series_${series.date_from}_${series.date_to}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [series]);

  const runReplay = useCallback(async () => {
    if (!result?.evidence_id) return;
    setBusy("analyze");
    setError(null);
    try {
      setReplay(await api.replay(result.evidence_id));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [result]);

  const copyEvidence = useCallback(async () => {
    if (!result?.evidence_id) return;
    try {
      await navigator.clipboard.writeText(result.evidence_id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard blocked; the id is still visible */
    }
  }, [result]);

  const downloadGeoJson = useCallback(() => {
    if (!bbox) return;
    const featureCollection = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {
            index: indexKey,
            evidence_id: result?.evidence_id ?? null,
            stats: result?.stats ?? null,
            generated_at: new Date().toISOString(),
          },
          geometry: {
            type: "Polygon",
            coordinates: [
              [
                [bbox[0], bbox[1]],
                [bbox[2], bbox[1]],
                [bbox[2], bbox[3]],
                [bbox[0], bbox[3]],
                [bbox[0], bbox[1]],
              ],
            ],
          },
        },
      ],
    };
    const blob = new Blob([JSON.stringify(featureCollection, null, 2)], {
      type: "application/geo+json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `sage2-${indexKey}-${result?.evidence_id ?? "aoi"}.geojson`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, [bbox, indexKey, result]);

  const runDownload = useCallback(async () => {
    if (!bbox) return;
    setDlBusy(true);
    setError(null);
    setErrorCode(null);
    setDlNote(null);
    const from = dlMode === "single" ? dlDay : dlFrom;
    const to = dlMode === "single" ? dlDay : dlTo;
    try {
      const res = await api.download({
        bbox,
        date_from: from,
        date_to: to,
        kind: dlKind,
        index: indexKey,
      });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(res.blob);
      a.download = res.filename;
      a.click();
      URL.revokeObjectURL(a.href);
      setDlNote(
        `${res.images} image${res.images === 1 ? "" : "s"} · ${from}${from === to ? "" : ` → ${to}`}`,
      );
    } catch (e) {
      // no_imagery is a coverage answer, not a crash; show its code like any other
      if (e instanceof ApiError) {
        setErrorCode(e.code);
        setError(e.message);
      } else {
        setError((e as Error).message);
      }
    } finally {
      setDlBusy(false);
    }
  }, [bbox, dlMode, dlDay, dlFrom, dlTo, dlKind, indexKey]);

  const supportedIndices = Object.entries(meta?.indices ?? {}).filter(([, v]) => v.supported);

  return (
    <>
      <div className="flex h-screen flex-col bg-ink print:hidden">
      {/* header */}
      <header className="flex shrink-0 items-center justify-between border-b border-edge px-4 py-2.5">
        <div className="flex items-baseline gap-3">
          <h1 className="text-base font-semibold tracking-tight text-slate-100">
            SAGE
          </h1>
          <span className="hidden text-[11px] text-slate-500 sm:block">
            satellite AOI search · deterministic indices · replayable evidence
          </span>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          {providers.length > 0 &&
            providers.map((p) => (
              <span
                key={p.name}
                className="hidden items-center gap-1.5 md:inline-flex"
                title={p.error ?? `${p.count} scenes in ${p.elapsed_ms} ms`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${p.ok ? "bg-emerald-400" : "bg-rose-500"}`}
                />
                <span className={p.ok ? "text-slate-400" : "text-rose-300"}>
                  {providerLabel(p.name)}
                </span>
              </span>
            ))}
          {cache && (
            <span className="hidden font-mono text-slate-600 sm:inline" title="response cache hits / misses for this run">
              cache {cache.hits}/{cache.misses}
            </span>
          )}
          {meta && <span className="font-mono text-slate-600">v{meta.version}</span>}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* left rail */}
        <aside className="w-[400px] shrink-0 overflow-y-auto border-r border-edge bg-panel/40 p-4">
          {/* query */}
          <label className="mb-1.5 block text-[11px] font-medium uppercase tracking-wide text-slate-500">
            Query
          </label>
          <div className="flex gap-2">
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void runInterpret()}
              placeholder="e.g. cloud free NDVI of Goa in March 2024"
              className="min-w-0 flex-1 rounded-md border border-edge bg-ink px-2.5 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-sky-500"
            />
            <button
              onClick={() => void runInterpret()}
              disabled={busy !== null}
              className="shrink-0 rounded-md bg-sky-500 px-3 py-2 text-sm font-medium text-ink transition hover:bg-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy === "interpret" ? "…" : "Run"}
            </button>
          </div>
          <p className="mt-1.5 text-[10px] leading-relaxed text-slate-600">
            Rule-based parser — no language model. The interpretation below is shown so you can
            check it against what you meant.
          </p>

          <div className="mt-2 flex flex-wrap gap-1">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                onClick={() => void runInterpret({ query: example })}
                disabled={busy !== null}
                className="rounded-full border border-edge px-2 py-0.5 text-[10px] text-slate-400 transition hover:border-sky-500/60 hover:text-sky-300 disabled:opacity-40"
              >
                {example}
              </button>
            ))}
          </div>

          {error && (
            <div className="mt-3 rounded-md border border-rose-500/40 bg-rose-500/10 p-2.5 text-xs text-rose-200">
              {errorCode && (
                <div className="mb-1 font-mono text-[10px] uppercase tracking-wide text-rose-400/80">
                  {errorCode}
                </div>
              )}
              {error}
            </div>
          )}

          {/* ambiguous place: choose rather than guess */}
          {candidates.length > 1 && errorCode === "ambiguous_place" && (
            <div className="mt-2 rounded-md border border-edge bg-ink/60 p-2">
              <div className="mb-1 text-[10px] uppercase tracking-wide text-slate-500">
                Choose the place you meant
              </div>
              <div className="space-y-1">
                {candidates.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => void runInterpret({ placeId: c.id })}
                    className="w-full rounded border border-edge px-2 py-1.5 text-left text-[11px] text-slate-300 transition hover:border-sky-500/60 hover:bg-panel"
                  >
                    <span className="font-mono text-[10px] text-sky-400">{c.id}</span>{" "}
                    <span className="text-slate-200">{c.name}</span>
                    <span className="block truncate text-[10px] text-slate-500">{c.label}</span>
                  </button>
                ))}
              </div>
              <button
                onClick={() => void runInterpret({ bestMatch: true })}
                className="mt-1.5 w-full rounded border border-sky-500/40 py-1 text-[10px] text-sky-300 hover:bg-sky-500/10"
              >
                use best match
              </button>
            </div>
          )}

          {/* intent */}
          <div className="mt-4">
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Interpretation
            </div>
            {intent ? (
              <div className="flex flex-wrap gap-1.5">
                <Chip label="place" value={intent.place ?? "—"} />
                <Chip label="dates" value={`${intent.date_from ?? "…"} → ${intent.date_to ?? "…"}`} />
                <Chip label="cloud ≤" value={intent.max_cloud === null ? "any" : `${intent.max_cloud}%`} />
                <Chip label="mission" value={intent.mission ?? "any"} />
                <Chip label="index" value={intent.index ?? "—"} />
              </div>
            ) : (
              <p className="text-xs text-slate-600">Run a query, or draw an AOI on the map.</p>
            )}
            {chosen && (
              <div className="mt-2 text-[10px] text-slate-500">
                resolved to{" "}
                <span className="font-mono text-slate-400">{chosen.id}</span>{" "}
                <a
                  href={chosen.source_url ?? "#"}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sky-400 hover:underline"
                >
                  {chosen.name}
                </a>
              </div>
            )}
            {intent && intent.notes.length > 0 && (
              <ul className="mt-2 space-y-0.5">
                {intent.notes.map((note, i) => (
                  <li key={i} className="text-[10px] leading-relaxed text-slate-500">
                    · {note}
                  </li>
                ))}
              </ul>
            )}
            {plan && (
              <div className="mt-2 rounded border border-edge/70 bg-ink/40 px-2 py-1.5 text-[10px] text-slate-400">
                plan: {plan.requests} request{plan.requests === 1 ? "" : "s"} across{" "}
                {plan.providers.length} provider{plan.providers.length === 1 ? "" : "s"} ·{" "}
                <span className="font-mono text-slate-300">~{plan.estimated_seconds.toFixed(1)}s</span>
                <span className="ml-1 text-slate-600">(nominal estimate, not measured)</span>
              </div>
            )}
          </div>

          {/* AOI */}
          <div className="mt-4 rounded-lg border border-edge bg-ink/40 p-3">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
                Area of interest
              </span>
              <div className="flex gap-1.5">
                <button
                  onClick={() => void loadDemo()}
                  title="Load the cached reference measurement — works without network"
                  className="rounded border border-edge px-2 py-1 text-[11px] text-slate-400 transition hover:border-amber-500/60 hover:text-amber-300"
                >
                  Demo
                </button>
                <button
                  onClick={() => setDrawMode((v) => !v)}
                  className={`rounded px-2 py-1 text-[11px] font-medium transition ${
                    drawMode
                      ? "bg-sky-500 text-ink"
                      : "border border-edge text-slate-300 hover:border-sky-500/60"
                  }`}
                >
                  {drawMode ? "Draw (active)" : "Draw"}
                </button>
              </div>
            </div>
            {bbox ? (
              <>
                <div className="mt-2 font-mono text-[11px] leading-relaxed text-slate-400">
                  W {bbox[0].toFixed(4)} · S {bbox[1].toFixed(4)}
                  <br />E {bbox[2].toFixed(4)} · N {bbox[3].toFixed(4)}
                </div>
                <div className="mt-1 text-[11px] text-slate-500">
                  ≈ {areaKm2(bbox).toLocaleString(undefined, { maximumFractionDigits: 0 })} km²
                </div>
              </>
            ) : (
              <p className="mt-2 text-xs text-slate-600">
                No AOI yet. Press Draw, then drag a box on the map.
              </p>
            )}
            {drawMode && (
              <p className="mt-2 text-[10px] text-sky-400">
                Drag on the map to define the box. Panning is paused while drawing.
              </p>
            )}
          </div>

          {/* one-click download */}
          <div className="mt-4 rounded-lg border border-edge bg-ink/40 p-3">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
                Download imagery
              </span>
              <div className="flex overflow-hidden rounded border border-edge">
                {(["single", "range"] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => setDlMode(m)}
                    className={`px-2 py-0.5 text-[10px] transition ${
                      dlMode === m
                        ? "bg-sky-500/20 text-sky-300"
                        : "text-slate-500 hover:text-slate-300"
                    }`}
                  >
                    {m === "single" ? "Single day" : "Date range"}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-2 flex items-center gap-1.5">
              {dlMode === "single" ? (
                <input
                  type="date"
                  value={dlDay}
                  onChange={(e) => setDlDay(e.target.value)}
                  className="min-w-0 flex-1 rounded border border-edge bg-ink px-1.5 py-1 font-mono text-[11px] text-slate-200 [color-scheme:dark]"
                />
              ) : (
                <>
                  <input
                    type="date"
                    value={dlFrom}
                    onChange={(e) => setDlFrom(e.target.value)}
                    className="min-w-0 flex-1 rounded border border-edge bg-ink px-1.5 py-1 font-mono text-[11px] text-slate-200 [color-scheme:dark]"
                  />
                  <span className="text-[11px] text-slate-600">→</span>
                  <input
                    type="date"
                    value={dlTo}
                    onChange={(e) => setDlTo(e.target.value)}
                    className="min-w-0 flex-1 rounded border border-edge bg-ink px-1.5 py-1 font-mono text-[11px] text-slate-200 [color-scheme:dark]"
                  />
                </>
              )}
            </div>

            <div className="mt-1.5 grid grid-cols-2 gap-1.5">
              {(["truecolor", "index"] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setDlKind(k)}
                  className={`rounded border px-1 py-1 text-[10px] font-medium transition ${
                    dlKind === k
                      ? "border-sky-500 bg-sky-500/15 text-sky-300"
                      : "border-edge text-slate-400 hover:border-slate-500"
                  }`}
                >
                  {k === "truecolor" ? "True colour" : `${indexKey.toUpperCase()} image`}
                </button>
              ))}
            </div>

            <button
              onClick={() => void runDownload()}
              disabled={!bbox || dlBusy}
              className="mt-1.5 w-full rounded-md border border-emerald-500/50 bg-emerald-500/10 py-2 text-sm font-medium text-emerald-300 transition hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {dlBusy ? "Packing…" : "Download .zip"}
            </button>
            {dlNote && <p className="mt-1 text-[10px] text-emerald-300/80">{dlNote}</p>}
            <p className="mt-1.5 text-[10px] leading-relaxed text-slate-600">
              One image per acquisition day, pinned by catalogue id, with a manifest carrying
              checksums, dates and attribution.
            </p>
          </div>

          {/* index analysis */}
          <div className="mt-4">
            <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-slate-500">
              Spectral index
            </div>
            <div className="grid grid-cols-4 gap-1.5">
              {supportedIndices.map(([key, spec]) => (
                <button
                  key={key}
                  onClick={() => {
                    setIndexKey(key);
                    setResult(null);
                    setReplay(null);
                  }}
                  title={spec.description}
                  className={`rounded-md border px-1 py-1.5 text-xs font-medium transition ${
                    indexKey === key
                      ? "border-sky-500 bg-sky-500/15 text-sky-300"
                      : "border-edge text-slate-400 hover:border-slate-500"
                  }`}
                >
                  {spec.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => void runAnalyze()}
              disabled={!bbox || busy !== null}
              className="mt-2 w-full rounded-md border border-sky-500/50 bg-sky-500/10 py-2 text-sm font-medium text-sky-300 transition hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy === "analyze" ? "Computing…" : `Analyze ${indexKey.toUpperCase()} over AOI`}
            </button>

            <button
              onClick={() => void runSeries()}
              disabled={!bbox || tsBusy}
              className="mt-1.5 w-full rounded-md border border-edge py-1.5 text-[11px] text-slate-300 transition hover:border-sky-500/60 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {tsBusy ? "Sampling acquisitions…" : `Trend of ${indexKey.toUpperCase()} over time`}
            </button>

            {series && (
              <div className="mt-2 rounded-lg border border-edge bg-ink/50 p-2.5">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] text-slate-300">
                    {series.label} · {series.count} acquisition{series.count === 1 ? "" : "s"}
                  </span>
                  <button
                    onClick={downloadSeriesCsv}
                    className="rounded border border-edge px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-sky-500/60"
                  >
                    CSV
                  </button>
                </div>
                <Series series={series} />
                {series.change && (
                  <div className="mt-1 font-mono text-[10px] text-slate-400">
                    Δ {series.change.delta > 0 ? "+" : ""}
                    {series.change.delta.toFixed(4)} between {series.change.between[0]} and{" "}
                    {series.change.between[1]}
                    <span className="block text-slate-600">difference, not a fitted trend</span>
                  </div>
                )}
                {series.skipped.length > 0 && (
                  <p className="mt-1 text-[10px] text-amber-300/70">
                    {series.skipped.length} acquisition(s) had no valid pixels and were skipped.
                  </p>
                )}
              </div>
            )}
          </div>

          {/* result */}
          {result && (
            <div className="mt-4 rounded-lg border border-edge bg-ink/50 p-3">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-slate-200">{result.label}</span>
                <span className="flex items-center gap-1.5">
                  {result.stats?.source?.includes("fixture") && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                      offline fixture
                    </span>
                  )}
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      result.status === "ok"
                        ? "bg-emerald-500/15 text-emerald-300"
                        : "bg-amber-500/15 text-amber-300"
                    }`}
                  >
                    {result.status}
                  </span>
                </span>
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-slate-500">{result.formula}</div>

              {result.stats ? (
                <>
                  <div className="mt-2">
                    <StatRow label="mean" value={fmt(result.stats.mean, 4)} />
                    <StatRow label="min" value={fmt(result.stats.min, 4)} />
                    <StatRow label="max" value={fmt(result.stats.max, 4)} />
                    <StatRow label="std dev" value={fmt(result.stats.std, 4)} />
                    <StatRow
                      label="valid pixels"
                      value={result.stats.count.toLocaleString()}
                      hint="server-side"
                    />
                  </div>
                  {result.histogram?.counts?.length ? (
                    <Histogram
                      counts={result.histogram.counts}
                      min={result.histogram.min}
                      max={result.histogram.max}
                    />
                  ) : null}
                  <div className="mt-2 text-[10px] text-slate-600">
                    source: {result.stats.source} · function: {result.stats.raster_function}
                  </div>
                </>
              ) : (
                <p className="mt-2 text-xs leading-relaxed text-amber-200/80">{result.message}</p>
              )}

              {result.visual_png_url && (
                <img
                  src={result.visual_png_url}
                  alt={`${result.label} rendering over the AOI`}
                  className="mt-2.5 w-full rounded border border-edge"
                />
              )}

              {result.evidence_id && (
                <div className="mt-2.5 border-t border-edge pt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wide text-slate-500">
                      evidence id
                    </span>
                    <div className="flex gap-1.5">
                      <button
                        onClick={() => void copyEvidence()}
                        className="rounded border border-edge px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-sky-500/60"
                      >
                        {copied ? "copied" : "copy"}
                      </button>
                      <button
                        onClick={() => void runReplay()}
                        disabled={busy !== null}
                        className="rounded border border-edge px-1.5 py-0.5 text-[10px] text-slate-300 hover:border-sky-500/60 disabled:opacity-40"
                      >
                        replay
                      </button>
                    </div>
                  </div>
                  <div className="mt-1 break-all font-mono text-[11px] text-sky-300">
                    {result.evidence_id}
                  </div>
                </div>
              )}

              {replay && (
                <div
                  className={`mt-2 rounded border p-2 text-[10px] leading-relaxed ${
                    replay.comparison.matched
                      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                      : "border-amber-500/40 bg-amber-500/10 text-amber-200"
                  }`}
                >
                  <div className="font-medium">
                    replay {replay.comparison.matched ? "matched" : "differs"}
                  </div>
                  <div className="mt-0.5 opacity-80">{replay.interpretation}</div>
                  {Object.entries(replay.comparison.absolute_differences).map(([k, v]) => (
                    <div key={k} className="mt-0.5 font-mono opacity-70">
                      Δ{k} = {v.toExponential(2)}
                    </div>
                  ))}
                </div>
              )}

              <button
                onClick={downloadGeoJson}
                className="mt-2 w-full rounded border border-edge py-1.5 text-[11px] text-slate-300 hover:border-sky-500/60"
              >
                Export GeoJSON
              </button>
              <button
                onClick={() => window.print()}
                className="mt-1.5 w-full rounded border border-edge py-1.5 text-[11px] text-slate-300 hover:border-sky-500/60"
              >
                Print / save as PDF
              </button>
            </div>
          )}

          {warnings.length > 0 && (
            <div className="mt-4 rounded-md border border-amber-500/30 bg-amber-500/5 p-2.5">
              <div className="text-[10px] font-medium uppercase tracking-wide text-amber-400/80">
                Notes from this run
              </div>
              <ul className="mt-1 space-y-1">
                {warnings.map((w, i) => (
                  <li key={i} className="text-[10px] leading-relaxed text-amber-200/70">
                    · {w}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-4 border-t border-edge pt-3 text-[10px] leading-relaxed text-slate-600">
            Index statistics come from the ArcGIS Sentinel-2 ImageServer, which holds a rolling
            ~14-month window. Historical ranges are served by the STAC catalogues, which return
            scene metadata but not server-side index values.
          </p>
        </aside>

        {/* map + scene strip */}
        <main className="relative min-w-0 flex-1">
          <MapView
            bbox={bbox}
            drawMode={drawMode}
            onBbox={handleBbox}
            onDrawEnd={() => setDrawMode(false)}
          />

          <div className="pointer-events-none absolute inset-x-0 bottom-0 p-3">
            <div className="pointer-events-auto rounded-lg border border-edge bg-ink/85 p-2.5 backdrop-blur">
              <div className="mb-2 flex items-center justify-between px-0.5">
                <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                  Scenes
                  <span className="ml-1.5 font-mono text-slate-500">{scenes.length}</span>
                </span>
                <span className="text-[10px] text-slate-600">
                  ranked by clarity · dates · index support · resolution
                </span>
              </div>
              {scenes.length === 0 ? (
                <p className="px-0.5 pb-1 text-[11px] text-slate-600">
                  {busy === "search" || busy === "interpret"
                    ? "Searching providers…"
                    : "No scenes yet. Run a query or draw an AOI."}
                </p>
              ) : (
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {scenes.map((scene) => (
                    <SceneCard
                      key={`${scene.provider}-${scene.id}`}
                      scene={scene}
                      onPick={() => {
                        if (!scene.bbox || scene.bbox.length !== 4) return;
                        setResult(null);
                        setReplay(null);
                        setBbox(scene.bbox as BBox);
                      }}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
        </main>
      </div>
      </div>

      <Report result={result} bbox={bbox} intent={intent} chosen={chosen} />
    </>
  );
}
