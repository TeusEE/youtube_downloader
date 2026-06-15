# youtube_downloader
yt-dlp를 사용해서 구현된 간단한 유튜브 동영상 다운로더입니다.

사용방법

[uv](https://docs.astral.sh/uv/)가 필요합니다. (설치: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
```bash
git clone https://github.com/TeusEE/youtube_downloader.git
cd youtube_downloader
uv sync            # .venv 생성 + 의존성 설치 (uv.lock 기준)
uv run fastapi run main.py
```
**IP 주소: Xxx.Yyy.Zzz.Aaa**
형태의 주소가 뜨면

http://Xxx.Yyy.Zzz.Aaa:8000

위치로 접근 후 다운로드를 원하는 유튜브 주소를 넣어주시면 됩니다.
(아이폰, 아이패드와 같은 IOS 기준으로 포맷이 맞추어져있음)

## 개발 환경 (uv)

이 프로젝트는 [uv](https://docs.astral.sh/uv/)로 의존성과 가상환경을 관리합니다.
의존성은 `pyproject.toml`에 정의되고 `uv.lock`에 정확한 버전이 고정됩니다.

- Python: 3.11 (`.python-version`로 고정)

자주 쓰는 명령어
```bash
uv sync                      # .venv 생성 + 의존성 설치 (uv.lock 기준)
uv run fastapi run main.py   # 앱 실행 (venv 활성화 불필요)
uv add <패키지>              # 의존성 추가 (pyproject.toml + uv.lock 자동 갱신)
uv remove <패키지>           # 의존성 제거
uv lock --upgrade            # 잠금 파일 최신 버전으로 갱신
```

`uv run`을 사용하면 `source .venv/bin/activate` 없이 바로 실행됩니다.
