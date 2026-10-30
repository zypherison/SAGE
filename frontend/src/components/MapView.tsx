import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { BBox } from "../types";

interface Props {
  bbox: BBox | null;
  drawMode: boolean;
  onBbox: (bbox: BBox) => void;
  onDrawEnd: () => void;
}

/**
 * Context basemaps. Both are keyless, openly licensed and need no account, so a
 * clone of this repo renders without any signup. The previous layer was CARTO's
 * `dark_all`, which is a proprietary raster style: free for non-commercial use
 * under their terms, but not open data, so it contradicted the project's own
 * "open sources only" claim.
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

/**
 * Leaflet map with a plain drag-to-draw AOI. Implemented with native Leaflet
 * events instead of a draw plugin: one fewer dependency and no CSS to import.
 */
export default function MapView({ bbox, drawMode, onBbox, onDrawEnd }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const rectRef = useRef<L.Rectangle | null>(null);
  const tileRef = useRef<L.TileLayer | null>(null);
  const [basemap, setBasemap] = useState<string>(BASEMAPS[0].id);
  const dragRef = useRef<{ start: L.LatLng | null; preview: L.Rectangle | null }>({
    start: null,
    preview: null,
  });
  // keep callbacks fresh without re-binding leaflet handlers
  const cbRef = useRef({ onBbox, onDrawEnd });
  cbRef.current = { onBbox, onDrawEnd };

  useEffect(() => {
    if (!hostRef.current || mapRef.current) return;
    const map = L.map(hostRef.current, { zoomControl: true, worldCopyJump: true })
      .setView([21.5, 79], 4);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
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

    if (!drawMode) {
      map.dragging.enable();
      map.getContainer().style.cursor = "";
      return;
    }

    map.dragging.disable();
    map.getContainer().style.cursor = "crosshair";

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
      if (Math.abs(width) < 1e-4 || Math.abs(height) < 1e-4) return; // ignore stray clicks

      cbRef.current.onBbox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
      cbRef.current.onDrawEnd();
    };

    map.on("mousedown", onDown);
    map.on("mousemove", onMove);
    map.on("mouseup", onUp);

    return () => {
      map.off("mousedown", onDown);
      map.off("mousemove", onMove);
      map.off("mouseup", onUp);
      map.dragging.enable();
      map.getContainer().style.cursor = "";
    };
  }, [drawMode]);

  // render the AOI
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    if (!bbox) {
      rectRef.current?.remove();
      rectRef.current = null;
      return;
    }

    const bounds = L.latLngBounds([bbox[1], bbox[0]], [bbox[3], bbox[2]]);
    if (!rectRef.current) {
      rectRef.current = L.rectangle(bounds, {
        color: "#38bdf8",
        weight: 2,
        fillColor: "#38bdf8",
        fillOpacity: 0.08,
      }).addTo(map);
    } else {
      rectRef.current.setBounds(bounds);
    }

    if (map.getSize().x > 0) map.fitBounds(bounds.pad(0.35), { maxZoom: 13 });
  }, [bbox]);

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
