"""단계 7: 공고 탐색(네이버 검색 모킹)과 조례 동기화 로직. 외부 호출 없음."""

from datetime import date

import pytest
import responses

from core import notice_hunter as nh
from core import ordinance_sync as sync
from core.llm.mock import MockProvider

TODAY = date(2026, 11, 15)


@pytest.fixture
def naver_env(monkeypatch):
    monkeypatch.setattr(nh, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("NAVER_API_STYLE", "developers")
    monkeypatch.setenv("NAVER_CLIENT_ID", "id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "secret")


def news_item(title, url, pub="Sun, 15 Nov 2026 09:00:00 +0900"):
    return {"title": title, "originallink": url, "link": url, "description": "<b>안전점검</b> 신청", "pubDate": pub}


# ---------------------------------------------------------------- 네이버 검색


def test_naver_not_configured(monkeypatch):
    monkeypatch.setattr(nh, "load_dotenv", lambda *a, **k: None)
    for k in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "NAVER_API_STYLE"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(nh.NotConfigured):
        nh.naver_config()
    monkeypatch.setenv("NAVER_API_STYLE", "hub")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "b")  # ID만 비어 있는 경우
    with pytest.raises(nh.NotConfigured) as e:
        nh.naver_config()
    assert "NAVER_CLIENT_ID" in str(e.value) and "NAVER_CLIENT_SECRET" not in str(e.value)


def web_item(title, url, desc="소규모 노후 건축물 안전점검 신청"):
    return {"title": title, "link": url, "description": desc}


@pytest.fixture
def hub_env(monkeypatch):
    monkeypatch.setattr(nh, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("NAVER_API_STYLE", "hub")
    monkeypatch.setenv("NAVER_CLIENT_ID", "hub-id")
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "hub-secret")
    for k in ("NAVER_HUB_WEB_URL", "NAVER_HUB_NEWS_URL"):
        monkeypatch.delenv(k, raising=False)


@responses.activate
def test_hub_style_web_and_news(hub_env):
    responses.get(nh.NAVER_HUB["web_url"], json={"items": [web_item("2026년 <b>소규모 노후 건축물</b> 안전점검 신청 안내", "https://www.jongno.go.kr/notice/1")]})
    # 가이드 응답 예시 형태
    responses.get(nh.NAVER_HUB["news_url"], json={
        "lastBuildDate": "Thu, 11 Jun 2026 19:03:00 +0900", "total": 1, "start": 1, "display": 1,
        "items": [news_item("<b>종로구</b> 노후건축물 안전점검 신청", "http://www.example-news.co.kr/a", "Thu, 11 Jun 2026 18:34:00 +0900")],
    })
    cands, calls = nh.search_candidates("11110", TODAY)
    assert calls == 3  # 웹문서 2 + 뉴스 1
    urls = [c.request.url for c in responses.calls]
    assert urls[0].startswith("https://naverapihub.apigw.ntruss.com/search/v1/webkr?")
    assert urls[2].startswith("https://naverapihub.apigw.ntruss.com/search/v1/news?") and "sort=sim" in urls[2]
    req = responses.calls[0].request
    assert req.headers["X-NCP-APIGW-API-KEY-ID"] == "hub-id" and req.headers["X-NCP-APIGW-API-KEY"] == "hub-secret"
    assert "format=json" in req.url
    # 구청 사이트(jongno.go.kr = 종로) 올해 신청 안내가 뉴스보다 앞
    assert [c.url for c in cands] == ["https://www.jongno.go.kr/notice/1", "http://www.example-news.co.kr/a"]
    assert cands[0].year == 2026 and cands[0].score >= nh.AUTO_MIN_SCORE
    assert cands[1].date == "2026-06-11" and cands[1].title == "종로구 노후건축물 안전점검 신청"


@responses.activate
def test_hub_error_code_in_message(hub_env):
    responses.get(nh.NAVER_HUB["web_url"], status=400, json={"errorMessage": "Invalid display value", "errorCode": "SE02"})
    with pytest.raises(nh.HunterError) as e:
        nh.search_candidates("서울특별시 종로구", TODAY)
    assert "SE02" in str(e.value)


@responses.activate
def test_search_candidates_developers_headers_and_dedupe(naver_env):
    url_a = "https://www.jongno.go.kr/notice/1"
    responses.get(nh.NAVER_DEVELOPERS["web_url"], json={"items": [web_item("종로구 소규모 노후건축물 안전점검 공고", url_a)]})
    responses.get(nh.NAVER_DEVELOPERS["news_url"], json={"items": [news_item("<b>종로구</b> 소규모 노후건축물 점검", "https://news.example.com/a")]})
    cands, calls = nh.search_candidates("서울특별시 종로구", TODAY)
    assert calls == 3
    assert [c.url for c in cands] == [url_a, "https://news.example.com/a"]  # 두 웹 검색의 같은 글은 하나로
    first = responses.calls[0].request
    assert first.headers["X-Naver-Client-Id"] == "id" and first.headers["X-Naver-Client-Secret"] == "secret"
    assert "sort=" not in first.url  # 웹문서는 정렬 파라미터 없음
    assert cands[1].source_type == "NEWS" and cands[0].source_type == "OFFICIAL_NOTICE"


@responses.activate
def test_search_auth_error(naver_env):
    responses.get(nh.NAVER_DEVELOPERS["web_url"], status=401, json={"errorMessage": "x"})
    with pytest.raises(nh.HunterError):
        nh.search_candidates("서울특별시 종로구", TODAY)


def test_search_target_names():
    assert nh.search_target("30140") == nh.SearchTarget("대전 중구", "중구", "대전")  # 중구는 여러 곳 → 시·도 약칭
    assert nh.search_target("41113").query_name == "수원시"  # 일반구 → 시 단위 공고
    assert nh.search_target("11110") == nh.SearchTarget("종로구", "종로")
    assert nh.search_target("36110").query_name == "세종"
    qs = nh.queries_for(nh.search_target("30140"), TODAY)
    assert [k for k, _, _ in qs] == ["web", "web", "news"] and "2026년 대전 중구" in qs[1][1]


def test_romanize_and_host_match():
    assert [nh.romanize(w) for w in ("종로", "중구", "고성", "수원", "신라")] == ["jongno", "junggu", "goseong", "suwon", "silla"]
    t = nh.search_target("30140")
    assert nh._host_is_region("www.djjunggu.go.kr", t) and not nh._host_is_region("www.bsjunggu.go.kr", t)
    assert nh._host_is_region("www.suwon.go.kr", nh.search_target("41113"))


def test_score_filters_region_noise_and_ranks():
    t = nh.search_target("30140")
    C = lambda title, url, kind="web", d="", snip="": nh.Candidate(title, url, nh.urlparse(url).netloc, d, kind, snip)  # noqa: E731
    cands = [
        C("2026년 소규모 노후 건축물 안전점검 신청 안내", "https://www.djjunggu.go.kr/bbs/1"),  # 지역 사이트(주소로 확인)
        C("대전 중구, 소규모 노후건축물 안전점검 실시", "https://news.example.com/articleView.html?idxno=1", d="2025-04-08"),
        C("2026년 소규모 노후 건축물 안전점검 신청 안내", "https://www.bsjunggu.go.kr/bbs/9"),  # 부산 중구 → 제외
        C("울산 중구 소규모 노후건축물 안전점검", "https://news.example.com/b", "news", "2026-05-01"),  # 다른 지역 → 제외
        C("대전 중구 건축물관리점검기관 모집 공고 노후 건축물 점검", "https://www.djjunggu.go.kr/bbs/2"),  # 점검기관 모집 → 제외
        C("2024년 대전 중구 소규모 노후건축물 점검 결과", "https://www.djjunggu.go.kr/bbs/3"),
    ]
    out = nh.score_candidates(cands, t, TODAY)
    assert [c.url for c in out] == ["https://www.djjunggu.go.kr/bbs/1", "https://news.example.com/articleView.html?idxno=1", "https://www.djjunggu.go.kr/bbs/3"]
    assert out[0].score == 4 + 3 + 2 and out[1].kind == "news"  # 웹문서에 나온 기사는 뉴스로
    assert nh.auto_pick(out) == [out[0]]


def test_file_links_not_auto_picked():
    c = nh.Candidate("2026년 노후건축물 안전점검 공고문.hwp", "https://www.suwon.go.kr/component/file/ND_fileDownload.do?id=1", "www.suwon.go.kr", "", "web", score=9)
    assert nh.is_file_link(c) and nh.auto_pick([c]) == []


@responses.activate
def test_hunt_auto_extracts_top_candidate(hub_env, monkeypatch):
    monkeypatch.setattr(nh, "load_notices", lambda: [])
    url = "https://www.djjunggu.go.kr/bbs/1"
    responses.get(nh.NAVER_HUB["web_url"], json={"items": [web_item("2026년 소규모 노후 건축물 안전점검 신청 안내", url)]})
    responses.get(nh.NAVER_HUB["news_url"], json={"items": []})
    responses.get("https://www.djjunggu.go.kr/robots.txt", status=404)
    long_page = PAGE.replace("</body>", "<p>" + "접수기간 2026. 7. 1. ~ 7. 31. 방문 또는 우편 신청. " * 5 + "</p></body>")
    responses.get(url, body=long_page, content_type="text/html; charset=utf-8")
    r = nh.hunt("30140", "대전광역시 중구", TODAY, has_ordinance=False, auto_provider=MockProvider())
    assert r.stage == "search" and r.auto and r.auto.extract is not None
    n = r.auto.extract.notice
    assert n.source_url == url and n.reviewed is False and n.sigungu_codes == ["30140"] and not r.auto.region_mismatch
    # 다른 지역을 고른 상태에서 대전 중구 글이 추출되면 불일치로 표시
    r2 = nh.hunt("11110", "서울특별시 종로구", TODAY, has_ordinance=False, auto_provider=MockProvider())
    assert r2.auto is None or r2.auto.extract is None or r2.auto.region_mismatch


@responses.activate
def test_auto_extract_skips_blocked_pages(hub_env):
    blocked = nh.Candidate("2026년 소규모 노후 건축물 안전점검 신청", "https://gu.example.go.kr/a", "gu.example.go.kr", "", "web", score=9)
    responses.get("https://gu.example.go.kr/robots.txt", body="User-agent: *\nDisallow: /\n")
    a = nh.extract_sources([blocked], MockProvider())
    assert a.extract is None and a.skipped and "robots" in a.skipped[0][1]


@responses.activate
def test_extract_sources_merges_and_caches(hub_env):
    long_page = PAGE.replace("</body>", "<p>" + "접수기간 2026. 7. 1. ~ 7. 31. 방문 또는 우편 신청. " * 5 + "</p></body>")
    urls = ["https://www.djjunggu.go.kr/bbs/1", "https://news.example.com/articleView.html?idxno=2"]
    for u in urls:
        host = nh.urlparse(u).netloc
        responses.get(f"https://{host}/robots.txt", status=404)
        responses.get(u, body=long_page, content_type="text/html; charset=utf-8")
    cands = [nh.Candidate("2026년 안전점검 신청 안내", urls[0], "www.djjunggu.go.kr", "", "web", score=9, year=2026),
             nh.Candidate("대전 중구 안전점검", urls[1], "news.example.com", "2026-03-01", "news", score=5, year=2026)]
    cache: dict = {}
    a = nh.extract_sources(cands, MockProvider(), "30140", max_sources=2, cache=cache)
    assert len(a.used) == 2 and a.merged is not None
    assert a.merged.notice.source_urls == urls and a.merged.notice.source_url == urls[0]
    assert a.extract.notice.sigungu_codes == ["30140"] and a.extract.issues[0].startswith("글 2건을 합쳤습니다")
    n_calls = len(responses.calls)
    a2 = nh.extract_sources(cands, MockProvider(), "30140", max_sources=2, cache=cache)
    assert len(responses.calls) == n_calls and len(a2.used) == 2  # 같은 글은 다시 가져오지 않음


def test_extract_sources_marks_other_region(hub_env, monkeypatch):
    cand = nh.Candidate("2026년 안전점검 신청 안내", "https://x.example.go.kr/1", "x.example.go.kr", "", "web", score=9)
    monkeypatch.setattr(nh, "fetch_page", lambda url, session=None: nh.PageContent(url, "소규모 노후건축물 안전점검 신청 " * 20, []))
    a = nh.extract_sources([cand], MockProvider(), "11110")  # 목업 결과는 대전 중구
    assert a.merged is None and a.region_mismatch and a.sources[0].status == "region_mismatch"


def test_hunt_without_keys_falls_back(monkeypatch):
    monkeypatch.setattr(nh, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    monkeypatch.setattr(nh, "load_notices", lambda: [])  # 실제 data/notices에 무엇이 있든 같은 결과
    r = nh.hunt("11110", "서울특별시 종로구", TODAY, has_ordinance=True)
    assert r.stage == "none" and "조례 기준" in r.message and "직접 올려" in r.message


def test_hunt_prefers_reviewed(monkeypatch, tmp_path):
    from core.conditions import BusinessType, NoticeConditions

    n = NoticeConditions(notice_id="x", sigungu_codes=["11110"], business_type=BusinessType.APPLY_INSPECTION, reviewed=True)
    monkeypatch.setattr(nh, "load_notices", lambda: [n])
    r = nh.hunt("11110", "서울특별시 종로구", TODAY, has_ordinance=True)
    assert r.stage == "reviewed" and r.notices == [n] and r.calls == 0


def test_hunt_searches_again_when_reviewed_notice_closed(monkeypatch):
    from core.conditions import BusinessType, NoticeConditions

    old = NoticeConditions(notice_id="x", sigungu_codes=["11110"], business_type=BusinessType.APPLY_INSPECTION, reviewed=True,
                           apply_start=date(2025, 3, 1), apply_end=date(2025, 3, 31))
    monkeypatch.setattr(nh, "load_notices", lambda: [old])
    monkeypatch.setattr(nh, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
    r = nh.hunt("11110", "서울특별시 종로구", TODAY, has_ordinance=True)
    assert r.stage == "none" and "네이버 검색" in r.message  # 마감된 공고만 있으면 새 공고를 찾으러 간다


# ---------------------------------------------------------------- 본문 가져오기


PAGE = """<html><head><script>var x=1</script><style>p{}</style></head><body>
<nav>메뉴</nav><h1>2026년 소규모 노후건축물 안전점검 신청 공고</h1>
<p>사용승인 후 30년 이상, 3층 이하</p><table><tr><td>연면적</td><td>200㎡ 미만</td></tr></table>
<a href="/files/공고문.hwpx">공고문.hwpx</a> <a href="/board/2">다음 글</a>
<a href="/download.do?id=7">신청서 내려받기</a></body></html>"""


@responses.activate
def test_fetch_page_text_and_attachments():
    responses.get("https://gu.example.go.kr/robots.txt", body="User-agent: *\nDisallow: /private\n")
    responses.get("https://gu.example.go.kr/board/1", body=PAGE, content_type="text/html; charset=utf-8")
    page = nh.fetch_page("https://gu.example.go.kr/board/1")
    assert "3층 이하" in page.text and "연면적 | 200㎡ 미만" in page.text
    assert "var x" not in page.text and "메뉴" not in page.text
    urls = [u for _, u in page.attachments]
    assert "https://gu.example.go.kr/files/공고문.hwpx" in urls and "https://gu.example.go.kr/download.do?id=7" in urls
    assert "https://gu.example.go.kr/board/2" not in urls


@responses.activate
def test_robots_disallow_is_respected():
    responses.get("https://gu.example.go.kr/robots.txt", body="User-agent: *\nDisallow: /\n")
    with pytest.raises(nh.HunterError):
        nh.fetch_page("https://gu.example.go.kr/board/1")
    assert len(responses.calls) == 1  # 본문은 요청하지 않음


@responses.activate
def test_non_html_rejected():
    responses.get("https://gu.example.go.kr/robots.txt", status=404)
    responses.get("https://gu.example.go.kr/a.pdf", body=b"%PDF", content_type="application/pdf")
    with pytest.raises(nh.HunterError):
        nh.fetch_page("https://gu.example.go.kr/a.pdf")


@responses.activate
def test_extract_from_news_candidate_is_unreviewed_with_warning():
    responses.get("https://news.example.com/robots.txt", status=404)
    responses.get("https://news.example.com/a", body=PAGE, content_type="text/html")
    cand = nh.Candidate("t", "https://news.example.com/a", "news.example.com", "2026-11-15", "news")
    page, c, r = nh.extract_from_candidate(cand, MockProvider())
    assert r.notice.reviewed is False and r.notice.source_type == "NEWS"
    assert r.issues[0].startswith("뉴스·보도자료")


# ---------------------------------------------------------------- 조례 동기화


BODY_TEXT = """제5조(소규모 노후 건축물 등 점검 대상) ① 「건축물관리법」 제15조제1항제1호에서 "조례로 정하는 규모의 건축물"이란
법 제15조제1항에 따른 건축물 중 2층 이하로서 연면적 500제곱미터 미만인 건축물을 말한다.
  제6조(소규모 노후 건축물 등 지원) ① 구청장은 법 제15조제3항에 따라 지원할 수 있다.
제6조의2(재정지원) 예산의 범위에서 지원한다."""


def test_split_articles_only_at_line_start():
    arts = sync.split_articles(BODY_TEXT)
    assert [n for n, _ in arts] == ["5", "6", "6의2"]
    assert "제15조제1항에 따른" in arts[0][1]  # 인용에서 자르지 않음
    assert arts[1][1].startswith("제6조(")


def test_find_records_and_articles_regardless_of_envelope():
    payload = {"자치법규": {"기본정보": {"자치법규ID": "123"}, "조문": {"조": [
        {"조문번호": "000500", "조문여부": "Y", "조제목": "소규모 노후 건축물 등 점검 대상", "조내용": "제5조(…) ① 2층 이하"},
        {"조문번호": "000600", "조문여부": "Y", "조제목": "소규모 노후 건축물 등 지원", "조내용": "제6조(…)"},
        {"조문번호": "000100", "조문여부": "N", "조제목": "제1장 총칙", "조내용": ""},
    ]}}}
    arts = sync.articles_from_body(payload)
    assert len(arts) == 3
    a = sync.inspection_article(arts)
    assert a.number == "000500" and a.row_text.startswith("000500Y소규모 노후 건축물 등 점검 대상제5조")
    assert sync.find_records(payload, "자치법규ID")[0]["자치법규ID"] == "123"


def raw(j, n, d, body="000500Y소규모 노후 건축물 등 점검 대상제5조(…) 2층 이하로서 연면적 500제곱미터 미만인 건축물을 말한다."):
    return {"지자체": j, "조례명": n, "시행일": d, "조문": body}


def test_detect_changes():
    rows = [raw("서울특별시 종로구", "서울특별시 종로구 건축물관리 조례", "20251121"),
            raw("서울특별시 중구", "서울특별시 중구 건축물관리 조례", "20240101"),
            raw("경상북도 포항시", "포항시 건축물관리 조례", "20260708"),
            raw("경상북도 포항시", "포항시 건축물관리 조례", "20260708", "000602Y지원…")]
    listed = [
        {"지자체기관명": "서울특별시 종로구", "자치법규명": "서울특별시 종로구 건축물관리 조례", "시행일자": "20261201", "자치법규ID": 1, "자치법규일련번호": 9},
        {"지자체기관명": "서울특별시 중구", "자치법규명": "서울특별시 중구 건축물관리 조례", "시행일자": "20240101", "자치법규ID": 2, "자치법규일련번호": 8},
        {"지자체기관명": "서울특별시 강남구", "자치법규명": "서울특별시 강남구 건축 조례", "시행일자": "20260101", "자치법규ID": 3, "자치법규일련번호": 7},
    ]
    cs = sync.detect_changes(rows, listed)
    assert [(c.jurisdiction, c.old_date, c.new_date, c.ordin_id) for c in cs.changes] == [("서울특별시 종로구", "20251121", "20261201", "1")]
    assert cs.unchanged == 1
    assert cs.missing == [("경상북도포항시", "포항시건축물관리조례")]


def test_proposal_diff_and_apply():
    import classify_ordinances

    rows = [raw("서울특별시 중구", "서울특별시 중구 건축물관리 조례", "20240101"),
            raw("서울특별시 중구", "서울특별시 중구 건축물관리 조례", "20240101", "000602Y소규모 노후 건축물등 지원제5조의2(…) 지원할 수 있다.")]
    ch = sync.Change("updated", "서울특별시 중구", "서울특별시 중구 건축물관리 조례", "20240101", "20261201", "2", "8")
    arts = [sync.Article("000500", "Y", "소규모 노후 건축물 등 점검 대상",
                         "제5조(…) “조례로 정하는 규모의 건축물”이란 3층 이하로서 연면적 1천제곱미터 이하인 건축물을 말한다.")]
    prop = sync.proposal_rows(ch, arts)
    old = classify_ordinances.classify(rows[0])
    new = classify_ordinances.classify(prop[0])
    diffs = dict((f, (o, n)) for f, o, n in sync.diff_classified(old, new))
    assert diffs["max_floors"] == ("2", "3") and diffs["max_area"] == ("500", "1000") and diffs["area_bound"] == ("LT", "LE")
    is_insp = lambda r: classify_ordinances.classify(r)["row_role"] == "점검대상"  # noqa: E731
    updated, n = sync.apply_proposal(rows, prop, is_insp)
    assert n == 1 and len(updated) == 2
    assert updated[0]["시행일"] == "20261201" and "3층 이하" in updated[0]["조문"]
    assert updated[1] == rows[1]  # 지원 조문 행은 그대로


def test_proposal_without_inspection_article_is_empty():
    ch = sync.Change("updated", "a", "b", "1", "2", "3", "4")
    assert sync.proposal_rows(ch, [sync.Article("000700", "Y", "소규모 노후 건축물 등 지원", "…")]) == []


def test_real_response_shapes_list_number_and_regular_inspection_article():
    # 실제 응답(2026-11-15): 조문번호가 목록, 종로구 조례는 '정기점검 대상' 조문이 앞에 있다
    payload = {"LawService": {"자치법규기본정보": {"자치법규ID": "2203792"}, "조문": {"조": [
        {"조문번호": ["000300", "000300"], "조문여부": "Y", "조제목": "정기점검 대상", "조내용": "제3조(정기점검 대상) …"},
        {"조문번호": ["000500", "000500"], "조문여부": "Y", "조제목": "소규모 노후 건축물 등 점검 대상", "조내용": "제5조(…) ①"},
    ]}}}
    a = sync.inspection_article(sync.articles_from_body(payload))
    assert a.number == "000500" and a.row_text.startswith("000500Y소규모 노후 건축물 등 점검 대상제5조")
    envelope = {"OrdinSearch": {"law": [{"자치법규ID": "1", "자치법규명": "a"}], "totalCnt": "1"}}
    assert sync.find_records(envelope, "자치법규ID") == [{"자치법규ID": "1", "자치법규명": "a"}]
