import { partitionNotices, pickPrimaryMatch } from "./batch";
import { flat } from "./download";
import { matchSido } from "./korea-sido";
import { geocodeAddress, type MapPin } from "./map-model";
import { mockRegistryLookup } from "./registry";
import type { Extracted, Verdict } from "./types";

function norm(s: string): string {
  return s.replace(/\s+/g, "").toLowerCase();
}

export type MapFocus = {
  lat: number;
  lng: number;
  address: string;
  overall?: Verdict;
  noticeName?: string;
  sample?: boolean;
  noNotice?: boolean;
  regionName?: string;
};

export type SearchHit =
  | { kind: "verdict"; pin: MapPin; source: "match" | "mock"; focus: MapFocus }
  | { kind: "no-notice"; focus: MapFocus; regionName: string };

function sidoOfNotice(n: Extracted) {
  return matchSido(`${n.공고} ${flat(n.결과["지자체"]?.value)}`);
}

function sidoOfAddress(...texts: string[]) {
  for (const t of texts) {
    const s = matchSido(t);
    if (s) return s;
  }
  return undefined;
}

export function noticesInSameSido(addressText: string, notices: Extracted[]): Extracted[] {
  const sido = sidoOfAddress(addressText);
  if (!sido) return [];
  return notices.filter((n) => sidoOfNotice(n)?.id === sido.id);
}

function pinFromJudge(address: string, lat: number, lng: number, notices: Extracted[], source: "match" | "mock"): SearchHit {
  const fill = mockRegistryLookup(address) ?? {
    address,
    ownerHint: "",
    approvalDate: "",
    ageYears: "",
    floors: "",
    basementFloors: "",
    floorArea: "",
    use: "",
    structure: "",
  };
  const notice = notices[0];
  const parts = partitionNotices(notices, fill);
  const primary = pickPrimaryMatch(parts);
  const overall: Verdict = primary?.overall ?? "확인필요";
  const pin: MapPin = {
    id: `search-${norm(address).slice(0, 40)}`,
    lat,
    lng,
    address: fill.address || address,
    overall,
    noticeName: primary?.notice.공고 ?? notice?.공고 ?? "공고",
    sample: source === "mock",
  };
  return {
    kind: "verdict",
    pin,
    source,
    focus: {
      lat,
      lng,
      address: pin.address,
      overall: pin.overall,
      noticeName: pin.noticeName,
      sample: pin.sample,
    },
  };
}

function noNoticeHit(address: string, lat: number, lng: number, regionName: string): SearchHit {
  return {
    kind: "no-notice",
    regionName,
    focus: { lat, lng, address, noNotice: true, regionName },
  };
}

export function matchExistingPin(query: string, pins: MapPin[]): MapPin | undefined {
  const n = norm(query);
  if (!n) return undefined;
  const querySido = sidoOfAddress(query);
  return pins.find((p) => {
    const a = norm(p.address);
    if (!(a.includes(n) || n.includes(a))) return false;
    if (!querySido) return true;
    return sidoOfAddress(p.address)?.id === querySido.id;
  });
}

export async function searchMapAddress(query: string, pins: MapPin[], notices: Extracted[]): Promise<SearchHit | { error: string }> {
  const q = query.trim();
  if (q.length < 2) return { error: "주소를 2자 이상 입력해 주세요." };

  const existing = matchExistingPin(q, pins);
  if (existing) {
    const regional = noticesInSameSido(`${existing.address} ${q}`, notices);
    if (!regional.length) {
      return noNoticeHit(existing.address, existing.lat, existing.lng, sidoOfAddress(existing.address)?.name ?? "이 지역");
    }
    return {
      kind: "verdict",
      pin: existing,
      source: existing.sample ? "mock" : "match",
      focus: {
        lat: existing.lat,
        lng: existing.lng,
        address: existing.address,
        overall: existing.overall,
        noticeName: existing.noticeName,
        sample: existing.sample,
      },
    };
  }

  let lat: number | null = null;
  let lng: number | null = null;
  let label = q;
  try {
    const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`, { signal: AbortSignal.timeout(6000) });
    const body = (await res.json()) as { lat?: number; lng?: number; label?: string; error?: string };
    if (res.ok && Number.isFinite(body.lat) && Number.isFinite(body.lng)) {
      lat = body.lat as number;
      lng = body.lng as number;
      label = body.label || q;
    }
  } catch {
    /* fallback below */
  }
  if (lat == null || lng == null) {
    const local = geocodeAddress(q);
    if (!local) return { error: "해당 주소를 지도에서 찾지 못했습니다. 시·군·구를 포함해 입력해 주세요." };
    lat = local[0];
    lng = local[1];
  }

  const sido = sidoOfAddress(label, q);
  const regional = noticesInSameSido(`${label} ${q}`, notices);
  if (!sido || !regional.length) {
    return noNoticeHit(label, lat, lng, sido?.name ?? "이 지역");
  }
  return pinFromJudge(label, lat, lng, regional, "mock");
}
