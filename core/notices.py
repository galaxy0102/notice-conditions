"""data/notices/의 공고 조건 JSON 로더.

공고 조건은 공개 문서 정보라 저장을 허용한다(건축물대장·좌표는 저장 금지).
reviewed=False 공고도 불러오되 화면·내보내기에 '미검수'로 표시한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from core.conditions import NoticeConditions

NOTICE_DIR = Path(__file__).resolve().parents[1] / "data" / "notices"


def load_notices(directory: Path = NOTICE_DIR, skipped: list[str] | None = None) -> list[NoticeConditions]:
    """읽지 못한 파일은 건너뛰고 skipped에 '파일 이름: 사유'를 남긴다(파일 하나 때문에 화면 전체가 멈추지 않게)."""
    out = []
    for p in sorted(directory.glob("*.json")):
        try:
            out.append(NoticeConditions.model_validate_json(p.read_text(encoding="utf-8")))
        except (ValueError, OSError) as e:
            if skipped is not None:
                skipped.append(f"{p.name}: {str(e).splitlines()[0]}")
    return out


def save_notice(n: NoticeConditions, directory: Path = NOTICE_DIR, overwrite: bool = False) -> Path:
    """검수된 공고 조건을 data/notices/{notice_id}.json으로 저장(공개 문서 정보라 저장 허용)."""
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in n.notice_id).strip("_")  # 한글도 isalnum
    if not safe:
        raise ValueError("notice_id가 비어 있습니다.")
    path = directory / f"{safe}.json"
    if path.exists() and not overwrite:
        raise FileExistsError(f"같은 이름의 공고가 이미 있습니다: {path.name}")
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(n.model_dump_json(indent=2), encoding="utf-8")
    return path


def notices_for(sigungu_cd: str, notices: list[NoticeConditions]) -> list[NoticeConditions]:
    """해당 시군구가 sigungu_codes에 있는 공고. 접수 시작일이 최신인 것부터(시작일 없는 공고는 뒤로)."""
    hits = [n for n in notices if sigungu_cd in n.sigungu_codes]
    return sorted(hits, key=lambda n: (n.apply_start is not None, n.apply_start or date.min), reverse=True)


def notice_label(n: NoticeConditions, today: date) -> str:
    parts = [n.program_name or n.notice_id]
    if n.apply_start or n.apply_end:
        parts.append(f"{n.apply_start or '?'}~{n.apply_end or '?'}")
    if n.is_expired(today):
        parts.append("접수 마감")
    if not n.reviewed:
        parts.append("미검수")
    return " · ".join(str(p) for p in parts)


def condition_hash(*parts: object) -> str:
    """공고·조례·옵션·기준일이 같으면 같은 값 → 재판정 캐시 키."""
    def dump(p: object) -> object:
        if p is None:
            return None
        if hasattr(p, "model_dump"):
            return p.model_dump(mode="json")
        if hasattr(p, "__dataclass_fields__"):
            return {k: getattr(p, k) for k in p.__dataclass_fields__}
        return str(p)

    raw = json.dumps([dump(p) for p in parts], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
