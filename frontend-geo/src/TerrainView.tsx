import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection, Polygon } from "geojson";
import type { PolygonGeometry } from "./types";

/**
 * True 3D view: Esri satellite raster draped over the AWS/Mapterhorn terrain tiles
 * (Terrarium encoding). Both services are keyless, which is why the 3D mode works
 * without a Mapbox token.
 *
 * Terrain is exaggerated 1.4x: at 10 m imagery over a city, real relief is close to
 * invisible at screen scale, and pretending otherwise would give a flat-looking
 * "3D" view rather than a legible one.
 */
const SATELLITE =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
const TERRAIN = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

export default function TerrainView({ geometry }: { geometry: PolygonGeometry | null }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || mapRef.current) return;

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
          paint: { "fill-color": "#1d4ed8", "fill-opacity": 0.14 },
        },
        {
          id: "aoi-line",
          type: "line",
          source: "aoi",
          paint: { "line-color": "#1d4ed8", "line-width": 1.6 },
        },
      ],
    };

    const map = new maplibregl.Map({
      container: host,
      style,
      center: [78.4738, 17.4239],
      zoom: 12.6,
      pitch: 62,
      bearing: -18,
      maxPitch: 85,
      attributionControl: { compact: true },
    });
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
      map.fitBounds(
        [
          [Math.min(...lons), Math.min(...lats)],
          [Math.max(...lons), Math.max(...lats)],
        ],
        { padding: 80, duration: 700, pitch: 62, bearing: -18 },
      );
    };

    if (map.loaded()) apply();
    else map.once("load", apply);
  }, [geometry]);

  return <div ref={hostRef} className="terrain-canvas" />;
}
