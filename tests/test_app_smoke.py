"""Streamlit 화면 자동 점검(AppTest). 외부 API(건축물대장·브이월드·LLM)는 모두 가짜로 바꾼다.

화면을 사람이 눌러 보기 전에 '예외 없이 그려지는지', '조회 → 판정 → 안내문·지도'가 이어지는지를 잡는다.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core import boundaries, building_api

APP = str(Path(__file__).resolve().parents[1] / "app" / "main.py")


def fake_items(bjd: str) -> list[dict]:
    base = {
        "platPlc": "가상시 가상구 가상동 {n}번지", "newPlatPlc": "", "bldNm": "", "dongNm": "",
        "mainAtchGbCdNm": "주건축물", "regstrGbCdNm": "일반", "regstrKindCdNm": "일반건축물",
        "strctCdNm": "벽돌구조", "etcStrct": "", "mainPurpsCdNm": "단독주택", "etcPurps": "주택",
        "grndFlrCnt": 2, "ugrndFlrCnt": 0, "hhldCnt": 1, "fmlyCnt": 0,
        "sigunguCd": "11110", "bjdongCd": bjd, "platGbCd": "0", "ji": "0",
    }
    rows = []
    for n, (apr, area) in enumerate([("19800101", 120.0), ("", 90.0), ("20150101", 100.0), ("19750505", 900.0)], 1):
        rows.append(dict(base, platPlc=base["platPlc"].format(n=n), useAprDay=apr, totArea=area, bun=str(n),
                         mgmBldrgstPk=f"{bjd}{n:04d}"))
    return rows


@pytest.fixture
def fakes(monkeypatch):
    calls = []

    def fetch_dong(sgg, bjd, **kw):
        calls.append(bjd)
        df, missing = building_api.to_dataframe(fake_items(bjd))
        return building_api.DongResult(df, len(df), 1, 0.01, missing, [])

    square = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"sig_cd": "11110", "emd_cd": "11110101", "full_nm": "가상동"},
        "geometry": {"type": "MultiPolygon", "coordinates": [[[[126.96, 37.58], [126.98, 37.58], [126.98, 37.60], [126.96, 37.58]]]]},
    }]}
    monkeypatch.setattr(building_api, "fetch_dong", fetch_dong)
    monkeypatch.setattr(building_api, "api_key", lambda: "TEST")
    monkeypatch.setattr(boundaries, "sigungu_boundary", lambda *a, **k: square)
    monkeypatch.setattr(boundaries, "emd_boundaries", lambda *a, **k: square)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    from core import notice_hunter
    monkeypatch.setattr(notice_hunter, "search_candidates", lambda *a, **k: ([], 0))  # 네이버 검색 호출 없음
    return calls


@pytest.fixture
def notice_dir(tmp_path, monkeypatch):
    """'검수 완료로 저장'이 data/notices 대신 임시 폴더에 쓰고 읽게."""
    from app import sidebar, tab_upload
    from core import notices

    monkeypatch.setattr(tab_upload, "save_notice", lambda n, overwrite=False: notices.save_notice(n, tmp_path, overwrite))
    monkeypatch.setattr(sidebar, "load_notices", lambda **k: notices.load_notices(tmp_path, **k))
    return tmp_path


class FakeWheels:
    """처음 화면 휠 대신: 마지막으로 그린 휠(옵션·비활성)을 기록하고, pick()으로 고른 값을 다음 실행에 돌려준다."""

    def __init__(self):
        self.shown: dict[str, dict] = {}
        self.picks: dict[str, object] = {}

    def __call__(self, label, options, value, *, key, format_func=str, disabled=False, placeholder="", **_):
        from app import wheel_picker

        self.shown[key] = {"label": label, "options": list(options), "value": value, "disabled": disabled,
                           "reveal": _.get("reveal", False)}
        return self.picks.pop(key, wheel_picker.NOTHING)

    def pick(self, at, k: str, value):
        self.picks[f"home-{k}-wheel"] = value
        at.run()

    def hit(self, at, q: str, i: int = 0):
        """검색어 q의 결과 휠에서 i번째를 고른다."""
        self.picks[f"home-hit-wheel-{q}"] = i
        at.run()

    def opts(self, k: str) -> list:
        return self.shown[f"home-{k}-wheel"]["options"]

    def off(self, k: str) -> bool:
        return self.shown[f"home-{k}-wheel"]["disabled"]

    def reveal(self, k: str) -> bool:
        return self.shown[f"home-{k}-wheel"]["reveal"]

    def val(self, k: str):
        return self.shown[f"home-{k}-wheel"]["value"]


@pytest.fixture
def wheels(monkeypatch):
    from app import wheel_picker

    fake = FakeWheels()
    monkeypatch.setattr(wheel_picker, "wheel", fake)
    return fake


def open_work(sgg: str = "11110", dongs: list[str] | None = None, all_dongs: bool = False) -> AppTest:
    """처음 화면을 건너뛰고 작업 화면을 연다(지역: 종로구 청운동)."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.session_state["region"] = {"sgg": sgg, "all": all_dongs, "dongs": dongs if dongs is not None else ["10100"], "v": 1}
    at.session_state["stage"] = "work"
    at.run()
    assert not at.exception
    return at


