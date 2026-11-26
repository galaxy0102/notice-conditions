# 소규모 노후건축물 안전점검 지원 대상 선별 시스템

구청 건축안전센터 실무자가 지자체 공고문·조례의 지원 조건을 건축물대장과 대조해
안전점검 지원 **자격 후보** 건물을 찾고, 소유자 안내문 초안을 만드는 도구입니다.
결과는 "선정 대상"이 아니라 "자격 후보"이며, 최종 선정은 실무자·위원회가 합니다.

> **개발 지식 없이 써 보려면** [처음_사용하는_분께.txt](처음_사용하는_분께.txt)를 보세요.
> `시작하기(Mac).command` / `시작하기(Windows).bat` 더블클릭으로 설치·실행됩니다.

- 공통 규칙: [.cursor/rules/project.mdc](.cursor/rules/project.mdc)
- 단계별 작업 계획: [docs/cursor_prompts/](docs/cursor_prompts/)
- 기존 코드 조사 보고: [docs/legacy_inventory.md](docs/legacy_inventory.md)

## 현재 상태 (단계 7까지)

| 영역 | 상태 |
|---|---|
| `core/notice_hunter.py` | 공고 찾기: 검수된 공고(마감 전) → 네이버 검색(웹문서·뉴스, 관련도 점수) → 올해 공고면 바로 본문 추출(미검수, 원문 주소 보관). robots.txt 존중, 첨부는 링크만 |
| `core/ordinance_sync.py`, `scripts/sync_ordinances.py` | 법제처 자치법규 변경 감지 → 제안·diff 리포트 → 사람이 승인하면 반영(운영자 PC 전용) |
| `core/llm/` | LLM 공급자 추상화(Claude·Gemini·mock), HWP·HWPX 전처리, 분류·추출, 사용량 로그(토큰·해시만) |
| `app/` | Streamlit 화면(처음 화면, 작업 화면: 지역 일괄 대조 · 지도, ③ 안내문) |
| `core/geocode.py`, `core/boundaries.py` | 브이월드 지오코더 2.0(필지당 1회, 지번→도로명), 2D 데이터 행정경계. 세션 캐시만 |
| `core/pipeline.py` | 여러 법정동 조회 → 메모리에서 판정. 조회한 동은 재조회 없이 재판정 |
| `core/export.py`, `core/letters.py` | Excel·CSV, 안내문(내장 틀 3종 + 검증, docx). 모두 메모리에서 생성 |
| `core/building_api.py` | 건축물대장 표제부(getBrTitleInfo) 법정동 단위 일괄 조회. 메모리에서만 처리, 저장 없음 |
| `core/regions.py` | 법정동코드: 시도·시군구(일반구는 구 단위)·법정동(리 포함) 목록, 조례 지역 매핑 |
| `core/conditions.py` | 공고 조건표 스키마 `NoticeConditions`, 접수 마감 판정 |
| `core/ordinances.py` | 조례 조건표 `OrdinanceConditions` 로더, 시군구코드 매핑 |
| `core/normalize.py` | 대장 구조·용도 정규화(`data/ref/structure_map.csv`, `use_map.csv`) |
| `core/evaluator.py` | 판정 엔진 v2 (CANDIDATE / NEEDS_CHECK / ORDINANCE_ONLY / NOT_ELIGIBLE) |
| legacy 파이썬 코드·정답셋 | **미수령** → [scripts/legacy/README.md](scripts/legacy/README.md). legacy 회귀 테스트는 건너뜀 |

## 앱 실행

```bash
.venv/bin/python -m streamlit run app/main.py
```

http://localhost:8501 에서 열립니다.

앱 흐름
0. **처음 화면**: 왼쪽 지역 선택(도·특별시·광역시 → 시·군 → 구·군 → 읍·면·동 여러 개, 또는 이름 검색 '청운동'·'수원 장안'),
   오른쪽 공고문 직접 올리기. 검색으로 같은 구의 동을 이어 고르면 여러 동이 쌓입니다.
