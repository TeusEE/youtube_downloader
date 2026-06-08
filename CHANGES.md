# 변경 사항 — 다운로드 효율 개선 (직접 URL / 서버 병합 이원화)

날짜: 2026-06-01

## 배경

기존 `main.py`는 모든 요청에 대해 `yt-dlp`로 영상을 **서버 디스크에 받아 병합한 뒤** `FileResponse`로 클라이언트에 다시 전송했다. 즉 데이터가 항상 `YouTube → 서버 → 클라이언트`로 두 번 흐르는(왕복) 비효율 구조였다. 목적은 **파일을 디바이스에 받아 오프라인 시청**하는 것.

핵심 사실:
- **720p 이하**: YouTube에 비디오+오디오가 합쳐진 단일(progressive) 포맷이 존재 → 서버는 **직접 다운로드 URL만 추출**해 넘기고 클라이언트가 구글 CDN에서 직접 받으면 **왕복이 사라진다**.
- **1080p 이상**: 비디오/오디오가 분리(DASH)되어 있어 mp4로 **병합** 필요. iOS 브라우저는 병합 불가 → 병합 주체인 서버가 데이터를 한 번 거쳐야 함 → **왕복 불가피**. 여기서는 서버 측 비효율만 제거.

결정: **두 경로를 모두 제공하고, 화질 선택에 따라 서버가 전달 방식을 자동 라우팅**.

## 변경된 파일

| 파일 | 변경 |
|---|---|
| `main.py` | 엔드포인트 전면 재작성 (아래 상세) |
| `requirements.txt` | 신규 — 기존 오타 파일 `requirments.txt` 대체 |
| `requirments.txt` | 삭제 (오타) |
| `video/` 폴더 | 삭제 (요청별 임시 폴더로 대체) |

## 동작 변경

`GET /` 폼에 화질 선택 라디오 버튼 추가:
- **빠름 (≤720p)** → `quality=fast` → 경로 A (왕복 없음)
- **고화질 (1080p+)** → `quality=hq` → 경로 B (서버 병합)

`POST /filedown`이 `quality` 폼 필드로 분기한다.

### 경로 A — 직접 URL (서버가 데이터를 거치지 않음)
- `yt-dlp -f "b[ext=mp4][acodec!=none][vcodec!=none]/b[ext=mp4]" -g <URL>` 로 progressive 단일 포맷의 직접 CDN URL만 추출 (다운로드 X, 디스크 사용 X).
- 결과 페이지(HTMLResponse)에 링크 + "길게 눌러 → 파일에 저장" 안내 표시.
  - iOS Safari는 mp4 URL을 인라인 재생하므로 강제 다운로드 불가 → 수동 저장 한 단계 필요.

### 경로 B — 서버 병합 (고화질, 왕복 유지 + 서버 최적화)
기존 병합 로직을 유지하되 다음을 개선:

1. **명령 주입 제거 + 비블로킹**: `os.system(rf'... {vd_dir}')` → `asyncio.create_subprocess_exec("yt-dlp", *args)` (셸 미사용, 인자 리스트 전달, `await`).
2. **요청별 격리 디렉토리**: `tempfile.mkdtemp()` 사용 → 기존 `os.listdir(SHARED_FOLDER)[0]` 동시요청 경쟁 조건 제거.
3. **정확한 파일명 획득**: `--print after_move:filepath` 로 병합 후 최종 경로 출력. 진행률 줄이 stdout에 섞이므로 마지막 줄 추측 대신 **실제 존재하는 파일 경로를 역순 탐색**(`os.path.isfile`)으로 확정 → `listdir[0]` 추측 제거.
4. **iOS 신뢰성**: `FileResponse(..., media_type="video/mp4", content_disposition_type="attachment")` → `Content-Length` 포함, 원탭 저장 가능.
5. **견고한 정리**: `background_tasks.add_task(shutil.rmtree, tmpdir, ignore_errors=True)` 로 임시 폴더 전체 정리.
6. **에러 처리**: `proc.returncode != 0` 시 stderr 일부를 담아 `HTTPException(400)` → yt-dlp 실패가 조용히 404로 빠지지 않음.

