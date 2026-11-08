import { XMLParser } from "fast-xml-parser";
import { buildingRegisterApiKey } from "./http";

const TITLE_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo";
export const REGISTER_PAGE_SIZE = 20;

export type RegisterBuilding = {
  id: string;
  address: string;
  roadAddress: string;
  approvalDate: string;
  floors: string;
  basementFloors: string;
  floorArea: string;
  use: string;
  structure: string;
  name: string;
  lat: number | null;
  lng: number | null;
};

export type RegisterFailure = {
  ok: false;
  reason: "no_key" | "rejected" | "upstream" | "network";
  message: string;
};

export type RegisterPage = {
  ok: true;
  page: number;
  pageSize: number;
  totalCount: number;
  buildings: RegisterBuilding[];
};

const parser = new XMLParser({
  ignoreAttributes: true,
  trimValues: true,
  parseTagValue: false,
});

function textOf(value: unknown): string {
  if (value == null) return "";
  return String(value).trim();
}

function encodeServiceKey(key: string): string {
  let decoded = key.trim();
  if (decoded.includes("%")) {
    try {
      decoded = decodeURIComponent(decoded);
    } catch {
      decoded = key.trim();
    }
  }
  return encodeURIComponent(decoded);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function findString(node: unknown, names: string[]): string {
  const wanted = new Set(names);
  const stack = [node];
  while (stack.length) {
    const current = stack.pop();
    const record = asRecord(current);
    if (!record) continue;
    for (const [key, value] of Object.entries(record)) {
      if (wanted.has(key) && (typeof value === "string" || typeof value === "number")) {
        const text = textOf(value);
        if (text) return text;
      }
      if (value && typeof value === "object") stack.push(value);
    }
  }
  return "";
}

function approvalDate(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (digits.length !== 8 || digits === "00000000") return "";
  return `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
}

function joined(parts: string[]): string {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of parts) {
    const text = part.trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
  }
  return out.join(" ");
}

function itemRecords(body: Record<string, unknown> | null): Record<string, unknown>[] {
  if (!body) return [];
  const items = asRecord(body.items);
  const item = items?.item;
  if (Array.isArray(item)) {
    return item.map(asRecord).filter((row): row is Record<string, unknown> => row != null);
  }
  const one = asRecord(item);
  return one ? [one] : [];
}

export function parseRegisterBody(payload: string): RegisterPage | RegisterFailure {
  const trimmed = payload.trim();
  if (!trimmed) {
    return { ok: false, reason: "upstream", message: "건축물대장 응답이 비어 있습니다." };
  }
  let doc: unknown;
  try {
    doc = trimmed.startsWith("<") ? parser.parse(trimmed) : JSON.parse(trimmed);
  } catch {
    return { ok: false, reason: "upstream", message: "건축물대장 응답을 읽지 못했습니다." };
  }
  const auth = findString(doc, ["returnAuthMsg", "errMsg", "resultMsg", "returnReasonCode"]);
  const code = findString(doc, ["resultCode", "returnReasonCode"]);
  const rejected = /SERVICE_KEY|AUTH|KEY|30|20/.test(`${code} ${auth}`) && code !== "00";
  if (rejected || (code && code !== "00" && code !== "NORMAL SERVICE")) {
    const keyProblem = /KEY|AUTH|30|20/.test(`${code} ${auth}`);
    return {
      ok: false,
      reason: keyProblem ? "rejected" : "upstream",
      message: keyProblem
        ? "건축물대장 키가 거부되었습니다. 키와 활용신청을 확인해 주세요."
        : "건축물대장을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.",
    };
  }
  const root = asRecord(doc);
  const response = asRecord(root?.response) ?? root;
  const body = asRecord(response?.body);
  const totalCount = Number(textOf(body?.totalCount) || "0");
  const buildings = itemRecords(body).map(toBuilding).filter((row) => row.address || row.id);
  return {
    ok: true,
    page: Number(textOf(body?.pageNo) || "1") || 1,
    pageSize: REGISTER_PAGE_SIZE,
    totalCount: Number.isFinite(totalCount) ? totalCount : buildings.length,
    buildings,
  };
}

function toBuilding(row: Record<string, unknown>): RegisterBuilding {
  const useApr = textOf(row.useAprDay);
  return {
    id: textOf(row.mgmBldrgstPk),
    address: textOf(row.platPlc),
    roadAddress: textOf(row.newPlatPlc),
    approvalDate: approvalDate(useApr),
    floors: textOf(row.grndFlrCnt),
    basementFloors: textOf(row.ugrndFlrCnt),
    floorArea: textOf(row.totArea),
    use: joined([textOf(row.mainPurpsCdNm), textOf(row.etcPurps)]),
    structure: joined([textOf(row.strctCdNm), textOf(row.etcStrct)]),
    name: textOf(row.bldNm),
    lat: null,
    lng: null,
  };
}

export async function fetchRegisterPage(
  sigunguCd: string,
  bjdongCd: string,
  page: number,
): Promise<RegisterPage | RegisterFailure> {
  const key = buildingRegisterApiKey();
  if (!key) {
    return {
      ok: false,
      reason: "no_key",
      message: "건축물대장 연결 키가 없어 건물 핀을 불러오지 못했습니다. 지역 경계는 그대로 볼 수 있습니다.",
    };
  }
  const params = new URLSearchParams({
    sigunguCd,
    bjdongCd,
    numOfRows: String(REGISTER_PAGE_SIZE),
    pageNo: String(page),
  });
  const url = `${TITLE_URL}?${params.toString()}&serviceKey=${encodeServiceKey(key)}`;
  try {
    const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(20_000) });
    const payload = (await response.text()).slice(0, 1_000_000);
    if (!response.ok && !payload.trim()) {
      return { ok: false, reason: "upstream", message: "건축물대장을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요." };
    }
    return parseRegisterBody(payload);
  } catch {
    return { ok: false, reason: "network", message: "건축물대장 연결에 실패했습니다. 잠시 후 다시 시도해 주세요." };
  }
}
