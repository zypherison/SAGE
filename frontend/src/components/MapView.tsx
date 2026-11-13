import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { BBox, PolygonGeometry } from "../types";

export type DrawMode = "none" | "rect" | "polygon";

interface Props {
  bbox: BBox | null;
  polygon: PolygonGeometry | null;
  drawMode: DrawMode;
  onAoi: (bbox: BBox, polygon: PolygonGeometry | null) => void;
  onDrawEnd: () => void;
}

/**
 * Context basemaps. Both are keyless, openly licensed and need no account, so a
 * clone of this repo renders without any signup.
 */
const BASEMAPS = [
  {
    id: "osm",
    label: "Streets",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors (ODbL)',
    maxZoom: 19,
  },
  {
    id: "topo",
    label: "Terrain",
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attribution:
      '&copy; OpenStreetMap contributors, SRTM &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
    maxZoom: 17,
  },
] as const;

const ringToLatLngs = (ring: number[][]): L.LatLngExpression[] =>
  ring.map(([lon, lat]) => [lat, lon] as [number, number]);

const toLatLngs = (geom: PolygonGeometry): L.LatLngExpression[] =>
  ringToLatLngs(geom.coordinates[0] ?? []);

/** Close a vertex list into a GeoJSON exterior ring, rounded to ~0.1 m. */
const toGeometry = (verts: L.LatLng[]): PolygonGeometry => {
  const ring = verts.map((v) => [
    Number(v.lng.toFixed(6)),
    Number(v.lat.toFixed(6)),
  ]);
  ring.push(ring[0]);
  return { type: "Polygon", coordinates: [ring] };
};

/**
 * Leaflet map with two AOI tools: drag a rectangle, or click out a polygon and
 * double-click to close it.
 *
 * The polygon is not decoration. A lake shore, a river reach or a ward boundary
 * has a shape, and its envelope includes land the user did not select; the
 * statistics service clips to the polygon when it is sent, so the numbers
 * describe the drawn shape. Both tools still produce a bbox, because catalogues
 * and imagery requests need one.
 */
