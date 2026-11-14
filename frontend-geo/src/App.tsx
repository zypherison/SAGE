import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import MapCanvas, { type DrawMode } from "./MapCanvas";
import {
  aoiMetrics,
  exportMap,
  getCapabilities,
  runIndex,
  runLandcover,
  runPopulation,
} from "./api";
import type {
  AoiMetrics,
  Capabilities,
  IndexResult,
  LandcoverResult,
  MapResult,
  PolygonGeometry,
  PopulationResult,
} from "./types";
import {
  IconChart,
  IconCursor,
  IconEmpty,
  IconExternal,
  IconLayers,
  IconMark,
  IconPolygon,
  IconSquare,
  IconTerrain,
  IconTrash,
} from "./icons";

// Split out: MapLibre is only paid for when someone opens the 3D view.
const TerrainView = lazy(() => import("./TerrainView"));

const INDEXES = [
  { id: "ndvi", label: "NDVI", description: "Vegetation" },
  { id: "ndwi", label: "NDWI", description: "Surface water" },
  { id: "ndbi", label: "NDBI", description: "Built-up" },
  { id: "nbr", label: "NBR", description: "Burn severity" },
  { id: "ndmi", label: "NDMI", description: "Moisture" },
];

const isoDate = (date: Date) => date.toISOString().slice(0, 10);
const daysAgo = (days: number) => isoDate(new Date(Date.now() - days * 86_400_000));
const decimal = (value: number | null | undefined, digits = 3) =>
  value === null || value === undefined || Number.isNaN(value) ? "—" : value.toFixed(digits);
const integer = (value: number | null | undefined) =>
  value === null || value === undefined ? "—" : value.toLocaleString("en-IN");

type Tab = "tools" | "results";

