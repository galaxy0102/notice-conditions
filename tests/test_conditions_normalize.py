import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

import migrate_notices
from core import normalize
from core.conditions import (
    BusinessType,
    NoticeConditions,
    NotJudgeableNoticeError,
    parse_area_text,
    parse_floor_text,
    parse_period,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- conditions


def test_is_expired_uses_code_not_llm():
    n = NoticeConditions(notice_id="x", apply_start=date(2026, 7, 1), apply_end=date(2026, 7, 31))
    assert not n.is_expired(date(2026, 7, 31))  # 마감일 당일은 접수 가능
    assert n.is_expired(date(2026, 8, 1))
    assert not NoticeConditions(notice_id="y").is_expired(date(2030, 1, 1))  # 종료일 미상


def test_require_judgeable():
    NoticeConditions(notice_id="a", business_type=BusinessType.APPLY_INSPECTION).require_judgeable()
    with pytest.raises(NotJudgeableNoticeError):
        NoticeConditions(notice_id="b", business_type=BusinessType.EX_OFFICIO_INSPECTION).require_judgeable()
    with pytest.raises(NotJudgeableNoticeError):
        NoticeConditions(notice_id="c").require_judgeable()  # 사업유형 미정


def test_schema_validation():
    with pytest.raises(ValidationError):
        NoticeConditions(notice_id="x", years_basis="BUILT_BEFORE_YEAR")  # 기준 연도 없음
    with pytest.raises(ValidationError):
        NoticeConditions(notice_id="x", sigungu_codes=["1111"])
    with pytest.raises(ValidationError):
        NoticeConditions(notice_id="x", apply_start=date(2026, 8, 1), apply_end=date(2026, 7, 1))


def test_korean_alias_fields_accepted():
    n = NoticeConditions.model_validate({"notice_id": "x", "지자체": "서울특별시 종로구", "경과연수": 40, "용도조건": ["단독주택"]})
    assert (n.local_gov, n.min_years, n.use_filter) == ("서울특별시 종로구", 40, ["단독주택"])


@pytest.mark.parametrize(
    "text, expected",
    [("3층 이하", (3, "LE")), ("2층 미만", (2, "LT")), ("3층", None), (None, None)],
)
def test_parse_floor_text(text, expected):
    assert parse_floor_text(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("1000㎡ 이하", (1000.0, "LE")),
        ("연면적 200㎡ 미만", (200.0, "LT")),
        ("연면적 5백제곱미터 미만", (500.0, "LT")),
        ("1,000m² 이하", (1000.0, "LE")),
        ("1000㎡", None),  # 이하/미만 없으면 추측하지 않음
    ],
)
def test_parse_area_text(text, expected):
    assert parse_area_text(text) == expected


def test_parse_period():
    assert parse_period("2026. 7. 1. ~ 2026. 7. 31.") == (date(2026, 7, 1), date(2026, 7, 31))
    assert parse_period("2026.3.2.(월) ~ 3.20.(금)") == (date(2026, 3, 2), date(2026, 3, 20))
    assert parse_period("예산 소진 시까지") == (None, None)


def test_migrate_sample_extract():
    data = json.loads((ROOT / "tests/fixtures/sample-extract.json").read_text(encoding="utf-8"))
    n, issues = migrate_notices.convert(data, "sample")
    assert n.business_type == BusinessType.APPLY_INSPECTION
    assert n.sigungu_codes == ["30140"]  # 대전광역시 중구
    assert (n.min_years, n.max_floors, n.floors_bound, n.max_area, n.area_bound) == (30, 3, "LE", 1000.0, "LE")
    assert n.use_filter == ["주택", "제1종근생", "제2종근생"]
    assert n.structure_filter == ["목조", "조적조"]
    assert (n.apply_start, n.apply_end) == (date(2026, 7, 1), date(2026, 7, 31))
    assert n.offmap_conditions == ["현장 확인", "위원회 심사"]
    assert n.reviewed is False
    assert any("접수처" in i for i in issues)


# ---------------------------------------------------------------- normalize


def test_structure_map_only_certain_values():
    assert normalize.structure_std("벽돌구조", "") == "조적조"
    assert normalize.structure_std("기타구조", "시멘트벽돌조") == "조적조"  # 기타용 구조명으로 보충
    assert normalize.structure_std("일반철골구조", "") == "철골조"
    assert normalize.structure_std("철골철근콘크리트구조", "") is None  # 확실하지 않은 값은 매핑하지 않음


def test_use_tokens():
    assert normalize.use_tokens("제1·2종 근린생활시설") == (["제1종근생", "제2종근생"], [])
    assert normalize.use_tokens("단독주택(다가구주택 제외)") == (["단독주택", "!다가구주택"], [])
    assert normalize.use_tokens("주택") == (["주택"], [])
    assert normalize.use_tokens("알수없는용도") == ([], ["알수없는용도"])


