"""공고 자동 탐색 (단계 7-1, 후순위).

순서: data/notices의 검수된 공고 → 네이버 검색(뉴스 + 웹문서) → 없으면 조례 기준 판정 + 수동 업로드 안내.

- 검색 결과는 후보 링크 목록(제목, 출처, 날짜)으로만 보여 준다. 사용자가 고른 링크만 본문을 가져와 단계 6 분류·추출로 넘긴다.
- 자동 추출 결과는 검수 없이 저장하지 않는다(reviewed=False).
- 뉴스·보도자료 출처는 source_type=NEWS/PRESS_RELEASE로 표시하고 '원문 공고 확인 필요' 경고.
- robots.txt와 접근 제한을 존중한다. 첨부파일(HWP/PDF)은 링크만 보여 주고 사용자가 내려받아 업로드한다.

네이버 검색 API 설정
- 키를 발급받은 곳에 따라 엔드포인트·인증 헤더가 다르다 → NAVER_API_STYLE=developers | hub
- developers(네이버 개발자센터): 아래 NAVER_DEVELOPERS 값. ※ 공식 문서를 자동으로 읽지 못해(접근 차단)
  알려진 형식으로 넣었다. 실제 키로 응답을 확인하기 전까지 '확인 필요'.
- hub(NAVER API HUB): 사용자가 준 공식 가이드 PDF('뉴스 검색 결과 조회', 2026-11-15)로 확인한 값.
  GET https://naverapihub.apigw.ntruss.com/search/v1/news, 헤더 X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY,
  파라미터 query, display(1~100), start(1~1000), sort(sim|date), format(json|xml). 응답 items: title, originallink,
  link, description(<b> 강조), pubDate. 오류 SE01~SE06(400), SE05(404), SE99(500).
  웹문서는 같은 경로의 /search/v1/webkr (2026-11-19 실제 키로 확인: news·webkr·blog·cafearticle 200, web·doc 404).
  응답 items: title, link, description(날짜 없음). NAVER_HUB_WEB_URL로 바꿀 수 있다.
- 하루 호출 한도 25,000회(가이드 기준, 검색 API 공통). 한 번 찾기에 3회(웹문서 2 + 뉴스 1).
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from email.utils import parsedate_to_datetime
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv

from core import regions
from core.conditions import NoticeConditions
from core.llm.base import LLMProvider, Part
from core.notices import load_notices, notices_for

USER_AGENT = "notice-conditions-bot/0.1 (+municipal building safety tool)"
TIMEOUT = 15
MAX_PAGE_BYTES = 3 * 1024 * 1024
DAILY_LIMIT = 25_000
ATTACHMENT_RE = re.compile(r"\.(hwpx?|pdf|zip|docx?|xlsx?)(\?|$)", re.I)
NEWS_HOST_HINT = ("news", "press", "media", "yna.co.kr", "newsis", "news1")

# 네이버 개발자센터 검색 API (확인 필요: 공식 문서 미확인, 실제 응답으로 검증할 것)
NAVER_DEVELOPERS = {
    "news_url": "https://openapi.naver.com/v1/search/news.json",
    "web_url": "https://openapi.naver.com/v1/search/webkr.json",
    "id_header": "X-Naver-Client-Id",
    "secret_header": "X-Naver-Client-Secret",
    "extra_params": {},
}

# NAVER API HUB 검색 (뉴스: 공식 가이드 PDF, 웹문서 webkr: 실제 호출로 확인)
NAVER_HUB = {
    "news_url": "https://naverapihub.apigw.ntruss.com/search/v1/news",
    "web_url": "https://naverapihub.apigw.ntruss.com/search/v1/webkr",  # 2026-11-19 실제 키로 응답 확인
    "id_header": "X-NCP-APIGW-API-KEY-ID",
    "secret_header": "X-NCP-APIGW-API-KEY",
    "extra_params": {"format": "json"},
}


class HunterError(RuntimeError):
    pass


class NotConfigured(HunterError):
    """키나 엔드포인트 설정이 없다."""


@dataclass
class Candidate:
    title: str
    url: str
    source: str  # 도메인
    date: str  # YYYY-MM-DD 또는 ""
    kind: str  # news | web
    snippet: str = ""
    score: int = 0  # 관련도(score_candidates)
    year: int | None = None  # 글의 연도(날짜·제목·주소에서)

    @property
    def source_type(self) -> str:
        if self.kind == "news":
            return "NEWS"
        if any(h in self.source for h in NEWS_HOST_HINT):
            return "PRESS_RELEASE"
        return "OFFICIAL_NOTICE"


@dataclass
class HuntResult:
    stage: str  # reviewed | search | none
    notices: list[NoticeConditions] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    message: str = ""
    calls: int = 0
    auto: "AutoExtract | None" = None


# ---------------------------------------------------------------- 네이버 검색


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def naver_config() -> dict:
    load_dotenv()
    style = (os.environ.get("NAVER_API_STYLE") or "developers").strip().lower()
    cid = (os.environ.get("NAVER_CLIENT_ID") or "").strip()
    secret = (os.environ.get("NAVER_CLIENT_SECRET") or "").strip()
    if style == "hub":
        cfg = dict(NAVER_HUB)
        for key in ("news_url", "web_url", "id_header", "secret_header"):  # 가이드가 바뀌면 .env로 덮어쓴다
            override = (os.environ.get(f"NAVER_HUB_{key.upper()}") or "").strip()
            if override:
                cfg[key] = override
    elif style == "developers":
        cfg = dict(NAVER_DEVELOPERS)
    else:
        raise NotConfigured(f"NAVER_API_STYLE은 developers 또는 hub: {style}")
    missing = [n for n, v in (("NAVER_CLIENT_ID", cid), ("NAVER_CLIENT_SECRET", secret)) if not v]
    if missing:
        raise NotConfigured(f".env에 {', '.join(missing)}가 비어 있습니다(Client ID와 Client Secret 둘 다 필요).")
    cfg.update(style=style, id=cid, secret=secret)
    return cfg


def _naver_date(item: dict) -> str:
    raw = item.get("pubDate") or item.get("postdate") or ""
    if re.fullmatch(r"\d{8}", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (TypeError, ValueError):
        return ""


def parse_naver(payload: dict, kind: str) -> list[Candidate]:
    out = []
    for it in payload.get("items") or []:
        url = it.get("originallink") or it.get("link") or ""
        if not url.startswith("http"):
            continue
        out.append(
            Candidate(
                title=_clean(it.get("title", "")),
                url=url,
                source=urlparse(url).netloc,
                date=_naver_date(it),
                kind=kind,
                snippet=_clean(it.get("description", ""))[:200],
            )
        )
    return out


# ---------------------------------------------------------------- 검색어·관련도

SIDO_SHORT = {
    "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남", "전남광주통합특별시": "광주",
}


@dataclass(frozen=True)
class SearchTarget:
    query_name: str  # 검색어에 넣는 이름: '대전 중구'(같은 이름이 여러 곳), '수원시'(일반구는 시 단위로 공고)
    term: str  # 제목·요약에 있어야 하는 지역 말: '중구', '수원'
    qualifier: str = ""  # 같은 이름이 여러 곳이면 함께 있어야 하는 시·도 약칭: '대전'


def _short_sido(sido: str) -> str:
    return SIDO_SHORT.get(sido, sido[:2])


@lru_cache(maxsize=1)
def _name_counts() -> dict[str, int]:
    """시·군·구(일반구를 둔 시는 시) 이름이 전국에 몇 번 나오는지. '중구' 7, '고성군' 2 …"""
    seen: dict[str, set[str]] = {}
    for sido in regions.list_sido():
        for _, name in regions.list_sigungu(sido):
            seen.setdefault(name.split(" ")[0], set()).add(sido)
    return {k: len(v) for k, v in seen.items()}


def search_target(region: str) -> SearchTarget:
    """시군구코드(5자리) 또는 '대전광역시 중구' 같은 이름 → 검색어용 지역 이름."""
    full = (regions.name_of(region) if re.fullmatch(r"\d{5}", region) else region) or region
    toks = full.split()
    short = _short_sido(toks[0])
    if len(toks) == 1:  # 세종처럼 시·도 단위
        return SearchTarget(short, short)
    name = toks[1]  # '경기도 수원시 장안구' → 수원시(소규모 노후건축물 점검은 시 건축안전센터가 공고)
    term = name[:-1] if len(name) >= 3 and name[-1] in "시군구" else name
    if _name_counts().get(name, 1) > 1:
        return SearchTarget(f"{short} {name}", term, short)
    return SearchTarget(name, term)


def queries_for(target: SearchTarget, today: date) -> list[tuple[str, str, dict]]:
    """(검색 종류, 검색어, 추가 파라미터). 웹문서는 구청·시청 게시판 공고를, 뉴스는 보도된 모집 소식을 찾는다.

    공고 제목에 흔한 말: '소규모 노후 건축물 (무료) 안전점검 신청 안내·모집 공고'. 연도를 넣은 검색어로 올해 공고를 앞으로 끌어온다.
    블로그·카페는 광고가 대부분이라 쓰지 않는다.
    """
    n = target.query_name
    return [
        ("web", f"{n} 소규모 노후건축물 안전점검 신청", {}),
        ("web", f"{today.year}년 {n} 소규모 노후 건축물 안전점검 공고", {}),
        ("news", f"{n} 노후 건축물 무료 안전점검 신청", {"sort": "sim"}),
    ]


RELEVANT_RE = re.compile(r"노후\s*(건축물|건물)|소규모\s*건축물|건축물\s*(무료\s*)?안전\s*점검")
NOISE_RE = re.compile(r"점검기관|점검단|후보자|매뉴얼|관리자\s*교육|간판|승강기|교량|재건축|재개발|정비사업|빈집|모아타운|어린이|놀이시설|결재|조례|자치법규|회의록|의회")
APPLY_RE = re.compile(r"신청|모집|접수|공고|안내")
DONE_RE = re.compile(r"완료|마쳐|마무리|결과|성료|계획")
# 여러 기관이 함께 쓰는 관공서 사이트: 한 글에 지역 이름이 있어도 사이트 전체를 그 지역 것으로 보지 않는다
SHARED_HOSTS = ("opengov.", "news.seoul.go.kr", "elis.go.kr", "law.go.kr", "molit.go.kr", "korea.kr", "epeople.go.kr", "kira.or.kr")
LOCAL_GOV_HOST_RE = re.compile(r"\.go\.kr$|\.(seoul|busan|daegu|incheon|gwangju|daejeon|ulsan)\.kr$")
FILE_URL_RE = re.compile(r"filedown|fileDownload|download\.|FileDown|DWN\.jsp", re.I)
AUTO_MIN_SCORE = 5  # 자동 추출 기준: 올해 글(또는 작년 관공서 공고) 이상
AUTO_MAX_SOURCES = 3  # 자동으로 합치는 글 수(글마다 LLM 1회)


def _year_of(c: Candidate) -> int | None:
    if c.date:
        return int(c.date[:4])
    for pat, text in ((r"(20\d\d)\s*년", c.title), (r"(?<!\d)(20\d\d)[01]\d[0-3]\d", c.url), (r"(20\d\d)\s*[.년]", c.snippet)):
        m = re.search(pat, text)
        if m:
            return int(m.group(1))
    return None


def _official(host: str) -> bool:
    """지자체·관공서 사이트(구청 사이트는 seogu.gwangju.kr처럼 go.kr이 아닌 곳도 있다)."""
    return bool(LOCAL_GOV_HOST_RE.search(host))


_CHO = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
_JUNG = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi", "yu", "eu", "ui", "i"]
_JONG = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t", "t", "ng", "t", "t", "k", "t", "p", "t"]


def romanize(text: str) -> str:
    """한글 → 로마자(국어의 로마자 표기법, 단순화). 지자체 사이트 주소 비교용: 종로→jongno, 중구→junggu, 고성→goseong."""
    out = []
    syl = [ord(ch) - 0xAC00 for ch in text if 0 <= ord(ch) - 0xAC00 < 11172]
    for i, v in enumerate(syl):
        cho, jung, jong = v // 588, (v % 588) // 28, v % 28
        c = _CHO[cho]
        if i > 0 and cho == 5:  # ㄹ 앞 받침에 따른 소리: ㅇ·ㅁ·ㄱ·ㅂ 뒤 n, ㄴ·ㄹ 뒤 l
            prev = syl[i - 1] % 28
            c = "n" if prev in (21, 16, 1, 17) else "l" if prev in (4, 8) else "r"
            if prev == 4:  # 신라 → silla
                out[-1] = out[-1][:-1] + "l"
        elif i > 0 and cho == 11 and syl[i - 1] % 28 not in (0, 21):  # 받침 + ㅇ: 받침이 넘어가 소리남(근사)
            c = ""
        out.append(c + _JUNG[jung] + _JONG[jong])
    return "".join(out)


def _host_is_region(host: str, target: SearchTarget) -> bool:
    """사이트 주소가 그 지자체 것으로 보이는지: www.djjunggu.go.kr(대전 중구), www.goseong.go.kr(고성), suwon.go.kr."""
    h = host.replace("-", "").lower()
    name = romanize(target.term if target.term.endswith(("구", "군")) or len(target.term) < 2 else target.term)
    if not name or name not in h:
        return False
    if not target.qualifier:
        return True
    q = romanize(target.qualifier)
    initials = "".join(_CHO[(ord(ch) - 0xAC00) // 588][:1] for ch in target.qualifier if 0 <= ord(ch) - 0xAC00 < 11172)
    return q in h or h.split(".")[-3:-2] == [q] or h.replace("www.", "").startswith(initials + name)


def _shared(host: str) -> bool:
    return any(h in host for h in SHARED_HOSTS)


def _newsish(url: str, host: str) -> bool:
    return any(h in host for h in NEWS_HOST_HINT) or "articleView" in url or "/news/" in url


def is_file_link(c: Candidate) -> bool:
    """첨부파일 바로가기(HWP·PDF 내려받기). 본문을 자동으로 가져오지 않는다."""
    return bool(FILE_URL_RE.search(c.url) or ATTACHMENT_RE.search(c.url) or ATTACHMENT_RE.search(c.title))


def score_candidates(cands: list[Candidate], target: SearchTarget, today: date) -> list[Candidate]:
    """관련 없는 글·다른 지역 글을 빼고 점수순으로. 점수: 올해 +4·작년 +1·더 오래 -2, 관공서 +3, 신청·모집 +2, 완료·결과 -2, 뉴스 -1.

    지역 확인: 제목·요약에 지역 이름(같은 이름이 여러 곳이면 시·도 약칭도)이 있거나, 그런 글이 올라온 관공서 도메인의 글.
    """
    def text(c: Candidate) -> str:
        return f"{c.title} {c.snippet}"

    def names_region(t: str) -> bool:
        return target.term in t and (not target.qualifier or target.qualifier in t)

    kept = [c for c in cands if RELEVANT_RE.search(text(c)) and "점검" in text(c) and not NOISE_RE.search(c.title)]
    for c in kept:
        if c.kind == "web" and _newsish(c.url, c.source):
            c.kind = "news"  # 웹문서 검색에 나온 기사
    # 제목에 지역 이름이 있는 글이 올라온 지자체 사이트 → 그 사이트 글은 제목에 지역 이름이 없어도 같은 지역
    domains = {c.source for c in kept if _official(c.source) and not _shared(c.source)
               and (names_region(c.title) or _host_is_region(c.source, target))}
    out = []
    for c in kept:
        if c.source in domains:
            pass
        elif _official(c.source):
            if not names_region(c.title):  # 다른 지자체·공용 사이트 글은 제목에 지역 이름이 있어야
                continue
        elif not names_region(text(c)):
            continue
        c.year = _year_of(c)
        s = 0
        if c.year == today.year:
            s += 4
        elif c.year == today.year - 1:
            s += 1
        elif c.year is not None:
            s -= 2
        s += 3 if _official(c.source) else 0
        s += 2 if APPLY_RE.search(c.title) else 0
        s -= 2 if DONE_RE.search(c.title) else 0
        s -= 1 if c.kind == "news" else 0
        c.score = s
        out.append(c)
    return sorted(out, key=lambda c: (c.score, c.date or str(c.year or "")), reverse=True)


def search_candidates(region_name: str, today: date, session: requests.Session | None = None, display: int = 20) -> tuple[list[Candidate], int]:
    """region_name: 시군구코드(5자리) 또는 '대전광역시 중구' 같은 이름. 반환: (관련도순 후보, 호출 횟수)."""
    cfg = naver_config()
    session = session or requests.Session()
    headers = {cfg["id_header"]: cfg["id"], cfg["secret_header"]: cfg["secret"]}
    target = search_target(region_name)
    seen: dict[str, Candidate] = {}
    calls = 0
    urls = {"news": cfg["news_url"], "web": cfg["web_url"]}
    for kind, q, extra in queries_for(target, today):
        url = urls[kind]
        if not url:
            continue  # 웹문서 엔드포인트가 설정되지 않음
        params = {"query": q, "display": display, "start": 1, **cfg.get("extra_params", {}), **extra}
        try:
            r = session.get(url, params=params, headers=headers, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise HunterError("네이버 검색에 연결하지 못했습니다. 잠시 뒤 다시 시도하세요.") from e
        calls += 1
        if r.status_code in (401, 403):
            raise HunterError("네이버 검색 인증 실패: Client ID·Secret과 NAVER_API_STYLE을 확인하세요.")
        if r.status_code == 429:
            raise HunterError("네이버 검색 호출 한도를 넘었습니다.")
        if r.status_code >= 400:
            code = ""
            try:
                body = r.json()
                code = body.get("errorCode") or (body.get("error") or {}).get("errorCode") or ""
            except (ValueError, AttributeError):
                pass
            raise HunterError(f"네이버 검색 오류(HTTP {r.status_code}{' ' + str(code) if code else ''})")
        try:
            payload = r.json()
        except ValueError as e:
            raise HunterError("네이버 검색 응답을 읽지 못했습니다(JSON 아님).") from e
        for c in parse_naver(payload, kind):
            seen.setdefault(c.url, c)
    return score_candidates(list(seen.values()), target, today), calls


# ---------------------------------------------------------------- 본문 가져오기


@dataclass
class PageContent:
    url: str
    text: str
    attachments: list[tuple[str, str]]  # (링크 글자, 절대 URL) — 내려받지 않는다


def robots_allows(url: str, session: requests.Session | None = None) -> bool:
    p = urlparse(url)
    robots = f"{p.scheme}://{p.netloc}/robots.txt"
    rp = robotparser.RobotFileParser()
    try:
        r = (session or requests).get(robots, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    except requests.RequestException:
        return True  # robots.txt를 못 읽으면 일반 규칙대로 허용
    if r.status_code in (401, 403):
        return False
    if r.status_code >= 400:
        return True
    rp.parse(r.text.splitlines())
    return rp.can_fetch(USER_AGENT, url)


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", raw)
    raw = re.sub(r"(?i)</t[dh]>", " | ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [re.sub(r"[ \t ]+", " ", ln).strip(" |") for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def find_attachments(raw: str, base_url: str) -> list[tuple[str, str]]:
    out = []
    for href, label in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw):
        label_text = _clean(label)
        if ATTACHMENT_RE.search(href) or ATTACHMENT_RE.search(label_text) or "download" in href.lower() or "fileDown" in href:
            out.append((label_text or href, urljoin(base_url, html.unescape(href))))
    return list(dict.fromkeys(out))


def fetch_page(url: str, session: requests.Session | None = None) -> PageContent:
    if not url.startswith(("http://", "https://")):
        raise HunterError("http(s) 링크만 가져옵니다.")
    session = session or requests.Session()
    if not robots_allows(url, session):
        raise HunterError("이 사이트의 robots.txt가 자동 수집을 허용하지 않습니다. 직접 열어 확인하세요.")
    r = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, stream=True)
    if r.status_code in (401, 403):
        raise HunterError("접근이 제한된 페이지입니다. 직접 열어 확인하세요.")
    if r.status_code >= 400:
        raise HunterError(f"페이지를 가져오지 못했습니다(HTTP {r.status_code}).")
    ctype = r.headers.get("Content-Type", "")
    if "html" not in ctype and "text" not in ctype:
        raise HunterError("HTML 페이지가 아닙니다(첨부파일은 내려받아 직접 업로드하세요).")
    body = r.raw.read(MAX_PAGE_BYTES + 1, decode_content=True)
    if len(body) > MAX_PAGE_BYTES:
        raise HunterError("페이지가 너무 큽니다.")
    raw = body.decode(r.encoding or r.apparent_encoding or "utf-8", errors="replace")
    return PageContent(url=url, text=html_to_text(raw), attachments=find_attachments(raw, url))


def page_parts(page: PageContent) -> list[Part]:
    return [Part.of_text(f"[웹 페이지: {urlparse(page.url).netloc}]\n{page.text[:60_000]}", name=page.url)]


def extract_from_candidate(cand: Candidate, provider: LLMProvider, session: requests.Session | None = None, page: PageContent | None = None):
    """고른 링크 하나 → 본문 → 분류·추출. 결과는 reviewed=False(검수 전 저장 금지), 원문 주소를 남긴다."""
    from core.llm import extractor

    page = page or fetch_page(cand.url, session)
    parts = page_parts(page)
    c, r = extractor.analyze(parts, provider)  # 분류 + 추출 한 번에
    if r is None:
        return page, c, None
    st = cand.source_type
    if st in ("NEWS", "PRESS_RELEASE") or r.notice.source_type in ("NEWS", "PRESS_RELEASE"):
        r.notice = r.notice.model_copy(update={"source_type": st if st != "OFFICIAL_NOTICE" else r.notice.source_type})
        msg = "뉴스·보도자료 출처입니다. 원문 공고를 확인하세요"
        r.issues = [msg] + [i for i in r.issues if i != msg]
    r.notice = r.notice.model_copy(update={"reviewed": False, "source_url": cand.url})
    return page, c, r


@dataclass
class SourceResult:
    """글 하나의 처리 결과."""

    candidate: Candidate
    page: PageContent | None = None
    classify: object | None = None  # extractor.ClassifyResult
    extract: object | None = None  # extractor.ExtractResult (안전점검 신청형일 때)
    status: str = ""  # used | other_type | region_mismatch | skipped | error
    reason: str = ""


@dataclass
class AutoExtract:
    """여러 글을 분류·추출해 한 공고 조건으로 합친 결과(core.notice_merge)."""

    sources: list[SourceResult] = field(default_factory=list)
    merged: object | None = None  # notice_merge.Merged
    error: str = ""

    @property
    def used(self) -> list[SourceResult]:
        return [r for r in self.sources if r.status == "used"]

    @property
    def extract(self):
        """합친 결과(extractor.ExtractResult 모양). 쓸 글이 없으면 None."""
        if self.merged is None:
            return None
        from core.llm.extractor import ExtractResult

        return ExtractResult(self.merged.notice, self.merged.issues)

    @property
    def skipped(self) -> list[tuple[str, str]]:
        return [(r.candidate.url, r.reason) for r in self.sources if r.status in ("skipped", "error")]

    @property
    def region_mismatch(self) -> bool:
        return not self.used and any(r.status == "region_mismatch" for r in self.sources)

    @property
    def usages(self) -> list:
        return [r.classify.usage for r in self.sources if r.classify is not None]


def auto_pick(cands: list[Candidate]) -> list[Candidate]:
    """자동 추출에 쓸 글: 점수 기준 이상, 첨부파일 바로가기 제외, 점수순."""
    return [c for c in cands if c.score >= AUTO_MIN_SCORE and not is_file_link(c)]


def process_source(
    cand: Candidate, provider: LLMProvider, sigungu_cd: str | None = None, session: requests.Session | None = None,
) -> SourceResult:
    """글 하나: 본문 가져오기 → 분류·추출(LLM 1회) → 지역 확인."""
    from core.llm.base import LLMError

    r = SourceResult(cand)
    if is_file_link(cand):
        r.status, r.reason = "skipped", "첨부파일 링크입니다(내려받아 직접 올리세요)"
        return r
    try:
        r.page = fetch_page(cand.url, session)
    except (HunterError, requests.RequestException) as e:
        r.status, r.reason = "skipped", str(e) if isinstance(e, HunterError) else "페이지 연결 실패"
        return r
    if len(r.page.text) < 150 or "점검" not in r.page.text:
        r.status, r.reason = "skipped", "본문에 공고 내용이 없습니다(첨부파일에만 있을 수 있음)"
        return r
    try:
        _, r.classify, r.extract = extract_from_candidate(cand, provider, page=r.page)
    except LLMError as e:
        r.status, r.reason = "error", str(e)
        return r
    if r.extract is None:
        from core.conditions import BUSINESS_TYPE_LABEL

        r.status, r.reason = "other_type", f"'{BUSINESS_TYPE_LABEL[r.classify.business_type]}' 글이라 조건을 뽑지 않았습니다"
    elif sigungu_cd and r.extract.notice.sigungu_codes and sigungu_cd not in r.extract.notice.sigungu_codes:
        r.status, r.reason = "region_mismatch", f"다른 지역 글입니다({r.extract.notice.local_gov or '?'})"
    else:
        r.status = "used"
    return r


def extract_sources(
    cands: list[Candidate], provider: LLMProvider, sigungu_cd: str | None = None,
    session: requests.Session | None = None, max_sources: int = 3, max_tries: int = 5,
    cache: dict | None = None,
) -> AutoExtract:
    """글 여러 개를 차례로 추출해(최대 max_sources건 성공, max_tries건 시도) 합친다.

    cache: {주소: SourceResult}. 같은 글을 다시 고르면 LLM을 다시 부르지 않는다(세션 한정).
    """
    from core import notice_merge

    out = AutoExtract()
    for cand in cands[:max_tries]:
        if len(out.used) >= max_sources:
            break
        if cache is not None and cand.url in cache:
            r = cache[cand.url]
        else:
            r = process_source(cand, provider, sigungu_cd, session)
            if cache is not None and r.status != "error":
                cache[cand.url] = r
        out.sources.append(r)
    if out.used:
        out.merged = notice_merge.merge([
            notice_merge.MergeSource(r.extract.notice, r.extract.issues, r.candidate.url, r.candidate.title, r.candidate.year)
            for r in out.used
        ])
    elif any(r.status == "error" for r in out.sources):
        out.error = next(r.reason for r in out.sources if r.status == "error")
    return out


# ---------------------------------------------------------------- 전체 순서


def hunt(
    sigungu_cd: str, region_name: str, today: date, has_ordinance: bool,
    session: requests.Session | None = None, auto_provider: LLMProvider | None = None, cache: dict | None = None,
) -> HuntResult:
    """검수된 공고(접수 마감 전) → 네이버 검색 → (auto_provider가 있으면) 올해 글 상위 몇 건을 추출해 합친다.

    region_name은 화면 표시용. 검색어는 시군구코드로 만든다(search_target).
    """
    reviewed = [n for n in notices_for(sigungu_cd, load_notices()) if n.reviewed and n.period_status(today)[0] != "CLOSED"]
    if reviewed:
        return HuntResult("reviewed", notices=reviewed, message=f"검수된 공고 {len(reviewed)}건이 있습니다.")
    try:
        cands, calls = search_candidates(sigungu_cd if re.fullmatch(r"\d{5}", sigungu_cd or "") else region_name, today, session)
    except NotConfigured as e:
        return HuntResult("none", message=f"네이버 검색을 쓸 수 없습니다({e}). " + _fallback(has_ordinance))
    if not cands:
        return HuntResult("none", calls=calls, message="이 지역 공고를 찾지 못했습니다. " + _fallback(has_ordinance))
    res = HuntResult("search", candidates=cands, calls=calls, message=f"검색 후보 {len(cands)}건.")
    if auto_provider is not None:
        picks = auto_pick(cands)
        if picks:
            res.auto = extract_sources(picks, auto_provider, sigungu_cd, session, max_sources=AUTO_MAX_SOURCES, cache=cache)
        else:
            res.message += " 올해 공고로 보이는 글이 없어 자동 추출은 하지 않았습니다. 링크를 골라 추출하세요."
    return res


def _fallback(has_ordinance: bool) -> str:
    base = "조례 기준으로 판정합니다" if has_ordinance else "법정 기준(30년)으로 판정합니다"
    return base + ". 공고 파일이 있으면 아래 '공고 파일 올려 조건 추출'에서 직접 올려 주세요."
