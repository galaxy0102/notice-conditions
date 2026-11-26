"""공고 조건표 스키마 (NoticeConditions).

기존 grok 웹앱의 추출 항목(frontend_grok/lib/schema.ts KEYMAP · 489d49a 이후 삭제)을 기준으로 하고, 판정에 필요한 필드를 더했다.
- 기존 항목 이름은 필드 alias(한국어)로 남겨 legacy JSON도 읽을 수 있게 했다.
- LLM은 날짜를 원문에서 뽑기만 한다. 마감 여부는 is_expired(today)로 코드가 계산한다.
- '이하(LE)'와 '미만(LT)'을 엄격히 구분한다.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BusinessType(str, Enum):
    APPLY_INSPECTION = "APPLY_INSPECTION"  # 신청형 안전점검 지원 → 판정 대상
    EX_OFFICIO_INSPECTION = "EX_OFFICIO_INSPECTION"  # 지자체 직권 점검
    REPAIR_SUPPORT = "REPAIR_SUPPORT"  # 보수·보강 지원
    MULTI_HOUSING_SUBSIDY = "MULTI_HOUSING_SUBSIDY"  # 공동주택 지원
    HOUSING_REDEVELOPMENT = "HOUSING_REDEVELOPMENT"  # 주거 정비
    UNRELATED = "UNRELATED"


BUSINESS_TYPE_LABEL = {
    BusinessType.APPLY_INSPECTION: "신청형 안전점검 지원",
    BusinessType.EX_OFFICIO_INSPECTION: "직권 안전점검",
    BusinessType.REPAIR_SUPPORT: "보수·보강 지원",
    BusinessType.MULTI_HOUSING_SUBSIDY: "공동주택 지원",
    BusinessType.HOUSING_REDEVELOPMENT: "주거 정비",
    BusinessType.UNRELATED: "관련 없음",
}

UpperBound = Literal["LE", "LT"]  # 이하, 미만
LowerBound = Literal["GE", "GT"]  # 이상, 초과
YearsBasis = Literal["ELAPSED_YEARS", "BUILT_BEFORE_YEAR"]
AgeReference = Literal["EVAL_DATE", "APPLY_START", "NOTICE_DATE"]
FloorsBasis = Literal["GROUND_ONLY", "GROUND_AND_UNDERGROUND"]
SourceType = Literal["OFFICIAL_NOTICE", "LEAFLET", "PRESS_RELEASE", "NEWS"]


class NotJudgeableNoticeError(ValueError):
    """신청형 안전점검(APPLY_INSPECTION)이 아닌 공고로 판정하려 했다."""


class Contact(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reception: str | None = Field(None, alias="접수처")
    inquiry: str | None = Field(None, alias="문의처")
    apply_method: str | None = Field(None, alias="신청방법")
    documents: list[str] = Field(default_factory=list, alias="제출서류")


class NoticeConditions(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    notice_id: str
    local_gov: str | None = Field(None, alias="지자체")
    program_name: str | None = Field(None, alias="사업명")
    year: str | None = Field(None, alias="연도")
    season: str | None = Field(None, alias="시기")
    business_type: BusinessType | None = None
    sigungu_codes: list[str] = Field(default_factory=list)

    # 날짜: 원문 그대로 추출, 마감 판정은 코드
    notice_date: date | None = None
    apply_start: date | None = None
    apply_end: date | None = None
    application_period: str | None = Field(None, alias="접수기간")  # 원문

    # 경과연수
    years_basis: YearsBasis = "ELAPSED_YEARS"
    min_years: int | None = Field(None, alias="경과연수")
    built_before_year: int | None = None
    built_before_inclusive: bool | None = None  # "1994년 이전"처럼 해석이 갈리면 None
    age_reference: AgeReference = "EVAL_DATE"

    # 규모
    max_floors: int | None = None
    floors_bound: UpperBound = "LE"
    min_floors: int | None = None
    min_floors_bound: LowerBound = "GE"
    floors_basis: FloorsBasis = "GROUND_ONLY"
    max_area: float | None = None
    area_bound: UpperBound = "LE"

    # 용도·구조 (core.normalize의 표준 토큰)
    use_filter: list[str] = Field(default_factory=list, alias="용도조건")
    structure_filter: list[str] = Field(default_factory=list, alias="구조조건")
    collective_excluded: bool | None = None
    multi_housing_excluded: bool | None = None
    exclusions: list[str] = Field(default_factory=list, alias="제외대상")  # 원문

    # 판정에 쓰지 않는 정보
    selection_method: str | None = Field(None, alias="선정방식")
    priority_rules: list[str] = Field(default_factory=list, alias="우선순위기준")
    offmap_conditions: list[str] = Field(default_factory=list, alias="확인필요")  # 대장 외 조건 → 현장확인
    support_content: list[str] = Field(default_factory=list, alias="지원내용")
    scale: str | None = Field(None, alias="규모")
    contact: Contact = Field(default_factory=Contact)

    source_type: SourceType = "OFFICIAL_NOTICE"
    source_url: str | None = Field(None, alias="원문주소")  # 인터넷에서 찾은 공고면 그 주소
    source_urls: list[str] = Field(default_factory=list, alias="원문주소목록")  # 여러 글을 합쳤으면 모든 주소(첫째 = source_url)
    reviewed: bool = False
    evidence: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "NoticeConditions":
        if self.years_basis == "BUILT_BEFORE_YEAR" and self.built_before_year is None:
            raise ValueError("years_basis=BUILT_BEFORE_YEAR 이면 built_before_year 가 필요합니다.")
        if self.apply_start and self.apply_end and self.apply_start > self.apply_end:
            raise ValueError("접수 시작일이 종료일보다 늦습니다.")
        for c in self.sigungu_codes:
            if not (len(c) == 5 and c.isdigit()):
                raise ValueError(f"sigungu_codes는 5자리 숫자여야 합니다: {c}")
        return self

    # --- 코드가 계산하는 것 -------------------------------------------------
    def is_expired(self, today: date) -> bool:
        """접수 마감 여부. 종료일이 없으면 마감으로 보지 않는다(화면에서 '기간 미상' 표시)."""
        return self.apply_end is not None and today > self.apply_end

    def period_status(self, today: date) -> tuple[str, str]:
        """접수기간과 오늘을 비교. (상태, 안내문)

        상태: OPEN(접수 중) · NOT_STARTED(접수 전) · CLOSED(마감) · UNKNOWN(기간 확인 불가).
        날짜 칸이 비어 있으면 원문 기간(application_period)에서 다시 읽어 본다.
        """
        start, end = self.apply_start, self.apply_end
        if start is None and end is None and self.application_period:
            start, end = parse_period(self.application_period)
        if end is not None and today > end:
            return "CLOSED", f"접수가 끝난 공고입니다(접수 종료 {end}, 오늘 {today}). 다음 공고 대비 참고용으로만 쓰세요."
        if start is not None and today < start:
            return "NOT_STARTED", f"아직 접수 전입니다(접수 시작 {start}, 오늘 {today})."
        if start is None and end is None:
            return "UNKNOWN", "공고문에서 접수기간을 확인할 수 없습니다. 지금 신청을 받는 사업인지 원문·담당 부서에 확인하세요."
        if end is None:
            return "UNKNOWN", f"접수 시작({start})만 있고 종료일을 확인할 수 없습니다. 마감 여부를 원문에서 확인하세요."
        return "OPEN", f"접수 중입니다({start or '?'} ~ {end})."

    def is_judgeable(self) -> bool:
        return self.business_type == BusinessType.APPLY_INSPECTION

    def require_judgeable(self) -> None:
        if not self.is_judgeable():
            label = BUSINESS_TYPE_LABEL.get(self.business_type, "사업유형 미정") if self.business_type else "사업유형 미정"
            raise NotJudgeableNoticeError(
                f"이 공고({self.program_name or self.notice_id})는 '{label}' 유형이라 건물 판정을 하지 않습니다. "
                "신청형 안전점검 지원(APPLY_INSPECTION) 공고만 판정합니다."
            )

    def age_reference_date(self, eval_date: date) -> tuple[date, str | None]:
        """경과연수 기준 날짜와 (대체했다면) 경고."""
        if self.age_reference == "APPLY_START":
            if self.apply_start:
                return self.apply_start, None
            return eval_date, "접수 시작일이 없어 판정 기준일로 경과연수를 계산했습니다."
        if self.age_reference == "NOTICE_DATE":
            if self.notice_date:
                return self.notice_date, None
            return eval_date, "공고일이 없어 판정 기준일로 경과연수를 계산했습니다."
        return eval_date, None


# ---------------------------------------------------------------- 원문 문자열 해석

def parse_number(text: str) -> float | None:
    """'1,000' '1천' '5백' '1천500' '660' → 숫자."""
    s = text.replace(",", "").replace(" ", "")
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"(\d*)천(\d*)", s)
    if m:
        return float(int(m.group(1) or 1) * 1000 + int(m.group(2) or 0))
    m = re.fullmatch(r"(\d*)백(\d*)", s)
    if m:
        return float(int(m.group(1) or 1) * 100 + int(m.group(2) or 0))
    return None


_BOUND_WORDS: dict[str, str] = {"이하": "LE", "미만": "LT", "이상": "GE", "초과": "GT"}


def parse_floor_text(text: str | None) -> tuple[int, str] | None:
    """'3층 이하' → (3, 'LE'). 이하/미만 표기가 없으면 None (추측하지 않는다)."""
    if not text:
        return None
    m = re.search(r"(\d+)\s*층\s*(이하|미만)", str(text))
    return (int(m.group(1)), _BOUND_WORDS[m.group(2)]) if m else None


def parse_area_text(text: str | None) -> tuple[float, str] | None:
    """'1000㎡ 이하', '연면적 5백제곱미터 미만' → (값, 'LE'|'LT'). 표기 없으면 None."""
    if not text:
        return None
    t = str(text).replace("m²", "㎡").replace("m2", "㎡").replace("제곱미터", "㎡")
    m = re.search(r"([\d,]*\s*[천백]?\s*[\d,]*(?:\.\d+)?)\s*㎡\s*(이하|미만)", t)
    if not m:
        return None
    val = parse_number(m.group(1))
    return (val, _BOUND_WORDS[m.group(2)]) if val is not None else None


_DATE_RE = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")


def parse_period(text: str | None) -> tuple[date | None, date | None]:
    """'2026. 7. 1. ~ 2026. 7. 31.' → (date, date). 날짜를 못 찾으면 None.

    종료일에 연도가 생략된 '2026. 7. 1. ~ 7. 31.' 형식도 시작 연도로 채우고, 해를 넘기면('12. 15. ~ 1. 9.') 다음 해로 본다.
    날짜 하나가 '~' 뒤에 있거나 '까지'가 붙으면 종료일이다('~ 2026. 7. 31.', '2026. 7. 31.까지').
    """
    if not text:
        return None, None

    def make(y: object, m: object, d: object) -> date | None:
        try:
            return date(int(y), int(m), int(d))
        except ValueError:
            return None

    found = [(make(*mt.groups()), mt) for mt in _DATE_RE.finditer(text)]
    found = [(d, mt) for d, mt in found if d is not None]
    if len(found) >= 2:
        return found[0][0], found[1][0]
    if len(found) == 1:
        day, mt = found[0]
        tilde = text.find("~")
        if (0 <= tilde < mt.start()) or re.match(r"\s*\.?\s*(\([^)]*\))?\s*까지", text[mt.end():]):
            return None, day
        if tilde < 0:
            return day, None
        m = re.search(r"(\d{1,2})\s*[.월]\s*(\d{1,2})", text[tilde + 1 :])
        end = make(day.year, m.group(1), m.group(2)) if m else None
        if end and end < day:
            end = make(day.year + 1, end.month, end.day)
        return day, end
    return None, None