@pytest.mark.parametrize(
    "main, etc, tokens, expected",
    [
        ("단독주택", "", ["주택"], ("PASS", None)),
        ("공동주택", "다세대주택", ["주택"], ("UNKNOWN", "HOUSING_SCOPE_UNCLEAR")),
        ("단독주택", "다가구주택", ["단독주택", "!다가구주택"], ("FAIL", None)),
        ("제2종근린생활시설", "주택및근린생활시설", ["주택"], ("UNKNOWN", "USE_MIXED")),
        ("제2종근린생활시설", "주택및근린생활시설", ["제2종근생"], ("PASS", None)),
        ("공장", "", ["주택"], ("FAIL", None)),
        ("", "", ["주택"], ("UNKNOWN", "USE_UNMAPPED")),
    ],
)
def test_match_use(main, etc, tokens, expected):
    assert normalize.match_use(tokens, normalize.use_info(main, etc)) == expected


def test_normalize_df_reports_unmapped():
    df = pd.DataFrame(
        [
            {"strctCdNm": "벽돌구조", "etcStrct": "", "mainPurpsCdNm": "단독주택", "etcPurps": ""},
            {"strctCdNm": "철골철근콘크리트구조", "etcStrct": "", "mainPurpsCdNm": "이상한용도", "etcPurps": ""},
            {"strctCdNm": "철골철근콘크리트구조", "etcStrct": "", "mainPurpsCdNm": "", "etcPurps": ""},
        ]
    )
    r = normalize.normalize_df(df)
    assert list(r.df["structure_std"]) == ["조적조", None, None]
    assert r.unmapped_structures == {"철골철근콘크리트구조": 2}
    assert r.unmapped_uses == {"이상한용도": 1, "(빈 값)": 1}


@pytest.mark.parametrize("text", ["제1종·제2종 근린생활시설", "제1종, 제2종 근린생활시설", "제1·2종 근린생활시설", "제1종 및 제2종 근린생활시설"])
def test_use_tokens_both_neighborhood_classes(text):
    # 예전에는 "제1종·제2종"에서 제2종이 빠져 제2종 근생 건물이 용도 조건에서 떨어졌다(2026-11-17 실추출에서 발견)
    from core import normalize

    assert normalize.use_tokens(text)[0][:2] == ["제1종근생", "제2종근생"]


@pytest.mark.parametrize("text, kind, n", [
    ("수원시", "parent_city", 4), ("경기도 수원시청", "parent_city", 4), ("대전광역시 중구청", "exact", 1),
    ("광주 서구", "exact", 1), ("서울특별시", "sido_only", 25), ("중구", "ambiguous", 0), ("청주시", "parent_city", 4),
])
def test_resolve_local_gov(text, kind, n):
    from core import regions

    r = regions.resolve_local_gov(text)
    assert r["kind"] == kind and len(r["codes"]) == n
    if kind == "ambiguous":
        assert len(r["candidates"]) > 1  # 고르지 않고 후보만 준다


def test_period_status():
    from datetime import date

    from core.conditions import NoticeConditions

    base = dict(notice_id="t", sigungu_codes=["11110"])
    today = date(2026, 11, 17)
    n = NoticeConditions(**base, apply_start=date(2026, 9, 1), apply_end=date(2026, 9, 30))
    assert n.period_status(today)[0] == "CLOSED"
    assert NoticeConditions(**base, apply_start=date(2026, 12, 1), apply_end=date(2026, 12, 31)).period_status(today)[0] == "NOT_STARTED"
    assert NoticeConditions(**base, apply_start=date(2026, 11, 1), apply_end=date(2026, 11, 30)).period_status(today)[0] == "OPEN"
    assert NoticeConditions(**base).period_status(today)[0] == "UNKNOWN"
    assert NoticeConditions(**base, apply_start=date(2026, 11, 1)).period_status(today)[0] == "UNKNOWN"
    # 날짜 칸이 비어도 원문 기간에서 읽는다
    assert NoticeConditions(**base, application_period="2026. 9. 1. ~ 9. 30.").period_status(today)[0] == "CLOSED"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("~ 2026. 7. 31.", (None, date(2026, 7, 31))),
        ("2026. 7. 31.(금)까지", (None, date(2026, 7, 31))),
        ("2026. 12. 15. ~ 1. 9.", (date(2026, 12, 15), date(2027, 1, 9))),
        ("2026. 7. 1.부터", (date(2026, 7, 1), None)),
    ],
)
def test_parse_period_end_only_and_year_crossing(text, expected):
    assert parse_period(text) == expected


@pytest.mark.parametrize("text", ["공동주택 제외", "근린생활시설을 제외한 건축물", "주거용 이외의 건축물"])
def test_use_exclusion_is_not_read_as_inclusion(text):
    assert normalize.use_tokens(text) == ([], [text])


def test_structure_exclusion_is_not_read_as_inclusion():
    assert normalize.structure_tokens("조적조 제외") == ([], ["조적조 제외"])
