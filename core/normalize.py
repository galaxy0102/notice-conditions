"""대장 구조·용도 정규화와 조건 토큰.

- 구조: data/ref/structure_map.csv (대장 표기 → 조적조/목조/철근콘크리트조/철골조/기타)
  확실한 값만 넣는다. 매칭 실패는 None → 조건이 있을 때만 UNKNOWN(STRUCTURE_UNMAPPED).
- 용도: data/ref/use_map.csv (mainPurpsCdNm·etcPurps → 표준 용도 + is_mixed)
- 실행마다 미매핑 값과 건수를 돌려줘 담당자가 CSV에 추가할 수 있게 한다.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

REF_DIR = Path(__file__).resolve().parents[1] / "data" / "ref"

STRUCTURE_STD = ("조적조", "목조", "철근콘크리트조", "철골조", "기타")

# 세부 용도(기타용도 etcPurps에서 찾는다) → 상위 표준 용도
USE_SUBTYPES: dict[str, str] = {
    "다가구주택": "단독주택",
    "다중주택": "단독주택",
    "공관": "단독주택",
    "아파트": "공동주택",
    "연립주택": "공동주택",
    "다세대주택": "공동주택",
    "기숙사": "공동주택",
    "지역아동센터": "노유자시설",
}

# 주택과 다른 용도를 함께 적은 기타용도 → 복합 용도로 본다 (해석 필요 → UNKNOWN 쪽으로만 작동)
_HOUSING_WORDS = ("주택", "아파트", "다세대", "연립", "다가구")
_OTHER_WORDS = ("근린생활", "점포", "상가", "사무소", "업무", "공장", "창고")


def _key(s: object) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return re.sub(r"\s+", "", str(s))


@lru_cache(maxsize=1)
def structure_map() -> dict[str, str]:
    with open(REF_DIR / "structure_map.csv", encoding="utf-8-sig", newline="") as f:
        out = {}
        for r in csv.DictReader(f):
            std = r["std"].strip()
            if std not in STRUCTURE_STD:
                raise ValueError(f"structure_map.csv: 표준 구조가 아님 '{std}' (허용: {STRUCTURE_STD})")
            out[_key(r["raw"])] = std
        return out


@lru_cache(maxsize=1)
def use_map() -> dict[str, tuple[str | None, bool]]:
    with open(REF_DIR / "use_map.csv", encoding="utf-8-sig", newline="") as f:
        return {
            _key(r["raw"]): (r["std"].strip() or None, r["is_mixed"].strip().upper() == "Y")
            for r in csv.DictReader(f)
        }


def standard_uses() -> set[str]:
    return {std for std, _ in use_map().values() if std}


# ---------------------------------------------------------------- 건물 한 동


def structure_std(strct_cd_nm: object, etc_strct: object) -> str | None:
    m = structure_map()
    return m.get(_key(strct_cd_nm)) or m.get(_key(etc_strct))


@dataclass(frozen=True)
class UseInfo:
    std: str | None  # 표준 주용도
    subtypes: tuple[str, ...]  # 기타용도에서 찾은 세부 용도
    is_mixed: bool


def use_info(main_purps: object, etc_purps: object) -> UseInfo:
    m = use_map()
    main = m.get(_key(main_purps))
    etc_key = _key(etc_purps)
    etc = m.get(etc_key)
    std = (main[0] if main else None) or (etc[0] if etc else None)
    subtypes = tuple(s for s in USE_SUBTYPES if s in etc_key)
    is_mixed = bool((main and main[1]) or (etc and etc[1]))
    if not is_mixed and etc_key:
        is_mixed = any(w in etc_key for w in _HOUSING_WORDS) and any(w in etc_key for w in _OTHER_WORDS)
    if std is None and len({USE_SUBTYPES[s] for s in subtypes}) == 1:
        std = USE_SUBTYPES[subtypes[0]]
    return UseInfo(std, subtypes, is_mixed)


# ---------------------------------------------------------------- DataFrame 단위


@dataclass
class NormalizeResult:
    df: pd.DataFrame
    unmapped_structures: Counter = field(default_factory=Counter)
    unmapped_uses: Counter = field(default_factory=Counter)


def normalize_df(df: pd.DataFrame) -> NormalizeResult:
    """structure_std, use_std, use_subtypes, use_is_mixed 컬럼을 더한 사본과 미매핑 집계."""
    out = df.copy()
    un_s: Counter = Counter()
    un_u: Counter = Counter()
    s_col, u_col, sub_col, mix_col = [], [], [], []
    for _, row in out.iterrows():
        s = structure_std(row.get("strctCdNm"), row.get("etcStrct"))
        if s is None:
            un_s[_key(row.get("strctCdNm")) or _key(row.get("etcStrct")) or "(빈 값)"] += 1
        u = use_info(row.get("mainPurpsCdNm"), row.get("etcPurps"))
        if u.std is None:
            un_u[_key(row.get("mainPurpsCdNm")) or _key(row.get("etcPurps")) or "(빈 값)"] += 1
        s_col.append(s)
        u_col.append(u.std)
        sub_col.append(list(u.subtypes))
        mix_col.append(u.is_mixed)
    # dtype=object: pandas 3의 기본 문자열 dtype은 None을 NaN으로 바꾼다. 미매핑은 None으로 둔다.
    out["structure_std"] = pd.Series(s_col, index=out.index, dtype="object")
    out["use_std"] = pd.Series(u_col, index=out.index, dtype="object")
    out["use_subtypes"] = pd.Series(sub_col, index=out.index, dtype="object")
    out["use_is_mixed"] = mix_col
    return NormalizeResult(out, un_s, un_u)


# ---------------------------------------------------------------- 조건 토큰 (공고·조례 원문 → 표준)

HOUSING_BROAD = "주택"  # 공고가 '주택'이라고만 한 경우. 공동주택 포함 여부 불명
_EXCLUDE_RE = re.compile(r"제외|이외|외의")  # '공동주택 제외'를 '공동주택만'으로 읽지 않게


def use_tokens(text: str) -> tuple[list[str], list[str]]:
    """공고·조례의 용도 표현 → (표준 토큰, 해석 못 한 원문).

    토큰: 표준 용도(단독주택, 제1종근생 …), 세부 용도(다가구주택 …), '주택'(범위 불명),
    '!다가구주택'(앞의 용도에서 제외할 세부 용도).
    """
    t = _key(text)
    if not t:
        return [], []
    if _EXCLUDE_RE.search(re.sub(r"\(?다가구주택(은|을)?제외\)?", "", t)):
        return [], [text]  # 다가구주택 제외 말고 다른 '제외'는 해석하지 않는다(판정에서 확인필요)
    tokens: list[str] = []
    if "근린생활시설" in t or "근생" in t:
        # "제1종·제2종", "제1종,제2종", "제1·2종", "제1종및제2종" → 둘 다. 종 언급이 없으면 둘 다.
        has1 = "1종" in t or re.search(r"1[·ㆍ,및]제?2", t) is not None
        has2 = "2종" in t
        if has1 == has2:
            tokens += ["제1종근생", "제2종근생"]
        else:
            tokens.append("제1종근생" if has1 else "제2종근생")
    rest = re.sub(r"(제?[12]종?[·ㆍ,및]?)*근(린생활시설|생)", "", t)
    if "단독주택" in rest:
        tokens.append("단독주택")
        if re.search(r"다가구주택(은|을)?제외", rest):
            tokens.append("!다가구주택")
    elif "공동주택" in rest:
        tokens.append("공동주택")
    elif re.fullmatch(r"(일반)?주택", rest) or rest in ("주거용건축물", "주거용"):
        tokens.append(HOUSING_BROAD)
    for sub in USE_SUBTYPES:
        if sub in rest and sub not in tokens and f"!{sub}" not in tokens:
            tokens.append(sub)
    for std in standard_uses():
        if std in rest and std not in tokens and std not in ("단독주택", "공동주택"):
            tokens.append(std)
    return (tokens, []) if tokens else ([], [text])


def structure_tokens(text: str) -> tuple[list[str], list[str]]:
    t = _key(text)
    if not t:
        return [], []
    if _EXCLUDE_RE.search(t):
        return [], [text]
    if t in structure_map():
        return [structure_map()[t]], []
    if re.search(r"조적|벽돌|블[럭록]|연와", t):
        return ["조적조"], []
    if t in ("목조", "목구조", "목재"):
        return ["목조"], []
    if t.startswith("철근콘크리트"):
        return ["철근콘크리트조"], []
    if t in ("철골", "철골조", "철골구조"):
        return ["철골조"], []
    return [], [text]


def match_use(tokens: list[str], info: UseInfo) -> tuple[str, str | None]:
    """용도 조건 평가 → (PASS|FAIL|UNKNOWN, 사유코드)."""
    include = [t for t in tokens if not t.startswith("!")]
    exclude = [t[1:] for t in tokens if t.startswith("!")]
    if info.std is None and not info.subtypes:
        return "UNKNOWN", "USE_UNMAPPED"
    if any(s in info.subtypes for s in exclude):
        return "FAIL", None
    if info.std in include or any(s in include for s in info.subtypes):
        return "PASS", None
    if HOUSING_BROAD in include:
        if info.std == "단독주택":
            return "PASS", None
        if info.std == "공동주택":
            return "UNKNOWN", "HOUSING_SCOPE_UNCLEAR"
    if info.is_mixed:
        return "UNKNOWN", "USE_MIXED"
    return "FAIL", None
