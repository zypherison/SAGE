import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection, GeoJSON, Polygon } from "geojson";
import type { PolygonGeometry } from "./types";

/**
 * True 3D view: Esri satellite raster draped over the AWS/Mapterhorn terrain tiles
 * (Terrarium encoding). Both services are keyless, which is why the 3D mode works
 * without a Mapbox token.
 *
 * Terrain is exaggerated 1.4x: at 10 m imagery over a city, real relief is close to
 * invisible at screen scale, and pretending otherwise would give a flat-looking
 * "3D" view rather than a legible one.
 *
 * The land-cover PNG is deliberately not draped here. An image source with terrain
 * enabled is drawn as a flat quad, so most of it is occluded by the hills and the
 * result reads as a grey wash (measured 2026-11-14). The class map belongs to the
 * 2D surface; 3D keeps the imagery, the relief and the AOI outline only.
 */
const SATELLITE =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const TERRAIN = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

export default function TerrainView({
  geometry,
  buildings,
  showBuildings,
  onExit,
}: {
  geometry: PolygonGeometry | null;
  buildings: { type: "FeatureCollection"; features: { properties: { height?: number | null } }[] } | null;
  showBuildings: boolean;
  onExit: () => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || mapRef.current) return;

    // WebGL is not guaranteed. Creating the Map without it throws, and that
    // error used to escape through Suspense and unmount the entire app, which
    // is what a white page after pressing 3D actually was (measured with
    // WebGL disabled on 2026-11-14). Test first and degrade honestly.
    const probe = document.createElement("canvas");
    const context =
      probe.getContext("webgl2") ?? probe.getContext("webgl") ?? probe.getContext("experimental-webgl");
    if (!context) {
      setFailure(
        "This browser has WebGL turned off, and the 3D view needs it. Enable hardware " +
          "acceleration in the browser settings (Chrome: Settings → System → Use graphics " +
          "acceleration when available), then reload. The 2D map is unaffected.",
      );
      return;
    }

    const style: maplibregl.StyleSpecification = {
      version: 8,
      sources: {
        satellite: {
          type: "raster",
          tiles: [SATELLITE],
          tileSize: 256,
          maxzoom: 19,
          attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics",
        },
        terrain: {
          type: "raster-dem",
          tiles: [TERRAIN],
          tileSize: 256,
          encoding: "terrarium",
          maxzoom: 14,
          attribution: "Elevation &copy; Mapzen / AWS Terrain Tiles",
        },
        aoi: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
      },
      layers: [
        { id: "satellite", type: "raster", source: "satellite" },
        {
          id: "aoi-fill",
          type: "fill",
          source: "aoi",
          paint: { "fill-color": "#1d4ed8", "fill-opacity": 0.02 },
        },
        {
          id: "aoi-line",
          type: "line",
          source: "aoi",
          paint: { "line-color": "#1d4ed8", "line-width": 1.6 },
        },
      ],
    };

    let map: maplibregl.Map;
    try {
      map = new maplibregl.Map({
        container: host,
        style,
        center: [78.4738, 17.4239],
        zoom: 12.6,
        pitch: 62,
        bearing: -18,
        maxPitch: 85,
        attributionControl: { compact: true },
      });
    } catch (exc) {
      setFailure(
        `The 3D renderer could not start: ${exc instanceof Error ? exc.message : String(exc)}. ` +
          "The 2D map still works.",
      );
      return;
    }
    mapRef.current = map;

    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-right");
    map.addControl(new maplibregl.ScaleControl({ unit: "metric", maxWidth: 110 }), "bottom-left");
    map.on("load", () => map.setTerrain({ source: "terrain", exaggeration: 1.4 }));

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const feature: Feature<Polygon> | null = geometry
      ? { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: geometry.coordinates } }
      : null;
    const data: Feature<Polygon> | FeatureCollection = feature ?? { type: "FeatureCollection", features: [] };

    const apply = () => {
      const source = map.getSource("aoi") as maplibregl.GeoJSONSource | undefined;
      source?.setData(data);
      if (!feature) return;
      const ring = feature.geometry.coordinates[0] ?? [];
      if (ring.length < 3) return;
      const lons = ring.map(([lon]) => lon);
      const lats = ring.map(([, lat]) => lat);
      const south = Math.min(...lats);
      const north = Math.max(...lats);
      const west = Math.min(...lons);
      const east = Math.max(...lons);
      map.fitBounds(
        [
          [west, south],
          [east, north],
        ],
        { padding: 30, maxZoom: 15.5, duration: 700, pitch: 60, bearing: -18 },
      );
      // A whole-AOI frame at city scale makes 8 m buildings invisible, so step
      // one zoom closer after the fit: the 3D view is for reading structure.
      map.once("moveend", () => {
        map.easeTo({
          center: [(west + east) / 2, (south + north) / 2],
          zoom: Math.min(map.getZoom() + 1.4, 16.5),
          pitch: 66,
          duration: 600,
        });
      });
    };

    if (map.loaded()) apply();
    else map.once("load", apply);
  }, [geometry]);  if (failure) {
    return (
      <div className="terrain-fallback" role="alert">
        <div className="terrain-fallback-card">
          <h3>3D view unavailable</h3>
          <p>{failure}</p>
          <button type="button" className="btn" onClick={onExit}>
            Back to the 2D map
          </button>
        </div>
      </div>
    );
  }

  // Buildings extruded to their modelled height. The height comes from the
  // Open Buildings 2.5D Temporal estimates, fetched on demand for the 3D view.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (map.getLayer("buildings-3d")) map.removeLayer("buildings-3d");
      if (map.getSource("buildings")) map.removeSource("buildings");
      if (!buildings || !showBuildings || buildings.features.length === 0) return;
      map.addSource("buildings", {
        type: "geojson",
        data: buildings as unknown as GeoJSON,
      });
      map.addLayer({
        id: "buildings-3d",
        type: "fill-extrusion",
        source: "buildings",
        paint: {
          "fill-extrusion-color": [
            "interpolate",
            ["linear"],
            ["coalesce", ["get", "height"], 6],
            0,
            "#fecaca",
            10,
            "#f87171",
            30,
            "#b91c1c",
            60,
            "#7f1d1d",
          ],
          "fill-extrusion-height": ["coalesce", ["get", "height"], 6],
          "fill-extrusion-opacity": 0.9,
        },
      } as maplibregl.LayerSpecification);
    };
    if (map.loaded()) apply();
    else map.once("load", apply);
  }, [buildings, showBuildings]);

  return <div ref={hostRef} className="terrain-canvas" />;
}
