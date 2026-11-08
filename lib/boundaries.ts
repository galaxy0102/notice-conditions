import { readFile } from "node:fs/promises";
import path from "node:path";
import { feature } from "topojson-client";
import type { Feature, FeatureCollection } from "geojson";
import type { GeometryCollection, Topology } from "topojson-specification";
import type { RegionLevel, RegionProps } from "./regions";

type LevelFile = { file: string; object: string };

const FILES: Record<RegionLevel, LevelFile> = {
  sido: { file: "sido.topo.json", object: "skorea_provinces_2018_geo" },
  sigungu: { file: "sigungu.topo.json", object: "skorea_municipalities_2018_geo" },
  emd: { file: "emd.topo.json", object: "skorea_submunicipalities_2018_geo" },
};

type Named = { name: string; sido: string };

let loaded: Promise<{
  collections: Record<RegionLevel, FeatureCollection>;
}> | null = null;

function dir(): string {
  return path.join(process.cwd(), "data", "boundaries");
}

function propsOf(value: unknown): { name: string; code: string } {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  return { name: String(record.name ?? ""), code: String(record.code ?? "") };
}

async function readTopology(level: RegionLevel): Promise<FeatureCollection> {
  const spec = FILES[level];
  const raw = await readFile(path.join(dir(), spec.file), "utf8");
  const topology = JSON.parse(raw) as Topology;
  const object = topology.objects[spec.object] as GeometryCollection;
  return feature(topology, object) as FeatureCollection;
}

async function load(): Promise<{ collections: Record<RegionLevel, FeatureCollection> }> {
  const [sido, sigungu, emd] = await Promise.all([
    readTopology("sido"),
    readTopology("sigungu"),
    readTopology("emd"),
  ]);

  const sidoByCode = new Map<string, string>();
  for (const item of sido.features) {
    const props = propsOf(item.properties);
    sidoByCode.set(props.code, props.name);
    item.properties = {
      code: props.code,
      name: props.name,
      level: "sido",
      sido: props.name,
      sigungu: "",
      label: props.name,
    } satisfies RegionProps;
  }

  const sigunguByCode = new Map<string, Named>();
  for (const item of sigungu.features) {
    const props = propsOf(item.properties);
    const sidoName = sidoByCode.get(props.code.slice(0, 2)) ?? "";
    sigunguByCode.set(props.code, { name: props.name, sido: sidoName });
    const region: RegionProps = {
      code: props.code,
      name: props.name,
      level: "sigungu",
      sido: sidoName,
      sigungu: props.name,
      label: `${sidoName} ${props.name}`.trim(),
    };
    item.properties = region;
  }

  for (const item of emd.features) {
    const props = propsOf(item.properties);
    const parent = sigunguByCode.get(props.code.slice(0, 5));
    const sidoName = parent?.sido ?? sidoByCode.get(props.code.slice(0, 2)) ?? "";
    const sigunguName = parent?.name ?? "";
    const region: RegionProps = {
      code: props.code,
      name: props.name,
      level: "emd",
      sido: sidoName,
      sigungu: sigunguName,
      label: [sidoName, sigunguName, props.name].filter(Boolean).join(" "),
    };
    item.properties = region;
  }

  return { collections: { sido, sigungu, emd } };
}

function ready() {
  loaded ??= load().catch((error: unknown) => {
    loaded = null;
    throw error;
  });
  return loaded;
}

export async function boundaryCollection(level: RegionLevel, parentCode?: string): Promise<FeatureCollection> {
  const { collections } = await ready();
  if (level !== "emd") return collections[level];
  const parent = (parentCode ?? "").replace(/\D/g, "");
  const features = parent
    ? collections.emd.features.filter((item) => propsOf(item.properties).code.startsWith(parent))
    : [];
  return { type: "FeatureCollection", features };
}

export function regionOf(feature: Feature): RegionProps | null {
  const props = feature.properties;
  if (!props || typeof props !== "object") return null;
  const record = props as RegionProps;
  if (!record.code || !record.level) return null;
  return record;
}
