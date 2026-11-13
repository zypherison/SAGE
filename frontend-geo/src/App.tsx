import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapCanvas, { type DrawMode } from "./MapCanvas";
import {
  aoiMetrics,
  exportMap,
  getCapabilities,
  runIndex,
  runPopulation,
} from "./api";
import type {
  AoiMetrics,
  Capabilities,
  IndexResult,
  MapResult,
  PolygonGeometry,
  PopulationResult,
} from "./types";

const INDEXES = [
  { id: "ndvi", label: "NDVI", hint: "vegetation" },
  { id: "ndwi", label: "NDWI", hint: "water" },
  { id: "ndbi", label: "NDBI", hint: "built-up" },
  { id: "nbr", label: "NBR", hint: "burn" },
  { id: "ndmi", label: "NDMI", hint: "moisture" },
];

const isoDate = (date: Date) => date.toISOString().slice(0, 10);
const daysAgo = (days: number) => isoDate(new Date(Date.now() - days * 86_400_000));
const number = (value: number | null | undefined, digits = 3) =>
  value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
const count = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : value.toLocaleString("en-IN");

type Tab = "tools" | "result";

export default function App() {
  const [geometry, setGeometry] = useState<PolygonGeometry | null>(null);
  const [mode, setMode] = useState<DrawMode>("none");
  const [tab, setTab] = useState<Tab>("tools");

  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [metrics, setMetrics] = useState<AoiMetrics | null>(null);
  const [index, setIndex] = useState<IndexResult | null>(null);
  const [population, setPopulation] = useState<PopulationResult | null>(null);
  const [mapResult, setMapResult] = useState<MapResult | null>(null);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [cursor, setCursor] = useState({ lat: 17.4239, lng: 78.4738, zoom: 11 });
  const [copied, setCopied] = useState(false);

  const [indexName, setIndexName] = useState("ndvi");
  const [dateFrom, setDateFrom] = useState(daysAgo(24));
  const [dateTo, setDateTo] = useState(isoDate(new Date()));
  const [maxCloud, setMaxCloud] = useState(20);
  const [cloudMask, setCloudMask] = useState(true);
  const [preview, setPreview] = useState(true);

  const [iso3, setIso3] = useState("IND");
  const [year, setYear] = useState(2020);

  const copyTimer = useRef<number | null>(null);

  useEffect(() => {
    getCapabilities().then(setCapabilities).catch((exc: Error) => setError(exc.message));
  }, []);

  // AOI metrics follow the drawing, so area is known before any index runs.
  useEffect(() => {
    if (!geometry) {
      setMetrics(null);
      return;
    }
    let cancelled = false;
    aoiMetrics(geometry)
      .then((result) => {
        if (!cancelled) setMetrics(result);
      })
      .catch((exc: Error) => {
        if (!cancelled) setError(exc.message);
      });
    return () => {
      cancelled = true;
    };
  }, [geometry]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMode("none");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const clearAll = useCallback(() => {
    setGeometry(null);
    setMetrics(null);
    setIndex(null);
    setPopulation(null);
    setMapResult(null);
    setError(null);
    setMode("none");
    setTab("tools");
  }, []);

  const withTiming = useCallback(async (label: string, work: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    const started = performance.now();
    try {
      await work();
      setElapsed((performance.now() - started) / 1000);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(null);
    }
  }, []);

  const analyse = () =>
    withTiming("index", async () => {
      if (!geometry) throw new Error("draw an AOI first");
      const result = await runIndex({
        geometry,
        index: indexName,
        dateFrom,
        dateTo,
        maxCloud,
        cloudMask,
        preview,
      });
      setIndex(result);
      setTab("result");
    });

  const estimatePopulation = () =>
    withTiming("population", async () => {
      if (!geometry) throw new Error("draw an AOI first");
      const result = await runPopulation(geometry, iso3.trim().toUpperCase(), Number(year));
      setPopulation(result);
      setTab("result");
    });

  const publishMap = () =>
    withTiming("map", async () => {
      if (!geometry) throw new Error("draw an AOI first");
      const result = await exportMap(geometry, "SAGE Geo Workbench AOI");
      setMapResult(result);
      window.open(result.url, "_blank", "noopener");
    });

  const copyCitation = async () => {
    if (!index) return;
    await navigator.clipboard.writeText(index.citation);
    setCopied(true);
    if (copyTimer.current) window.clearTimeout(copyTimer.current);
    copyTimer.current = window.setTimeout(() => setCopied(false), 1600);
  };

  const histogram = useMemo(() => {
    const bins = index?.histogram?.bins ?? [];
    const peak = Math.max(1, ...bins.map((bin) => bin.count));
    return bins.map((bin) => ({ ...bin, share: bin.count / peak }));
  }, [index]);

  const ready = Boolean(geometry) && !busy;

  return (
    <div className="workbench">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ◈
          </span>
          <div>
            <h1>
              SAGE <span>Geo Workbench</span>
            </h1>
            <p>branch geo/geostack · raster, vector and population lab</p>
          </div>
        </div>

        <div className="cap-chips" aria-label="Backend capabilities">
          <Chip label="Vector" on={capabilities?.vector_ready} />
          <Chip label="Raster" on={capabilities?.raster_ready} />
          <Chip label="WorldPop" on={capabilities?.population_ready} />
          <Chip
            label={capabilities?.earth_engine.configured ? "Earth Engine" : "EE off"}
            on={capabilities?.earth_engine.configured}
            muted={!capabilities?.earth_engine.configured}
          />
        </div>

        <div className="top-actions">
          <button type="button" className="ghost" onClick={publishMap} disabled={!ready}>
            {busy === "map" ? "Publishing…" : "Publish map"}
          </button>
          <button type="button" className="ghost" onClick={clearAll}>
            Clear
          </button>
        </div>
      </header>

      <main className="workspace">
        <section className="map-shell">
          <MapCanvas
            geometry={geometry}
            mode={mode}
            onGeometry={setGeometry}
            onFinish={() => setMode("none")}
            onCursor={(lat, lng, zoom) => setCursor({ lat, lng, zoom })}
          />

          <div className="draw-toolbar" role="toolbar" aria-label="AOI tools">
            <button
              type="button"
              className={mode === "none" ? "is-active" : ""}
              onClick={() => setMode("none")}
              title="Pan and zoom"
            >
              Pan
            </button>
            <button
              type="button"
              className={mode === "rect" ? "is-active" : ""}
              onClick={() => setMode("rect")}
            >
              Box
            </button>
            <button
              type="button"
              className={mode === "polygon" ? "is-active" : ""}
              onClick={() => setMode("polygon")}
            >
              Polygon
            </button>
          </div>

          <aside className="layers-card">
            <div className="layers-head">
              <span>Layers</span>
              <span className="badge">{geometry ? "1 AOI" : "empty"}</span>
            </div>
            <div className={`layer-row ${geometry ? "" : "is-empty"}`}>
              <span className="swatch teal" />
              <div>
                <strong>Area of interest</strong>
                <small>
                  {metrics ? `${number(metrics.area_km2, 2)} km² · ${metrics.projected.crs}` : "not drawn"}
                </small>
              </div>
            </div>
            <div className={`layer-row ${index?.preview_url ? "" : "is-empty"}`}>
              <span className="swatch lime" />
              <div>
                <strong>{index ? `${index.label} preview` : "Index raster"}</strong>
                <small>
                  {index?.scene.tile ?? "run an index"} · {index ? `${index.grid.res_m[0]} m` : "—"}
                </small>
              </div>
            </div>
            <p className="layers-note">
              {mode === "polygon"
                ? "Click each corner, double-click to close. Esc cancels."
                : mode === "rect"
                  ? "Drag a rectangle on the map."
                  : "Draw an AOI to enable the tools."}
            </p>
          </aside>
        </section>

        <aside className="dock">
          <nav className="dock-tabs">
            <button type="button" className={tab === "tools" ? "is-active" : ""} onClick={() => setTab("tools")}>
              Tools
            </button>
            <button type="button" className={tab === "result" ? "is-active" : ""} onClick={() => setTab("result")}>
              Output
              {(index || population) && <span className="dot" />}
            </button>
          </nav>

          <div className="dock-body">
            {error && (
              <div className="error-banner" role="alert">
                <span>{error}</span>
                <button type="button" onClick={() => setError(null)} aria-label="Dismiss">
                  ×
                </button>
              </div>
            )}

            {tab === "tools" ? (
              <>
                <section className="card">
                  <header>
                    <h2>Spectral index</h2>
                    <span className="muted">single scene · COG window</span>
                  </header>

                  <div className="index-picker">
                    {INDEXES.map((entry) => (
                      <button
                        key={entry.id}
                        type="button"
                        className={entry.id === indexName ? "is-active" : ""}
                        onClick={() => setIndexName(entry.id)}
                      >
                        <strong>{entry.label}</strong>
                        <small>{entry.hint}</small>
                      </button>
                    ))}
                  </div>

                  <div className="field-row">
                    <label>
                      From
                      <input type="date" value={dateFrom} max={dateTo} onChange={(e) => setDateFrom(e.target.value)} />
                    </label>
                    <label>
                      To
                      <input type="date" value={dateTo} min={dateFrom} onChange={(e) => setDateTo(e.target.value)} />
                    </label>
                  </div>

                  <label className="slider">
                    <span>
                      Max scene cloud <strong>{maxCloud}%</strong>
                    </span>
                    <input
                      type="range"
                      min={0}
                      max={60}
                      step={5}
                      value={maxCloud}
                      onChange={(e) => setMaxCloud(Number(e.target.value))}
                    />
                  </label>

                  <div className="toggles">
                    <label>
                      <input type="checkbox" checked={cloudMask} onChange={(e) => setCloudMask(e.target.checked)} />
                      SCL cloud mask
                    </label>
                    <label>
                      <input type="checkbox" checked={preview} onChange={(e) => setPreview(e.target.checked)} />
                      PNG preview
                    </label>
                  </div>

                  <button type="button" className="primary" onClick={analyse} disabled={!ready}>
                    {busy === "index" ? "Reading COG window…" : geometry ? `Run ${indexName.toUpperCase()}` : "Draw an AOI first"}
                  </button>
                </section>

                <section className="card">
                  <header>
                    <h2>Population</h2>
                    <span className="muted">WorldPop 1 km, cached</span>
                  </header>
                  <div className="field-row compact">
                    <label>
                      Country
                      <input
                        value={iso3}
                        maxLength={3}
                        onChange={(e) => setIso3(e.target.value.toUpperCase())}
                        placeholder="IND"
                      />
                    </label>
                    <label>
                      Year
                      <input
                        type="number"
                        min={2000}
                        max={2020}
                        value={year}
                        onChange={(e) => setYear(Number(e.target.value))}
                      />
                    </label>
                  </div>
                  <button type="button" className="secondary" onClick={estimatePopulation} disabled={!ready}>
                    {busy === "population" ? "Summing raster…" : "Estimate population"}
                  </button>
                </section>

                <section className="card subtle">
                  <header>
                    <h2>AOI</h2>
                    <span className="muted">geodesic + UTM check</span>
                  </header>
                  {metrics ? (
                    <dl className="metric-grid">
                      <div>
                        <dt>Area</dt>
                        <dd>{number(metrics.area_km2, 3)} km²</dd>
                      </div>
                      <div>
                        <dt>Perimeter</dt>
                        <dd>{number(metrics.perimeter_km, 2)} km</dd>
                      </div>
                      <div>
                        <dt>Projection Δ</dt>
                        <dd>
                          {(number(metrics.projected.relative_area_difference ? metrics.projected.relative_area_difference * 100 : null, 3))}%
                        </dd>
                      </div>
                      <div>
                        <dt>Compactness</dt>
                        <dd>{number(metrics.compactness, 3)}</dd>
                      </div>
                    </dl>
                  ) : (
                    <p className="empty">No AOI yet. Draw a box or polygon on the map.</p>
                  )}
                </section>
              </>
            ) : (
              <OutputPane
                index={index}
                population={population}
                mapResult={mapResult}
                histogram={histogram}
                copied={copied}
                onCopy={copyCitation}
              />
            )}
          </div>

          <footer className="dock-foot">
            <span>{busy ? `${busy} running…` : "ready"}</span>
            <span>{elapsed !== null ? `${elapsed.toFixed(1)} s last run` : "no runs yet"}</span>
          </footer>
        </aside>
      </main>

      <footer className="statusbar">
        <span className="coords">
          {cursor.lat.toFixed(4)}, {cursor.lng.toFixed(4)}
        </span>
        <span>z{cursor.zoom}</span>
        <span>{metrics ? `${number(metrics.area_km2, 2)} km²` : "no AOI"}</span>
        <span>
          {index ? `${count(index.pixels.valid)} px valid · ${index.scene.tile ?? index.scene.id}` : "no scene"}
        </span>
        <span className="grow" />
        <span className="dotline">
          <i className={capabilities ? "ok" : ""} />
          {capabilities ? "backend online" : "connecting"}
        </span>
      </footer>
    </div>
  );
}

function Chip({ label, on, muted }: { label: string; on?: boolean | null; muted?: boolean }) {
  return (
    <span className={`chip ${on ? "on" : ""} ${muted ? "muted" : ""}`}>
      <i />
      {label}
    </span>
  );
}

function OutputPane({
  index,
  population,
  mapResult,
  histogram,
  copied,
  onCopy,
}: {
  index: IndexResult | null;
  population: PopulationResult | null;
  mapResult: MapResult | null;
  histogram: { low: number; high: number; count: number; share: number }[];
  copied: boolean;
  onCopy: () => void;
}) {
  if (!index && !population) {
    return (
      <div className="empty-state">
        <span className="empty-mark">◌</span>
        <h3>Nothing measured yet</h3>
        <p>Draw an AOI, then run an index. Every result records its scene, grid and reflectance convention.</p>
      </div>
    );
  }

  return (
    <>
      {index && index.stats && (
        <section className="card result-card">
          <header>
            <h2>
              {index.label} <span className="muted">mean</span>
            </h2>
            <span className="badge">{index.scene.datetime?.slice(0, 10)}</span>
          </header>

          <div className="headline">
            <strong>{number(index.stats.mean, 3)}</strong>
            <span>
              median {number(index.stats.median, 3)} · p10 {number(index.stats.p10, 3)} · p90{" "}
              {number(index.stats.p90, 3)}
            </span>
          </div>

          <div className="histogram" aria-label="Index histogram">
            {histogram.map((bin) => (
              <span
                key={bin.low}
                style={{ height: `${Math.max(2, bin.share * 100)}%` }}
                title={`${bin.low.toFixed(2)}…${bin.high.toFixed(2)}: ${bin.count.toLocaleString()}`}
              />
            ))}
          </div>
          <div className="histogram-axis">
            <span>−1</span>
            <span>0</span>
            <span>+1</span>
          </div>

          <dl className="metric-grid">
            <div>
              <dt>Valid pixels</dt>
              <dd>{count(index.pixels.valid)}</dd>
            </div>
            <div>
              <dt>Cloud-masked</dt>
              <dd>{count(index.pixels.cloud_masked)}</dd>
            </div>
            <div>
              <dt>Std</dt>
              <dd>{number(index.stats.std, 3)}</dd>
            </div>
            <div>
              <dt>Read</dt>
              <dd>{index.read_seconds.toFixed(1)} s</dd>
            </div>
          </dl>

          {index.preview_url && (
            <figure className="preview">
              <img src={index.preview_url} alt={`${index.label} preview over the AOI`} loading="lazy" />
              <figcaption>
                {index.grid.width}×{index.grid.height} px at {index.grid.res_m[0]} m · {index.grid.crs}
              </figcaption>
            </figure>
          )}

          <div className="provenance">
            <p>
              <span>Scene</span> {index.scene.id}
            </p>
            <p>
              <span>Reflectance</span> DN × {index.reflectance.scale}
              {index.reflectance.offset === 0 ? " (offset already applied)" : ` ${index.reflectance.offset}`} ·{" "}
              {index.reflectance.source}
            </p>
            <p>
              <span>Mask</span>{" "}
              {index.cloud_mask.applied
                ? `SCL classes ${index.cloud_mask.classes.join(", ")} dropped`
                : "none"}
            </p>
            <p>
              <span>Evidence</span> <code>{index.evidence_id ?? "not stored"}</code>
            </p>
          </div>

          <div className="result-actions">
            <button type="button" className="ghost" onClick={onCopy}>
              {copied ? "Citation copied" : "Copy citation"}
            </button>
            {index.scene.source_url && (
              <a className="ghost" href={index.scene.source_url} target="_blank" rel="noreferrer">
                STAC item ↗
              </a>
            )}
          </div>
        </section>
      )}

      {population && (
        <section className="card result-card">
          <header>
            <h2>
              Population <span className="muted">{population.source.year}</span>
            </h2>
            <span className="badge">{population.status.replace(/_/g, " ")}</span>
          </header>
          <div className="headline">
            <strong>{count(population.population)}</strong>
            <span>
              {number(population.density_per_km2, 0)} /km² over {number(population.area_km2, 2)} km²
            </span>
          </div>
          <p className="small">
            {population.pixels_used} of the 1 km cells intersecting the AOI carry data · {population.source.dataset}
          </p>
          {population.notes.length > 0 && (
            <ul className="notes">
              {population.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {mapResult && (
        <section className="card result-card">
          <header>
            <h2>Exported map</h2>
            <span className="badge">{Math.round(mapResult.bytes / 1024)} KB</span>
          </header>
          <p className="small">{mapResult.layers.join(" · ")}</p>
          {mapResult.note && <p className="small muted">{mapResult.note}</p>}
          <a className="ghost block" href={mapResult.url} target="_blank" rel="noreferrer">
            Open interactive map ↗
          </a>
        </section>
      )}
    </>
  );
}
