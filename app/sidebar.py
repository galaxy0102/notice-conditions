"""사이드바: 지역 선택, 공고 선택, 판정 기준일, 옵션, 조건 편집기."""

from __future__ import annotations

from datetime import date

import streamlit as st

from app import region_picker, wheel_picker
from app.state import Ctx, clear
from core import ordinances, regions
from core.conditions import NoticeConditions
from core.evaluator import EvalOptions
from core.notices import load_notices, notice_label, notices_for

NO_NOTICE = "__none__"


def _bound_select(container, label: str, value: str, key: str) -> str:
    opts = ["LE", "LT"]
    names = {"LE": "이하", "LT": "미만"}
    return wheel_picker.wheel_select(container, label, opts, index=opts.index(value), format_func=names.get, key=key)


def _editor(n: NoticeConditions) -> None:
    """담당자 수정은 세션에만 반영한다. 수정본은 JSON 다운로드로만 내보낸다."""
    edits: dict = st.session_state["notice_edits"]
    cur = edits.get(n.notice_id, n)
    with st.sidebar.expander("조건 편집기" + (" · 수정됨" if n.notice_id in edits else ""), expanded=False):
        with st.form(f"edit-{n.notice_id}"):
            min_years = st.number_input("경과연수(년 이상)", 0, 100, cur.min_years or 0)
            c1, c2 = st.columns(2)
            max_floors = c1.number_input("층수", 0, 100, cur.max_floors or 0, help="0 = 조건 없음")
            fb = _bound_select(c2, "층수 경계", cur.floors_bound, f"fb-{n.notice_id}")
            c3, c4 = st.columns(2)
            max_area = c3.number_input("연면적(㎡)", 0.0, 100000.0, float(cur.max_area or 0), help="0 = 조건 없음")
            ab = _bound_select(c4, "연면적 경계", cur.area_bound, f"ab-{n.notice_id}")
            use = st.text_input("용도(쉼표로 구분)", ", ".join(cur.use_filter))
            strc = st.text_input("구조(쉼표로 구분)", ", ".join(cur.structure_filter))
            coll = st.checkbox("집합건물 제외", bool(cur.collective_excluded))
            offmap = st.text_area("대장 외 조건(한 줄에 하나)", "\n".join(cur.offmap_conditions))
            d1, d2 = st.columns(2)
            start = d1.date_input("접수 시작", cur.apply_start, format="YYYY-MM-DD")
            end = d2.date_input("접수 종료", cur.apply_end, format="YYYY-MM-DD")
            if st.form_submit_button("고친 조건 적용"):
                split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
                try:
                    edits[n.notice_id] = cur.model_copy(
                        update={
                            "min_years": int(min_years) or None,
                            "max_floors": int(max_floors) or None,
                            "floors_bound": fb,
                            "max_area": float(max_area) or None,
                            "area_bound": ab,
                            "use_filter": split(use),
                            "structure_filter": split(strc),
                            "collective_excluded": coll or None,
                            "offmap_conditions": [x.strip() for x in offmap.splitlines() if x.strip()],
                            "apply_start": start,
                            "apply_end": end,
                        }
                    )
                    NoticeConditions.model_validate(edits[n.notice_id].model_dump())
                    st.rerun()
                except ValueError as e:
                    edits.pop(n.notice_id, None)
                    st.error(f"고친 값을 적용하지 못했습니다: {e} 값을 고친 뒤 다시 누르세요.")
        if n.notice_id in edits:
            st.download_button(
                "고친 조건 내려받기(JSON)",
                edits[n.notice_id].model_dump_json(indent=2).encode("utf-8"),
                file_name=f"{n.notice_id}_수정본.json",
                mime="application/json",
            )
            if st.button("고친 조건 되돌리기"):
                edits.pop(n.notice_id)
                st.rerun()


def _name(code: str) -> str:
    loc = regions.locate(code)
    return " ".join(x for x in loc if x) if loc else code


