from fastapi import FastAPI, HTTPException, Form, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
import asyncio
import logging
import os
import shutil
import sys
import tempfile

app = FastAPI()

# yt-dlp 진행 상황 실시간 로거 (uvicorn 콘솔로 즉시 출력)
logger = logging.getLogger("yt-dlp")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [yt-dlp] %(message)s", "%H:%M:%S"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


async def _drain(stream, prefix: str, lines: list):
    """서브프로세스 출력 스트림을 줄 단위로 읽어 즉시 로그에 남긴다."""
    while True:
        raw = await stream.readline()
        if not raw:
            break
        text = raw.decode(errors="replace").rstrip()
        if text:
            lines.append(text)
            logger.info("%s%s", prefix, text)


async def run_ytdlp(args: list, label: str):
    """yt-dlp를 실행하며 stdout/stderr를 실시간 스트리밍한다. (returncode, stdout줄, stderr줄) 반환."""
    logger.info("▶ 시작: %s", label)
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_lines: list = []
    stderr_lines: list = []
    await asyncio.gather(
        _drain(proc.stdout, "", stdout_lines),
        _drain(proc.stderr, "! ", stderr_lines),
    )
    rc = await proc.wait()
    logger.info("■ 종료 (코드 %s): %s", rc, label)
    return rc, stdout_lines, stderr_lines


# 애플 스타일 공통 디자인 (모든 페이지에서 재사용)
BASE_STYLE = """
    :root {
        --bg: #f5f5f7;
        --card: rgba(255, 255, 255, 0.72);
        --text: #1d1d1f;
        --muted: #6e6e73;
        --accent: #0071e3;
        --accent-press: #0077ed;
        --border: rgba(0, 0, 0, 0.1);
        --field: #f5f5f7;
    }
    @media (prefers-color-scheme: dark) {
        :root {
            --bg: #000000;
            --card: rgba(28, 28, 30, 0.72);
            --text: #f5f5f7;
            --muted: #a1a1a6;
            --accent: #0a84ff;
            --accent-press: #409cff;
            --border: rgba(255, 255, 255, 0.12);
            --field: #1c1c1e;
        }
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; margin: 0; }
    body {
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text",
            "Helvetica Neue", "Apple SD Gothic Neo", sans-serif;
        background: var(--bg);
        color: var(--text);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 24px;
        -webkit-font-smoothing: antialiased;
    }
    .card {
        width: 100%;
        max-width: 420px;
        background: var(--card);
        backdrop-filter: saturate(180%) blur(20px);
        -webkit-backdrop-filter: saturate(180%) blur(20px);
        border: 1px solid var(--border);
        border-radius: 22px;
        padding: 36px 28px;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12);
    }
    h1 {
        font-size: 26px;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin: 0 0 6px;
        text-align: center;
    }
    .subtitle {
        font-size: 15px;
        color: var(--muted);
        text-align: center;
        margin: 0 0 28px;
    }
    label.field-label {
        display: block;
        font-size: 13px;
        font-weight: 600;
        color: var(--muted);
        margin: 0 0 8px;
    }
    input[type="text"] {
        width: 100%;
        font-size: 17px;
        padding: 14px 16px;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: var(--field);
        color: var(--text);
        outline: none;
        transition: border-color 0.15s, box-shadow 0.15s;
    }
    input[type="text"]:focus {
        border-color: var(--accent);
        box-shadow: 0 0 0 4px rgba(0, 113, 227, 0.15);
    }
    .options { margin: 24px 0 28px; display: flex; flex-direction: column; gap: 10px; }
    .option {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 14px 16px;
        border: 1px solid var(--border);
        border-radius: 14px;
        background: var(--field);
        cursor: pointer;
        transition: border-color 0.15s, background 0.15s;
    }
    .option:has(input:checked) {
        border-color: var(--accent);
        box-shadow: 0 0 0 3px rgba(0, 113, 227, 0.12);
    }
    .option input { margin-top: 3px; accent-color: var(--accent); width: 18px; height: 18px; }
    .option > span { display: block; }
    .option .opt-title { display: block; font-size: 16px; font-weight: 600; }
    .option .opt-desc { display: block; font-size: 13px; color: var(--muted); margin-top: 2px; }
    button {
        width: 100%;
        font-size: 17px;
        font-weight: 600;
        color: #fff;
        background: var(--accent);
        border: none;
        border-radius: 980px;
        padding: 15px;
        cursor: pointer;
        transition: background 0.15s, transform 0.05s;
    }
    button:hover { background: var(--accent-press); }
    button:active { transform: scale(0.98); }
    .link-btn {
        display: block;
        text-align: center;
        text-decoration: none;
        font-size: 17px;
        font-weight: 600;
        color: #fff;
        background: var(--accent);
        border-radius: 980px;
        padding: 15px;
        margin-top: 8px;
    }
    .hint {
        font-size: 14px;
        color: var(--muted);
        line-height: 1.5;
        text-align: center;
        margin: 0 0 22px;
    }
    .hint b { color: var(--text); }
    .back {
        display: block;
        text-align: center;
        text-decoration: none;
        color: var(--accent);
        font-size: 15px;
        margin-top: 22px;
    }
"""


