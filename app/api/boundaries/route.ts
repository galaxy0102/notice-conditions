import { boundaryCollection } from "../../../lib/boundaries";
import { json, sameSiteRead } from "../../../lib/http";
import type { RegionLevel } from "../../../lib/regions";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LEVELS = new Set<RegionLevel>(["sido", "sigungu", "emd"]);

export async function GET(request: Request) {
  if (!sameSiteRead(request)) return json({ error: "잘못된 요청 출처입니다." }, 403);
  const url = new URL(request.url);
  const level = url.searchParams.get("level") ?? "";
  if (!LEVELS.has(level as RegionLevel)) {
    return json({ error: "지역 단계가 올바르지 않습니다." }, 400);
  }
  const parent = (url.searchParams.get("parent") ?? "").replace(/\D/g, "");
  if (level === "emd" && parent.length !== 5) {
    return json({
      type: "FeatureCollection",
      features: [],
      hint: "시·군·구를 선택하면 읍·면·동 경계가 표시됩니다.",
    });
  }
  try {
    const collection = await boundaryCollection(level as RegionLevel, parent);
    return json({ ...collection, hint: "" });
  } catch {
    return json({ error: "지역 경계를 불러오지 못했습니다." }, 500);
  }
}
