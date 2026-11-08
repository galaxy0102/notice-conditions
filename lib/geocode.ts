import { buildingGeocoderApiKey } from "./http";
import type { RegisterBuilding } from "./building-register";

const GEO_URL = "https://api.vworld.kr/req/address";
const cache = new Map<string, { lat: number; lng: number } | null>();
const CACHE_MAX = 500;

export type GeocoderState = "missing" | "ok" | "error";

type Point = { lat: number; lng: number } | null;

function remember(key: string, point: Point): Point {
  if (cache.size > CACHE_MAX) {
    const oldest = cache.keys().next().value;
    if (oldest) cache.delete(oldest);
  }
  cache.set(key, point);
  return point;
}

function inKorea(lat: number, lng: number): boolean {
  return lat >= 33 && lat <= 39.5 && lng >= 124 && lng <= 132.5;
}

async function lookup(address: string, type: "road" | "parcel", key: string): Promise<Point | "bad-key"> {
  const cached = cache.get(`${type}:${address}`);
  if (cached !== undefined) return cached;
  const url = new URL(GEO_URL);
  url.searchParams.set("service", "address");
  url.searchParams.set("request", "getcoord");
  url.searchParams.set("version", "2.0");
  url.searchParams.set("crs", "epsg:4326");
  url.searchParams.set("format", "json");
  url.searchParams.set("type", type);
  url.searchParams.set("address", address);
  url.searchParams.set("key", key);
  try {
    const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(8_000) });
    const payload = (await response.json()) as {
      response?: { status?: string; error?: { code?: string }; result?: { point?: { x?: string; y?: string } } };
    };
    const status = payload.response?.status ?? "";
    const errorCode = payload.response?.error?.code ?? "";
    if (/KEY|AUTH/i.test(`${status} ${errorCode}`)) return "bad-key";
    const point = payload.response?.result?.point;
    const lng = Number(point?.x);
    const lat = Number(point?.y);
    if (status === "OK" && inKorea(lat, lng)) return remember(`${type}:${address}`, { lat, lng });
    return remember(`${type}:${address}`, null);
  } catch {
    return null;
  }
}

function roadQuery(address: string): string {
  return address.replace(/\s*\([^)]*\)\s*$/g, "").replace(/\s+/g, " ").trim();
}

function parcelQuery(address: string): string {
  return address.replace(/\s*번지\s*$/g, "").replace(/\s+/g, " ").trim();
}

async function one(building: RegisterBuilding, key: string): Promise<Point | "bad-key"> {
  const road = roadQuery(building.roadAddress);
  if (road) {
    const hit = await lookup(road, "road", key);
    if (hit === "bad-key" || hit) return hit;
  }
  const parcel = parcelQuery(building.address);
  if (parcel) return lookup(parcel, "parcel", key);
  return null;
}

export async function attachCoordinates(
  buildings: RegisterBuilding[],
): Promise<{ buildings: RegisterBuilding[]; geocoder: GeocoderState }> {
  const key = buildingGeocoderApiKey();
  if (!key) return { buildings, geocoder: "missing" };
  let geocoder: GeocoderState = "ok";
  const next: RegisterBuilding[] = [];
  const queue = buildings.slice();
  const workers = Math.min(4, queue.length);
  let cursor = 0;
  async function run() {
    while (cursor < queue.length) {
      const index = cursor;
      cursor += 1;
      const building = queue[index];
      if (geocoder === "error") {
        next[index] = building;
        continue;
      }
      const hit = await one(building, key);
      if (hit === "bad-key") {
        geocoder = "error";
        next[index] = building;
        continue;
      }
      next[index] = hit ? { ...building, lat: hit.lat, lng: hit.lng } : building;
    }
  }
  await Promise.all(Array.from({ length: workers }, () => run()));
  return { buildings: next, geocoder };
}
