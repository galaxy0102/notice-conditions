"use client";

import { useEffect, useRef } from "react";
import "leaflet/dist/leaflet.css";
import { KOREA_SIDO } from "@/lib/korea-sido";
import type { MapPin } from "@/lib/map-model";
import type { MapFocus } from "@/lib/map-search";
import type { Verdict } from "@/lib/types";

type SidoFeature = {
  type: "Feature";
  properties: { id: string; name: string };
  geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon;
};

type SidoCollection = { type: "FeatureCollection"; features: SidoFeature[] };

const PIN: Record<Verdict, { fill: string; label: string; shape: "circle" | "square" | "diamond" }> = {
  해당: { fill: "#059669", label: "해당", shape: "circle" },
  비해당: { fill: "#e11d48", label: "비해당", shape: "square" },
  확인필요: { fill: "#ea580c", label: "확인필요", shape: "diamond" },
};

function pinHtml(v: Verdict | "none", pulse = false): string {
  const extra = pulse ? "outline:3px solid #1d4ed8;outline-offset:2px;" : "";
  if (v === "none") {
    return `<span class="nc-pin-mark" style="width:16px;height:16px;background:#64748b;border:2px solid #fff;border-radius:999px;box-shadow:0 1px 4px rgba(0,0,0,.35);${extra}"></span>`;
  }
  const s = PIN[v];
  if (s.shape === "square") {
    return `<span class="nc-pin-mark" style="width:16px;height:16px;background:${s.fill};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35);${extra}"></span>`;
  }
  if (s.shape === "diamond") {
    return `<span class="nc-pin-mark" style="width:14px;height:14px;background:${s.fill};border:2px solid #fff;transform:rotate(45deg);box-shadow:0 1px 4px rgba(0,0,0,.35);${extra}"></span>`;
  }
  return `<span class="nc-pin-mark" style="width:16px;height:16px;background:${s.fill};border:2px solid #fff;border-radius:999px;box-shadow:0 1px 4px rgba(0,0,0,.35);${extra}"></span>`;
}

function schematicSido(): SidoCollection {
  return {
    type: "FeatureCollection",
    features: KOREA_SIDO.map((s) => {
      const ring = s.ring.map(([lat, lng]) => [lng, lat] as [number, number]);
      const first = ring[0];
      const last = ring[ring.length - 1];
      if (first && last && (first[0] !== last[0] || first[1] !== last[1])) ring.push([first[0], first[1]]);
      return {
        type: "Feature" as const,
        properties: { id: s.id, name: s.name },
        geometry: { type: "Polygon" as const, coordinates: [ring] },
      };
    }),
  };
}

async function loadSidoGeo(): Promise<SidoCollection> {
  try {
    const res = await fetch("/korea-sido.geo.json", { signal: AbortSignal.timeout(4000) });
    if (!res.ok) return schematicSido();
    const geo = (await res.json()) as SidoCollection;
    if (!geo?.features?.length) return schematicSido();
    return geo;
  } catch {
    return schematicSido();
  }
}

function attachTiles(L: typeof import("leaflet"), map: import("leaflet").Map) {
  const layers = [
    L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      maxZoom: 20,
      subdomains: "abcd",
    }),
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19,
    }),
  ];
  let index = 0;
  const step = () => {
    if (!map.getContainer()?.isConnected || index >= layers.length - 1) return;
    map.removeLayer(layers[index]);
    index += 1;
    layers[index].addTo(map);
    layers[index].once("tileerror", step);
  };
  layers[0].addTo(map);
  layers[0].once("tileerror", step);
}

function leafletNs(mod: typeof import("leaflet") | { default: typeof import("leaflet") }): typeof import("leaflet") {
  const m = mod as { default?: typeof import("leaflet"); map?: unknown };
  if (typeof m.map === "function") return m as typeof import("leaflet");
  return m.default as typeof import("leaflet");
}

function popupHtml(pin: MapPin): string {
  return `<p style="margin:0;font-weight:600">${pin.address}</p><p style="margin:6px 0 0;font-size:12px">1차 판정: <strong>${PIN[pin.overall].label}</strong> · ${pin.noticeName}${pin.sample ? " · 샘플" : ""}</p>`;
}

