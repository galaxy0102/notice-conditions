"use client";

import { useEffect, useRef, useState } from "react";
import type { Feature, GeoJsonObject, Geometry } from "geojson";
import type { Verdict } from "@/lib/types";
import type { RegionProps } from "@/lib/regions";
import "leaflet/dist/leaflet.css";

export type MapFeature = Feature<Geometry, RegionProps>;

export type MapPin = {
  id: string;
  lat: number;
  lng: number;
  verdict: Verdict;
  label: string;
};

const PIN_COLOR: Record<Verdict, string> = {
  해당: "#059669",
  비해당: "#e11d48",
  확인필요: "#ea580c",
};

type LeafletNS = typeof import("leaflet");

export function StatusMap({
  features,
  noticeCodes,
  selectedCode,
  pins,
  fitKey,
  onSelect,
  onPin,
}: {
  features: MapFeature[];
  noticeCodes: Set<string>;
  selectedCode: string;
  pins: MapPin[];
  fitKey: string;
  onSelect: (region: RegionProps) => void;
  onPin: (id: string) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<import("leaflet").Map | null>(null);
  const regionLayer = useRef<import("leaflet").GeoJSON | null>(null);
  const pinLayer = useRef<import("leaflet").LayerGroup | null>(null);
  const leafletRef = useRef<LeafletNS | null>(null);
  const fitRef = useRef("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let alive = true;
    void (async () => {
      const loaded = await import("leaflet");
      if (!alive) return;
      const L = (loaded.default ?? loaded) as LeafletNS;
      leafletRef.current = L;
      const map = L.map(host, { zoomControl: true, minZoom: 5 }).setView([36.4, 127.8], 7);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
        maxZoom: 18,
      }).addTo(map);
      regionLayer.current = L.geoJSON({ type: "FeatureCollection", features: [] } as GeoJsonObject).addTo(map);
      pinLayer.current = L.layerGroup().addTo(map);
      mapRef.current = map;
      window.setTimeout(() => map.invalidateSize(), 0);
      setReady(true);
    })();
    return () => {
      alive = false;
      mapRef.current?.remove();
      mapRef.current = null;
      regionLayer.current = null;
      pinLayer.current = null;
      setReady(false);
    };
  }, []);

  useEffect(() => {
    const L = leafletRef.current;
    const map = mapRef.current;
    const layer = regionLayer.current;
    if (!ready || !L || !map || !layer) return;
    layer.clearLayers();
    const collection = { type: "FeatureCollection", features } as GeoJsonObject;
    map.invalidateSize();
    const drawn = L.geoJSON(collection, {
      style: (feature) => {
        const code = String(feature?.properties?.code ?? "");
        const colored = noticeCodes.has(code);
        const selected = code === selectedCode;
        if (!colored) {
          return {
            color: selected ? "#1e293b" : "#475569",
            weight: selected ? 2.4 : 1.25,
            fillOpacity: 0,
          };
        }
        return {
          color: selected ? "#115e59" : "#0f766e",
          weight: selected ? 2.6 : 1.4,
          fillColor: "#14b8a6",
          fillOpacity: selected ? 0.55 : 0.38,
        };
      },
      onEachFeature: (feature, polygon) => {
        const region = feature.properties as RegionProps | null;
        if (!region?.code) return;
        polygon.bindTooltip(region.label, { sticky: true });
        polygon.on("click", () => onSelect(region));
      },
    });
    const collectionBounds = drawn.getBounds();
    drawn.eachLayer((child) => layer.addLayer(child));
    (pinLayer.current as { bringToFront?: () => void } | null)?.bringToFront?.();
    const selectedFeature = selectedCode
      ? features.find((item) => item.properties?.code === selectedCode)
      : undefined;
    const focus = selectedFeature ? L.geoJSON(selectedFeature).getBounds() : collectionBounds;
    const shouldFit = Boolean(selectedFeature) || (features.length > 0 && fitRef.current !== fitKey);
    if (shouldFit && focus.isValid()) {
      const frame = window.requestAnimationFrame(() => {
        map.invalidateSize();
        map.fitBounds(focus, { padding: [20, 20], maxZoom: selectedFeature ? 13 : 8 });
      });
      if (!selectedFeature) fitRef.current = fitKey;
      return () => window.cancelAnimationFrame(frame);
    }
  }, [features, noticeCodes, selectedCode, fitKey, onSelect, ready]);

  useEffect(() => {
    const L = leafletRef.current;
    const layer = pinLayer.current;
    if (!ready || !L || !layer) return;
    layer.clearLayers();
    for (const pin of pins) {
      const marker = L.circleMarker([pin.lat, pin.lng], {
        radius: 7,
        color: "#ffffff",
        weight: 1.5,
        fillColor: PIN_COLOR[pin.verdict],
        fillOpacity: 0.95,
      });
      marker.bindTooltip(`${pin.label} · ${pin.verdict}`, { direction: "top" });
      marker.on("click", () => onPin(pin.id));
      marker.addTo(layer);
    }
  }, [pins, onPin, ready]);

  return <div ref={hostRef} className="relative z-0 h-[32rem] w-full rounded-xl bg-white" />;
}
