import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { PolygonGeometry } from "./types";

export type DrawMode = "none" | "rect" | "polygon";

interface Props {
  geometry: PolygonGeometry | null;
  mode: DrawMode;
  onGeometry: (geometry: PolygonGeometry) => void;
  onFinish: () => void;
  onCursor: (lat: number, lng: number, zoom: number) => void;
}

/** Keyless basemaps, chosen for analysis rather than decoration. */
const BASEMAPS = [
  {
    id: "light",
    label: "Light",
    url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 20,
  },
  {
    id: "satellite",
    label: "Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics",
    maxZoom: 19,
  },
  {
    id: "dark",
    label: "Dark",
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
    maxZoom: 20,
  },
] as const;

const ringToLatLngs = (ring: number[][]): L.LatLngExpression[] =>
  ring.map(([lon, lat]) => [lat, lon] as [number, number]);

const closeRing = (verts: L.LatLng[]): PolygonGeometry => {
  const ring = verts.map((v) => [Number(v.lng.toFixed(6)), Number(v.lat.toFixed(6))]);
  ring.push(ring[0]);
  return { type: "Polygon", coordinates: [ring] };
};

export default function MapCanvas({ geometry, mode, onGeometry, onFinish, onCursor }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const aoiRef = useRef<L.LayerGroup | null>(null);
  const tileRef = useRef<L.TileLayer | null>(null);
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
    tileRef.current = L.tileLayer(config.url, {
      attribution: config.attribution,
      maxZoom: config.maxZoom,
      subdomains: "abcd",
      detectRetina: true,
    }).addTo(map);
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
      fillOpacity: 0.07,
    }).addTo(layer);
    if (map.getSize().x > 0) {
      map.fitBounds(L.latLngBounds(latlngs as [number, number][]).pad(0.4), { maxZoom: 14 });
    }
  }, [geometry]);

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
