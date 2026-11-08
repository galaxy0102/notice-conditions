import { extractCsrfOk } from "../../../../lib/csrf";
import { excludeBuilding, exclusionsInRegion, restoreBuilding } from "../../../../lib/exclusions";
import { json, sameOrigin, sameSiteRead } from "../../../../lib/http";
import { allowMapRequest } from "../../../../lib/map-rate";
import { clientIp } from "../../../../lib/rate-limit";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Body = {
  action?: string;
  buildingId?: string;
  address?: string;
  regionCode?: string;
  safetyCheck?: boolean;
  reinforcement?: boolean;
};

function cleanId(value: unknown): string | null {
  const text = String(value ?? "").trim();
  return /^[0-9A-Za-z]{4,40}$/.test(text) ? text : null;
}

export async function GET(request: Request) {
  if (!sameSiteRead(request)) return json({ error: "잘못된 요청 출처입니다." }, 403);
  const regionCode = new URL(request.url).searchParams.get("regionCode")?.replace(/\D/g, "") ?? "";
  if (regionCode.length !== 10) return json({ error: "법정동 코드가 올바르지 않습니다." }, 400);
  const excluded = await exclusionsInRegion(regionCode);
  return json({ excluded });
}

export async function POST(request: Request) {
  if (!sameOrigin(request)) return json({ error: "잘못된 요청 출처입니다." }, 403);
  if (!extractCsrfOk(request)) {
    return json({ error: "세션이 만료되었거나 잘못된 요청입니다. 페이지를 새로고침해 주세요." }, 403);
  }
  const ip = clientIp(request);
  if (!allowMapRequest(`exclude:${ip}`, 30, 10 * 60 * 1000)) {
    return json({ error: "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요." }, 429);
  }
  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return json({ error: "요청을 읽지 못했습니다." }, 400);
  }
  const buildingId = cleanId(body.buildingId);
  if (!buildingId) return json({ error: "건축물 관리번호가 없습니다." }, 400);
  if (body.action === "restore") {
    await restoreBuilding(buildingId);
    return json({ ok: true });
  }
  if (body.safetyCheck !== true || body.reinforcement !== true) {
    return json({ error: "안전점검과 보강이 모두 끝난 건축물만 대상에서 뺄 수 있습니다." }, 400);
  }
  const regionCode = String(body.regionCode ?? "").replace(/\D/g, "");
  if (regionCode.length !== 10) return json({ error: "법정동 코드가 올바르지 않습니다." }, 400);
  const address = String(body.address ?? "").replace(/\s+/g, " ").trim().slice(0, 200);
  await excludeBuilding({ buildingId, address, regionCode });
  return json({ ok: true });
}
