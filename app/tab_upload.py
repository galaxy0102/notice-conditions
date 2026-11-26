"""탭1 위쪽: 공고 파일 업로드 → 분류 → 추출 → 조건 편집(원문 근거) → 검수 완료로 저장.

업로드 파일은 메모리에서만 처리한다. 저장하는 것은 검수한 공고 조건 JSON뿐이다(공개 문서 정보).
"""

from __future__ import annotations

from datetime import date

import streamlit as st

from core import regions
from core.conditions import BUSINESS_TYPE_LABEL, Contact, NoticeConditions
from core.llm import extractor, factory
from core.llm.base import LLMError, usage_summary
from core.llm.preprocess import ACCEPTED, UploadedFile, to_parts
from app import sidebar, wheel_picker
from core.notices import save_notice

BOUND = {"LE": "이하", "LT": "미만"}
TRI = {"불명": None, "예": True, "아니오": False}
FIELD_LABELS = {
    "local_gov": "지자체", "program_name": "사업명", "application_period": "접수기간", "notice_date": "공고일",
    "min_years": "경과연수", "built_before_year": "기준 연도", "max_floors": "층수", "max_area": "연면적",
    "use_filter": "용도", "structure_filter": "구조", "exclusions": "제외대상", "collective_excluded": "집합건물 제외",
    "multi_housing_excluded": "공동주택 제외", "selection_method": "선정방식", "priority_rules": "우선순위",
    "offmap_conditions": "대장 외 조건", "support_content": "지원내용", "contact.reception": "접수처",
    "contact.inquiry": "문의처", "contact.apply_method": "신청방법", "contact.documents": "제출서류",
}


@st.cache_data(show_spinner=False)
def _sigungu_options() -> dict[str, str]:
    # 공개 코드표라 캐시해도 된다(대장·좌표 아님)
    out = {}
    for sido in regions.list_sido():
        for code, name in regions.list_sigungu(sido):
            out[code] = f"{sido} {name}" if name != sido else sido
    return out


def upload_box(prefix: str = "work") -> None:
    """공고 파일 올리기 + 추출 버튼(처음 화면·① 화면 공통)."""
    stt = factory.status("extract")
    if stt.is_mock:
        st.caption(f"지금은 샘플 모드라 파일을 외부로 보내지 않고 샘플 결과만 씁니다({stt.reason}).")
    else:
        st.caption(f"'공고문에서 조건 추출'을 누르면 올린 파일이 {stt.label}(으)로 전송됩니다.")
    files = st.file_uploader(
        "공고 파일(여러 개 = 한 공고 묶음)", type=[e.lstrip(".") for e in ACCEPTED], accept_multiple_files=True, key=f"{prefix}-upload"
    )
    if st.button("공고문에서 조건 추출", type="primary", disabled=not files, key=f"{prefix}-upload-run"):
        _run(files)


def _run(files) -> None:
    pre = to_parts([UploadedFile(f.name, f.getvalue()) for f in files])
    for s in pre.skipped:
        st.warning(s)
    for w in pre.warnings:
        st.warning(w)
    if not pre.parts:
        st.error("읽을 수 있는 파일이 없습니다.")
        return
    provider = factory.get_provider("extract")
    try:
        with st.spinner("사업유형 분류·조건 추출 중"):
            c, r = extractor.analyze(pre.parts, provider)  # 한 번 호출로 분류 + 추출
        if r is None:
            st.session_state.pop("draft", None)
            st.warning(f"이 공고는 '{BUSINESS_TYPE_LABEL[c.business_type]}'로 분류되어 조건을 추출하지 않습니다.")
            st.markdown(f"**근거** {c.evidence or '(인용 없음)'}  \n**확신도** {c.confidence}")
            if c.usage.note:
                st.caption(c.usage.note)
            return
    except LLMError as e:
        st.error(str(e))
        return
    region_msg = sidebar.apply_region(r.notice.sigungu_codes, r.notice.local_gov or "")
    if r.notice.sigungu_codes:
        st.session_state["pending_notice"] = (list(r.notice.sigungu_codes), r.notice.notice_id)
    st.session_state.pop("reviewed", None)  # 새 초안은 다시 검수해야 한다
    st.session_state["draft"] = {
        "origin": "upload",  # 공고 검색이 이 초안을 덮어쓰지 않게
        "region": region_msg,
        "notice": r.notice,
        "issues": r.issues,
        "classify": {"evidence": c.evidence, "confidence": c.confidence, "repair": c.has_repair_section},
        "provider": f"{provider.name}:{c.usage.model}",
        "usage": usage_summary([c.usage]),
    }
    st.rerun()