1. 구·군을 고르고 **'공고·조례 검색'**을 누르면 그 지역 **조례**(소규모 노후건축물 점검 대상 연수·규모)를 보여 주고, **공고를 검색**(네이버 웹문서 2회 + 뉴스 1회)합니다. 지역을 고르기만 해서는 검색하지 않습니다.
   올해 공고로 보이는 글이 있으면 최대 3건을 각각 추출(글마다 LLM 1회)해 **한 조건으로 합치고** 지역 선택(읍·면·동 전체)과
   사이드바 '공고'(추출 초안·미검수)에 적용합니다. '공고 검색 결과'에서 여러 글에 체크해 다시 합칠 수 있습니다(한 번에 6건까지,
   이미 추출한 글은 LLM을 다시 부르지 않음). 합치는 규칙: 최신 회차 글끼리만, 공고 → 보도자료 → 뉴스 순으로 값을 쓰고,
   값이 다르면 '출처마다 다름'으로 알리며, 근거에는 [출처 n]을 붙입니다(`core/notice_merge.py`). ① 화면의 '공고 검색'·'다시 검색' 버튼으로도 찾을 수 있습니다.
   처음 화면의 '② 조건 검수'에서 원문 근거와 대조해 고치고 '검수 완료로 저장'합니다.
2. '후보 건물 찾기 →' → 작업 화면(지역 일괄 대조 | 지도). 위 단계 표시줄(1 지역·공고 조건 · 2 후보 목록·지도 · 3 안내문)을 눌러 단계를 옮깁니다. 지역 일괄 대조: 조건에 맞는 후보 목록 → CSV·엑셀. 지도: 1차 후보(물방울 핀 ✓)·확인 필요(네모 핀 ?)·
   조례만(원형 핀)을 모양과 색으로 구분, 가까운 건물은 클러스터. 조건은 처음 화면·사이드바 편집기에서, 지역은 사이드바에서 고칩니다.
3. 안내문 생성. 접수가 마감된 공고로도 만들 수 있고, 안내문마다 맨 위에 마감된 공고라고 적습니다.

공고 검색 키워드(`core/notice_hunter.py`): '{지역} 소규모 노후건축물 안전점검 신청', '{올해}년 {지역} 소규모 노후 건축물 안전점검 공고'(웹문서),
'{지역} 노후 건축물 무료 안전점검 신청'(뉴스). 지역은 '중구'처럼 같은 이름이 여러 곳이면 '대전 중구', 일반구는 시('수원시').
결과는 다른 지역 글·점검기관 모집·결재문서 등을 빼고 점수순(올해 글, 구청 사이트, 신청·모집 안내일수록 위). 구청 사이트는 제목에 지역 이름이
있거나 주소가 지역 이름의 로마자(djjunggu, suwon)를 담으면 같은 지역으로 봅니다. robots.txt가 막은 사이트·첨부파일은 자동으로 가져오지 않습니다.
- 지역 일괄 대조: 처음 화면에서 고른 지역(사이드바에서 바꿀 수 있음) → ‘조회·판정’
- 다른 지역 공고를 시험으로 적용하려면 사이드바 ‘다른 지역 공고 적용(시험용)’
- 지도: 법정동 경계(조회한 동은 1차 후보 수로 채색) → ‘지도 좌표 가져오기’ → 핀·표에서 건물 선택 → 오른쪽 상세.
  미조회 동을 누르면 ‘이 동 조회·판정’으로 바로 조회합니다.
- 조회 결과·좌표는 브라우저 세션에만 있습니다. 새로고침하면 사라지는 것이 정상입니다
  (브이월드 지오코더 응답은 저장 금지).
- 배경지도는 OSM입니다. 브이월드 배경지도 타일은 인증키가 브라우저에 노출되어 쓰지 않습니다.
- 브이월드 인증키 발급 시 서비스 URL에 `localhost:8501`과 배포 도메인을 등록하세요.