export default function App() {
  const [geometry, setGeometry] = useState<PolygonGeometry | null>(null);
  const [mode, setMode] = useState<DrawMode>("none");
  const [view3d, setView3d] = useState(false);
  const [tab, setTab] = useState<Tab>("tools");

  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [metrics, setMetrics] = useState<AoiMetrics | null>(null);
  const [index, setIndex] = useState<IndexResult | null>(null);
  const [population, setPopulation] = useState<PopulationResult | null>(null);
  const [landcover, setLandcover] = useState<LandcoverResult | null>(null);
  const [mapResult, setMapResult] = useState<MapResult | null>(null);
  const [showPublished, setShowPublished] = useState(false);

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
    getCapabilities()
      .then(setCapabilities)
      .catch((exc: Error) => setError(exc.message));
  }, []);

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
      if (!geometry) throw new Error("Draw an area of interest first.");
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
      setTab("results");
    });

  const estimatePopulation = () =>
    withTiming("population", async () => {
      if (!geometry) throw new Error("Draw an area of interest first.");
      const result = await runPopulation(geometry, iso3.trim().toUpperCase(), Number(year));
      setPopulation(result);
      setTab("results");
    });

  const classifyLand = () =>
    withTiming("landcover", async () => {
      if (!geometry) throw new Error("Draw an area of interest first.");
      const result = await runLandcover(geometry, dateFrom, dateTo);
      setLandcover(result);
      setTab("results");
    });

  const publishMap = () =>
    withTiming("map", async () => {
      if (!geometry) throw new Error("Draw an area of interest first.");
      const result = await exportMap(geometry, "SAGE Geo Workbench AOI", Boolean(landcover));
      setMapResult(result);
      setShowPublished(true);
    });

  const copyCitation = async () => {
    if (!index) return;
    await navigator.clipboard.writeText(index.citation);
    setCopied(true);
    if (copyTimer.current) window.clearTimeout(copyTimer.current);
    copyTimer.current = window.setTimeout(() => setCopied(false), 1800);
  };

  const histogram = useMemo(() => {
    const bins = index?.histogram?.bins ?? [];
    const peak = Math.max(1, ...bins.map((bin) => bin.count));
    return bins.map((bin) => ({ ...bin, share: bin.count / peak }));
  }, [index]);

  const ready = Boolean(geometry) && !busy;
  const resultCount =
    Number(Boolean(index)) + Number(Boolean(population)) + Number(Boolean(landcover));
  const selectedIndex = INDEXES.find((entry) => entry.id === indexName) ?? INDEXES[0];

  return (
    <div className="app">
      <header className="appbar">
        <div className="brand">
          <span className="brand-mark">
            <IconMark size={20} />
          </span>
          <div className="brand-text">
            <h1>
              SAGE <span>Geo Workbench</span>
            </h1>
            <p>Raster and vector analysis · branch geo/geostack</p>
          </div>
        </div>

        <div className="appbar-meta">
          <span className={`connection ${capabilities ? "is-live" : ""}`}>
            <i />
            {capabilities ? "API connected" : "Connecting"}
          </span>
          <span className="meta-divider" />
          <span className="meta">
            rasterio {capabilities?.installed.rasterio ?? "—"}
          </span>
          <span className="meta">
            {capabilities?.earth_engine.configured
              ? `Earth Engine · ${capabilities.earth_engine.project ?? "project set"}`
              : "Earth Engine not configured"}
          </span>
        </div>

        <div className="appbar-actions">
          <button type="button" className="btn" onClick={publishMap} disabled={!ready}>
            <IconExternal />
            {busy === "map" ? "Publishing…" : "Publish map"}
          </button>
          <button
            type="button"
            className="btn btn-icon"
            onClick={clearAll}
            title="Clear area of interest"
            aria-label="Clear area of interest"
          >
            <IconTrash />
          </button>
        </div>
      </header>

      <div className="stage">
        <main className="map-pane">
          <MapCanvas
            geometry={geometry}
            mode={mode}
            onGeometry={setGeometry}
            onFinish={() => setMode("none")}
            onCursor={(lat, lng, zoom) => setCursor({ lat, lng, zoom })}
          />

          {view3d && (
            <div className="terrain-pane">
              <Suspense fallback={<div className="terrain-loading">Loading 3D terrain…</div>}>
                <TerrainView geometry={geometry} />
              </Suspense>
            </div>
          )}

          <div className="map-tools" role="toolbar" aria-label="Draw tools">
            <button
              type="button"
              className={mode === "none" ? "is-active" : ""}
              onClick={() => setMode("none")}
              title="Pan and zoom"
              aria-label="Pan and zoom"
            >
              <IconCursor />
            </button>
            <span className="tool-separator" />
            <button
              type="button"
              className={mode === "rect" ? "is-active" : ""}
              onClick={() => setMode("rect")}
              disabled={view3d}
              title={view3d ? "Switch to the 2D view to draw" : "Draw a rectangle"}
            >
              <IconSquare />
              Box
            </button>
            <button
              type="button"
              className={mode === "polygon" ? "is-active" : ""}
              onClick={() => setMode("polygon")}
              disabled={view3d}
              title={view3d ? "Switch to the 2D view to draw" : "Draw a polygon"}
            >
              <IconPolygon />
              Polygon
            </button>
            <span className="tool-separator" />
            <button
              type="button"
              className={view3d ? "is-active" : ""}
              onClick={() => {
                setView3d((value) => !value);
                setMode("none");
              }}
              title="Toggle 3D terrain view"
            >
              <IconTerrain />
              3D
            </button>
            {mapResult && (
              <>
                <span className="tool-separator" />
                <button
                  type="button"
                  className={showPublished ? "is-active" : ""}
                  onClick={() => setShowPublished((value) => !value)}
                  title="Show the published Earth Engine map"
                >
                  <IconLayers />
                  Published
                </button>
              </>
            )}
          </div>

          <section className="layer-panel" aria-label="Layers">
            <header>
              <span>
                <IconLayers />
                Layers
              </span>
              <span className="layer-count">{geometry ? 1 : 0}</span>
            </header>
            <ul>
              <li className={geometry ? "" : "is-empty"}>
                <i className="dot dot-aoi" />
                <div>
                  <strong>Area of interest</strong>
                  <span>
                    {metrics
                      ? `${decimal(metrics.area_km2, 2)} km² · ${metrics.projected.crs}`
                      : "Not drawn"}
                  </span>
                </div>
              </li>
              <li className={index?.preview_url ? "" : "is-empty"}>
                <i className="dot dot-index" />
                <div>
                  <strong>{index ? `${index.label} preview` : "Index raster"}</strong>
                  <span>
                    {index
                      ? `${index.grid.width} × ${index.grid.height} px · ${index.grid.res_m[0]} m`
                      : "Run an index to populate"}
                  </span>
                </div>
              </li>
            </ul>
            <p className="layer-hint">
              {view3d
                ? "3D terrain: drag to orbit, right-drag to pan. Drawing needs the 2D view."
                : mode === "polygon"
                  ? "Click each corner, then double-click to close. Esc cancels."
                  : mode === "rect"
                    ? "Drag a rectangle over the area to analyse."
                    : geometry
                      ? "Draw again to replace the current area."
                      : "Select Box or Polygon to begin."}
            </p>
          </section>

          {mapResult && showPublished && (
            <section className="published-map" aria-label="Published map">
              <div className="published-bar">
                <span className="published-title">
                  <IconLayers />
                  {mapResult.title} · {mapResult.layers.join(" + ")}
                </span>
                <span className="published-actions">
                  <a className="btn" href={mapResult.url} target="_blank" rel="noreferrer">
                    <IconExternal />
                    New tab
                  </a>
                  <button type="button" className="btn" onClick={() => setShowPublished(false)}>
                    Back to canvas
                  </button>
                </span>
              </div>
              <iframe src={mapResult.url} title={`Published map: ${mapResult.title}`} />
            </section>
          )}
        </main>

        <aside className="panel">
          <div className="panel-head">
            <div className="tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={tab === "tools"}
                className={tab === "tools" ? "is-active" : ""}
                onClick={() => setTab("tools")}
              >
                Analysis
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === "results"}
                className={tab === "results" ? "is-active" : ""}
                onClick={() => setTab("results")}
              >
                Results
                {resultCount > 0 && <span className="tab-count">{resultCount}</span>}
              </button>
            </div>
          </div>

          <div className="panel-body">
            {error && (
              <div className="alert" role="alert">
                <span>{error}</span>
                <button type="button" onClick={() => setError(null)} aria-label="Dismiss">
                  ×
                </button>
              </div>
            )}

            {tab === "tools" ? (
              <>
                <section className="section">
                  <div className="section-head">
                    <h2>Spectral index</h2>
                    <p>Sentinel-2 L2A · single scene · COG window read in place</p>
                  </div>

                  <div className="segmented" role="group" aria-label="Index">
                    {INDEXES.map((entry) => (
                      <button
                        key={entry.id}
                        type="button"
                        className={entry.id === indexName ? "is-active" : ""}
                        onClick={() => setIndexName(entry.id)}
                        title={entry.description}
                      >
                        {entry.label}
                      </button>
                    ))}
                  </div>
                  <p className="field-note">
                    {selectedIndex.label} — {selectedIndex.description}
                  </p>

                  <div className="field-row">
                    <label className="field">
                      <span>Start date</span>
                      <input
                        type="date"
                        value={dateFrom}
                        max={dateTo}
                        onChange={(event) => setDateFrom(event.target.value)}
                      />
                    </label>
                    <label className="field">
                      <span>End date</span>
                      <input
                        type="date"
                        value={dateTo}
                        min={dateFrom}
                        onChange={(event) => setDateTo(event.target.value)}
                      />
                    </label>
                  </div>

                  <div className="field">
                    <div className="field-label">
                      <span>Maximum scene cloud cover</span>
                      <strong>{maxCloud}%</strong>
                    </div>
                    <input
                      className="range"
                      type="range"
                      min={0}
                      max={60}
                      step={5}
                      value={maxCloud}
                      onChange={(event) => setMaxCloud(Number(event.target.value))}
                    />
                  </div>

                  <div className="checks">
                    <label>
                      <input
                        type="checkbox"
                        checked={cloudMask}
                        onChange={(event) => setCloudMask(event.target.checked)}
                      />
                      Apply SCL cloud mask
                    </label>
                    <label>
                      <input
                        type="checkbox"
                        checked={preview}
                        onChange={(event) => setPreview(event.target.checked)}
                      />
                      Generate preview image
                    </label>
                  </div>

                  <button type="button" className="btn btn-primary btn-block" onClick={analyse} disabled={!ready}>
                    {busy === "index"
                      ? "Reading scene…"
                      : geometry
                        ? `Run ${selectedIndex.label}`
                        : "Draw an area to analyse"}
                  </button>
                </section>

                <hr className="divider" />

                <section className="section">
                  <div className="section-head">
                    <h2>Land cover and structures</h2>
                    <p>Dynamic World 10 m classes, plus Open Buildings counts, over the same window</p>
                  </div>
                  <button
                    type="button"
                    className="btn btn-block"
                    onClick={classifyLand}
                    disabled={!ready}
                  >
                    {busy === "landcover" ? "Classifying…" : "Classify land cover"}
                  </button>
                  <p className="field-note">
                    Modelled cover per pixel, not object outlines: built-up is an area share.
                  </p>
                </section>

                <hr className="divider" />

                <section className="section">
                  <div className="section-head">
                    <h2>Population estimate</h2>
                    <p>WorldPop 1 km UN-adjusted, cached locally</p>
                  </div>
                  <div className="field-row">
                    <label className="field">
                      <span>Country (ISO3)</span>
                      <input
                        value={iso3}
                        maxLength={3}
                        onChange={(event) => setIso3(event.target.value.toUpperCase())}
                        placeholder="IND"
                        spellCheck={false}
                      />
                    </label>
                    <label className="field">
                      <span>Year</span>
                      <input
                        type="number"
                        min={2000}
                        max={2020}
                        value={year}
                        onChange={(event) => setYear(Number(event.target.value))}
                      />
                    </label>
                  </div>
                  <button
                    type="button"
                    className="btn btn-block"
                    onClick={estimatePopulation}
                    disabled={!ready}
                  >
                    {busy === "population" ? "Summing raster…" : "Estimate population"}
                  </button>
                </section>

                <hr className="divider" />

                <section className="section">
                  <div className="section-head">
                    <h2>Area of interest</h2>
                    <p>Geodesic measurement with a projected cross-check</p>
                  </div>
                  {metrics ? (
                    <table className="data-table">
                      <tbody>
                        <tr>
                          <th>Area</th>
                          <td>{decimal(metrics.area_km2, 3)} km²</td>
                        </tr>
                        <tr>
                          <th>Perimeter</th>
                          <td>{decimal(metrics.perimeter_km, 2)} km</td>
                        </tr>
                        <tr>
                          <th>Centroid</th>
                          <td>
                            {metrics.centroid[1].toFixed(4)}, {metrics.centroid[0].toFixed(4)}
                          </td>
                        </tr>
                        <tr>
                          <th>Projected area</th>
                          <td>
                            {decimal(metrics.projected.area_km2, 3)} km²
                            <span className="muted">
                              {" "}
                              · Δ
                              {metrics.projected.relative_area_difference === null
                                ? "—"
                                : (metrics.projected.relative_area_difference * 100).toFixed(3)}
                              %
                            </span>
                          </td>
                        </tr>
                        <tr>
                          <th>Vertices</th>
                          <td>{metrics.vertices}</td>
                        </tr>
                        <tr>
                          <th>Geometry</th>
                          <td>
                            {metrics.audit.repaired ? "Repaired on load" : "Valid on load"}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  ) : (
                    <p className="empty-note">
                      No area drawn. Use Box or Polygon on the map; measurements update as soon as
                      the shape is closed.
                    </p>
                  )}
                </section>
              </>
            ) : (
              <ResultsPane
                index={index}
                population={population}
                landcover={landcover}
                mapResult={mapResult}
                histogram={histogram}
                copied={copied}
                onCopy={copyCitation}
                onOpenTools={() => setTab("tools")}
                onShowMap={() => setShowPublished(true)}
              />
            )}
          </div>

          <div className="panel-foot">
            <span>{busy ? `${busy} in progress` : "Ready"}</span>
            <span>{elapsed === null ? "No runs yet" : `${elapsed.toFixed(1)} s last run`}</span>
          </div>
        </aside>
      </div>

      <footer className="statusbar">
        <span className="coordinates">
          {cursor.lat.toFixed(4)}, {cursor.lng.toFixed(4)}
        </span>
        <span>Zoom {cursor.zoom}</span>
        <span className="status-divider" />
        <span>{metrics ? `${decimal(metrics.area_km2, 2)} km² selected` : "No selection"}</span>
        <span className="status-divider" />
        <span>
          {index
            ? `${integer(index.pixels.valid)} valid pixels · ${index.scene.tile ?? index.scene.id}`
            : "No scene loaded"}
        </span>
        <span className="grow" />
        <span className="status-end">SAGE v2 · geostack</span>
      </footer>
    </div>
  );
}

