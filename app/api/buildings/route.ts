import { fetchRegisterPage } from "../../../lib/building-register";
import { excludedIdSet, exclusionsInRegion } from "../../../lib/exclusions";
import { attachCoordinates } from "../../../lib/geocode";
import { clientIp } from "../../../lib/rate-limit";
import { json, sameSiteRead } from "../../../lib/http";
import { allowMapRequest } from "../../../lib/map-rate";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function digits(value: string, length: number): string | null {
  const cleaned = value.replace(/\D/g, "");
  return cleaned.length === length ? cleaned : null;
}

export async function GET(request: Request) {
  if (!sameSiteRead(request)) return json({ error: "잘못된 요청 출처입니다." }, 403);
  const ip = clientIp(request);
  if (!allowMapRequest(`buildings:${ip}`, 40, 10 * 60 * 1000)) {
    return json({ error: "건축물대장 조회가 너무 많습니다. 잠시 후 다시 시도해 주세요." }, 429);
  }
  const url = new URL(request.url);
  const sigunguCd = digits(url.searchParams.get("sigunguCd") ?? "", 5);
  const bjdongCd = digits(url.searchParams.get("bjdongCd") ?? "", 5);
  const page = Math.max(1, Math.min(500, Number(url.searchParams.get("page") ?? "1") || 1));
  if (!sigunguCd || !bjdongCd) return json({ error: "법정동 코드가 올바르지 않습니다." }, 400);

  const pageResult = await fetchRegisterPage(sigunguCd, bjdongCd, page);
  if (!pageResult.ok) return json(pageResult);

  const regionCode = `${sigunguCd}${bjdongCd}`;
  const [excluded, hidden] = await Promise.all([
    exclusionsInRegion(regionCode),
    excludedIdSet(pageResult.buildings.map((building) => building.id).filter(Boolean)),
  ]);
  const visible = pageResult.buildings.filter((building) => building.id && !hidden.has(building.id));
  const placed = await attachCoordinates(visible);
  return json({
    ok: true,
    page: pageResult.page,
    pageSize: pageResult.pageSize,
    totalCount: pageResult.totalCount,
    geocoder: placed.geocoder,
    buildings: placed.buildings,
    excluded: excluded.map((row) => ({ buildingId: row.buildingId, address: row.address })),
  });
}
