# 공고 조건 추출기

지자체 소규모 노후 건축물 안전점검 공고문에서 지원 조건을 뽑는 웹 앱입니다.

## 비밀 값

코드에 API 키를 넣지 마세요. 서버는 환경 변수만 읽습니다.

- `ANTHROPIC_API_KEY` — 공고 조건 추출
- `BUILDING_REGISTER_API_KEY` — 공공데이터포털 건축HUB 건축물대장 (`BldRgstHubService`) serviceKey
- `BUILDING_GEOCODER_API_KEY` — 브이월드 Geocoder API 2.0 키. 건물 핀 좌표용이며, 없어도 지역 경계는 표시됩니다.

게시된 앱: Cursor 채팅의 게시 대화상자 또는 cursor.com 앱 비밀 설정에서 `ANTHROPIC_API_KEY`를 넣습니다. 값을 채팅에 붙여 넣지 마세요.

키가 없으면 파일 읽기·건너뛰기·내려받기는 그대로 되고, 조건 항목은 샘플 데이터로 채워집니다.
