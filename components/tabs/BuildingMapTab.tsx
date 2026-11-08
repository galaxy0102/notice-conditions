"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useDashboard } from "@/components/DashboardProvider";
import { StatusMap, type MapFeature, type MapPin } from "@/components/map/StatusMap";
import { judgeBuilding } from "@/lib/compare";
import { flat } from "@/lib/download";
import { yearsSince } from "@/lib/registry";
import { ancestry, compactName, matchFeature, parseGov, type ParsedGov, type RegionLevel, type RegionProps } from "@/lib/regions";
import type { Extracted, Verdict } from "@/lib/types";

type BuildingRow = {
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

type LegalDong = {
  name: string;
  sigunguCd: string;
  bjdongCd: string;
  ri: { name: string; bjdongCd: string }[];
};

type BuildingPayload = {
  ok: true;
  page: number;
  pageSize: number;
  totalCount: number;
  geocoder: "missing" | "ok" | "error";
  buildings: BuildingRow[];
  excluded: { buildingId: string; address: string }[];
};

const LEVELS: { id: RegionLevel; label: string }[] = [
  { id: "sido", label: "시·도" },
  { id: "sigungu", label: "시·군·구" },
  { id: "emd", label: "읍·면·동" },
];

function govText(notice: Extracted): string {
  return flat(notice.결과["지자체"]?.value).trim();
}

function badgeClass(verdict: Verdict): string {
  if (verdict === "해당") return "bg-emerald-100 text-emerald-900 ring-emerald-300";
  if (verdict === "비해당") return "bg-rose-100 text-rose-900 ring-rose-300";
  return "bg-orange-100 text-orange-950 ring-orange-300";
}

export function BuildingMapTab() {
  const { batches, buildingRegister, buildingGeocoder, csrf, refreshCsrf } = useDashboard();
  const [level, setLevel] = useState<RegionLevel>("sigungu");
  const [selected, setSelected] = useState<RegionProps | null>(null);
  const [sigunguFocus, setSigunguFocus] = useState<RegionProps | null>(null);
  const [features, setFeatures] = useState<MapFeature[]>([]);
  const [hint, setHint] = useState("");
  const [boundaryError, setBoundaryError] = useState<string | null>(null);
  const [dongs, setDongs] = useState<LegalDong[]>([]);
  const [dongKey, setDongKey] = useState("");
  const [page, setPage] = useState(1);
  const [payload, setPayload] = useState<BuildingPayload | null>(null);
  const [buildingError, setBuildingError] = useState<string | null>(null);
  const [loadingBuildings, setLoadingBuildings] = useState(false);
  const [pickedId, setPickedId] = useState("");
  const [noticeIndex, setNoticeIndex] = useState(0);

  const notices = useMemo(
    () =>
      batches.flatMap((batch) =>
        batch.items
          .map((item) => ({ item, gov: parseGov(govText(item)) }))
          .filter((row): row is { item: Extracted; gov: ParsedGov } => row.gov != null),
      ),
    [batches],
  );

  const parentCode = level === "emd" ? sigunguFocus?.code ?? "" : "";
  const fitKey = `${level}:${parentCode}`;

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ level });
    if (level === "emd" && parentCode) params.set("parent", parentCode);
    setBoundaryError(null);
    void fetch(`/api/boundaries?${params.toString()}`, {
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then(async (response) => {
        const body = (await response.json()) as { error?: string; features?: MapFeature[]; hint?: string };
        if (!response.ok) throw new Error(body.error || "지역 경계를 불러오지 못했습니다.");
        setFeatures(body.features ?? []);
        setHint(body.hint ?? "");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setFeatures([]);
        setBoundaryError(error instanceof Error ? error.message : "지역 경계를 불러오지 못했습니다.");
      });
    return () => controller.abort();
  }, [level, parentCode]);

  const noticeCodes = useMemo(() => {
    const codes = new Set<string>();
    for (const feature of features) {
      if (notices.some((row) => matchFeature(feature.properties, row.gov))) codes.add(feature.properties.code);
    }
    return codes;
  }, [features, notices]);

  const withNotice = features.filter((feature) => noticeCodes.has(feature.properties.code));
  const withoutNotice = features.filter((feature) => !noticeCodes.has(feature.properties.code));
  const unmatched = useMemo(() => {
    if (!features.length) return [];
    return notices.filter((row) => {
      const applies =
        level === "sido"
          ? !row.gov.sigungu && !row.gov.emd
          : level === "sigungu"
            ? Boolean(row.gov.sigungu) && !row.gov.emd
            : Boolean(row.gov.emd);
      if (!applies) return false;
      return !features.some((feature) => matchFeature(feature.properties, row.gov));
    });
  }, [features, level, notices]);

  const scope = useMemo(() => {
    if (!selected) return null;
    for (const feature of ancestry(selected)) {
      const hit = notices.filter((row) => matchFeature(feature, row.gov)).map((row) => row.item);
      if (hit.length) return { feature, notices: hit };
    }
    return null;
  }, [notices, selected]);

  const place = selected?.level === "sigungu" ? selected : selected?.level === "emd" ? sigunguFocus : null;

  useEffect(() => {
    if (!place) {
      setDongs([]);
      setDongKey("");
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({ sido: place.sido, sigungu: place.name });
    void fetch(`/api/legal-dong?${params.toString()}`, {
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal,
    })
      .then(async (response) => {
        const body = (await response.json()) as { dongs?: LegalDong[]; error?: string };
        if (!response.ok) throw new Error(body.error || "법정동 코드를 불러오지 못했습니다.");
        const rows = body.dongs ?? [];
        setDongs(rows);
        if (selected?.level === "emd") {
          const hit = rows.filter((dong) => compactName(dong.name) === compactName(selected.name));
          setDongKey(hit.length === 1 ? `${hit[0].sigunguCd}:${hit[0].bjdongCd}` : "");
        } else {
          setDongKey("");
        }
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setDongs([]);
        setDongKey("");
      });
    return () => controller.abort();
  }, [place, selected]);

  const activeDong = dongs.find((dong) => `${dong.sigunguCd}:${dong.bjdongCd}` === dongKey) ?? null;
  const [riCode, setRiCode] = useState("");
  useEffect(() => {
    setRiCode("");
    setPage(1);
    setPayload(null);
    setBuildingError(null);
    setPickedId("");
  }, [dongKey]);

  const queryCode = riCode || activeDong?.bjdongCd || "";
  const activeNotice = scope?.notices[noticeIndex] ?? scope?.notices[0];

  const loadBuildings = useCallback(
    async (nextPage: number) => {
      if (!activeDong || !queryCode || !scope) return;
      setLoadingBuildings(true);
      setBuildingError(null);
      try {
        const params = new URLSearchParams({
          sigunguCd: activeDong.sigunguCd,
          bjdongCd: queryCode,
          page: String(nextPage),
        });
        const response = await fetch(`/api/buildings?${params.toString()}`, {
          cache: "no-store",
          credentials: "same-origin",
        });
        const body = (await response.json()) as BuildingPayload | { ok?: false; message?: string; error?: string };
        if (!response.ok || !("ok" in body) || body.ok !== true) {
          const fail = body as { message?: string; error?: string };
          setPayload(null);
          setBuildingError(fail.message || fail.error || "건축물대장을 불러오지 못했습니다.");
          return;
        }
        setPayload(body);
        setPage(body.page);
        setPickedId("");
      } catch {
        setPayload(null);
        setBuildingError("건축물대장을 불러오지 못했습니다.");
      } finally {
        setLoadingBuildings(false);
      }
    },
    [activeDong, queryCode, scope],
  );

  const judged = useMemo(() => {
    if (!activeNotice || !payload) return [];
    return payload.buildings.map((building) => {
      const result = judgeBuilding(activeNotice, {
        address: building.address,
        ageYears: yearsSince(building.approvalDate),
        floors: building.floors,
        basementFloors: building.basementFloors,
        floorArea: building.floorArea,
        use: building.use,
        structure: building.structure,
      });
      return { building, ...result };
    });
  }, [activeNotice, payload]);

  const counts = judged.reduce(
    (all, row) => {
      all[row.overall] += 1;
      return all;
    },
    { 해당: 0, 비해당: 0, 확인필요: 0 } as Record<Verdict, number>,
  );

  const pins: MapPin[] = judged
    .filter((row) => row.building.lat != null && row.building.lng != null)
    .map((row) => ({
      id: row.building.id,
      lat: row.building.lat as number,
      lng: row.building.lng as number,
      verdict: row.overall,
      label: row.building.address || row.building.name || "건축물",
    }));

  const picked = judged.find((row) => row.building.id === pickedId) ?? null;

  const choose = useCallback((region: RegionProps) => {
    setSelected(region);
    setNoticeIndex(0);
    if (region.level === "sigungu") setSigunguFocus(region);
  }, []);

  async function exclude(action: "exclude" | "restore", building: { id: string; address: string }) {
    if (!activeDong || !queryCode) return;
    const token = csrf || (await refreshCsrf());
    const response = await fetch("/api/buildings/exclude", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json", "x-extract-csrf": token },
      body: JSON.stringify({
        action,
        buildingId: building.id,
        address: building.address,
        regionCode: `${activeDong.sigunguCd}${queryCode}`,
        safetyCheck: true,
        reinforcement: true,
      }),
    });
    const body = (await response.json()) as { error?: string };
    if (!response.ok) {
      setBuildingError(body.error || "제외를 저장하지 못했습니다.");
      return;
    }
    await loadBuildings(page);
  }

  return (
    <div className="space-y-4">
      <section>
        <h2 className="text-lg font-semibold text-gov-900">지역별 노후건축물 현황 지도</h2>
        <p className="mt-1 text-sm text-slate-600">
          공고가 올라온 지역은 색으로 칠하고, 공고가 없는 지역은 회색 경계만 둡니다. 건물 핀은 건축물대장과 현재 공고 조건으로
          판정합니다.
        </p>
      </section>

      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <aside className="space-y-3">
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gov-900">지역 통계 · {LEVELS.find((item) => item.id === level)?.label}</h3>
            <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
              <div className="rounded-lg bg-teal-50 px-3 py-2">
                <dt className="text-xs text-teal-900">공고 있음</dt>
                <dd className="text-lg font-bold text-teal-950">{withNotice.length}</dd>
              </div>
              <div className="rounded-lg bg-slate-100 px-3 py-2">
                <dt className="text-xs text-slate-600">공고 없음</dt>
                <dd className="text-lg font-bold text-slate-800">{withoutNotice.length}</dd>
              </div>
            </dl>
            <RegionList title="공고가 있는 지역" empty="이 단계에는 공고가 연결된 지역이 없습니다." rows={withNotice} onPick={choose} />
            <RegionList
              title="공고가 없는 지역"
              empty={hint || "표시 중인 경계가 없습니다."}
              rows={withoutNotice}
              onPick={choose}
            />
            {level === "sido" && notices.some((row) => row.gov.sigungu || row.gov.emd) ? (
              <p className="mt-2 text-xs text-slate-500">시·군·구 또는 읍·면·동 공고는 그 단계에서 색이 들어갑니다.</p>
            ) : null}
            {unmatched.length ? (
              <div className="mt-3">
                <h4 className="text-xs font-semibold text-slate-500">경계에서 찾지 못한 공고</h4>
                <ul className="mt-1 space-y-1 text-xs text-slate-600">
                  {unmatched.map((row) => (
                    <li key={row.item.공고}>{row.gov.raw}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </section>
        </aside>

        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {LEVELS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setLevel(item.id)}
                className={`rounded-full px-3 py-1.5 text-sm font-semibold ring-1 ${
                  level === item.id ? "bg-gov-800 text-white ring-gov-800" : "bg-white text-slate-700 ring-slate-300"
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
          {boundaryError ? <p className="text-sm text-red-700">{boundaryError}</p> : null}
          {hint ? <p className="text-sm text-slate-600">{hint}</p> : null}
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <StatusMap
              features={features}
              noticeCodes={noticeCodes}
              selectedCode={selected?.code ?? ""}
              pins={pins}
              fitKey={fitKey}
              onSelect={choose}
              onPin={setPickedId}
            />
            <div className="flex flex-wrap gap-3 border-t border-slate-100 px-4 py-2 text-xs text-slate-600">
              <span className="inline-flex items-center gap-1">
                <i className="inline-block h-3 w-3 rounded-sm bg-teal-500" /> 공고 있음
              </span>
              <span className="inline-flex items-center gap-1">
                <i className="inline-block h-3 w-3 rounded-sm border border-slate-400 bg-white" /> 공고 없음 · 경계만
              </span>
              <span className="inline-flex items-center gap-1">
                <i className="inline-block h-3 w-3 rounded-full bg-emerald-600" /> 해당
              </span>
              <span className="inline-flex items-center gap-1">
                <i className="inline-block h-3 w-3 rounded-full bg-rose-600" /> 비해당
              </span>
              <span className="inline-flex items-center gap-1">
                <i className="inline-block h-3 w-3 rounded-full bg-orange-600" /> 확인필요
              </span>
            </div>
          </div>

          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h3 className="text-sm font-semibold text-gov-900">건축물 핀</h3>
            {buildingRegister === null ? <p className="mt-2 text-sm text-slate-600">연결 상태를 확인하는 중입니다.</p> : null}
            {buildingRegister === false ? (
              <p className="mt-2 text-sm text-slate-700">
                건축물대장 연결 키가 없어 건물 핀을 표시할 수 없습니다. 지역 경계와 공고 유무는 그대로 볼 수 있습니다.
              </p>
            ) : null}
            {buildingRegister && !scope ? (
              <p className="mt-2 text-sm text-slate-600">
                공고가 있는 지역을 선택하면 그 조건으로 건축물대장을 조회합니다. 공고가 없는 지역은 핀을 만들지 않습니다.
              </p>
            ) : null}
            {buildingRegister && scope && !place ? (
              <p className="mt-2 text-sm text-slate-600">시·군·구를 선택하면 그 안의 법정동 건축물을 조회할 수 있습니다.</p>
            ) : null}
            {buildingRegister && scope && place ? (
              <div className="mt-3 space-y-3">
                <p className="text-sm text-slate-700">
                  판정 기준 <span className="font-semibold text-gov-900">{scope.feature.label}</span> 공고 · {activeNotice?.공고}
                </p>
                {scope.notices.length > 1 ? (
                  <label className="block text-sm">
                    적용할 공고
                    <select
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                      value={noticeIndex}
                      onChange={(event) => setNoticeIndex(Number(event.target.value))}
                    >
                      {scope.notices.map((notice, index) => (
                        <option key={notice.공고} value={index}>
                          {notice.공고}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : null}
                {selected?.level === "emd" && !activeDong ? (
                  <p className="text-sm text-slate-600">
                    이 행정동 이름과 같은 법정동이 없습니다. 아래에서 법정동을 고르면 건축물대장을 조회합니다.
                  </p>
                ) : null}
                <div className="grid gap-2 sm:grid-cols-2">
                  <label className="text-sm">
                    법정동
                    <select
                      className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                      value={dongKey}
                      onChange={(event) => setDongKey(event.target.value)}
                    >
                      <option value="">법정동 선택</option>
                      {dongs.map((dong) => (
                        <option key={`${dong.sigunguCd}:${dong.bjdongCd}`} value={`${dong.sigunguCd}:${dong.bjdongCd}`}>
                          {dong.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {activeDong && activeDong.ri.length ? (
                    <label className="text-sm">
                      리
                      <select
                        className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                        value={riCode}
                        onChange={(event) => setRiCode(event.target.value)}
                      >
                        <option value="">읍·면 전체</option>
                        {activeDong.ri.map((ri) => (
                          <option key={ri.bjdongCd} value={ri.bjdongCd}>
                            {ri.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                </div>
                {!dongs.length ? <p className="text-sm text-slate-600">이 시·군·구 이름의 법정동 코드를 찾지 못했습니다.</p> : null}
                <button
                  type="button"
                  className="rounded-lg bg-gov-700 px-4 py-2 text-sm font-semibold text-white hover:bg-gov-800 disabled:opacity-50"
                  disabled={!activeDong || !queryCode || loadingBuildings}
                  onClick={() => void loadBuildings(1)}
                >
                  {loadingBuildings ? "조회 중…" : "건축물대장 조회"}
                </button>
              </div>
            ) : null}

            {buildingError ? <p className="mt-3 text-sm text-red-700">{buildingError}</p> : null}
            {payload ? (
              <div className="mt-4 space-y-3">
                <p className="text-sm text-slate-700">
                  대장 {payload.totalCount.toLocaleString("ko-KR")}건 중 이 페이지 {judged.length}건을 판정했습니다. 페이지 숫자이며
                  구 전체 집계가 아닙니다. 제외 {payload.excluded.length}건은 대상에서 빠졌습니다.
                </p>
                <dl className="grid grid-cols-3 gap-2 text-sm">
                  <Count label="해당" value={counts.해당} tone="emerald" />
                  <Count label="비해당" value={counts.비해당} tone="rose" />
                  <Count label="확인필요" value={counts.확인필요} tone="orange" />
                </dl>
                {payload.geocoder === "missing" || buildingGeocoder === false ? (
                  <p className="text-sm text-slate-700">
                    좌표 변환 키가 없어 핀은 표시하지 않습니다. 아래 목록의 판정은 건축물대장 값으로 계산했습니다. 지역 경계는
                    그대로입니다.
                  </p>
                ) : null}
                {payload.geocoder === "error" ? (
                  <p className="text-sm text-slate-700">좌표 변환 키가 거부되어 핀을 찍지 못했습니다. 판정 목록은 아래에 있습니다.</p>
                ) : null}
                {payload.geocoder === "ok" && judged.some((row) => row.building.lat == null) ? (
                  <p className="text-sm text-slate-600">좌표를 찾지 못한 건축물은 목록에만 있습니다.</p>
                ) : null}
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
                    disabled={page <= 1 || loadingBuildings}
                    onClick={() => void loadBuildings(page - 1)}
                  >
                    이전
                  </button>
                  <button
                    type="button"
                    className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm disabled:opacity-40"
                    disabled={page * payload.pageSize >= payload.totalCount || loadingBuildings}
                    onClick={() => void loadBuildings(page + 1)}
                  >
                    다음
                  </button>
                </div>
                <ul className="divide-y divide-slate-100 rounded-lg border border-slate-100">
                  {judged.map((row) => (
                    <li key={row.building.id || row.building.address}>
                      <button
                        type="button"
                        className={`flex w-full items-start justify-between gap-3 px-3 py-2 text-left text-sm ${
                          pickedId === row.building.id ? "bg-sky-50" : "hover:bg-slate-50"
                        }`}
                        onClick={() => setPickedId(row.building.id)}
                      >
                        <span>
                          <span className="font-medium text-slate-900">{row.building.address || "주소 없음"}</span>
                          <span className="mt-0.5 block text-xs text-slate-500">
                            {row.building.name ? `${row.building.name} · ` : ""}
                            {row.building.use || "용도 없음"} · {row.building.floors || "—"}층 · {row.building.floorArea || "—"}㎡
                          </span>
                        </span>
                        <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-bold ring-1 ${badgeClass(row.overall)}`}>
                          {row.overall}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                {!judged.length ? <p className="text-sm text-slate-600">이 페이지에서 대상에 남은 건축물이 없습니다.</p> : null}
                {payload.excluded.length ? (
                  <div>
                    <h4 className="text-sm font-semibold text-slate-800">대상에서 뺀 건축물</h4>
                    <ul className="mt-2 space-y-1 text-sm">
                      {payload.excluded.map((row) => (
                        <li key={row.buildingId} className="flex items-center justify-between gap-2">
                          <span>{row.address || row.buildingId}</span>
                          <button
                            type="button"
                            className="text-xs font-medium text-gov-700 underline"
                            onClick={() => void exclude("restore", { id: row.buildingId, address: row.address })}
                          >
                            제외 해제
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {picked ? (
                  <BuildingDetail
                    address={picked.building.address}
                    overall={picked.overall}
                    checks={picked.checks}
                    canExclude={Boolean(picked.building.id)}
                    onExclude={(safety, reinforcement) => {
                      if (!safety || !reinforcement) return;
                      void exclude("exclude", picked.building);
                    }}
                  />
                ) : null}
              </div>
            ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}

function RegionList({
  title,
  empty,
  rows,
  onPick,
}: {
  title: string;
  empty: string;
  rows: MapFeature[];
  onPick: (region: RegionProps) => void;
}) {
  return (
    <div className="mt-3">
      <h4 className="text-xs font-semibold text-slate-500">
        {title} {rows.length ? `${rows.length}곳` : ""}
      </h4>
      {rows.length ? (
        <ul className="mt-1 max-h-40 space-y-1 overflow-auto text-sm">
          {rows.map((feature) => (
            <li key={feature.properties.code}>
              <button type="button" className="text-left text-gov-800 hover:underline" onClick={() => onPick(feature.properties)}>
                {feature.properties.label}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-xs text-slate-500">{empty}</p>
      )}
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: number; tone: "emerald" | "rose" | "orange" }) {
  const cls =
    tone === "emerald" ? "bg-emerald-50 text-emerald-950" : tone === "rose" ? "bg-rose-50 text-rose-950" : "bg-orange-50 text-orange-950";
  return (
    <div className={`rounded-lg px-3 py-2 ${cls}`}>
      <dt className="text-xs">{label}</dt>
      <dd className="text-lg font-bold">{value}</dd>
    </div>
  );
}

function BuildingDetail({
  address,
  overall,
  checks,
  canExclude,
  onExclude,
}: {
  address: string;
  overall: Verdict;
  checks: { item: string; condition: string; building: string; verdict: Verdict; note: string }[];
  canExclude: boolean;
  onExclude: (safety: boolean, reinforcement: boolean) => void;
}) {
  const [safety, setSafety] = useState(false);
  const [reinforcement, setReinforcement] = useState(false);
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium text-slate-900">{address || "주소 없음"}</p>
        <span className={`rounded-full px-2 py-0.5 text-xs font-bold ring-1 ${badgeClass(overall)}`}>{overall}</span>
      </div>
      <table className="mt-2 w-full text-left text-xs">
        <tbody>
          {checks.map((check) => (
            <tr key={check.item} className="border-t border-slate-100">
              <td className="py-1 pr-2 font-medium">{check.item}</td>
              <td className="py-1 pr-2">{check.condition}</td>
              <td className="py-1 pr-2">{check.building}</td>
              <td className="py-1">{check.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {canExclude ? (
        <fieldset className="mt-3 space-y-2 text-sm">
          <legend className="font-medium text-slate-800">안전점검과 보강이 모두 끝나면 대상에서 뺍니다.</legend>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={safety} onChange={(event) => setSafety(event.target.checked)} />
            안전점검 완료
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={reinforcement} onChange={(event) => setReinforcement(event.target.checked)} />
            보강 완료
          </label>
          <button
            type="button"
            className="rounded-lg bg-slate-800 px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            disabled={!safety || !reinforcement}
            onClick={() => onExclude(safety, reinforcement)}
          >
            대상에서 제외
          </button>
        </fieldset>
      ) : (
        <p className="mt-3 text-xs text-slate-500">관리번호가 없어 제외를 저장할 수 없습니다.</p>
      )}
    </div>
  );
}
