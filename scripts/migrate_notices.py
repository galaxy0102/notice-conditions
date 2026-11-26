"""기존 공고 추출 결과(grok 웹앱 형식) → NoticeConditions JSON 변환.

기존 형식: {"공고": ..., "결과": {"지자체": {"value", "evidence"}, "층수": ..., ...}}
(489d49a 이후 삭제된 frontend_grok/lib/types.ts Extracted, lib/schema.ts KEYMAP)

사용법 (저장소 루트):
  python scripts/migrate_notices.py                       # data/notices/legacy/*.json → data/notices/*.json
  python scripts/migrate_notices.py <입력.json> ... --dry-run   # 저장하지 않고 리포트만

변환하지 못한 필드는 리포트로 출력한다. 변환 결과는 reviewed=False 이다(사람이 검수 후 True).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import normalize, regions  # noqa: E402
from core.conditions import (  # noqa: E402
    BusinessType,
    Contact,
    NoticeConditions,
    parse_area_text,
    parse_floor_text,
    parse_period,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_DIR = ROOT / "data" / "notices" / "legacy"
OUT_DIR = ROOT / "data" / "notices"

PROGRAM_TYPE = {"신청형": BusinessType.APPLY_INSPECTION, "직권형": BusinessType.EX_OFFICIO_INSPECTION}


def _v(result: dict, key: str):
    f = result.get(key)
    return f.get("value") if isinstance(f, dict) else f


def _ev(result: dict, key: str) -> str | None:
    f = result.get(key)
    e = f.get("evidence") if isinstance(f, dict) else None
    return str(e) if e else None


def _list(v) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip() for s in re.split(r"[\n;]|\s/\s", str(v)) if s.strip()]


def _slug(name: str) -> str:
    return re.sub(r"[^\w가-힣-]+", "_", name).strip("_") or "notice"


def convert(data: dict, notice_id: str) -> tuple[NoticeConditions, list[str]]:
    """반환: (NoticeConditions, 변환 못 한 항목 리포트)."""
    r = data.get("결과", {})
    issues: list[str] = []
    evidence: dict[str, str] = {}

    def keep_ev(field: str, key: str) -> None:
        e = _ev(r, key)
        if e:
            evidence[field] = e

    # 지자체 → sigungu_codes (정확히 일치할 때만)
    local_gov = _v(r, "지자체")
    codes: list[str] = []
    if local_gov:
        parts = str(local_gov).split(maxsplit=1)
        m = regions.match_sigungu(parts[0], parts[1] if len(parts) > 1 else "")
        codes = m["codes"]
        if not codes:
            issues.append(f"지자체 '{local_gov}' → 시군구코드 매칭 실패({m['kind']}). sigungu_codes 수동 입력 필요")
    else:
        issues.append("지자체 없음 → sigungu_codes 비움")
    keep_ev("local_gov", "지자체")

    # 사업유형
    pt = _v(r, "사업유형")
    business = PROGRAM_TYPE.get(str(pt)) if pt else None
    if business is None:
        issues.append(f"사업유형 '{pt}' → business_type 미정(사람이 6종 중 선택)")
    keep_ev("business_type", "사업유형")

    # 경과연수
    min_years = None
    y = _v(r, "경과연수")
    if y not in (None, ""):
        try:
            min_years = int(float(y))
        except (TypeError, ValueError):
            issues.append(f"경과연수 '{y}' 숫자 아님")
    keep_ev("min_years", "경과연수")

    # 층수·연면적: 이하/미만 표기가 없으면 추측하지 않는다
    floors = _v(r, "층수")
    fl = parse_floor_text(floors) if floors else None
    if floors and not fl:
        issues.append(f"층수 '{floors}' 해석 실패(이하/미만 표기 필요)")
    keep_ev("max_floors", "층수")
    area = _v(r, "연면적")
    ar = parse_area_text(area) if area else None
    if area and not ar:
        issues.append(f"연면적 '{area}' 해석 실패(㎡와 이하/미만 표기 필요)")
    keep_ev("max_area", "연면적")

    # 용도·구조
    use_filter: list[str] = []
    for u in _list(_v(r, "용도조건")):
        t, bad = normalize.use_tokens(u)
        use_filter += t
        issues += [f"용도조건 '{b}' 표준 용도로 변환 실패" for b in bad]
    keep_ev("use_filter", "용도조건")
    structure_filter: list[str] = []
    for s in _list(_v(r, "구조조건")):
        t, bad = normalize.structure_tokens(s)
        structure_filter += t
        issues += [f"구조조건 '{b}' 표준 구조로 변환 실패" for b in bad]
    keep_ev("structure_filter", "구조조건")

    # 제외대상
    exclusions = _list(_v(r, "제외대상"))
    collective = True if any("집합" in e for e in exclusions) else None
    multi = True if any("공동주택" in e for e in exclusions) else None
    keep_ev("exclusions", "제외대상")

    # 접수기간
    period = _v(r, "접수기간")
    start, end = parse_period(str(period)) if period else (None, None)
    if period and not end:
        issues.append(f"접수기간 '{period}'에서 종료일을 찾지 못함 → 마감 판정 불가")
    keep_ev("application_period", "접수기간")

    for k, f in (("선정방식", "selection_method"), ("우선순위기준", "priority_rules"), ("지원내용", "support_content"), ("규모", "scale"), ("신청자격방법", "contact.apply_method")):
        keep_ev(f, k)

    n = NoticeConditions(
        notice_id=notice_id,
        local_gov=local_gov,
        program_name=_v(r, "사업명"),
        year=str(_v(r, "연도")) if _v(r, "연도") else None,
        season=_v(r, "시기"),
        business_type=business,
        sigungu_codes=codes,
        apply_start=start,
        apply_end=end,
        application_period=period,
        min_years=min_years,
        max_floors=fl[0] if fl else None,
        floors_bound=fl[1] if fl else "LE",
        max_area=ar[0] if ar else None,
        area_bound=ar[1] if ar else "LE",
        use_filter=list(dict.fromkeys(use_filter)),
        structure_filter=list(dict.fromkeys(structure_filter)),
        collective_excluded=collective,
        multi_housing_excluded=multi,
        exclusions=exclusions,
        selection_method=_v(r, "선정방식"),
        priority_rules=_list(_v(r, "우선순위기준")),
        offmap_conditions=_list(_v(r, "확인필요")),
        support_content=_list(_v(r, "지원내용")),
        scale=_v(r, "규모"),
        contact=Contact(apply_method=_v(r, "신청자격방법")),
        reviewed=False,
        evidence=evidence,
    )
    issues += [
        "접수처·문의처·제출서류: 기존 형식에 없음 → 비움",
        "notice_date(공고일): 기존 형식에 없음 → 비움",
    ]
    return n, issues


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    files = args.files or sorted(LEGACY_DIR.glob("*.json"))
    if not files:
        print(f"변환할 파일이 없습니다: {LEGACY_DIR}/*.json (팀 공고 6건 원본을 여기에 두세요)")
        return
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        nid = _slug(data.get("공고") or path.stem)
        try:
            n, issues = convert(data, nid)
        except Exception as e:  # 리포트에 남기고 다음 파일로
            print(f"\n## {path.name}: 변환 실패 — {e}")
            continue
        print(f"\n## {path.name} → {nid}.json")
        print(f"- business_type={n.business_type and n.business_type.value}, sigungu_codes={n.sigungu_codes}")
        print(f"- 경과연수={n.min_years}, 층수={n.max_floors}{n.floors_bound if n.max_floors else ''}, "
              f"연면적={n.max_area}{n.area_bound if n.max_area else ''}, 용도={n.use_filter}, 구조={n.structure_filter}")
        print(f"- 접수 {n.apply_start} ~ {n.apply_end}, 대장 외 조건 {n.offmap_conditions}")
        print("- 변환하지 못한 항목:")
        for i in issues:
            print(f"  - {i}")
        if not args.dry_run:
            out = OUT_DIR / f"{nid}.json"
            out.write_text(n.model_dump_json(indent=2, by_alias=False), encoding="utf-8")
            print(f"- 저장: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