function ResultsPane({
  index,
  population,
  landcover,
  mapResult,
  histogram,
  copied,
  onCopy,
  onOpenTools,
  onShowMap,
}: {
  index: IndexResult | null;
  population: PopulationResult | null;
  landcover: LandcoverResult | null;
  mapResult: MapResult | null;
  histogram: { low: number; high: number; count: number; share: number }[];
  copied: boolean;
  onCopy: () => void;
  onOpenTools: () => void;
  onShowMap: () => void;
}) {
  if (!index && !population && !landcover) {
    return (
      <div className="empty-state">
        <span className="empty-icon">
          <IconEmpty />
        </span>
        <h3>No results yet</h3>
        <p>
          Draw an area on the map, then run a spectral index. Each result records the scene, the
          grid and the reflectance convention it used.
        </p>
        <button type="button" className="btn" onClick={onOpenTools}>
          <IconChart />
          Go to analysis
        </button>
      </div>
    );
  }

  return (
    <>
      {index && index.stats && (
        <section className="result">
          <header className="result-head">
            <div>
              <h2>{index.label}</h2>
              <p>
                {index.scene.collection ?? "sentinel-2-l2a"} · {index.scene.datetime?.slice(0, 10)}
              </p>
            </div>
            <span className="pill">
              {index.scene.cloud_cover === null
                ? "cloud n/a"
                : `${index.scene.cloud_cover.toFixed(1)}% cloud`}
            </span>
          </header>

          <div className="headline-stat">
            <div className="headline-value">
              <strong>{decimal(index.stats.mean, 3)}</strong>
              <span>mean {index.label}</span>
            </div>
            <dl className="headline-grid">
              <div>
                <dt>Median</dt>
                <dd>{decimal(index.stats.median, 3)}</dd>
              </div>
              <div>
                <dt>p10</dt>
                <dd>{decimal(index.stats.p10, 3)}</dd>
              </div>
              <div>
                <dt>p90</dt>
                <dd>{decimal(index.stats.p90, 3)}</dd>
              </div>
            </dl>
          </div>

          <figure className="chart">
            <div className="chart-bars" role="img" aria-label={`${index.label} distribution`}>
              {histogram.map((bin) => (
                <span
                  key={bin.low}
                  style={{ height: `${Math.max(3, bin.share * 100)}%` }}
                  title={`${bin.low.toFixed(2)} to ${bin.high.toFixed(2)}: ${bin.count.toLocaleString()} pixels`}
                />
              ))}
            </div>
            <div className="chart-axis">
              <span>−1</span>
              <span>0</span>
              <span>+1</span>
            </div>
            <figcaption>Pixel distribution across the area of interest</figcaption>
          </figure>

          <table className="data-table">
            <tbody>
              <tr>
                <th>Valid pixels</th>
                <td>{integer(index.pixels.valid)}</td>
              </tr>
              <tr>
                <th>Cloud-masked</th>
                <td>{integer(index.pixels.cloud_masked)}</td>
              </tr>
              <tr>
                <th>Standard deviation</th>
                <td>{decimal(index.stats.std, 3)}</td>
              </tr>
              <tr>
                <th>Range</th>
                <td>
                  {decimal(index.stats.min, 3)} to {decimal(index.stats.max, 3)}
                </td>
              </tr>
              <tr>
                <th>Grid</th>
                <td>
                  {index.grid.width} × {index.grid.height} px · {index.grid.res_m[0]} m ·{" "}
                  {index.grid.crs}
                </td>
              </tr>
              <tr>
                <th>Read time</th>
                <td>{index.read_seconds.toFixed(1)} s</td>
              </tr>
            </tbody>
          </table>

          {index.preview_url && (
            <figure className="preview">
              <img src={index.preview_url} alt={`${index.label} preview over the area of interest`} />
              <figcaption>{index.label} across the selected window</figcaption>
            </figure>
          )}

          <div className="provenance">
            <h3>Provenance</h3>
            <dl>
              <div>
                <dt>Scene</dt>
                <dd>{index.scene.id}</dd>
              </div>
              <div>
                <dt>Reflectance</dt>
                <dd>
                  DN × {index.reflectance.scale}
                  {index.reflectance.offset === 0 ? " (offset pre-applied)" : ` + ${index.reflectance.offset}`}
                  <span className="muted"> · {index.reflectance.source}</span>
                </dd>
              </div>
              <div>
                <dt>Cloud mask</dt>
                <dd>
                  {index.cloud_mask.applied
                    ? `SCL classes ${index.cloud_mask.classes.join(", ")} removed`
                    : "Not applied"}
                </dd>
              </div>
              <div>
                <dt>Evidence ID</dt>
                <dd>
                  <code>{index.evidence_id ?? "not stored"}</code>
                </dd>
              </div>
            </dl>
          </div>

          <div className="result-actions">
            <button type="button" className="btn" onClick={onCopy}>
              {copied ? "Citation copied" : "Copy citation"}
            </button>
            {index.scene.source_url && (
              <a className="btn" href={index.scene.source_url} target="_blank" rel="noreferrer">
                <IconExternal />
                STAC item
              </a>
            )}
          </div>
        </section>
      )}

      {population && (
        <section className="result">
          <header className="result-head">
            <div>
              <h2>Population</h2>
              <p>
                WorldPop 1 km · {population.source.year} · {population.source.iso3}
              </p>
            </div>
            <span className="pill">{populatedLabel(population)}</span>
          </header>

          <div className="headline-stat">
            <div className="headline-value">
              <strong>{integer(population.population)}</strong>
              <span>estimated residents</span>
            </div>
            <dl className="headline-grid">
              <div>
                <dt>Density</dt>
                <dd>{decimal(population.density_per_km2, 0)}/km²</dd>
              </div>
              <div>
                <dt>Area</dt>
                <dd>{decimal(population.area_km2, 2)} km²</dd>
              </div>
              <div>
                <dt>Cells</dt>
                <dd>{population.pixels_used}</dd>
              </div>
            </dl>
          </div>

          {population.notes.length > 0 && (
            <ul className="notes">
              {population.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {landcover && (
        <section className="result">
          <header className="result-head">
            <div>
              <h2>Land cover</h2>
              <p>
                Dynamic World · {landcover.scene_count} scenes · {landcover.scale_m} m
              </p>
            </div>
            <span className="pill">{landcover.total_km2.toFixed(2)} km²</span>
          </header>

          <ul className="class-list">
            {[...landcover.classes]
              .sort((a, b) => b.percent - a.percent)
              .filter((entry) => entry.percent > 0)
              .map((entry) => (
                <li key={entry.id}>
                  <i className="class-swatch" style={{ background: entry.color }} />
                  <span className="class-name">{entry.name}</span>
                  <span className="class-bar">
                    <i style={{ width: `${Math.max(entry.percent, 2)}%`, background: entry.color }} />
                  </span>
                  <span className="class-value">{entry.percent.toFixed(1)}%</span>
                </li>
              ))}
          </ul>

          {landcover.structures && (
            <div className="headline-stat">
              <div className="headline-value">
                <strong>{landcover.structures.count === null ? "—" : integer(landcover.structures.count)}</strong>
                <span>mapped structures</span>
              </div>
              <span className="pill">Open Buildings v3</span>
            </div>
          )}

          <ul className="notes">
            {landcover.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
            {landcover.structures?.note && <li>{landcover.structures.note}</li>}
          </ul>
        </section>
      )}

      {mapResult && (
        <section className="result">
          <header className="result-head">
            <div>
              <h2>Exported map</h2>
              <p>{mapResult.layers.join(" · ")}</p>
            </div>
            <span className="pill">{Math.round(mapResult.bytes / 1024)} KB</span>
          </header>
          {mapResult.note && <p className="empty-note">{mapResult.note}</p>}
          <div className="result-actions">
            <button type="button" className="btn" onClick={onShowMap}>
              <IconLayers />
              Show on map
            </button>
            <a className="btn" href={mapResult.url} target="_blank" rel="noreferrer">
              <IconExternal />
              New tab
            </a>
          </div>
        </section>
      )}
    </>
  );
}

function populatedLabel(result: PopulationResult) {
  if (result.status === "no_population_pixels") return "no populated cells";
  if (result.status !== "ok") return result.status.replace(/_/g, " ");
  return "modelled estimate";
}