- 공고문 직접 올리기(처음 화면): HWP·HWPX·PDF·DOCX·XLSX·TXT·PNG·JPG 업로드(여러 개 = 한 공고) → 사업유형 분류 + 조건 추출(LLM 호출 1번) → 원문 근거와 대조해 고친 뒤
  ‘검수 완료로 저장’ → `data/notices/`에 저장되고 작업 화면 사이드바 '공고' 목록에 바로 나타납니다.
  - 오늘 날짜가 접수기간 밖이거나(마감·접수 전) 기간을 확인할 수 없으면 경고합니다(지역 일괄 대조·지도도 같은 기준).
  - 공고의 지자체(예: '대전광역시 중구청', '수원시')를 시군구코드로 바꿔 왼쪽 검색 필터(시·도, 시·군·구, 법정동 전체)에 자동 적용합니다.
    일반구를 둔 시는 첫 구를 열고 나머지 구는 '· 공고 지역' 표시로 안내합니다. '중구'처럼 여러 곳이면 고르지 않고 직접 고르게 합니다.
- LLM 사용량은 `data/logs/llm_usage.csv`(git 제외)에 시각·공급자·모델·작업·토큰(캐시 읽기·쓰기 포함)·문서 해시·폴백 사유만 남습니다.

### LLM 토큰 절약 방식 (`core/llm/`)
- **글자로 바꿔 보내기**(`preprocess.py`): PDF는 pymupdf로 본문·표(마크다운), DOCX·XLSX·HWPX는 표까지 마크다운, HWP는 문단 텍스트.
  글자층이 없는 PDF 쪽(스캔)과 사진만 이미지로 보낸다.
- **이미지**: pillow로 회색조·대비 보정(이진화는 안 함), 긴 변 1568px·조각당 약 1.15MP 이하로 줄이고 세로로 긴 리플릿은 겹쳐 자른다.
  조각당 약 1,500 토큰. 작은 글씨가 안 읽히면 `.env` `LLM_IMAGE_MAX_EDGE`(최대 2576)를 올린다.
- **출력 최소화**: 사고(thinking)를 끄고(Haiku: disabled, Sonnet 5.5: between_tools), 서술형 이유 없이 JSON만 받는다.
  근거는 채운 항목만 60자 이내 원문 구절.
- **호출 1번**: 분류와 추출을 한 요청으로(`extractor.analyze`). 출력 스키마가 다르면 캐시가 이어지지 않아(실측) 합쳤다.
- **프롬프트 캐싱**: 시스템 프롬프트와 공고 본문 끝에 cache_control. 다른 공고를 이어서 분석하면 시스템 프롬프트·스키마(약 4,900 토큰)를 캐시로 읽는다.
- **상위 모델 폴백**(`fallback.py`): 기본 모델(`LLM_MODEL_EXTRACT`, 예: claude-haiku-5-5)이 오류·거절·스키마 실패이거나
  확신도 low, 또는 안전점검 공고인데 핵심 조건(경과연수·층수·연면적·용도·구조)을 하나도 못 찾으면 `LLM_MODEL_FALLBACK`(예: claude-sonnet-5-5)으로 다시 한다.
- Claude 구조화 출력 한도(null 허용 항목 16개, 문법 크기) 때문에 Claude에는 납작한 스키마로 받고 코드에서 원래 모양으로 되돌린다(`base.compact_json_schema`).

## 운영 스크립트

```bash
.venv/bin/python scripts/sync_ordinances.py check
```
법제처 Open API로 조례 개정 여부 확인 → `data/ordinances/sync/<날짜>/proposal.csv`, `diff.md`. 원본은 바꾸지 않습니다.
(2026-11-15 실행: 목록 861건 중 우리 조례 220건 대조, 개정 2건 — 충남 예산군, 전남광주 해남군. 둘 다 분류 결과 변화 없음)
법제처 API는 **신청 때 등록한 IP의 PC에서만** 동작합니다(`LAW_API_OC` 필요). 검토 후 반영:

```bash
.venv/bin/python scripts/sync_ordinances.py apply data/ordinances/sync/YYYYMMDD/proposal.csv --yes
```

```bash
.venv/bin/python scripts/compare_providers.py
```
정답셋(`data/eval/<공고ID>/` 원본 파일 + `truth.json`)으로 키가 있는 공급자를 모두 돌려 일치율·분류 정확도·토큰을 표로 출력합니다(선택은 사람이).

