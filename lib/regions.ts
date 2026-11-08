export type RegionLevel = "sido" | "sigungu" | "emd";

export type RegionProps = {
  code: string;
  name: string;
  level: RegionLevel;
  sido: string;
  sigungu: string;
  label: string;
};

export type ParsedGov = {
  raw: string;
  sido: string;
  sigungu: string;
  emd: string;
};

const RENAME: Record<string, string> = {
  강원도: "강원특별자치도",
  강원특별자치도: "강원특별자치도",
  전라북도: "전북특별자치도",
  전북특별자치도: "전북특별자치도",
  제주도: "제주특별자치도",
  제주특별자치도: "제주특별자치도",
};

const FAMILY: Record<string, string> = {
  광주광역시: "전남광주",
  전라남도: "전남광주",
  전남광주통합특별시: "전남광주",
};

export function compactName(name: string): string {
  return name.replace(/\s+/g, "");
}

/** 1:1 renames (강원도 → 강원특별자치도). The 2018 boundary polygon keeps the old name. */
export function canonSido(name: string): string {
  const compact = compactName(name);
  return RENAME[compact] ?? compact;
}

/** Lookup family for legal-dong codes. 광주·전남은 2026 법정동 코드에서 한 시·도로 합쳐져 있다. */
export function sidoFamily(name: string): string {
  const canon = canonSido(name);
  return FAMILY[canon] ?? canon;
}

export function sameSigungu(a: string, b: string): boolean {
  const left = compactName(a);
  const right = compactName(b);
  if (!left || !right) return false;
  if (left === right) return true;
  const sejong = ["세종시", "세종특별자치시"];
  return sejong.includes(left) && sejong.includes(right);
}

export function parseGov(raw: string): ParsedGov | null {
  const text = raw.replace(/\s+/g, " ").trim();
  if (!text) return null;
  const match = text.match(/^(.*?특별자치도|.*?특별자치시|.*?특별시|.*?광역시|.*?도)(?:\s+(.*))?$/);
  if (!match) return null;
  const sido = match[1];
  const rest = (match[2] ?? "").trim();
  if (!rest) return { raw: text, sido, sigungu: "", emd: "" };
  const parts = rest.split(/\s+/);
  const last = parts[parts.length - 1] ?? "";
  const isEmd = /(?:동|읍|면|가|로)$/.test(last) && !/(?:구|시|군)$/.test(last);
  let emd = "";
  if (isEmd) {
    emd = parts.pop() ?? "";
  }
  return { raw: text, sido, sigungu: parts.join(" "), emd };
}

function sameSidoScope(featureSido: string, govSido: string, level: RegionLevel): boolean {
  if (level === "sido") {
    if (canonSido(featureSido) === canonSido(govSido)) return true;
    const notice = compactName(govSido);
    const polygon = compactName(featureSido);
    return notice === "전남광주통합특별시" && (polygon === "광주광역시" || polygon === "전라남도");
  }
  return sidoFamily(featureSido) === sidoFamily(govSido);
}

export function matchFeature(feature: RegionProps, gov: ParsedGov): boolean {
  if (feature.level === "sido") {
    if (gov.sigungu || gov.emd) return false;
    return sameSidoScope(feature.name, gov.sido, "sido");
  }
  if (feature.level === "sigungu") {
    if (gov.emd || !sameSidoScope(feature.sido, gov.sido, "sigungu")) return false;
    if (!gov.sigungu) {
      return compactName(feature.name) === "세종시" && canonSido(gov.sido) === "세종특별자치시";
    }
    return sameSigungu(feature.name, gov.sigungu);
  }
  if (!gov.emd || !sameSidoScope(feature.sido, gov.sido, "emd")) return false;
  if (gov.sigungu && !sameSigungu(feature.sigungu, gov.sigungu)) return false;
  return compactName(feature.name) === compactName(gov.emd);
}

export function ancestry(feature: RegionProps): RegionProps[] {
  const chain: RegionProps[] = [feature];
  if (feature.level === "emd") {
    chain.push({
      level: "sigungu",
      code: feature.code.slice(0, 5),
      name: feature.sigungu,
      sido: feature.sido,
      sigungu: feature.sigungu,
      label: `${feature.sido} ${feature.sigungu}`.trim(),
    });
  }
  if (feature.level !== "sido") {
    chain.push({
      level: "sido",
      code: feature.code.slice(0, 2),
      name: feature.sido,
      sido: feature.sido,
      sigungu: "",
      label: feature.sido,
    });
  }
  return chain;
}
