"use client";

import { TABS } from "@/lib/types";
import { KindBadge } from "./KindBadge";
import { useDashboard } from "./DashboardProvider";
import { NoticeAnalysisTab } from "./tabs/NoticeAnalysisTab";
import { BuildingMatchTab } from "./tabs/BuildingMatchTab";
import { NoticeReportTab } from "./tabs/NoticeReportTab";
import { BuildingMapTab } from "./tabs/BuildingMapTab";

export function DashboardShell() {
  const { tab, setTab, mock, simulated, signedIn, viewer, batches, selectedBatch } = useDashboard();
  const batch = batches[selectedBatch];

  return (
    <div className="min-h-screen bg-gov-50">
      <header className="bg-gov-900 text-white shadow-md print:hidden">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:py-5">
          <div className="flex items-start gap-3">
            <div className="mt-0.5 hidden h-10 w-10 shrink-0 items-center justify-center rounded-md bg-white/10 text-sm font-bold tracking-tight sm:flex">
              안전
            </div>
            <div>
              <p className="text-xs font-medium tracking-wide text-sky-200/90">건축안전센터 · 소규모 노후건축물</p>
              <h1 className="text-lg font-bold leading-snug sm:text-xl">
                소규모 노후건축물 안전점검 지원 대상 선별 시스템
              </h1>
            </div>
          </div>
          <p className="text-xs text-slate-300 sm:text-right">구청 담당자용 · 내부 업무 화면</p>
        </div>
        <nav className="border-t border-white/10 bg-gov-800" aria-label="주요 업무">
          <div className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-2 sm:px-4">
            {TABS.map((item) => {
              const active = tab === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTab(item.id)}
                  className={`relative min-w-max px-3 py-3 text-sm font-medium transition sm:px-4 ${
                    active ? "text-white" : "text-slate-300 hover:text-white"
                  }`}
                  aria-current={active ? "page" : undefined}
                >
                  <span className="flex items-center gap-2">
                    <span className="sm:hidden">{item.short}</span>
                    <span className="hidden sm:inline">{item.label}</span>
                    {batch && (item.id === "extract" || item.id === "compare") ? (
                      <KindBadge kind={batch.kind} count={batch.items.length} />
                    ) : null}
                  </span>
                  {active ? (
                    <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-teal-300 sm:inset-x-4" />
                  ) : null}
                </button>
              );
            })}
          </div>
        </nav>
      </header>

      {mock ? (
        <div className="border-b border-amber-200 bg-amber-50 text-amber-950 print:hidden">
          <p className="mx-auto max-w-6xl px-4 py-2.5 text-sm">
            지금은 샘플 데이터로 동작합니다. Anthropic API 키가 이 환경에 없습니다. 게시된 앱 비밀 값에{" "}
            <code className="rounded bg-white/70 px-1">ANTHROPIC_API_KEY</code>를 넣으면 실제 추출이 됩니다.
            {simulated ? " 미리보기 사용자는 모의 로그인입니다." : null}
          </p>
        </div>
      ) : null}
      {!signedIn ? (
        <div className="border-b border-amber-200 bg-amber-50 text-amber-950 print:hidden">
          <p className="mx-auto max-w-6xl px-4 py-2.5 text-sm">
            조건 추출은 Cursor에 로그인한 담당자만 쓸 수 있습니다. 가능하면 팀으로, 팀이 없으면 비공개로 열어 주세요.
          </p>
        </div>
      ) : null}
      {signedIn && viewer ? (
        <div className="border-b border-slate-200 bg-white text-slate-600 print:hidden">
          <p className="mx-auto max-w-6xl px-4 py-2 text-xs">로그인: {viewer}</p>
        </div>
      ) : null}
      <main className="mx-auto max-w-6xl px-4 py-6 sm:py-8">
        {tab === "extract" ? <NoticeAnalysisTab /> : null}
        {tab === "compare" ? <BuildingMatchTab /> : null}
        {tab === "report" ? <NoticeReportTab /> : null}
        {tab === "map" ? <BuildingMapTab /> : null}
      </main>
    </div>
  );
}
