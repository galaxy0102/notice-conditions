import { json, sameSiteRead } from "../../../lib/http";
import { legalDongsIn } from "../../../lib/legal-dong";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  if (!sameSiteRead(request)) return json({ error: "잘못된 요청 출처입니다." }, 403);
  const url = new URL(request.url);
  const sido = (url.searchParams.get("sido") ?? "").trim();
  const sigungu = (url.searchParams.get("sigungu") ?? "").trim();
  if (!sido || !sigungu) return json({ error: "시·도와 시·군·구가 필요합니다." }, 400);
  try {
    const dongs = await legalDongsIn(sido, sigungu);
    return json({
      dongs: dongs.map((dong) => ({
        name: dong.name,
        sigunguCd: dong.sigunguCd,
        bjdongCd: dong.bjdongCd,
        ri: dong.ri,
      })),
    });
  } catch {
    return json({ error: "법정동 코드를 불러오지 못했습니다." }, 500);
  }
}
