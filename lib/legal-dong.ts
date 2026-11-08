import { readFile } from "node:fs/promises";
import path from "node:path";
import { sameSigungu, sidoFamily } from "./regions";

export type LegalRi = { name: string; bjdongCd: string };

export type LegalDong = {
  sido: string;
  sigungu: string;
  name: string;
  sigunguCd: string;
  bjdongCd: string;
  ri: LegalRi[];
};

type RawFile = { dong: string[][]; ri: string[][] };

let pending: Promise<LegalDong[]> | null = null;

async function load(): Promise<LegalDong[]> {
  const raw = JSON.parse(
    await readFile(path.join(process.cwd(), "data", "legal-dong.json"), "utf8"),
  ) as RawFile;
  const ri = new Map<string, LegalRi[]>();
  for (const row of raw.ri ?? []) {
    const [parent, name, bjdongCd] = row;
    if (!parent || !name || !bjdongCd) continue;
    const list = ri.get(parent) ?? [];
    list.push({ name, bjdongCd });
    ri.set(parent, list);
  }
  return (raw.dong ?? []).map((row) => {
    const [sido, sigungu, name, sigunguCd, bjdongCd] = row;
    const kids = ri.get(`${sigunguCd}${bjdongCd}`) ?? [];
    return { sido, sigungu, name, sigunguCd, bjdongCd, ri: kids };
  });
}

function all(): Promise<LegalDong[]> {
  pending ??= load().catch((error: unknown) => {
    pending = null;
    throw error;
  });
  return pending;
}

export async function legalDongsIn(sido: string, sigungu: string): Promise<LegalDong[]> {
  const rows = await all();
  const family = sidoFamily(sido);
  return rows
    .filter((row) => sidoFamily(row.sido) === family && sameSigungu(row.sigungu, sigungu))
    .sort((a, b) => a.name.localeCompare(b.name, "ko"));
}