def run_query(at: AppTest) -> AppTest:
    next(b for b in at.button if b.label == "조회·판정").click().run()
    assert not at.exception
    return at


def metric(at: AppTest, label: str) -> str:
    return next(m.value for m in at.metric if m.label == label)


def open_home(mode: str | None = "region") -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    if mode:
        at.button(key=f"home-pick-{mode}").click().run()
    assert not at.exception
    return at


def test_home_choose_one_of_region_or_upload(fakes, wheels):
    at = open_home(None)
    labels = " ".join(b.label for b in at.button)
    assert "지역 선택" in labels and "공고문 직접 올리기" in labels and any("또는" in m.value for m in at.markdown)
    assert not wheels.shown and not at.get("file_uploader")  # 고르기 전에는 둘 다 접혀 있음
    at.button(key="home-pick-region").click().run()
    assert list(wheels.shown) == ["home-sido-wheel"]  # 도를 고르기 전에는 시·군·구·군 휠이 없다
    assert wheels.shown["home-sido-wheel"]["label"] == "도·특별시·광역시"
    wheels.pick(at, "sido", "경기도")
    assert "home-city-wheel" in wheels.shown and "home-gu-wheel" not in wheels.shown  # 도 → 시·군이 나온다
    assert wheels.reveal("city") and not wheels.reveal("sido")  # 다음 휠은 미끄러져 나온다
    assert not at.get("file_uploader")
    wheels.shown.clear()
    at.button(key="home-pick-upload").click().run()  # 다른 쪽을 고르면 바뀐다
    assert at.get("file_uploader") and not wheels.shown
    at.button(key="home-pick-upload").click().run()  # 같은 쪽을 다시 누르면 접힘
    assert not at.get("file_uploader")
    assert not any(b.label == "조회·판정" for b in at.button) and fakes == [] and not at.exception


def test_home_cascade_doe_si_gu_dong_then_start(fakes, wheels):
    at = open_home()
    wheels.pick(at, "sido", "경기도")
    assert "수원시" in wheels.opts("city") and not wheels.off("city")
    wheels.pick(at, "city", "수원시")
    assert wheels.opts("gu") == ["장안구", "권선구", "팔달구", "영통구"]
    wheels.pick(at, "gu", "장안구")
    assert not at.exception and at.session_state["region"]["sgg"] == "41111"
    assert not any("점검 대상" in m.value for m in at.markdown)  # 고르기만 해서는 검색하지 않음
    at.button(key="home-search").click().run()
    assert any("점검 대상" in m.value for m in at.markdown)  # 조례 카드
    start = at.button(key="home-start")
    assert start.disabled  # 동을 아직 안 고름
    from core import regions

    two = [c for c, _ in regions.list_bjdong("41111")[:2]]
    at.multiselect(key="home-dongs-41111").set_value(two).run()
    assert at.session_state["region"]["dongs"] == two
    at.button(key="home-start").click().run()
    assert not at.exception and at.session_state["stage"] == "work"
    assert at.session_state["sb-gu"] == "장안구" and len(at.multiselect(key="sb-dongs-41111").value) == 2