```bash
.venv/bin/python scripts/smoke_building_api.py
```
청운동 표제부 실조회(건수·호출 수·소요시간) → 판정 엔진 v2 연결까지 확인. 주소는 출력하지 않습니다. `BUILDING_API_KEY` 필요.

```bash
.venv/bin/python scripts/check_ordinance_mapping.py
```
조례 ↔ 시군구코드 매핑 점검(실패·충돌·1:N·개편 지역·조례 없는 시군구).

```bash
.venv/bin/python scripts/migrate_notices.py --dry-run
```
기존 공고 추출 결과(`data/notices/legacy/*.json`) → `NoticeConditions` 변환 리포트.

## 설치

Python 3.11 이상.

```bash
python3 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

## .env 설정

```bash
cp .env.example .env
```

`.env`에 실제 키를 넣습니다. `.env`는 git에 올라가지 않습니다. 코드·테스트·로그에 키를 쓰지 않습니다.

| 키 | 필요한 단계 | 발급처 |
|---|---|---|
| BUILDING_API_KEY | 3 이후 | 공공데이터포털 "국토교통부_건축HUB_건축물대장정보 서비스" (Decoding 키) |
| VWORLD_GEOCODER_KEY | 5 | 브이월드 지오코더 API 2.0 키. 발급 시 서비스 URL에 `localhost:8501`과 배포 도메인 등록 |
| VWORLD_DATA_KEY | 5 | 브이월드 2D 데이터 API 키(행정경계). 지오코더 키와 따로 발급 |
| LLM_PROVIDER, LLM_MODEL_EXTRACT, LLM_MODEL_WRITE | 6 | `claude`면 ANTHROPIC_API_KEY, `gemini`면 GEMINI_API_KEY. 키나 모델명이 없으면 `mock`(샘플 결과, 외부 전송 없음) |

## 서버 배포 (Streamlit Community Cloud)

1. share.streamlit.io 에 GitHub 계정으로 로그인. **배포하는 계정은 리포지토리 admin 권한이 있어야 합니다**(초대받은 협업자는 보통 없음 → 소유자가 배포하거나 admin 권한을 줌).
2. Create app → 리포지토리·브랜치 선택, Main file path `app/main.py`.
3. Advanced settings → Python 3.12 또는 3.13, **Secrets** 칸에 `.env`와 같은 이름으로 붙여 넣기:
   ```toml
   BUILDING_API_KEY = "..."
   VWORLD_GEOCODER_KEY = "..."
   VWORLD_DATA_KEY = "..."
   LLM_PROVIDER = "gemini"
   LLM_MODEL_EXTRACT = "gemini-3.8-flash"
   LLM_MODEL_WRITE = "gemini-3.8-flash"
   GEMINI_API_KEY = "..."
   ```
   앱이 시작할 때 Secrets를 환경변수로 옮깁니다(`app/state.py` `load_secrets_into_env`). `LAW_API_OC`는 IP 등록 PC 전용이라 넣지 않습니다.
4. 비공개 리포지토리에서 배포하면 앱도 비공개입니다. Share → 팀원 이메일로 초대(계정당 비공개 앱 1개).
5. 브이월드 키 설정의 서비스 URL에 배포 주소(`*.streamlit.app`)를 추가합니다.
6. 서버 디스크는 재시작 때 지워집니다. ①에서 ‘검수 완료로 저장’한 공고는 서버에서 사라지므로, 확정 공고는 `data/notices/`에 커밋해 두세요.

## 테스트

```bash
.venv/bin/python -m pytest -q
```

외부 API를 실제로 부르는 테스트는 `@pytest.mark.live`로 표시하며 기본으로 건너뜁니다.

```bash
.venv/bin/python -m pytest -q -m live
```

## 조례 분류 초안 다시 만들기

```bash
.venv/bin/python scripts/classify_ordinances.py
```

자세한 컬럼 설명은 [data/ordinances/README.md](data/ordinances/README.md).

## 기존 grok 웹앱 (삭제됨)

팀원 grok 웹앱(Next.js) 원본은 저장소에서 지웠습니다. 보려면 `git checkout 489d49a -- frontend_grok`.