### 유지된 부분
- `get_local_ip()` — 시작 시 접속 IP 출력.
- 경로 B의 yt-dlp 포맷 셀렉터(avc1/mp4a 조합)는 기존 로직 재사용.

## UI 디자인 (애플 스타일)

폼 페이지와 결과 페이지에 공통 스타일(`BASE_STYLE`)을 적용:
- **프로스티드 글래스 카드** — 반투명 + `backdrop-filter: blur`, 22px 둥근 모서리, 부드러운 그림자.
- **SF 시스템 폰트**(`-apple-system`) + 한글 Apple SD Gothic Neo, 음수 자간 제목.
- **시스템 블루 액센트**(`#0071e3`) — 알약(pill) 버튼, 포커스 링, 선택된 옵션 강조.
- **선택형 옵션 카드** — 라디오를 카드형으로, 제목/설명 2단 구성.
- **iOS 친화** — `viewport-fit=cover`, autocapitalize/spellcheck off, 입력 17px(자동 확대 방지).
- **다크/라이트 자동 대응** — `prefers-color-scheme`로 테마 변수 분리.

## 실시간 진행 로그

기존에는 `await proc.communicate()`가 프로세스 종료 시까지 출력을 버퍼링해 다운로드 진행 상황을 실시간으로 볼 수 없었다. 다음으로 해결:

1. **스트리밍 러너**: `run_ytdlp()` + `_drain()` 추가 — stdout/stderr를 **줄 단위로 읽어 즉시 로그 출력**. `communicate()` 제거.
2. **전용 로거**: `logging.getLogger("yt-dlp")`, `HH:MM:SS [yt-dlp] ...` 포맷. `StreamHandler`가 레코드마다 flush → 콘솔 즉시 표시. 시작(`▶`)·종료(`■ 코드 N`) 마커 기록.
3. **진행률 강제 출력**: 경로 B에서 `--no-progress` 제거, **`--progress`**(`--print`가 켜는 quiet 모드에서도 진행률 출력) + **`--newline`**(진행률을 `\r` 대신 줄바꿈으로 → 줄 단위 스트리밍 가능) 추가.

결과(서버 콘솔, 다운로드 *도중* 실시간):
```
HH:MM:SS [yt-dlp] ▶ 시작: 병합 다운로드 https://...
HH:MM:SS [yt-dlp] [download]   3.3% of  245.69MiB at   10.93MiB/s ETA 00:21
HH:MM:SS [yt-dlp] ■ 종료 (코드 0): ...
```

## 함께 해결된 기존 버그
- **명령 주입 취약점**: URL이 `os.system` 셸 문자열에 직접 삽입되던 문제.
- **동시 요청 경쟁 조건**: `os.listdir[0]`이 다른 요청의 파일을 집을 수 있던 문제.
- **조용한 실패**: yt-dlp 실패 시 무조건 404 반환되던 문제.
- **고아 파일**: 연결 중단 시 잔여 파일이 남던 문제(요청별 temp + rmtree로 해소).

## 검증 결과 (실제 영상으로 실행)
- ✅ 경로 A: 직접 googlevideo URL 추출 정상.
- ✅ 경로 B: 유효한 MP4 반환, `Content-Disposition: attachment` + `Content-Length` 정상, 임시 폴더 자동 정리 확인.
- ✅ 주입 방지: `; touch ...` 입력해도 셸 미실행(PWNED 파일 미생성), 명확한 400 반환.
- ✅ 에러: 잘못된 URL은 404 아닌 명확한 400 메시지 반환.

## 알려진 제약 / 후속 고려
- **경로 A의 IP 바인딩**: 추출된 CDN URL에 `ip=<서버 공인IP>`가 포함됨. 같은 LAN/NAT 환경이면 정상이나, 클라이언트가 다른 네트워크면 403 가능 → 이 경우 경로 B 사용 또는 서버 프록시 필요.
- **JS 런타임 경고**: 테스트 환경에 `deno` 미설치 경고 발생. 추출은 됐으나 일부 영상에서 포맷 누락 가능 → 안정성 위해 `deno` 설치 권장.