def test_home_wheel_change_resets_lower_levels(fakes, wheels):
    """도를 바꾸면 시·군·구·군은 비고(자리 표시), 고른 지역도 풀린다."""
    at = open_home()
    wheels.pick(at, "sido", "경기도")
    wheels.pick(at, "city", "수원시")
    wheels.pick(at, "gu", "장안구")
    wheels.shown.clear()
    wheels.pick(at, "sido", "강원특별자치도")
    assert wheels.val("city") is None and "home-gu-wheel" not in wheels.shown
    assert at.session_state["region"]["sgg"] is None and not at.exception


def test_home_search_picks_dongs_one_after_another(fakes, wheels):
    at = open_home()
    at.text_input(key="home-q").input("청운동").run()
    wheels.hit(at, "청운동")
    assert at.session_state["region"]["sgg"] == "11110" and at.session_state["region"]["dongs"] == ["10100"]
    assert wheels.val("sido") == "서울특별시" and wheels.val("gu") == "종로구"  # 휠이 검색 결과로 굴러간다
    at.text_input(key="home-q").input("종로 신교동").run()
    wheels.hit(at, "종로 신교동")
    assert at.session_state["region"]["dongs"] == ["10100", "10200"]  # 같은 구 동은 쌓인다
    assert not at.exception


def test_metro_has_no_city_level(fakes, wheels):
    at = open_home()
    wheels.pick(at, "sido", "대구광역시")
    assert "home-city-wheel" not in wheels.shown  # 광역시는 시·군 단계 없이
    assert "달성군" in wheels.opts("gu")          # 바로 구·군 휠


def test_query_judge_and_rerun_without_refetch(fakes):
    at = run_query(open_work())
    assert fakes == ["10100"]
    assert metric(at, "총 건물 수") == "4"
    # 조례(종로구, 규모 수치 없음) 기준: 30년 이상 2건 후보, 사용승인일 공란 1건 확인필요, 2015년 1건 비해당
    assert (metric(at, "1차 후보"), metric(at, "확인필요"), metric(at, "비해당")) == ("2", "1", "1")
    at.run()  # 다시 그려도 재조회하지 않음
    assert fakes == ["10100"] and not at.exception
    assert any(d.label == "Excel 내려받기" for d in at.download_button)


def test_letters_and_map_sections_use_session_result(fakes):
    at = run_query(open_work())
    at.button(key="step-3").click().run()  # ③ 안내문은 위 단계 표시줄에서
    assert not at.exception and not at.segmented_control
    assert any("1건 미리보기" in m.value for m in at.markdown)
    assert any("안내문 docx" in d.label for d in at.download_button)
    at.button(key="step-2").click().run()
    assert at.segmented_control[0].value == "region"
    at.segmented_control[0].set_value("map").run()
    assert not at.exception
    assert any("지도 좌표 가져오기" in b.label for b in at.button)  # 좌표는 버튼을 눌러야 가져온다
    assert fakes == ["10100"]


def test_work_sections_have_no_notice_tab(fakes):
    at = open_work()
    assert at.segmented_control[0].options == ["지역 일괄 대조", "지도"]
    assert at.segmented_control[0].value == "region"
    assert at.button(key="step-2-cur").proto.type == "primary" and not at.button(key="step-3").disabled


def test_missing_building_key_shows_error(fakes, monkeypatch):
    def no_key():
        raise building_api.BuildingApiAuthError("NO_KEY", ".env에 BUILDING_API_KEY가 없습니다.")

    monkeypatch.setattr(building_api, "api_key", no_key)
    at = open_work()
    assert any("BUILDING_API_KEY" in e.value for e in at.error)


def test_notice_region_applied_to_region_picker(fakes, wheels):
    """공고 지역(수원시 = 구 4개) 적용: 도·시·구와 읍·면·동 전체가 바뀌고 같은 공고 지역에 표시가 붙는다."""
    at = open_home()
    at.session_state["region"] = {"sgg": "41111", "all": True, "dongs": [], "v": 7}
    at.session_state["notice_region_codes"] = ["41111", "41113", "41115", "41117"]
    at.run()
    assert not at.exception
    assert wheels.val("sido") == "경기도" and wheels.val("city") == "수원시"
    assert wheels.val("gu") == "장안구" and at.checkbox(key="home-all-41111").value is True


