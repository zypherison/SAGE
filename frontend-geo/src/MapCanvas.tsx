import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { GeoJsonObject } from "geojson";
import type { PolygonGeometry } from "./types";

export type DrawMode = "none" | "rect" | "polygon";

interface Props {
  geometry: PolygonGeometry | null;
  mode: DrawMode;
  onGeometry: (geometry: PolygonGeometry) => void;
  onFinish: () => void;
  onCursor: (lat: number, lng: number, zoom: number) => void;
  landcover: { url: string; bounds: [[number, number], [number, number]] } | null;
  buildings: { type: "FeatureCollection"; features: unknown[] } | null;
  showLandcover: boolean;
  showBuildings: boolean;
  features: { category: string; type: "FeatureCollection"; features: unknown[] }[];
}

/**
 * Keyless basemaps. CARTO's raster tiles now answer "API key required", so all three
 * come from Esri's ArcGIS Online tile services, which serve without a key
 * (verified 2026-11-14: 200 image/jpeg for each). The grey canvases keep their labels
 * as a separate reference layer, hence `overlayUrl`.
 */
const BASEMAPS = [
  {
    id: "satellite",
    label: "Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics",
    maxZoom: 19,
  },
  {
    id: "light",
    label: "Light",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    overlayUrl:
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
    attribution: "Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
    maxZoom: 16,
  },
  {
    id: "dark",
    label: "Dark",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
    overlayUrl:
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
    attribution: "Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
    maxZoom: 16,
  },
] as const;

const ringToLatLngs = (ring: number[][]): L.LatLngExpression[] =>
  ring.map(([lon, lat]) => [lat, lon] as [number, number]);

const closeRing = (verts: L.LatLng[]): PolygonGeometry => {
  const ring = verts.map((v) => [Number(v.lng.toFixed(6)), Number(v.lat.toFixed(6))]);
  ring.push(ring[0]);
  return { type: "Polygon", coordinates: [ring] };
};

