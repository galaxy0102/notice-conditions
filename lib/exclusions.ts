import { sql } from "./db";

export type ExclusionRow = {
  buildingId: string;
  address: string;
  regionCode: string;
  excludedAt: string;
};

type DbRow = {
  building_id: string;
  address: string;
  region_code: string;
  excluded_at: string | Date;
};

function mapRow(row: DbRow): ExclusionRow {
  return {
    buildingId: row.building_id,
    address: row.address,
    regionCode: row.region_code,
    excludedAt: row.excluded_at instanceof Date ? row.excluded_at.toISOString() : String(row.excluded_at),
  };
}

export async function exclusionsInRegion(regionCode: string): Promise<ExclusionRow[]> {
  const rows = await sql<DbRow>(
    "select building_id, address, region_code, excluded_at from building_exclusions where region_code = $1 order by excluded_at desc",
    [regionCode],
  );
  return rows.map(mapRow);
}

export async function excludedIdSet(ids: string[]): Promise<Set<string>> {
  if (!ids.length) return new Set();
  const placeholders = ids.map((_, index) => `$${index + 1}`).join(", ");
  const rows = await sql<{ building_id: string }>(
    `select building_id from building_exclusions where building_id in (${placeholders})`,
    ids,
  );
  return new Set(rows.map((row) => row.building_id));
}

export async function excludeBuilding(input: {
  buildingId: string;
  address: string;
  regionCode: string;
}): Promise<void> {
  await sql(
    `insert into building_exclusions (building_id, address, region_code, safety_check, reinforcement)
     values ($1, $2, $3, true, true)
     on conflict (building_id) do update set
       address = excluded.address,
       region_code = excluded.region_code,
       safety_check = true,
       reinforcement = true,
       excluded_at = now()`,
    [input.buildingId, input.address, input.regionCode],
  );
}

export async function restoreBuilding(buildingId: string): Promise<void> {
  await sql("delete from building_exclusions where building_id = $1", [buildingId]);
}