@app.get("/", response_class=HTMLResponse)
async def main():
    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
        <head>
            <meta charset="utf-8">
            <title>YouTube 다운로더</title>
            <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
            <style>{BASE_STYLE}</style>
        </head>
        <body>
            <div class="card">
                <h1>YouTube 다운로더</h1>
                <p class="subtitle">영상을 디바이스에 저장해 오프라인으로 시청하세요.</p>
                <form action="/filedown" method="post">
                    <label class="field-label" for="vd_dir">유튜브 주소</label>
                    <input type="text" id="vd_dir" name="vd_dir"
                        placeholder="https://www.youtube.com/watch?v=..."
                        autocomplete="off" autocapitalize="off" spellcheck="false">
                    <div class="options">
                        <label class="option">
                            <input type="radio" name="quality" value="fast" checked>
                            <span>
                                <span class="opt-title">빠름 · 최대 720p</span>
                                <span class="opt-desc">서버를 거치지 않음 · 저장 시 한 단계 더 필요</span>
                            </span>
                        </label>
                        <label class="option">
                            <input type="radio" name="quality" value="hq">
                            <span>
                                <span class="opt-title">고화질 · 1080p 이상</span>
                                <span class="opt-desc">서버에서 병합 후 바로 저장</span>
                            </span>
                        </label>
                    </div>
                    <button type="submit">다운로드</button>
                </form>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


# 경로 A — 직접 URL 추출 (서버가 데이터를 거치지 않음, ≤720p progressive)
async def get_direct_url(vd_dir: str) -> str:
    rc, stdout_lines, stderr_lines = await run_ytdlp(
        ["-f", "b[ext=mp4][acodec!=none][vcodec!=none]/b[ext=mp4]", "-g", vd_dir],
        label=f"URL 추출 {vd_dir}",
    )
    if rc != 0:
        raise HTTPException(
            status_code=400,
            detail=f"URL 추출 실패: {' '.join(stderr_lines)[:500]}",
        )
    url = next((line for line in stdout_lines if line.startswith("http")), "")
    if not url:
        raise HTTPException(status_code=404, detail="다운로드 가능한 단일 포맷을 찾지 못했습니다.")
    return url


# 경로 B — 서버에서 병합 후 파일 전송 (1080p+, 요청별 격리 디렉토리)
async def merge_and_path(vd_dir: str, tmpdir: str) -> str:
    rc, stdout_lines, stderr_lines = await run_ytdlp(
        [
            "-f", "bv*[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/best[vcodec^=avc1][ext=mp4]",
            "--merge-output-format", "mp4",
            "-o", os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "--print", "after_move:filepath",
            "--no-simulate",
            "--progress",  # --print이 활성화하는 quiet 모드에서도 진행률 강제 출력
            "--newline",   # 진행률을 \r 대신 줄바꿈으로 출력 → 실시간 로그 가능
            vd_dir,
        ],
        label=f"병합 다운로드 {vd_dir}",
    )
    if rc != 0:
        raise HTTPException(
            status_code=400,
            detail=f"다운로드/병합 실패: {' '.join(stderr_lines)[:500]}",
        )
    # 진행률 줄이 섞여 있으므로, 실제 존재하는 파일 경로 줄을 역순으로 탐색
    file_path = next((line for line in reversed(stdout_lines) if os.path.isfile(line)), "")
    if not file_path:
        raise HTTPException(status_code=404, detail="병합된 파일을 찾지 못했습니다.")
    return file_path


# 파일 다운로드용 엔드포인트
@app.post("/filedown")
async def down_yt(
    vd_dir: str = Form(...),
    quality: str = Form("fast"),
    background_tasks: BackgroundTasks = None,
):
    # 경로 A: 직접 URL — 서버는 URL만 추출, 클라이언트가 CDN에서 직접 다운로드
    if quality == "fast":
        url = await get_direct_url(vd_dir)
        html_content = f"""
        <!DOCTYPE html>
        <html lang="ko">
            <head>
                <meta charset="utf-8">
                <title>다운로드 링크</title>
                <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
                <style>{BASE_STYLE}</style>
            </head>
            <body>
                <div class="card">
                    <h1>준비 완료</h1>
                    <p class="hint">아래 버튼을 <b>길게 눌러 → "파일에 저장"</b>으로<br>오프라인 저장하세요.</p>
                    <a class="link-btn" href="{url}" download>▶ 영상 다운로드</a>
                    <a class="back" href="/">← 다시 다운로드</a>
                </div>
            </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)

    # 경로 B: 고화질 — 서버에서 병합 후 파일 전송
    tmpdir = tempfile.mkdtemp(prefix="ytdl_")
    background_tasks.add_task(shutil.rmtree, tmpdir, ignore_errors=True)
    file_path = await merge_and_path(vd_dir, tmpdir)
    return FileResponse(
        file_path,
        filename=os.path.basename(file_path),
        media_type="video/mp4",
        content_disposition_type="attachment",
    )


import socket
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

ip_address = get_local_ip()
print("="*50, f"\nIP 주소: {ip_address}\n", "="*50)