def _lines(text: str) -> list[str]:
    return [x.strip() for x in text.splitlines() if x.strip()]


def editor(draft: dict) -> None:
    n: NoticeConditions = draft["notice"]
    st.markdown(
        f"**추출 결과** · {draft['provider']} · :orange-badge[미검수]"
        + (f" · {draft['usage']}" if draft.get("usage") else "")
    )
    state, msg = n.period_status(date.today())
    if state == "OPEN":
        st.success(msg)
    else:
        st.warning(("⚠️ 접수기간 확인 불가 · " if state == "UNKNOWN" else "⚠️ 사업기간 아님 · ") + msg)
    if draft.get("sources"):
        st.markdown("**합친 글(인터넷 주소)**  \n" + "  \n".join(f"[출처 {i}] {kind} · [{title}]({url})" for i, title, url, kind in draft["sources"]))
    elif n.source_url:
        st.markdown(f"**인터넷 주소** [{n.source_url}]({n.source_url})")
    if draft.get("region"):
        st.info(draft["region"])
    if draft["classify"].get("repair"):
        st.info("이 문서에는 보수·보강 지원도 함께 있습니다. 안전점검 부분의 조건만 추출했습니다.")
    for i in draft["issues"]:
        st.warning(i)

    left, right = st.columns([3, 2])
    opts = _sigungu_options()
    with left, st.form("draft-form"):
        nid = st.text_input("공고 ID(파일 이름)", n.notice_id)
        program = st.text_input("사업명", n.program_name or "")
        gov = st.text_input("지자체", n.local_gov or "")
        codes = st.multiselect("적용 시군구", list(opts), default=[c for c in n.sigungu_codes if c in opts], format_func=opts.get)
        period = st.text_input("접수기간(원문)", n.application_period or "")
        d1, d2 = st.columns(2)
        start = d1.date_input("접수 시작", n.apply_start, format="YYYY-MM-DD")
        end = d2.date_input("접수 종료", n.apply_end, format="YYYY-MM-DD")
        y1, y2 = st.columns(2)
        min_years = y1.number_input("경과연수(년 이상, 0=없음)", 0, 100, n.min_years or 0)
        f1, f2 = st.columns(2)
        max_floors = f1.number_input("층수(0=없음)", 0, 100, n.max_floors or 0)
        fb = wheel_picker.wheel_select(f2, "층수 경계", ["LE", "LT"], index=["LE", "LT"].index(n.floors_bound), format_func=BOUND.get, key=f"draft-fb-{n.notice_id}")
        a1, a2 = st.columns(2)
        max_area = a1.number_input("연면적 ㎡(0=없음)", 0.0, 100000.0, float(n.max_area or 0))
        ab = wheel_picker.wheel_select(a2, "연면적 경계", ["LE", "LT"], index=["LE", "LT"].index(n.area_bound), format_func=BOUND.get, key=f"draft-ab-{n.notice_id}")
        uses = st.text_input("용도(쉼표로 구분)", ", ".join(n.use_filter))
        structs = st.text_input("구조(쉼표로 구분)", ", ".join(n.structure_filter))
        c1, c2 = st.columns(2)
        coll = wheel_picker.wheel_select(c1, "집합건물 제외", list(TRI), index=list(TRI.values()).index(n.collective_excluded), key=f"draft-coll-{n.notice_id}")
        multi = wheel_picker.wheel_select(c2, "공동주택 제외", list(TRI), index=list(TRI.values()).index(n.multi_housing_excluded), key=f"draft-multi-{n.notice_id}")
        excl = st.text_area("제외대상(한 줄에 하나)", "\n".join(n.exclusions))
        offmap = st.text_area("대장 외 조건 · 현장 확인(한 줄에 하나)", "\n".join(n.offmap_conditions))
        r1, r2 = st.columns(2)
        reception = r1.text_input("접수처", n.contact.reception or "")
        inquiry = r2.text_input("문의처", n.contact.inquiry or "")
        method = st.text_input("신청방법", n.contact.apply_method or "")
        docs = st.text_input("제출서류(쉼표로 구분)", ", ".join(n.contact.documents))
        overwrite = st.checkbox("같은 공고 ID가 있으면 덮어쓰기")
        saved = st.form_submit_button("검수 완료로 저장", type="primary")
    with right:
        st.markdown("**원문 근거**")
        keys = [k for k in FIELD_LABELS if k in n.evidence]
        if keys:
            k = wheel_picker.wheel_select(st, "항목", keys, format_func=FIELD_LABELS.get, key="ev-key", rows=5)
            st.info(n.evidence[k])
        else:
            st.caption("원문 인용이 없습니다.")
        st.caption("값을 원문과 대조한 뒤 저장하세요. 저장하면 작업 화면 사이드바 '공고' 목록에 바로 나타납니다.")

    if saved:
        split = lambda s: [x.strip() for x in s.split(",") if x.strip()]  # noqa: E731
        try:
            final = n.model_copy(
                update={
                    "notice_id": nid.strip(),
                    "program_name": program or None,
                    "local_gov": gov or None,
                    "sigungu_codes": codes,
                    "application_period": period or None,
                    "apply_start": start,
                    "apply_end": end,
                    "min_years": int(min_years) or None,
                    "max_floors": int(max_floors) or None,
                    "floors_bound": fb,
                    "max_area": float(max_area) or None,
                    "area_bound": ab,
                    "use_filter": split(uses),
                    "structure_filter": split(structs),
                    "collective_excluded": TRI[coll],
                    "multi_housing_excluded": TRI[multi],
                    "exclusions": _lines(excl),
                    "offmap_conditions": _lines(offmap),
                    "contact": Contact(reception=reception or None, inquiry=inquiry or None, apply_method=method or None, documents=split(docs)),
                    "reviewed": True,
                }
            )
            final = NoticeConditions.model_validate(final.model_dump())
            if not final.sigungu_codes:
                st.error("적용 시군구를 하나 이상 고르세요. 그래야 작업 화면 사이드바 '공고' 목록에 나타납니다.")
                return
            path = save_notice(final, overwrite=overwrite)
        except FileExistsError as e:
            st.error(str(e) + " 바꿔 저장하려면 '같은 공고 ID가 있으면 덮어쓰기'를 켜고 다시 저장하세요.")
            return
        except ValueError as e:
            st.error(f"저장할 수 없습니다: {e}")
            return
        origin = draft.get("origin")
        st.session_state.pop("draft", None)
        st.session_state["reviewed"] = {"notice": final, "origin": origin}  # 처음 화면: 검수 끝 → '후보 건물 찾기' 열림
        msg = sidebar.apply_region(final.sigungu_codes, final.local_gov or "")
        st.session_state["pending_notice"] = (list(final.sigungu_codes), final.notice_id)  # 작업 화면에서 이 공고로 판정
        st.success(f"검수 완료로 저장했습니다({final.notice_id} · {date.today().isoformat()}). {msg}")
        st.download_button(
            "저장한 공고 조건 내려받기(JSON)", final.model_dump_json(indent=2).encode("utf-8"),
            file_name=path.name, mime="application/json",
        )
        st.caption("서버(Streamlit Cloud)에 저장한 파일은 앱이 다시 시작되면 사라집니다. "
                   "내려받은 파일을 저장소의 data/notices/에 넣어 두세요.")