function focusPopup(focus: MapFocus): string {
  if (focus.noNotice) {
    return `<p style="margin:0;font-weight:600">${focus.address}</p><p style="margin:6px 0 0;font-size:12px">${focus.regionName || "이 지역"}에 등록된 공고가 없습니다. 판정하지 않습니다.</p>`;
  }
  return `<p style="margin:0;font-weight:600">${focus.address}</p><p style="margin:6px 0 0;font-size:12px">1차 판정: <strong>${focus.overall ? PIN[focus.overall].label : "—"}</strong>${focus.noticeName ? ` · ${focus.noticeName}` : ""}${focus.sample ? " · 샘플" : ""}</p>`;
}

export function LeafletKoreaMap({
  coloredIds,
  pins,
  focus,
}: {
  coloredIds: string[];
  pins: MapPin[];
  focus: MapFocus | null;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<import("leaflet").Map | null>(null);
  const LRef = useRef<typeof import("leaflet") | null>(null);
  const focusLayerRef = useRef<import("leaflet").Marker | null>(null);
  const focusRef = useRef(focus);
  focusRef.current = focus;

  const dataKey = `${coloredIds.join("|")}|${pins.map((p) => `${p.id}:${p.overall}:${p.lat}:${p.lng}`).join("|")}`;

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    let cancelled = false;
    let map: import("leaflet").Map | undefined;
    let ro: ResizeObserver | undefined;

    void (async () => {
      const [leafletMod, geo] = await Promise.all([import("leaflet"), loadSidoGeo()]);
      if (cancelled || !hostRef.current) return;
      const L = leafletNs(leafletMod);
      LRef.current = L;

      map = L.map(hostRef.current, {
        center: [36.35, 127.4],
        zoom: 7,
        minZoom: 6,
        maxZoom: 18,
        scrollWheelZoom: true,
        attributionControl: true,
      });
      mapRef.current = map;

      attachTiles(L, map);

      L.geoJSON(geo, {
        style: (feat) => {
          const id = (feat?.properties as { id?: string } | undefined)?.id;
          const on = Boolean(id && coloredIds.includes(id));
          return {
            color: on ? "#123a63" : "#64748b",
            weight: on ? 2 : 1,
            fillColor: on ? "#2563eb" : "#94a3b8",
            fillOpacity: on ? 0.42 : 0.22,
          };
        },
        onEachFeature: (feat, layer) => {
          const props = feat.properties as { id: string; name: string };
          const on = coloredIds.includes(props.id);
          layer.bindTooltip(`${props.name}${on ? " · 공고 있음" : " · 공고 없음"}`, { sticky: true });
        },
      }).addTo(map);

      for (const pin of pins) {
        const icon = L.divIcon({
          className: "nc-pin",
          html: pinHtml(pin.overall),
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        });
        L.marker([pin.lat, pin.lng], { icon, zIndexOffset: 500 }).bindPopup(popupHtml(pin)).addTo(map);
      }

      const initial = focusRef.current;
      if (initial) {
        map.setView([initial.lat, initial.lng], 16, { animate: false });
      } else if (pins.length) {
        const bounds = L.latLngBounds(pins.map((p) => [p.lat, p.lng] as [number, number]));
        map.fitBounds(bounds.pad(0.55), { maxZoom: 14, animate: false });
      } else {
        map.setView([36.5, 127.8], 7);
      }

      const invalidate = () => map?.invalidateSize();
      requestAnimationFrame(invalidate);
      window.setTimeout(invalidate, 120);
      ro = new ResizeObserver(invalidate);
      ro.observe(hostRef.current);
    })();

    return () => {
      cancelled = true;
      ro?.disconnect();
      focusLayerRef.current = null;
      map?.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- dataKey encodes overlay data; focus applied in a later effect
  }, [dataKey]);

  useEffect(() => {
    const map = mapRef.current;
    const L = LRef.current;
    if (!map || !L || !focus) return;
    focusLayerRef.current?.remove();
    const icon = L.divIcon({
      className: "nc-pin",
      html: pinHtml(focus.noNotice ? "none" : (focus.overall ?? "확인필요"), true),
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    const marker = L.marker([focus.lat, focus.lng], { icon, zIndexOffset: 800 }).bindPopup(focusPopup(focus)).addTo(map);
    focusLayerRef.current = marker;
    map.flyTo([focus.lat, focus.lng], 16, { duration: 0.6 });
    window.setTimeout(() => marker.openPopup(), 650);
  }, [focus]);

  return (
    <div
      ref={hostRef}
      className="h-[36rem] w-full"
      style={{ height: "36rem", width: "100%" }}
      role="application"
      aria-label="대한민국 시·도 현황 지도"
    />
  );
}