def apply_region(codes: list[str], label: str = "", current: str | None = None) -> str:
    """공고 지역을 지역 선택(도·시·구, 읍·면·동 전체)에 적용한다. 다음에 그려지는 선택 위젯에 반영된다.

    일반구를 둔 시(예: 수원시 4개 구)는 첫 구를 열고 나머지 구 이름을 안내한다.
    current(지금 고른 시군구)가 공고 지역에 있으면 그 구를 연다. 반환: 화면 안내문.
    """
    codes = [c for c in codes if regions.locate(c)]
    if not codes:
        return ""
    if current in codes:
        codes = [current] + [c for c in codes if c != current]
    region_picker.select(codes[0], all_dongs=True)
    st.session_state["notice_region_codes"] = codes  # 목록에 '공고 지역' 표시용
    names = [_name(c) for c in codes]
    head = f"공고 지역({label or names[0]})을 지역 선택에 적용했습니다: {names[0]} · 읍·면·동 전체."
    if len(codes) > 1:
        rest = [n.rsplit(" ", 1)[-1] for n in names[1:6]]
        head += f" 같은 공고 지역의 나머지 {len(codes) - 1}곳({', '.join(rest)}" + (" 등" if len(codes) > 6 else "") + ")은 구·군에서 바꿔 고르세요."
    return head


def take_pending_notice() -> None:
    """추출 직후 사이드바 '공고'에서 그 초안을 고른 상태로(위젯을 만들기 전에 불러야 한다)."""
    pick = st.session_state.pop("pending_notice", None)
    if pick:
        for code in pick[0]:
            st.session_state[f"notice-{code}-False"] = pick[1]


def render() -> Ctx | None:
    """사이드바. 지역(시군구)이 정해지지 않았으면 None."""
    sb = st.sidebar
    if sb.button("← 처음 화면", use_container_width=True):
        st.session_state["stage"] = "home"
        st.rerun()
    sb.header("지역·조건")
    take_pending_notice()
    reg = region_picker.render(sb, "sb", search=True)
    if not reg.ready:
        return None
    sgg, sido, sgg_name = reg.sigungu_cd, reg.sido, reg.sigungu_name
    bjds, dong_names, all_sel = reg.bjdong_cds, reg.bjdong_names, reg.all_selected

    eval_date = sb.date_input("판정 기준일", date.today(), format="YYYY-MM-DD")

    bad: list[str] = []
    all_notices = load_notices(skipped=bad)
    for b in bad:
        sb.warning(f"공고 파일을 읽지 못해 건너뜁니다 · {b}")
    local = notices_for(sgg, all_notices)
    test_mode = sb.checkbox("다른 지역 공고 적용(시험용)", help="결과 전체에 '시험용'이 표시됩니다.")
    pool = all_notices if test_mode else local
    # 방금 추출한 초안(미검수)도 고를 수 있게: 저장 전에 바로 판정해 볼 수 있다
    draft = (st.session_state.get("draft") or {}).get("notice")
    is_draft = bool(draft and not test_mode and sgg in draft.sigungu_codes and draft.notice_id not in {n.notice_id for n in pool})
    if is_draft:
        pool = [draft] + pool
    labels = {NO_NOTICE: "공고 없이 조례 기준"} | {n.notice_id: notice_label(n, eval_date) for n in pool}
    if is_draft:
        labels[draft.notice_id] = "추출 초안 · " + labels[draft.notice_id]
    order = [n.notice_id for n in pool] + [NO_NOTICE]  # 접수 시작일이 최신인 공고가 기본
    chosen = wheel_picker.wheel_select(sb, "공고", order, format_func=labels.get, key=f"notice-{sgg}-{test_mode}", font_size=".9rem")
    notice = next((n for n in pool if n.notice_id == chosen), None)
    is_test = bool(test_mode and notice and sgg not in notice.sigungu_codes)

    edited = False
    if notice:
        _editor(notice)
        if notice.notice_id in st.session_state["notice_edits"]:
            notice = st.session_state["notice_edits"][notice.notice_id]
            edited = True

    with sb.expander("판정 옵션"):
        include_annex = st.checkbox("부속건축물도 판정", False)
        excl_coll = st.checkbox("집합건물 제외(공고·조례 공통)", False)

    sb.divider()
    if sb.button("조회 결과 모두 지우기", help="이 창에서 조회한 건축물대장·판정 결과·고친 조건을 모두 지웁니다."):
        clear()
        st.rerun()

    return Ctx(
        sido=sido,
        sigungu_cd=sgg,
        sigungu_name=sgg_name,
        bjdong_cds=bjds,
        bjdong_names=dong_names,
        all_selected=all_sel,
        eval_date=eval_date,
        notice=notice,
        notice_edited=edited,
        test_mode=is_test,
        ordinance=ordinances.get_ordinance(sgg),
        options=EvalOptions(include_annex=include_annex, exclude_collective=excl_coll or None),
    )