def test_home_review_editor_shows_period_warning_and_sources(fakes):
    """처음 화면에서 바로 조건 검수·수정: 접수기간 경고, 합친 글 주소, 저장 버튼."""
    from datetime import date

    from core.conditions import NoticeConditions

    n = NoticeConditions(notice_id="daejeon-test", local_gov="대전광역시 중구", sigungu_codes=["30140"],
                         apply_start=date(2026, 9, 1), apply_end=date(2026, 9, 30), min_years=30)
    at = open_home("upload")
    at.session_state["draft"] = {"origin": "upload", "notice": n, "issues": [], "classify": {"repair": False},
                                 "provider": "mock:mock", "usage": "", "region": "공고 지역(대전광역시 중구)을 지역 선택에 적용했습니다",
                                 "sources": [(1, "2026년 안전점검 신청 안내", "https://www.djjunggu.go.kr/1", "공고")]}
    at.session_state["region"] = {"sgg": "30140", "all": True, "dongs": [], "v": 4}
    at.run()
    assert not at.exception
    assert any("② 조건 검수" in m.value for m in at.markdown)
    warnings = " ".join(w.value for w in at.warning)
    if date.today() > date(2026, 9, 30):
        assert "사업기간 아님" in warnings
    assert any("[출처 1]" in m.value and "djjunggu" in m.value for m in at.markdown)
    assert any(b.label == "검수 완료로 저장" for b in at.button)


def _merged_result(url: str, sgg: str = "11110"):
    from core import notice_hunter as nh
    from core import notice_merge
    from core.llm import extractor
    from core.llm.base import Part
    from core.llm.mock import MockProvider

    c, r = extractor.analyze([Part.of_text("공고")], MockProvider(), log=False)
    r.notice = r.notice.model_copy(update={"source_url": url, "sigungu_codes": [sgg], "notice_id": "auto-notice"})
    cand = nh.Candidate("2026년 소규모 노후 건축물 안전점검 신청 안내", url, nh.urlparse(url).netloc, "", "web", score=9, year=2026)
    src = nh.SourceResult(cand, classify=c, extract=r, status="used")
    merged = notice_merge.merge([notice_merge.MergeSource(r.notice, r.issues, url, cand.title, 2026)])
    return nh.HuntResult("search", candidates=[cand], auto=nh.AutoExtract(sources=[src], merged=merged))


def test_home_auto_hunt_extracts_and_applies(fakes, notice_dir, wheels, monkeypatch):
    """구를 고르면 공고를 찾아 바로 추출: 위에 인터넷 주소, 읍·면·동 전체 적용, 작업 화면 사이드바 공고에 초안."""
    from core import notice_hunter as nh

    url = "https://www.jongno.go.kr/notice/1"
    res = _merged_result(url)
    seen = []
    monkeypatch.setattr(nh, "hunt", lambda *a, **k: seen.append(a[0]) or res)
    at = open_home()
    at.text_input(key="home-q").input("종로구").run()
    wheels.hit(at, "종로구")
    assert not at.exception and seen == []  # 지역만 골라서는 검색하지 않음
    at.button(key="home-search").click().run()
    assert not at.exception
    assert seen == ["11110"]
    top = " ".join(x.value for x in at.success) + " ".join(x.value for x in at.warning)
    assert url in top
    assert at.checkbox(key="home-all-11110").value is True  # 공고 지역 → 동 전체
    assert any("추출한 공고 조건" in m.value for m in at.markdown)
    assert at.button(key="home-start").disabled is True  # ② 검수 전에는 ③ 후보 건물 찾기 잠김
    assert any("② 조건 검수" in m.value for m in at.markdown)
    next(b for b in at.button if b.label == "검수 완료로 저장").click().run()
    assert not at.exception
    assert (notice_dir / "auto-notice.json").exists()
    assert at.button(key="home-start").disabled is False
    at.button(key="home-start").click().run()
    assert not at.exception
    assert at.session_state["notice-11110-False"] == "auto-notice"
    assert seen == ["11110"]  # 작업 화면에서도 다시 찾지 않음
    assert any("**공고**" in m.value and url in m.value for m in at.markdown)  # 작업 화면에는 적용 공고 한 줄(원문 주소)만
    assert not any("합쳤습니다" in x.value for x in at.success)  # 처음 화면의 검색 요약은 되풀이하지 않는다