export default function MapCanvas({
  geometry,
  mode,
  onGeometry,
  onFinish,
  onCursor,
  landcover,
  buildings,
  showLandcover,
  showBuildings,
  features,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const aoiRef = useRef<L.LayerGroup | null>(null);
  const landcoverRef = useRef<L.ImageOverlay | null>(null);
  const buildingsRef = useRef<L.GeoJSON | null>(null);
  const featuresRef = useRef<L.LayerGroup | null>(null);
  const tileRef = useRef<L.TileLayer | null>(null);
  const labelRef = useRef<L.TileLayer | null>(null);
  const [basemap, setBasemap] = useState<string>(BASEMAPS[0].id);

  const callbacks = useRef({ onGeometry, onFinish, onCursor });
  callbacks.current = { onGeometry, onFinish, onCursor };

  useEffect(() => {
    const host = hostRef.current;
    if (!host || mapRef.current) return;

    const map = L.map(host, {
      zoomControl: false,
      worldCopyJump: true,
      zoomSnap: 0.25,
      attributionControl: true,
    }).setView([17.4239, 78.4738], 11);

    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.scale({ position: "bottomright", imperial: false, maxWidth: 120 }).addTo(map);
    map.on("mousemove", (event: L.LeafletMouseEvent) =>
      callbacks.current.onCursor(event.latlng.lat, event.latlng.lng, map.getZoom()),
    );
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
      aoiRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const config = BASEMAPS.find((b) => b.id === basemap) ?? BASEMAPS[0];
    tileRef.current?.remove();
    labelRef.current?.remove();
    tileRef.current = L.tileLayer(config.url, {
      attribution: config.attribution,
      maxZoom: config.maxZoom,
      detectRetina: true,
    }).addTo(map);
    if ("overlayUrl" in config && config.overlayUrl) {
      labelRef.current = L.tileLayer(config.overlayUrl, {
        maxZoom: config.maxZoom,
        detectRetina: true,
      }).addTo(map);
    }
  }, [basemap]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const container = map.getContainer();

    if (mode === "none") {
      container.style.cursor = "";
      map.dragging.enable();
      map.doubleClickZoom.enable();
      return;
    }

    container.style.cursor = "crosshair";
    map.doubleClickZoom.disable();

    if (mode === "rect") {
      map.dragging.disable();
      let start: L.LatLng | null = null;
      let preview: L.Rectangle | null = null;

      const onDown = (event: L.LeafletMouseEvent) => {
        start = event.latlng;
        preview?.remove();
        preview = L.rectangle(L.latLngBounds(start, start), {
          color: "#1d4ed8",
          weight: 1.5,
          dashArray: "5 4",
          fillColor: "#1d4ed8",
          fillOpacity: 0.06,
        }).addTo(map);
      };
      const onMove = (event: L.LeafletMouseEvent) => {
        if (start && preview) preview.setBounds(L.latLngBounds(start, event.latlng));
      };
      const onUp = (event: L.LeafletMouseEvent) => {
        const from = start;
        start = null;
        preview?.remove();
        preview = null;
        if (!from) return;
        const bounds = L.latLngBounds(from, event.latlng);
        if (
          Math.abs(bounds.getEast() - bounds.getWest()) < 1e-4 ||
          Math.abs(bounds.getNorth() - bounds.getSouth()) < 1e-4
        ) {
          return;
        }
        callbacks.current.onGeometry(
          closeRing([
            L.latLng(bounds.getSouth(), bounds.getWest()),
            L.latLng(bounds.getSouth(), bounds.getEast()),
            L.latLng(bounds.getNorth(), bounds.getEast()),
            L.latLng(bounds.getNorth(), bounds.getWest()),
          ]),
        );
        callbacks.current.onFinish();
      };

      map.on("mousedown", onDown);
      map.on("mousemove", onMove);
      map.on("mouseup", onUp);
      return () => {
        map.off("mousedown", onDown);
        map.off("mousemove", onMove);
        map.off("mouseup", onUp);
        preview?.remove();
        map.dragging.enable();
        container.style.cursor = "";
      };
    }

    map.dragging.disable();
    const vertices: L.LatLng[] = [];
    let sketch: L.Polyline | null = null;

    const redraw = (cursor: L.LatLng | null) => {
      const points: L.LatLngExpression[] = vertices.map((v) => [v.lat, v.lng]);
      if (cursor && points.length) points.push([cursor.lat, cursor.lng]);
      if (!sketch) {
        sketch = L.polyline(points, {
          color: "#1d4ed8",
          weight: 1.5,
          dashArray: "5 4",
        }).addTo(map);
      } else {
        sketch.setLatLngs(points);
      }
    };
    const onClick = (event: L.LeafletMouseEvent) => {
      const last = vertices[vertices.length - 1];
      if (last && last.distanceTo(event.latlng) < 2) return;
      vertices.push(event.latlng);
      redraw(null);
    };
    const onMove = (event: L.LeafletMouseEvent) => {
      if (vertices.length) redraw(event.latlng);
    };
    const onDoubleClick = () => {
      if (vertices.length < 3) {
        vertices.length = 0;
        sketch?.remove();
        sketch = null;
        return;
      }
      const polygon = closeRing(vertices);
      vertices.length = 0;
      sketch?.remove();
      sketch = null;
      callbacks.current.onGeometry(polygon);
      callbacks.current.onFinish();
    };

    map.on("click", onClick);
    map.on("mousemove", onMove);
    map.on("dblclick", onDoubleClick);
    return () => {
      map.off("click", onClick);
      map.off("mousemove", onMove);
      map.off("dblclick", onDoubleClick);
      sketch?.remove();
      map.dragging.enable();
      container.style.cursor = "";
    };
  }, [mode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!aoiRef.current) aoiRef.current = L.layerGroup().addTo(map);
    const layer = aoiRef.current;
    layer.clearLayers();
    if (!geometry || (geometry.coordinates[0]?.length ?? 0) < 4) return;

    const latlngs = ringToLatLngs(geometry.coordinates[0]);
    L.polygon(latlngs, {
      color: "#1d4ed8",
      weight: 2,
      fillColor: "#1d4ed8",
      fillOpacity: 0.03,
    }).addTo(layer);
    if (map.getSize().x > 0) {
      map.fitBounds(L.latLngBounds(latlngs as [number, number][]).pad(0.4), { maxZoom: 14 });
    }
  }, [geometry]);

  // Land-cover PNG from Earth Engine: one image, not nine polygon layers.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    landcoverRef.current?.remove();
    landcoverRef.current = null;
    if (!landcover || !showLandcover) return;
    landcoverRef.current = L.imageOverlay(landcover.url, landcover.bounds, {
      opacity: 0.42,
      interactive: false,
    }).addTo(map);
  }, [landcover, showLandcover]);

  // Building footprints arrive as GeoJSON from Open Buildings.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    buildingsRef.current?.remove();
    buildingsRef.current = null;
    if (!buildings || !showBuildings || buildings.features.length === 0) return;
    buildingsRef.current = L.geoJSON(buildings as unknown as GeoJsonObject, {
      style: {
        color: "#7f1d1d",
        weight: 0.9,
        fillColor: "#c4281b",
        fillOpacity: 0.12,
      },
      interactive: false,
    }).addTo(map);
  }, [buildings, showBuildings]);

  // Chat-driven OSM layers: roads as lines, monuments as points. Each category
  // keeps its own GeoJSON so marking roads does not erase the monuments.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    featuresRef.current?.remove();
    featuresRef.current = null;
    if (features.length === 0) return;
    const group = L.layerGroup().addTo(map);
    features.forEach((layer) => {
      if (layer.features.length === 0) return;
      L.geoJSON(layer as unknown as GeoJsonObject, {
        pointToLayer: (_feature, latlng) =>
          L.circleMarker(latlng, {
            radius: 4.5,
            weight: 1,
            color: "#4c1d95",
            fillColor: "#8b5cf6",
            fillOpacity: 0.95,
          }),
        style: (feature) => {
          const type = (feature as { geometry?: { type?: string } } | undefined)?.geometry?.type;
          if (type === "LineString") {
            return { color: "#f59e0b", weight: 1.4, opacity: 0.85 };
          }
          return { color: "#4c1d95", weight: 1.2, fillColor: "#8b5cf6", fillOpacity: 0.3 };
        },
      }).addTo(group);
    });
    featuresRef.current = group;
  }, [features]);

  return (
    <div className="map-canvas">
      <div ref={hostRef} className="map-host" />
      <div className="basemap-switch" role="group" aria-label="Basemap">
        {BASEMAPS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            aria-pressed={entry.id === basemap}
            className={entry.id === basemap ? "is-active" : ""}
            onClick={() => setBasemap(entry.id)}
          >
            {entry.label}
          </button>
        ))}
      </div>
    </div>
  );
}