export default function MapView({ bbox, polygon, drawMode, onAoi, onDrawEnd }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const aoiRef = useRef<L.LayerGroup | null>(null);
  const tileRef = useRef<L.TileLayer | null>(null);
  const [basemap, setBasemap] = useState<string>(BASEMAPS[0].id);
  const dragRef = useRef<{ start: L.LatLng | null; preview: L.Rectangle | null }>({
    start: null,
    preview: null,
  });
  const polyRef = useRef<{ verts: L.LatLng[]; line: L.Polyline | null }>({
    verts: [],
    line: null,
  });
  // keep callbacks fresh without re-binding leaflet handlers
  const cbRef = useRef({ onAoi, onDrawEnd });
  cbRef.current = { onAoi, onDrawEnd };

  useEffect(() => {
    if (!hostRef.current || mapRef.current) return;
    const map = L.map(hostRef.current, { zoomControl: true, worldCopyJump: true })
      .setView([21.5, 79], 4);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      aoiRef.current = null;
    };
  }, []);

  // basemap layer: swapped without touching the view or the AOI
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const conf = BASEMAPS.find((b) => b.id === basemap) ?? BASEMAPS[0];
    tileRef.current?.remove();
    tileRef.current = L.tileLayer(conf.url, {
      attribution: conf.attribution,
      maxZoom: conf.maxZoom,
      subdomains: "abc",
    }).addTo(map);
  }, [basemap]);

  // draw-mode bindings
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    const clearPolygon = () => {
      polyRef.current.line?.remove();
      polyRef.current.line = null;
      polyRef.current.verts = [];
    };

    if (drawMode === "none") {
      map.dragging.enable();
      map.doubleClickZoom.enable();
      map.getContainer().style.cursor = "";
      clearPolygon();
      return;
    }

    map.getContainer().style.cursor = "crosshair";

    if (drawMode === "rect") {
      map.dragging.disable();

      const onDown = (e: L.LeafletMouseEvent) => {
        dragRef.current.start = e.latlng;
        dragRef.current.preview?.remove();
        dragRef.current.preview = L.rectangle(L.latLngBounds(e.latlng, e.latlng), {
          color: "#38bdf8",
          weight: 2,
          fillOpacity: 0.12,
          dashArray: "4 3",
        }).addTo(map);
      };

      const onMove = (e: L.LeafletMouseEvent) => {
        const { start, preview } = dragRef.current;
        if (!start || !preview) return;
        preview.setBounds(L.latLngBounds(start, e.latlng));
      };

      const onUp = (e: L.LeafletMouseEvent) => {
        const start = dragRef.current.start;
        dragRef.current.start = null;
        dragRef.current.preview?.remove();
        dragRef.current.preview = null;
        if (!start) return;

        const b = L.latLngBounds(start, e.latlng);
        const width = b.getEast() - b.getWest();
        const height = b.getNorth() - b.getSouth();
        if (Math.abs(width) < 1e-4 || Math.abs(height) < 1e-4) return; // stray click

        cbRef.current.onAoi([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], null);
        cbRef.current.onDrawEnd();
      };

      map.on("mousedown", onDown);
      map.on("mousemove", onMove);
      map.on("mouseup", onUp);

      return () => {
        map.off("mousedown", onDown);
        map.off("mousemove", onMove);
        map.off("mouseup", onUp);
        dragRef.current.preview?.remove();
        dragRef.current.preview = null;
        dragRef.current.start = null;
        map.dragging.enable();
        map.getContainer().style.cursor = "";
      };
    }

    // polygon: click to add a vertex, move for a rubber band, double-click to close
    map.dragging.disable();
    map.doubleClickZoom.disable();

    const redraw = (cursor: L.LatLng | null) => {
      const pts: L.LatLngExpression[] = polyRef.current.verts.map((v) => [v.lat, v.lng]);
      if (cursor && pts.length) pts.push([cursor.lat, cursor.lng]);
      if (!polyRef.current.line) {
        polyRef.current.line = L.polyline(pts, {
          color: "#38bdf8",
          weight: 2,
          dashArray: "4 3",
        }).addTo(map);
      } else {
        polyRef.current.line.setLatLngs(pts);
      }
    };

    const onClick = (e: L.LeafletMouseEvent) => {
      const verts = polyRef.current.verts;
      const last = verts[verts.length - 1];
      // A double-click fires two clicks; ignore the duplicate that lands on the
      // same spot, otherwise every polygon gains a zero-length edge.
      if (last && last.distanceTo(e.latlng) < 2) return;
      verts.push(e.latlng);
      redraw(null);
    };

    const onMove = (e: L.LeafletMouseEvent) => {
      if (!polyRef.current.verts.length) return;
      redraw(e.latlng);
    };

    const onFinish = () => {
      const verts = polyRef.current.verts;
      if (verts.length < 3) {
        clearPolygon();
        return;
      }
      const geom = toGeometry(verts);
      const lats = verts.map((v) => v.lat);
      const lngs = verts.map((v) => v.lng);
      clearPolygon();
      cbRef.current.onAoi(
        [Math.min(...lngs), Math.min(...lats), Math.max(...lngs), Math.max(...lats)],
        geom,
      );
      cbRef.current.onDrawEnd();
    };

    map.on("click", onClick);
    map.on("mousemove", onMove);
    map.on("dblclick", onFinish);

    return () => {
      map.off("click", onClick);
      map.off("mousemove", onMove);
      map.off("dblclick", onFinish);
      clearPolygon();
      map.dragging.enable();
      map.doubleClickZoom.enable();
      map.getContainer().style.cursor = "";
    };
  }, [drawMode]);

  // render the AOI: the polygon when there is one, otherwise its envelope
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!aoiRef.current) aoiRef.current = L.layerGroup().addTo(map);
    const layer = aoiRef.current;
    layer.clearLayers();

    let bounds: L.LatLngBounds | null = null;

    if (polygon && (polygon.coordinates[0]?.length ?? 0) >= 4) {
      const latlngs = toLatLngs(polygon);
      L.polygon(latlngs, {
        color: "#38bdf8",
        weight: 2,
        fillColor: "#38bdf8",
        fillOpacity: 0.12,
      }).addTo(layer);
      bounds = L.latLngBounds(latlngs as [number, number][]);
    } else if (bbox) {
      const b = L.latLngBounds([bbox[1], bbox[0]], [bbox[3], bbox[2]]);
      L.rectangle(b, {
        color: "#38bdf8",
        weight: 2,
        fillColor: "#38bdf8",
        fillOpacity: 0.08,
      }).addTo(layer);
      bounds = b;
    }

    if (bounds && map.getSize().x > 0) map.fitBounds(bounds.pad(0.35), { maxZoom: 13 });
  }, [bbox, polygon]);

  return (
    <div className="relative h-full w-full">
      <div ref={hostRef} className="h-full w-full" />
      {/* kept outside the Leaflet container so clicks never start an AOI drag */}
      <div
        className="absolute right-3 top-3 flex overflow-hidden rounded-md border border-edge bg-ink/90 backdrop-blur"
        style={{ zIndex: 1000 }}
      >
        {BASEMAPS.map((b) => (
          <button
            key={b.id}
            type="button"
            onClick={() => setBasemap(b.id)}
            className={`px-2.5 py-1 text-[11px] transition ${
              b.id === basemap ? "bg-sky-500/20 text-sky-200" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {b.label}
          </button>
        ))}
      </div>
    </div>
  );
}
