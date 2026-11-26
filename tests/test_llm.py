"""단계 6: LLM 공급자 추상화·전처리·분류/추출·사용량 로그·안내문 틀 (외부 호출 없음)."""

import csv
import io

import zipfile
from datetime import date
from types import SimpleNamespace

import pytest

import compare_providers
from core import letters
from core.conditions import BusinessType
from core.llm import extractor, factory, usage_log
from core.llm.base import LLMError, LLMRefusalError, LLMValidationError, Part, Usage, strict_json_schema
from core.llm.claude import ClaudeProvider
from core.llm.gemini import GeminiProvider
from core.llm.mock import MockProvider, sample_extract
from core.llm.preprocess import MAX_FILE_BYTES, UploadedFile, read_hwpx, to_parts
from core.llm.schemas import ClassifyOut, ExtractOut, IntField, ListField, StrField
from core.notices import load_notices, notices_for, save_notice

ADDRESS = "서울특별시 종로구 청운동 2번지"
BODY = "소규모 노후 건축물 안전점검 신청 안내 본문입니다. " * 10


# ---------------------------------------------------------------- 도우미


def hwpx_bytes(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    hp = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    ps = "".join(f"<hp:p><hp:run><hp:t>{t}</hp:t></hp:run></hp:p>" for t in paragraphs)
    if table:
        rows = "".join(
            "<hp:tr>" + "".join(f"<hp:tc><hp:subList><hp:p><hp:run><hp:t>{c}</hp:t></hp:run></hp:p></hp:subList></hp:tc>" for c in r) + "</hp:tr>"
            for r in table
        )
        ps += f"<hp:p><hp:run><hp:tbl>{rows}</hp:tbl></hp:run></hp:p>"
    xml = f'<?xml version="1.0" encoding="UTF-8"?><hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" {hp}>{ps}</hs:sec>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/section0.xml", xml)
    return buf.getvalue()


class FakeProvider:
    """미리 정한 원문 JSON/텍스트를 차례로 돌려주는 가짜 공급자(실제 공급자처럼 name != mock)."""

    def __init__(self, json_outputs=(), text_outputs=(), name="fake"):
        self.name, self.model = name, "fake-model"
        self.json_outputs, self.text_outputs = list(json_outputs), list(text_outputs)
        self.seen_parts: list[list[Part]] = []
        self.seen_prompts: list[str] = []

    def generate_json(self, system, parts, schema):
        from core.llm.base import parse_with_retry

        self.seen_parts.append(parts)
        usage = Usage(self.name, self.model)

        def call(hint):
            return self.json_outputs.pop(0), 100, 20

        return parse_with_retry(call, schema, usage), usage

    def generate_text(self, system, prompt):
        self.seen_prompts.append(prompt)
        out = self.text_outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out, Usage(self.name, self.model, 50, 30)


@pytest.fixture
def usage_file(tmp_path, monkeypatch):
    path = tmp_path / "llm_usage.csv"
    monkeypatch.setenv("LLM_USAGE_LOG", str(path))
    return path


# ---------------------------------------------------------------- 스키마·팩토리


def test_strict_schema_has_no_open_objects():
    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                assert n.get("additionalProperties") is False
                assert set(n["required"]) == set(n["properties"])
            assert "$ref" not in n and "default" not in n and "title" not in n
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(strict_json_schema(ExtractOut))
    walk(strict_json_schema(ClassifyOut))


@pytest.mark.parametrize(
    "env, reason_part",
    [
        ({"LLM_PROVIDER": "mock"}, "LLM_PROVIDER=mock"),
        ({"LLM_PROVIDER": "claude", "LLM_MODEL_EXTRACT": "m"}, "ANTHROPIC_API_KEY"),
        ({"LLM_PROVIDER": "claude", "ANTHROPIC_API_KEY": "k"}, "LLM_MODEL_EXTRACT"),
        ({"LLM_PROVIDER": "gemini", "LLM_MODEL_EXTRACT": "m"}, "GEMINI_API_KEY"),
    ],
)
def test_factory_falls_back_to_mock(monkeypatch, env, reason_part):
    monkeypatch.setattr(factory, "load_dotenv", lambda *a, **k: None)
    for k in ("LLM_PROVIDER", "LLM_MODEL_EXTRACT", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    st = factory.status("extract")
    assert st.is_mock and reason_part in st.reason
    assert isinstance(factory.get_provider("extract"), MockProvider)


def test_factory_builds_real_provider_without_calling(monkeypatch):
    monkeypatch.setattr(factory, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # 실제 .env 값이 섞이지 않게
    monkeypatch.delenv("LLM_MODEL_FALLBACK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("LLM_MODEL_EXTRACT", "claude-opus-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    p = factory.get_provider("extract")
    assert isinstance(p, ClaudeProvider) and p.model == "claude-opus-5-5"
    assert factory.available_providers() == ["claude"]


# ---------------------------------------------------------------- 재시도·검증


def test_validation_retry_once_then_success():
    good = ClassifyOut(business_type="APPLY_INSPECTION", evidence="x", has_repair_section=False, confidence="high").model_dump_json()
    p = FakeProvider(json_outputs=['{"business_type": "모름"}', good])
    out, usage = p.generate_json("s", [Part.of_text("t")], ClassifyOut)
    assert out.business_type == "APPLY_INSPECTION" and usage.retries == 1
    assert (usage.input_tokens, usage.output_tokens) == (200, 40)


def test_validation_fails_twice_raises_with_raw():
    p = FakeProvider(json_outputs=["{}", '{"bad": 1}'])
    with pytest.raises(LLMValidationError) as e:
        p.generate_json("s", [Part.of_text("t")], ClassifyOut)
    assert e.value.raw == '{"bad": 1}'


# ---------------------------------------------------------------- Claude·Gemini 요청 모양(가짜 클라이언트)


class FakeAnthropic:
    def __init__(self, responses):
        self.calls = []
        outer = self

        class Msgs:
            def create(self, **kw):
                outer.calls.append(kw)
                return responses.pop(0)

        self.messages = Msgs()
        self.beta = SimpleNamespace(messages=Msgs())


def claude_resp(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def test_claude_request_shape_and_fallbacks(monkeypatch):
    monkeypatch.delenv("LLM_CLAUDE_FALLBACKS", raising=False)
    good = ClassifyOut(business_type="REPAIR_SUPPORT", evidence="보수비 지원", has_repair_section=True, confidence="high").model_dump_json()
    client = FakeAnthropic([claude_resp(good)])
    p = ClaudeProvider("claude-opus-5-5", client=client)
    parts = [Part.of_text("[파일: a.pdf]"), Part.of_bytes(b"%PDF-1.4", "application/pdf"), Part.of_bytes(b"\x89PNG", "image/png")]
    out, usage = p.generate_json("sys", parts, ClassifyOut)
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    types_ = [b["type"] for b in kw["messages"][0]["content"]]
    assert types_ == ["text", "document", "image"]  # 조각 순서 유지([파일: 이름] → 그 파일)
    assert "thinking" not in kw and kw["output_config"]["effort"] == "low"  # Opus 5.5는 끌 수 없어 effort를 낮춘다
    assert out.business_type == "REPAIR_SUPPORT" and usage.input_tokens == 1000


def claude_resp_cache(text, read=0, write=0):
    r = claude_resp(text)
    r.usage = SimpleNamespace(input_tokens=50, output_tokens=20, cache_read_input_tokens=read, cache_creation_input_tokens=write)
    return r


def test_claude_haiku_no_thinking_and_cache_breakpoints(monkeypatch):
    monkeypatch.delenv("LLM_THINKING", raising=False)
    monkeypatch.delenv("LLM_EFFORT", raising=False)
    good = ClassifyOut(business_type="APPLY_INSPECTION", evidence="x", has_repair_section=False, confidence="high").model_dump_json()
    client = FakeAnthropic([claude_resp_cache(good, read=3000, write=10)])
    p = ClaudeProvider("Claude-Haiku-5.5", client=client)  # .env에 사람이 쓴 이름도 받는다
    parts = [Part.of_text("[파일: a.hwpx]\n본문"), Part.of_text("[파일: b.png]"), Part.of_bytes(b"\x89PNG", "image/png"), Part.of_text("[작업] 분류")]
    _, usage = p.generate_json("sys", parts, ClassifyOut)
    kw = client.calls[0]
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["thinking"] == {"type": "disabled"}  # 사고 토큰을 내지 않는다
    assert "fallbacks" not in kw  # Haiku 5.5는 서버 측 폴백이 없다
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    content = kw["messages"][0]["content"]
    assert [i for i, b in enumerate(content) if "cache_control" in b] == [2]  # 본문 마지막(작업 지시 바로 앞)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (3000, 10)


def test_claude_sonnet_55_thinking_off_uses_between_tools(monkeypatch):
    monkeypatch.delenv("LLM_THINKING", raising=False)
    monkeypatch.delenv("LLM_CLAUDE_FALLBACKS", raising=False)
    client = FakeAnthropic([claude_resp("틀")])
    ClaudeProvider("claude-sonnet-5-5", client=client).generate_text("s", "p")
    kw = client.calls[0]
    assert kw["thinking"] == {"type": "between_tools"} and kw["fallbacks"] == "default"


@pytest.mark.parametrize("raw, api", [("Claude-Haiku-5.5", "claude-haiku-5-5"), ("claude sonnet 5.5", "claude-sonnet-5-5"), ("claude-opus-5-5", "claude-opus-5-5")])
def test_normalize_model(raw, api):
    from core.llm.claude import normalize_model

    assert normalize_model(raw) == api


class ScriptedProvider:
    """정해진 결과나 예외를 차례로 돌려주는 가짜 공급자."""

    name = "claude"

    def __init__(self, model, results):
        self.model, self.results, self.calls = model, list(results), 0

    def generate_json(self, system, parts, schema):
        self.calls += 1
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r, Usage(self.name, self.model, 100, 10)

    def generate_text(self, system, prompt):
        return self.generate_json(system, [], None)


def _cls(conf):
    return ClassifyOut(business_type="APPLY_INSPECTION", evidence="x", has_repair_section=False, confidence=conf)


def test_fallback_on_low_confidence():
    from core.llm.fallback import FallbackProvider

    a, b = ScriptedProvider("claude-haiku-5-5", [_cls("low")]), ScriptedProvider("claude-sonnet-5-5", [_cls("high")])
    out, usage = FallbackProvider(a, b).generate_json("s", [], ClassifyOut)
    assert out.confidence == "high" and usage.model == "claude-sonnet-5-5"
    assert [u.model for u in usage.all()] == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert "확신 낮음" in usage.note and usage.total_input == 200


def test_fallback_not_used_when_confident():
    from core.llm.fallback import FallbackProvider

    a, b = ScriptedProvider("h", [_cls("medium")]), ScriptedProvider("s", [])
    out, usage = FallbackProvider(a, b).generate_json("s", [], ClassifyOut)
    assert b.calls == 0 and usage.model == "h" and not usage.note


def test_fallback_on_error_but_not_on_auth():
    from core.llm.claude import LLMAuthError
    from core.llm.fallback import FallbackProvider

    a, b = ScriptedProvider("h", [LLMRefusalError("거절")]), ScriptedProvider("s", [_cls("high")])
    out, usage = FallbackProvider(a, b).generate_json("s", [], ClassifyOut)
    assert usage.model == "s" and "거절" in usage.note
    a2, b2 = ScriptedProvider("h", [LLMAuthError("키")]), ScriptedProvider("s", [_cls("high")])
    with pytest.raises(LLMAuthError):
        FallbackProvider(a2, b2).generate_json("s", [], ClassifyOut)
    assert b2.calls == 0


def test_extract_fallback_reason_when_core_conditions_missing():
    from core.llm.mock import sample_extract

    out = sample_extract()
    assert out.fallback_reason() is None
    empty = out.model_copy(update={
        "min_years": IntField(value=None, evidence=None), "built_before_year": IntField(value=None, evidence=None),
        "floors_text": StrField(value=None, evidence=None), "area_text": StrField(value=None, evidence=None),
        "use_conditions": ListField(value=[], evidence=None), "structure_conditions": ListField(value=[], evidence=None),
    })
    assert "핵심 조건" in empty.fallback_reason()


def test_factory_wraps_fallback_model(monkeypatch):
    from core.llm.fallback import FallbackProvider

    monkeypatch.setattr(factory, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL_EXTRACT", "Claude-Haiku-5.5")
    monkeypatch.setenv("LLM_MODEL_FALLBACK", "claude-sonnet-5-5")
    p = factory.get_provider("extract")
    assert isinstance(p, FallbackProvider) and p.primary.model == "claude-haiku-5-5" and p.fallback.model == "claude-sonnet-5-5"


def test_claude_fallbacks_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("LLM_CLAUDE_FALLBACKS", "off")
    client = FakeAnthropic([claude_resp("안내문")])
    p = ClaudeProvider("m", client=client)
    text, _ = p.generate_text("s", "p")
    assert text == "안내문" and "fallbacks" not in client.calls[0]


def test_claude_refusal():
    p = ClaudeProvider("m", client=FakeAnthropic([claude_resp("", stop="refusal")]))
    with pytest.raises(LLMRefusalError):
        p.generate_text("s", "p")


def test_claude_needs_model():
    with pytest.raises(LLMError):
        ClaudeProvider("", client=FakeAnthropic([]))


def test_gemini_request_shape():
    seen = {}

    def generate_content(model, contents, config):
        seen.update(model=model, contents=contents, config=config)
        good = ClassifyOut(business_type="APPLY_INSPECTION", evidence=None, has_repair_section=False, confidence="high").model_dump_json()
        return SimpleNamespace(text=good, candidates=[SimpleNamespace(finish_reason="STOP")],
                               usage_metadata=SimpleNamespace(prompt_token_count=900, candidates_token_count=100))

    client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    p = GeminiProvider("gemini-test", client=client)
    out, usage = p.generate_json("sys", [Part.of_bytes(b"%PDF", "application/pdf"), Part.of_text("t")], ClassifyOut)
    assert seen["model"] == "gemini-test"
    assert seen["config"].response_mime_type == "application/json" and seen["config"].response_json_schema
    assert len(seen["contents"][0].parts) == 2
    assert out.business_type == "APPLY_INSPECTION" and (usage.input_tokens, usage.output_tokens) == (900, 100)


# ---------------------------------------------------------------- 전처리


def test_hwpx_tables_become_markdown():
    text = read_hwpx(hwpx_bytes(["소규모 노후건축물 안전점검 지원 공고"], [["구분", "기준"], ["층수", "3층 이하"], ["연면적", "200㎡ 미만"]]))
    assert "소규모 노후건축물 안전점검 지원 공고" in text
    assert "| 구분 | 기준 |" in text and "| 층수 | 3층 이하 |" in text and "| 연면적 | 200㎡ 미만 |" in text
    assert text.count("3층 이하") == 1  # 표 내용이 문단으로 한 번 더 나오지 않음


def png_bytes(w=400, h=300, fmt="PNG", color="white") -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h), color)
    ImageDraw.Draw(im).text((10, 10), "3층 이하", fill="black")
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def pdf_bytes(text_pages: list[str], blank_pages: int = 0, table: list[list[str]] | None = None) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for t in text_pages:
        page = doc.new_page()
        y = 72
        for line in t.split("\n"):
            page.insert_text((72, y), line, fontsize=10)
            y += 14
        if table:
            x0, y0, cw, rh = 72, y + 20, 120, 20
            for r, row in enumerate(table):
                for c, val in enumerate(row):
                    rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
                    page.draw_rect(rect, color=(0, 0, 0), width=0.8)
                    page.insert_text((rect.x0 + 4, rect.y1 - 6), val, fontsize=9)
    for _ in range(blank_pages):
        doc.new_page().draw_rect(pymupdf.Rect(50, 50, 300, 300), color=(0, 0, 0), fill=(0.5, 0.5, 0.5))
    return doc.tobytes()


def test_to_parts_bundle_and_warnings():
    files = [
        UploadedFile("공고.hwpx", hwpx_bytes([BODY])),
        UploadedFile("앞면.png", png_bytes()),
        UploadedFile("뒷면.JPG", png_bytes(fmt="JPEG")),
        UploadedFile("본문.pdf", pdf_bytes(["Safety inspection notice for small old buildings.\n" * 3])),
        UploadedFile("짧은.hwpx", hwpx_bytes(["짧음"])),
        UploadedFile("큰.pdf", b"0" * (MAX_FILE_BYTES + 1)),
        UploadedFile("메모.csv", b"x"),
        UploadedFile("깨진.hwp", b"not an ole file"),
    ]
    pre = to_parts(files)
    sent = [p for p in pre.parts if p.data is not None]
    assert [p.mime for p in sent] == ["image/png", "image/png"]  # 사진은 회색조 PNG로, PDF는 텍스트로
    assert any(p.text and "Safety inspection" in p.text for p in pre.parts)
    assert len({p.name for p in pre.parts}) == 4  # 한 요청에 4개 파일 모두
    assert len(pre.skipped) == 4
    assert any("8MB" in s for s in pre.skipped) and any("글자가" in s for s in pre.skipped)
    assert any("4개를 넣지 못했습니다" in w for w in pre.warnings)


def test_image_grayscale_resize_and_tiles():
    from PIL import Image

    from core.llm.preprocess import IMAGE_MAX_EDGE, IMAGE_MAX_PIXELS, prepare_image

    tiles = prepare_image(png_bytes(3000, 9000, "JPEG", color="#f0e0a0"))  # 세로로 긴 컬러 리플릿
    ims = [Image.open(io.BytesIO(t)) for t in tiles]
    assert len(ims) > 1 and all(i.mode == "L" for i in ims)
    assert all(i.width == IMAGE_MAX_EDGE and i.height <= IMAGE_MAX_EDGE and i.width * i.height <= IMAGE_MAX_PIXELS for i in ims)
    small = Image.open(io.BytesIO(prepare_image(png_bytes(400, 300))[0]))
    assert small.size == (400, 300)  # 작은 이미지는 키우지 않는다


def test_pdf_text_table_and_scanned_page():
    from core.llm.preprocess import read_pdf

    r = read_pdf(pdf_bytes(["Notice body line one.\nNotice body line two."], blank_pages=1,
                           table=[["Item", "Condition"], ["Floors", "3 or less"], ["Area", "under 200"]]))
    assert "Notice body line one." in r.text and "| Floors | 3 or less |" in r.text
    assert r.scanned_pages == [2] and r.images and r.images[0][0] == 2


def test_docx_and_xlsx_to_markdown():
    import docx
    import openpyxl

    from core.llm.preprocess import read_docx, read_xlsx

    d = docx.Document()
    d.add_heading("안전점검 지원 공고", 1)
    d.add_paragraph("사용승인 후 30년 이상")
    t = d.add_table(rows=2, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "층수", "연면적"
    t.rows[1].cells[0].text, t.rows[1].cells[1].text = "3층 이하", "200㎡ 미만"
    b = io.BytesIO()
    d.save(b)
    text = read_docx(b.getvalue())
    assert text.startswith("# 안전점검 지원 공고") and "| 3층 이하 | 200㎡ 미만 |" in text

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "조건"
    ws.append(["항목", "값", None])
    ws.append([None, None, None])
    ws.append(["경과연수", "30년 이상", None])
    xb = io.BytesIO()
    wb.save(xb)
    x = read_xlsx(xb.getvalue())
    assert "## 시트: 조건" in x and "| 경과연수 | 30년 이상 |" in x and "|  |" not in x


def test_usage_log_upgrades_old_header(usage_file):
    old = ["timestamp", "provider", "model", "task", "input_tokens", "output_tokens", "retries", "doc_hash"]
    with open(usage_file, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(old)
        w.writerow(["2026-11-01T00:00:00", "gemini", "g", "extract", 10, 5, 0, "0" * 16])
    u = Usage("claude", "claude-sonnet-5-5", 10, 5, cache_read_tokens=900, note="폴백")
    u.previous = [Usage("claude", "claude-haiku-5-5", 20, 3)]
    usage_log.record(u, "extract", "a" * 16)
    rows = list(csv.DictReader(open(usage_file, encoding="utf-8")))
    assert list(rows[0]) == usage_log.FIELDS and len(rows) == 3
    assert [r["model"] for r in rows[1:]] == ["claude-haiku-5-5", "claude-sonnet-5-5"] and rows[2]["cache_read_tokens"] == "900"


# ---------------------------------------------------------------- 분류·추출 변환


def test_to_notice_conversion_rules():
    out = sample_extract().model_copy(deep=True)
    out.floors_text.value = "2층 미만"
    out.area_text.value = "연면적 5백제곱미터 미만"
    out.local_gov.value = "없는시 없는구"
    out.apply_start.value, out.apply_end.value = "2026-03-02", "2026-02-30"
    out.selection_method.value = "추첨"
    out.source_type = "NEWS"
    r = extractor.to_notice(out, "t")
    n = r.notice
    assert (n.max_floors, n.floors_bound, n.max_area, n.area_bound) == (2, "LT", 500.0, "LT")
    assert n.sigungu_codes == [] and any("시군구코드" in i for i in r.issues)
    assert n.apply_start == date(2026, 3, 2) and n.apply_end is None and any("없는 날짜" in i for i in r.issues)
    assert n.selection_method == "기타"
    assert any("원문 공고를 확인" in i for i in r.issues)
    assert n.reviewed is False and n.business_type == BusinessType.APPLY_INSPECTION
    assert n.evidence["max_floors"] == "3층 이하"  # 원문 근거는 그대로


def test_to_notice_built_before_year():
    out = sample_extract().model_copy(deep=True)
    out.min_years.value = None
    out.built_before_year.value = 1994
    out.built_before_inclusive.value = None
    n = extractor.to_notice(out, "t").notice
    assert (n.years_basis, n.built_before_year, n.built_before_inclusive) == ("BUILT_BEFORE_YEAR", 1994, None)


def test_floor_text_without_bound_is_not_guessed():
    out = sample_extract().model_copy(deep=True)
    out.floors_text.value = "3층"
    r = extractor.to_notice(out, "t")
    assert r.notice.max_floors is None and any("이하/미만" in i for i in r.issues)


def test_classify_non_apply_stops(usage_file):
    p = FakeProvider(json_outputs=[ClassifyOut(business_type="REPAIR_SUPPORT", evidence="보수비 지원", has_repair_section=False, confidence="high").model_dump_json()])
    c = extractor.classify([Part.of_text(BODY)], p)
    assert not c.is_apply_inspection and c.evidence == "보수비 지원"


# ---------------------------------------------------------------- 사용량 로그: 본문·주소 없음


def test_usage_log_has_no_body_or_address(usage_file):
    parts = [Part.of_text(f"[파일: 공고.hwpx]\n{BODY}\n접수처 {ADDRESS}")]
    p = FakeProvider(json_outputs=[
        ClassifyOut(business_type="APPLY_INSPECTION", evidence=ADDRESS, has_repair_section=False, confidence="high").model_dump_json(),
        sample_extract().model_dump_json(),
    ], text_outputs=[letters.BUILTIN["official"]])
    extractor.classify(parts, p)
    extractor.extract(parts, p)
    n = extractor.to_notice(sample_extract(), "t").notice
    letters.make_template_llm(n, "official", p)
    raw = usage_file.read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(raw)))
    assert [r["task"] for r in rows] == ["classify", "extract", "template"]
    assert list(rows[0]) == usage_log.FIELDS
    for bad in (ADDRESS, "청운동", "안전점검", "공고.hwpx", "건축물", "접수"):
        assert bad not in raw
    assert all(len(r["doc_hash"]) == 16 for r in rows)


def test_usage_log_rejects_non_hash(usage_file):
    with pytest.raises(ValueError):
        usage_log.record(Usage("x", "m"), "extract", ADDRESS)


def test_mock_provider_is_not_logged(usage_file):
    extractor.extract([Part.of_text(BODY)], MockProvider())
    assert not usage_file.exists()


# ---------------------------------------------------------------- 안내문 틀 (LLM)


def test_template_llm_valid_used_and_input_has_no_building_data():
    n = extractor.to_notice(sample_extract(), "t").notice
    p = FakeProvider(text_outputs=[letters.BUILTIN["elder"]])
    text, warns, from_llm = letters.make_template_llm(n, "elder", p)
    assert from_llm and warns == [] and text == letters.BUILTIN["elder"].strip()
    prompt = p.seen_prompts[0]
    assert "2026. 7. 1." in prompt and "중구청 건축과" in prompt  # 공고 조건만
    for bad in ("청운동", "사용승인", "연면적 1"):
        assert bad not in prompt


@pytest.mark.parametrize(
    "llm_text",
    ["{지번주소} 건물은 연면적 200㎡ 이하라 선정되었습니다. {판정안내}\n" + letters.REVIEW_LINE, LLMError("타임아웃")],
)
def test_template_llm_invalid_falls_back(llm_text):
    p = FakeProvider(text_outputs=[llm_text])
    text, warns, from_llm = letters.make_template_llm(None, "official", p)
    assert text == letters.BUILTIN["official"] and not from_llm and warns


# ---------------------------------------------------------------- 탭1 흐름(mock) → 저장 → 탭2 목록


def test_mock_upload_extract_review_save_flow(tmp_path):
    pre = to_parts([UploadedFile("공고.hwpx", hwpx_bytes([BODY]))])
    p = MockProvider()
    c = extractor.classify(pre.parts, p)
    assert c.is_apply_inspection
    r = extractor.extract(pre.parts, p)
    reviewed = r.notice.model_copy(update={"reviewed": True})
    save_notice(reviewed, directory=tmp_path)
    with pytest.raises(FileExistsError):
        save_notice(reviewed, directory=tmp_path)
    loaded = load_notices(tmp_path)
    assert loaded[0].reviewed and notices_for("30140", loaded)[0].notice_id == reviewed.notice_id


# ---------------------------------------------------------------- 공급자 비교 스크립트(mock)


def test_compare_providers_with_mock(tmp_path):
    case = tmp_path / "daejeon"
    case.mkdir()
    (case / "공고.hwpx").write_bytes(hwpx_bytes([BODY]))
    truth = extractor.to_notice(sample_extract(), "daejeon").notice
    (case / "truth.json").write_text(truth.model_dump_json(), encoding="utf-8")
    s = compare_providers.run(MockProvider(), [case])
    assert s.cases == 1 and s.classify_ok == 1 and s.matched == s.compared == len(compare_providers.COMPARE_FIELDS)
    assert dict(s.by_format) == {"hwpx": [s.compared, s.compared]}


# ---------------------------------------------------------------- 실제 공급자 (기본 skip)


@pytest.mark.live
def test_live_provider_extract(usage_file):
    st = factory.status("extract")
    if st.is_mock:
        pytest.skip(st.reason)
    pre = to_parts([UploadedFile("공고.hwpx", hwpx_bytes([BODY], [["구분", "기준"], ["층수", "3층 이하"], ["연면적", "200㎡ 미만"]]))])
    r = extractor.extract(pre.parts, factory.get_provider("extract"))
    assert r.notice.max_floors == 3 and r.notice.area_bound == "LT"
    rows = list(csv.DictReader(io.StringIO(usage_file.read_text(encoding="utf-8"))))
    assert rows[-1]["task"] == "extract" and int(rows[-1]["input_tokens"]) > 0


def test_gemini_retries_503_and_timeout(monkeypatch):
    from google.genai import errors as genai_errors
    from tenacity import wait_none

    import core.llm.gemini as gem

    monkeypatch.setattr(gem, "RETRY_WAIT", wait_none())

    class ReadTimeout(Exception):  # httpx 계열 시간 초과와 같은 이름 규칙
        pass

    good = ClassifyOut(business_type="APPLY_INSPECTION", evidence=None, has_repair_section=False, confidence="high").model_dump_json()
    seq = [genai_errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}), ReadTimeout("slow")]

    def generate_content(model, contents, config):
        if seq:
            raise seq.pop(0)
        return SimpleNamespace(text=good, candidates=[SimpleNamespace(finish_reason="STOP")],
                               usage_metadata=SimpleNamespace(prompt_token_count=1, candidates_token_count=1))

    p = GeminiProvider("m", client=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    out, _ = p.generate_json("s", [Part.of_text("t")], ClassifyOut)
    assert out.business_type == "APPLY_INSPECTION" and seq == []


def test_gemini_404_hint():
    from google.genai import errors as genai_errors

    def generate_content(model, contents, config):
        raise genai_errors.ClientError(404, {"error": {"code": 404, "message": "not found", "status": "NOT_FOUND"}})

    p = GeminiProvider("없는모델", client=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    with pytest.raises(LLMError) as e:
        p.generate_text("s", "p")
    assert "모델명" in str(e.value)


def test_compact_schema_small_and_round_trip():
    import json

    from core.llm.base import compact_json_schema, from_compact
    from core.llm.schemas import AnalyzeOut

    s = json.dumps(compact_json_schema(AnalyzeOut))
    assert "anyOf" not in s and '"null"' not in s  # Claude 한도: null 허용 16개
    wire = {k: v["value"] if isinstance(v, dict) and set(v) == {"value", "evidence"} else v
            for k, v in json.loads(sample_extract().model_dump_json()).items()}
    wire = {k: ("" if v is None and k not in ("min_years", "built_before_year") else v) for k, v in wire.items()}
    wire.update(min_years=30, built_before_year=0, built_before_inclusive="unknown", floors_include_underground="false",
                collective_excluded="true", multi_housing_excluded="unknown",
                business_type="APPLY_INSPECTION", business_evidence="", has_repair_section=False,
                evidence=[{"field": "min_years", "quote": "30년 경과"}, {"field": "min_years", "quote": "사용승인 후"}, {"field": "bogus", "quote": "x"}])
    out = AnalyzeOut.model_validate_json(from_compact(json.dumps(wire, ensure_ascii=False), AnalyzeOut))
    assert out.min_years.value == 30 and out.min_years.evidence == "30년 경과 / 사용승인 후"
    assert out.built_before_year.value is None and out.built_before_inclusive.value is None
    assert out.floors_include_underground.value is False and out.collective_excluded.value is True
    assert out.notice_date.value is None and out.notice_date.evidence is None and out.business_evidence is None


def test_analyze_one_call_and_non_inspection():
    from core.llm.schemas import AnalyzeOut

    c, r = extractor.analyze([Part.of_text("[파일: a]\n본문")], MockProvider(), log=False)
    assert c.is_apply_inspection and r.notice.min_years == 30
    other = AnalyzeOut(business_type="REPAIR_SUPPORT", business_evidence="보수비", has_repair_section=True,
                       **sample_extract().model_dump())
    c2, r2 = extractor.analyze([Part.of_text("t")], ScriptedProvider("h", [other]), log=False)
    assert r2 is None and c2.business_type == BusinessType.REPAIR_SUPPORT and other.fallback_reason() is None


def test_to_notice_keeps_unparsed_use_and_structure():
    out = sample_extract().model_copy(deep=True)
    out.use_conditions.value = ["다중이용업소"]
    out.structure_conditions.value = ["석조"]
    r = extractor.to_notice(out, "t")
    # 사라지면 모든 건물이 통과한다 → 원문으로 남겨 판정에서 확인필요가 되게
    assert r.notice.use_filter == ["다중이용업소"] and r.notice.structure_filter == ["석조"]
    assert any("다중이용업소" in i for i in r.issues)