def test_uploaded_draft_is_not_overwritten_by_auto_search(fakes, monkeypatch):
    """공고문을 올려 추출한 뒤 지역 선택으로 가도 자동 검색은 검색만 한다(올린 공고 조건을 지키기)."""
    from core import notice_hunter as nh
    from core.conditions import NoticeConditions

    calls = []
    monkeypatch.setattr(nh, "hunt", lambda *a, **k: calls.append(k.get("auto_provider")) or nh.HuntResult("none", message="없음"))
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    n = NoticeConditions(notice_id="uploaded", sigungu_codes=["11110"], min_years=30)
    at = open_home("upload")
    at.session_state["draft"] = {"origin": "upload", "notice": n, "issues": [], "classify": {"repair": False},
                                 "provider": "mock:mock", "usage": "", "region": ""}
    at.session_state["region"] = {"sgg": "11110", "all": True, "dongs": [], "v": 3}
    at.run()
    assert any("추출한 공고 조건" in m.value for m in at.markdown)
    assert at.button(key="home-start").disabled is True  # 검수 전에는 잠김
    at.button(key="home-pick-region").click().run()
    assert not at.exception and calls == []  # 고르기만 해서는 검색하지 않음
    at.button(key="home-search").click().run()
    assert not at.exception and calls == [None]  # LLM 추출 없이 검색만
    assert at.session_state["draft"]["notice"].notice_id == "uploaded"


def test_upload_review_then_start(fakes, notice_dir):
    """공고 업로드 → 추출(초안) → ② 검수 저장 → ③ 후보 건물 찾기 순서. 새 초안이 오면 다시 잠긴다."""
    from core.conditions import NoticeConditions

    n = NoticeConditions(notice_id="uploaded-review", local_gov="서울특별시 종로구", sigungu_codes=["11110"], min_years=30)
    draft = {"origin": "upload", "notice": n, "issues": [], "classify": {"repair": False}, "provider": "mock:mock", "usage": "", "region": ""}
    at = open_home("upload")
    at.session_state["draft"] = draft
    at.session_state["region"] = {"sgg": "11110", "all": True, "dongs": [], "v": 3}
    at.run()
    start = at.button(key="home-start")
    assert start.disabled is True
    assert any("검수 완료로 저장" in c.value for c in at.caption)  # 왜 잠겼는지 안내
    next(b for b in at.button if b.label == "검수 완료로 저장").click().run()
    assert not at.exception
    assert "draft" not in at.session_state
    assert at.button(key="home-start").disabled is False  # 저장 뒤에도 버튼이 남고 열린다
    at.run()
    assert any("검수 완료" in s.value for s in at.success)
    at.session_state["draft"] = dict(draft, notice=n.model_copy(update={"notice_id": "second"}))
    at.session_state["reviewed"] = None
    at.run()
    assert at.button(key="home-start").disabled is True


def test_heading_shows_gu_and_dong(fakes):
    at = open_work(all_dongs=True)
    head = next(m.value for m in at.markdown if m.value.startswith("### "))
    assert "›" in head and "모든 동" in head and 'class="crumb-dong"' in head
    n = at.session_state["_motion"]["crumb"]["n"]
    at.checkbox(key="sb-all-11110").uncheck().run()
    head = next(m.value for m in at.markdown if m.value.startswith("### "))
    assert "청운동" in head and at.session_state["_motion"]["crumb"]["n"] == n + 1  # 전체를 풀면 고른 동으로


def test_home_step_bar_locks_later_steps():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert at.button(key="step-1-cur").proto.type == "primary"
    assert at.button(key="step-2").disabled and at.button(key="step-3").disabled  # 검수 전에는 못 간다
