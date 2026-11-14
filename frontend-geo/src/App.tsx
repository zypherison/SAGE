import {
  Component,
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import MapCanvas, { type DrawMode } from "./MapCanvas";
import {
  acquisitionDates,
  aoiMetrics,
  exportMap,
  getBuildings,
  getCapabilities,
  runFeatures,
  runIndex,
  runLandcover,
  runLandcoverOverlay,
  runPopulation,
} from "./api";
import { HELP_TEXT, SUGGESTIONS, parseCommand } from "./assistant";
import type {
  AcquisitionDates,
  AoiMetrics,
  BuildingLayer,
  Capabilities,
  FeatureLayer,
  IndexResult,
  LandcoverOverlay,
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

/**
 * The 3D view is the one part of this page that can fail at runtime (no WebGL,
 * driver reset, MapLibre init error). Without this boundary such a failure
 * unmounts the React root and leaves a white page.
 */
class TerrainBoundary extends Component<{ onReset: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    console.error("3D view failed", error);
  }

  private reset = () => {
    this.setState({ failed: false });
    this.props.onReset();
  };

  render() {
    if (this.state.failed) {
      return (
        <div className="terrain-fallback" role="alert">
          <div className="terrain-fallback-card">
            <h3>3D view unavailable</h3>
            <p>
              The 3D renderer stopped, so the map has been returned to 2D. Nothing else is
              affected.
            </p>
            <button type="button" className="btn" onClick={this.reset}>
              Back to the 2D map
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

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

type Tab = "tools" | "results" | "assistant";
type ChatMessage = { role: "user" | "agent"; text: string };

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
  const [overlay, setOverlay] = useState<LandcoverOverlay | null>(null);
  const [buildings, setBuildings] = useState<BuildingLayer | null>(null);
  const [dates, setDates] = useState<AcquisitionDates | null>(null);
  const [datesLoading, setDatesLoading] = useState(false);
  const [showLandcover, setShowLandcover] = useState(true);
  const [showBuildings, setShowBuildings] = useState(true);
  const [featureLayers, setFeatureLayers] = useState<FeatureLayer[]>([]);
  const [hiddenFeatures, setHiddenFeatures] = useState<Record<string, boolean>>({});
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
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
  const chatEndRef = useRef<HTMLDivElement | null>(null);

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
    // The class, building and OSM layers belong to the area they were computed for.
    setOverlay(null);
    setLandcover(null);
    setBuildings(null);
    setFeatureLayers([]);
    setHiddenFeatures({});
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
      // The drawn layer is a second Earth Engine pass; losing it must not hide
      // the numbers that already succeeded.
      try {
        const layer = await runLandcoverOverlay(geometry, dateFrom, dateTo);
        setOverlay(layer);
        if (layer.buildings) setBuildings(layer.buildings);
        setShowLandcover(true);
        setShowBuildings(true);
      } catch {
        setOverlay(null);
      }
    });

  const publishMap = () =>
    withTiming("map", async () => {
      if (!geometry) throw new Error("Draw an area of interest first.");
      const result = await exportMap(geometry, "SAGE Geo Workbench AOI", Boolean(landcover));
      setMapResult(result);
      setShowPublished(true);
    });

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages, chatBusy]);

  // Which passes actually covered this area — the honest source for a date picker.
  useEffect(() => {
    if (!geometry) {
      setDates(null);
      return;
    }
    let cancelled = false;
    setDatesLoading(true);
    acquisitionDates(geometry, dateFrom, dateTo, maxCloud)
      .then((result) => {
        if (!cancelled) setDates(result);
      })
      .catch(() => {
        if (!cancelled) setDates(null);
      })
      .finally(() => {
        if (!cancelled) setDatesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [geometry, dateFrom, dateTo, maxCloud]);

  // Buildings with modelled heights are only needed for the 3D view; fetch them
  // when 3D is opened rather than making every classification pay for it.
  useEffect(() => {
    if (!view3d || !geometry || (buildings?.heights && buildings.features.length > 0)) return;
    let cancelled = false;
    getBuildings(geometry, true)
      .then((result) => {
        if (!cancelled) setBuildings(result);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [view3d, geometry, buildings]);

  const pushMessage = (role: ChatMessage["role"], text: string) =>
    setMessages((current) => [...current, { role, text }]);

  const ensureOverlay = async () => {
    if (overlay) return overlay;
    if (!geometry) throw new Error("Draw an area of interest first.");
    const layer = await runLandcoverOverlay(geometry, dateFrom, dateTo);
    setOverlay(layer);
    return layer;
  };

  const ask = async (raw: string) => {
    const text = raw.trim();
    if (!text || chatBusy) return;
    pushMessage("user", text);
    setDraft("");
    if (!geometry) {
      pushMessage("agent", "Draw an area on the map first, then ask again.");
      return;
    }
    setChatBusy(true);
    const intent = parseCommand(text);
    try {
      if (intent.kind === "help") {
        pushMessage("agent", HELP_TEXT);
        return;
      }
      if (intent.kind === "features") {
        const result = await runFeatures(geometry, intent.category);
        setFeatureLayers((current) => [
          ...current.filter((layer) => layer.category !== result.category),
          result,
        ]);
        setHiddenFeatures((current) => ({ ...current, [result.category]: false }));
        const what = intent.category === "roads" ? "road segments" : "monuments and landmarks";
        const truncated = result.truncated
          ? ` Only the first ${result.limit.toLocaleString("en-IN")} were drawn.`
          : "";
        pushMessage(
          "agent",
          `Marked ${result.count.toLocaleString("en-IN")} ${what} from OpenStreetMap on the map.${truncated}`,
        );
        return;
      }
      if (intent.kind === "buildings") {
        const layer = await ensureOverlay();
        if (layer.buildings) setBuildings(layer.buildings);
        const current = layer.buildings;
        if (!current || current.returned === 0) {
          pushMessage("agent", "No building footprints came back for this area.");
          return;
        }
        setShowBuildings(true);
        const total = current.total === null ? "" : ` of ${current.total.toLocaleString("en-IN")}`;
        const truncated = current.truncated ? ` Showing the first ${current.returned.toLocaleString("en-IN")}.` : "";
        pushMessage(
          "agent",
          `Marked ${current.returned.toLocaleString("en-IN")} building footprints${total} ` +
            `(Open Buildings v3, confidence ≥ ${current.confidence ?? 0.7}).${truncated}`,
        );
        return;
      }
      if (intent.kind === "landcover") {
        const result = landcover ?? (await runLandcover(geometry, dateFrom, dateTo));
        if (!landcover) setLandcover(result);
        if (intent.focus !== null) {
          const focused = await runLandcoverOverlay(geometry, dateFrom, dateTo, intent.focus);
          setOverlay(focused);
          if (focused.buildings) setBuildings(focused.buildings);
        } else {
          const layer = await ensureOverlay();
          if (layer.buildings) setBuildings(layer.buildings);
        }
        setShowLandcover(true);
        if (intent.focus === null) {
          const top = [...result.classes].sort((a, b) => b.percent - a.percent).slice(0, 3);
          pushMessage(
            "agent",
            `Land cover over ${result.total_km2.toFixed(2)} km²: ` +
              top.map((entry) => `${entry.name.toLowerCase()} ${entry.percent.toFixed(1)}%`).join(", ") +
              ". The class layer is on the map.",
          );
        } else {
          const found = result.classes.find((entry) => entry.id === intent.focus);
          pushMessage(
            "agent",
            found
              ? `${found.name}: ${found.percent.toFixed(1)}% of the area (${found.km2.toFixed(2)} km²), from ` +
                  `${result.scene_count} Dynamic World scenes. The class layer is now shown.`
              : "That class did not appear in the result.",
          );
        }
        return;
      }
      if (intent.kind === "index") {
        const result = await runIndex({
          geometry,
          index: intent.index,
          dateFrom,
          dateTo,
          maxCloud,
          cloudMask,
          preview,
        });
        setIndex(result);
        setTab("results");
        pushMessage(
          "agent",
          `${result.label} mean ${result.stats?.mean?.toFixed(4) ?? "—"} over ` +
            `${result.stats?.count?.toLocaleString("en-IN") ?? "—"} valid pixels (scene ${result.scene.id}). ` +
            "The Results tab has the detail.",
        );
        return;
      }
      if (intent.kind === "population") {
        const result = await runPopulation(geometry, iso3.trim().toUpperCase(), Number(year));
        setPopulation(result);
        setTab("results");
        pushMessage(
          "agent",
          `About ${result.population?.toLocaleString("en-IN") ?? "—"} residents ` +
            `(${result.density_per_km2?.toFixed(0) ?? "—"}/km²), WorldPop ${result.source.year}.`,
        );
      }
    } catch (exc) {
      pushMessage("agent", `That failed: ${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setChatBusy(false);
    }
  };

  const copyCitation = async () => {
    if (!index) return;
    await navigator.clipboard.writeText(index.citation);
    setCopied(true);
    if (copyTimer.current) window.clearTimeout(copyTimer.current);
    copyTimer.current = window.setTimeout(() => setCopied(false), 1800);
  };

  const visibleFeatures = useMemo(
    () => featureLayers.filter((layer) => !hiddenFeatures[layer.category]),
    [featureLayers, hiddenFeatures],
  );

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
            features={visibleFeatures}
            landcover={
              overlay ? { url: overlay.image_url, bounds: overlay.bounds } : null
            }
            buildings={buildings}
            showLandcover={showLandcover}
            showBuildings={showBuildings}
          />

          {view3d && (
            <div className="terrain-pane">
              <TerrainBoundary onReset={() => setView3d(false)}>
                <Suspense fallback={<div className="terrain-loading">Loading 3D terrain…</div>}>
                  <TerrainView
                    geometry={geometry}
                    buildings={buildings}
                    showBuildings={showBuildings}
                    onExit={() => setView3d(false)}
                  />
                </Suspense>
              </TerrainBoundary>
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
              <span className="layer-count">
                {1 +
                  Number(Boolean(overlay)) +
                  Number(Boolean(buildings)) +
                  featureLayers.length}
              </span>
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
              {overlay && (
                <li className={showLandcover ? "" : "is-empty"}>
                  <i className="dot" style={{ background: "#397D49" }} />
                  <div>
                    <strong>Land cover</strong>
                    <span>
                      {overlay.classes.length} classes · {overlay.scene_count} scenes
                    </span>
                  </div>
                  <button
                    type="button"
                    className="layer-toggle"
                    onClick={() => setShowLandcover((value) => !value)}
                  >
                    {showLandcover ? "Hide" : "Show"}
                  </button>
                </li>
              )}
              {featureLayers.map((layer) => {
                const visible = !hiddenFeatures[layer.category];
                return (
                  <li key={layer.category} className={visible ? "" : "is-empty"}>
                    <i
                      className="dot"
                      style={{ background: layer.category === "roads" ? "#f59e0b" : "#8b5cf6" }}
                    />
                    <div>
                      <strong>{layer.label}</strong>
                      <span>
                        {layer.count.toLocaleString("en-IN")}
                        {layer.truncated ? ` (first ${layer.limit.toLocaleString("en-IN")})` : ""} · OSM
                      </span>
                    </div>
                    <button
                      type="button"
                      className="layer-toggle"
                      onClick={() =>
                        setHiddenFeatures((current) => ({
                          ...current,
                          [layer.category]: !current[layer.category],
                        }))
                      }
                    >
                      {visible ? "Hide" : "Show"}
                    </button>
                  </li>
                );
              })}
              {buildings && buildings.returned > 0 && (
                <li className={showBuildings ? "" : "is-empty"}>
                  <i className="dot" style={{ background: "#c4281b" }} />
                  <div>
                    <strong>Buildings</strong>
                    <span>
                      {buildings.returned.toLocaleString("en-IN")}
                      {buildings.total !== null && ` of ${buildings.total.toLocaleString("en-IN")}`}
                      {buildings.truncated ? " drawn" : " footprints"}
                      {buildings.heights ? " · heights" : ""}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="layer-toggle"
                    onClick={() => setShowBuildings((value) => !value)}
                  >
                    {showBuildings ? "Hide" : "Show"}
                  </button>
                </li>
              )}
            </ul>
            <p className="layer-hint">
              {view3d
                ? "3D terrain: drag to orbit, right-drag to pan. Buildings are extruded at their modelled heights; drawing needs the 2D view."
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
              <button
                type="button"
                role="tab"
                aria-selected={tab === "assistant"}
                className={tab === "assistant" ? "is-active" : ""}
                onClick={() => setTab("assistant")}
              >
                Ask
              </button>
            </div>
          </div>

          <div className={tab === "assistant" ? "panel-body chat-body" : "panel-body"}>
            {error && (
              <div className="alert" role="alert">
                <span>{error}</span>
                <button type="button" onClick={() => setError(null)} aria-label="Dismiss">
                  ×
                </button>
              </div>
            )}

            {tab === "assistant" ? (
              <>
                <div className="chat-log">
                  {messages.length === 0 && (
                    <div className="chat-intro">
                      <h3>Ask for something to be marked</h3>
                      <p>
                        Buildings, roads, monuments, water, trees, built-up area, a spectral index
                        or population — for the area you have drawn. Commands are matched to data
                        this deployment actually has.
                      </p>
                    </div>
                  )}
                  {messages.map((message, position) => (
                    <div key={position} className={`chat-msg chat-msg-${message.role}`}>
                      {message.text}
                    </div>
                  ))}
                  {chatBusy && <div className="chat-msg chat-msg-agent is-busy">Working…</div>}
                  <div ref={chatEndRef} />
                </div>

                <div className="chat-suggestions">
                  {SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className="chip"
                      onClick={() => void ask(suggestion)}
                      disabled={chatBusy}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>

                <form
                  className="chat-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void ask(draft);
                  }}
                >
                  <input
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="e.g. mark all buildings"
                    aria-label="Ask for an area to be marked"
                  />
                  <button type="submit" className="btn btn-primary" disabled={!draft.trim() || chatBusy}>
                    Send
                  </button>
                </form>
              </>
            ) : tab === "tools" ? (
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

                  <div className="date-strip">
                    <div className="field-label">
                      <span>Actual passes over this area</span>
                      <strong>
                        {datesLoading ? "checking…" : dates ? `${dates.days.length} days` : "—"}
                      </strong>
                    </div>
                    {dates && dates.days.length > 0 ? (
                      <div className="date-chips">
                        {dates.days.map((day) => {
                          const active = dateFrom === day.date && dateTo === day.date;
                          const label = new Date(`${day.date}T00:00:00Z`).toLocaleDateString("en-IN", {
                            day: "2-digit",
                            month: "short",
                            timeZone: "UTC",
                          });
                          return (
                            <button
                              key={day.date}
                              type="button"
                              className={active ? "date-chip is-active" : "date-chip"}
                              title={day.scenes
                                .map((scene) =>
                                  `${scene.datetime.slice(11, 19)}Z · ${scene.cloud ?? "?"}% cloud`,
                                )
                                .join("\n")}
                              onClick={() => {
                                setDateFrom(day.date);
                                setDateTo(day.date);
                              }}
                            >
                              {label}
                              {day.cloud_min !== null && <em>{day.cloud_min.toFixed(0)}%</em>}
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="field-note">
                        {datesLoading
                          ? "Checking STAC for passes…"
                          : "No scene covers this area in the window; widen the dates or raise the cloud limit."}
                      </p>
                    )}
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
