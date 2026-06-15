from fastapi import FastAPI, HTTPException, Form, BackgroundTasks, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from urllib.parse import quote

import httpx

app = FastAPI()

# yt-dlp 진행 상황 실시간 로거 (uvicorn 콘솔로 즉시 출력)
logger = logging.getLogger("yt-dlp")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [yt-dlp] %(message)s", "%H:%M:%S"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# 진행 중인 작업 저장소: job_id -> {queue, tmpdir, file_path, filename, direct_url}
jobs: dict = {}


def _safe_filename(title: str) -> str:
    """영상 제목을 안전한 .mp4 파일명으로 변환한다 (한글 유지)."""
    name = (title or "").strip() or "video"
    for ch in '/\\:*?"<>|\n\r\t':
        name = name.replace(ch, "_")
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    return name


def _content_disposition(filename: str) -> str:
    """iOS/모든 브라우저 호환 첨부 헤더 (ASCII 폴백 + RFC 5987 UTF-8)."""
    ascii_name = filename.encode("ascii", "ignore").decode() or "video.mp4"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


async def _drain(stream, prefix: str, lines: list, name: str, on_line):
    """서브프로세스 출력 스트림을 줄 단위로 읽어 즉시 로그에 남기고 콜백에 전달한다."""
    while True:
        raw = await stream.readline()
        if not raw:
            break
        text = raw.decode(errors="replace").rstrip()
        if text:
            lines.append(text)
            logger.info("%s%s", prefix, text)
            if on_line:
                try:
                    on_line(name, text)
                except Exception:
                    pass


async def run_ytdlp(args: list, label: str, on_line=None):
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
        _drain(proc.stdout, "", stdout_lines, "stdout", on_line),
        _drain(proc.stderr, "! ", stderr_lines, "stderr", on_line),
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
        margin-top: 18px;
    }
    /* 진행률 UI */
    .bar-track {
        width: 100%;
        height: 10px;
        background: var(--field);
        border: 1px solid var(--border);
        border-radius: 999px;
        overflow: hidden;
        margin: 18px 0;
    }
    .bar-fill {
        height: 100%;
        width: 0%;
        background: var(--accent);
        border-radius: 999px;
        transition: width 0.2s ease;
    }
    .status { font-size: 17px; font-weight: 600; text-align: center; margin: 0 0 4px; }
    .detail {
        font-size: 14px;
        color: var(--muted);
        text-align: center;
        margin: 0 0 8px;
        min-height: 18px;
        font-variant-numeric: tabular-nums;
    }
    #result { display: flex; flex-direction: column; }
"""

INDEX_HTML = """
<!DOCTYPE html>
<html lang="ko">
    <head>
        <meta charset="utf-8">
        <title>YouTube 다운로더</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
        <style>__STYLE__</style>
    </head>
    <body>
        <div class="card" id="form-card">
            <h1>YouTube 다운로더</h1>
            <p class="subtitle">영상을 디바이스에 저장해 오프라인으로 시청하세요.</p>
            <form id="dl-form">
                <label class="field-label" for="vd_dir">유튜브 주소</label>
                <input type="text" id="vd_dir" name="vd_dir"
                    placeholder="https://www.youtube.com/watch?v=..."
                    autocomplete="off" autocapitalize="off" spellcheck="false">
                <div class="options">
                    <label class="option">
                        <input type="radio" name="quality" value="fast" checked>
                        <span>
                            <span class="opt-title">빠름 · 최대 720p</span>
                            <span class="opt-desc">병합 없이 바로 저장 · 아이폰 호환</span>
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

        <div class="card" id="prog-card" style="display:none">
            <h1>다운로드</h1>
            <div class="bar-track"><div class="bar-fill" id="bar"></div></div>
            <p class="status" id="status">작업 시작 중...</p>
            <p class="detail" id="detail"></p>
            <div id="result"></div>
        </div>

        <script>
        const form = document.getElementById('dl-form');
        const formCard = document.getElementById('form-card');
        const progCard = document.getElementById('prog-card');
        const bar = document.getElementById('bar');
        const statusEl = document.getElementById('status');
        const detailEl = document.getElementById('detail');
        const resultEl = document.getElementById('result');
        let es = null;

        function setBar(pct) {
            bar.style.width = Math.max(0, Math.min(100, pct)) + '%';
        }

        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const fd = new FormData(form);
            formCard.style.display = 'none';
            progCard.style.display = 'block';
            resultEl.innerHTML = '';
            setBar(0);
            statusEl.textContent = '작업 시작 중...';
            detailEl.textContent = '';
            try {
                const res = await fetch('/start', { method: 'POST', body: fd });
                if (!res.ok) throw new Error('작업을 시작하지 못했습니다.');
                const data = await res.json();
                es = new EventSource('/progress/' + data.job_id);
                es.onmessage = (ev) => handleEvent(JSON.parse(ev.data));
            } catch (err) {
                showError(err.message || '오류가 발생했습니다.');
            }
        });

        function handleEvent(d) {
            if (d.type === 'progress') {
                const pct = parseFloat(d.percent) || 0;
                setBar(pct);
                statusEl.textContent = '다운로드 중 ' + (d.percent || '').trim();
                detailEl.textContent = [d.speed, d.eta ? 'ETA ' + d.eta : '']
                    .filter(Boolean).join('  ·  ');
            } else if (d.type === 'status') {
                statusEl.textContent = d.message;
            } else if (d.type === 'done') {
                setBar(100);
                if (es) es.close();
                statusEl.textContent = '완료!';
                detailEl.textContent = '아이폰은 버튼을 누른 뒤 "다운로드"를 선택하면 파일 앱에 저장됩니다.';
                resultEl.innerHTML =
                    '<a class="link-btn" href="' + d.url + '">▶ 영상 저장</a>' +
                    '<a class="back" href="/">← 다시 다운로드</a>';
                // 데스크톱 등에서는 자동으로 다운로드 시작
                window.location = d.url;
            } else if (d.type === 'error') {
                showError(d.message);
            }
        }

        function showError(msg) {
            if (es) es.close();
            setBar(0);
            statusEl.textContent = '오류가 발생했습니다';
            detailEl.textContent = msg;
            resultEl.innerHTML = '<a class="back" href="/">← 다시 시도</a>';
        }
        </script>
    </body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def main():
    return HTMLResponse(content=INDEX_HTML.replace("__STYLE__", BASE_STYLE), status_code=200)


# 경로 A — 직접 URL + 제목 추출 (≤720p progressive). 서버가 /stream으로 프록시한다.
async def get_direct_url(vd_dir: str, on_line=None) -> tuple[str, str]:
    rc, stdout_lines, stderr_lines = await run_ytdlp(
        [
            "-f", "b[ext=mp4][acodec!=none][vcodec!=none]/b[ext=mp4]",
            "--print", "%(title)s",  # 첫 줄: 제목
            "--print", "urls",        # 다음 줄: 직접 다운로드 URL
            vd_dir,
        ],
        label=f"URL 추출 {vd_dir}",
        on_line=on_line,
    )
    if rc != 0:
        raise HTTPException(status_code=400, detail=f"URL 추출 실패: {' '.join(stderr_lines)[:500]}")
    url = next((line for line in stdout_lines if line.startswith("http")), "")
    if not url:
        raise HTTPException(status_code=404, detail="다운로드 가능한 단일 포맷을 찾지 못했습니다.")
    title = next((line for line in stdout_lines if line and not line.startswith("http")), "")
    return url, title


# 경로 B — 서버에서 병합 후 파일 전송 (1080p+, 요청별 격리 디렉토리)
async def merge_and_path(vd_dir: str, tmpdir: str, on_line=None) -> str:
    rc, stdout_lines, stderr_lines = await run_ytdlp(
        [
            "-f", "bv*[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/best[vcodec^=avc1][ext=mp4]",
            "--merge-output-format", "mp4",
            "-o", os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "--print", "after_move:filepath",
            "--no-simulate",
            "--progress",  # --print이 활성화하는 quiet 모드에서도 진행률 강제 출력
            "--newline",   # 진행률을 \r 대신 줄바꿈으로 출력 → 줄 단위 스트리밍
            # 진행률을 파싱하기 쉬운 형식으로 출력 (퍼센트@@속도@@ETA)
            "--progress-template",
            "download:@@PROG@@%(progress._percent_str)s@@%(progress._speed_str)s@@%(progress._eta_str)s",
            vd_dir,
        ],
        label=f"병합 다운로드 {vd_dir}",
        on_line=on_line,
    )
    if rc != 0:
        raise HTTPException(status_code=400, detail=f"다운로드/병합 실패: {' '.join(stderr_lines)[:500]}")
    # 진행률 줄이 섞여 있으므로, 실제 존재하는 파일 경로 줄을 역순으로 탐색
    file_path = next((line for line in reversed(stdout_lines) if os.path.isfile(line)), "")
    if not file_path:
        raise HTTPException(status_code=404, detail="병합된 파일을 찾지 못했습니다.")
    return file_path


def _make_line_handler(queue: asyncio.Queue):
    """yt-dlp 출력 줄을 SSE 이벤트로 변환해 큐에 넣는 콜백을 만든다."""
    def handler(_name: str, text: str):
        if text.startswith("@@PROG@@"):
            parts = text[len("@@PROG@@"):].split("@@")
            queue.put_nowait({
                "type": "progress",
                "percent": parts[0].strip() if len(parts) > 0 else "",
                "speed": parts[1].strip() if len(parts) > 1 else "",
                "eta": parts[2].strip() if len(parts) > 2 else "",
            })
        elif "Merging formats" in text or "[Merger]" in text:
            queue.put_nowait({"type": "status", "message": "병합 중..."})
    return handler


async def _run_job(job_id: str, vd_dir: str, quality: str):
    job = jobs[job_id]
    queue: asyncio.Queue = job["queue"]
    handler = _make_line_handler(queue)
    try:
        if quality == "fast":
            queue.put_nowait({"type": "status", "message": "링크 추출 중..."})
            url, title = await get_direct_url(vd_dir, on_line=handler)
            job["direct_url"] = url
            job["filename"] = _safe_filename(title)
            # 직링크 대신 같은 출처 프록시 URL을 내려줘야 iOS가 첨부로 인식해 저장한다
            queue.put_nowait({"type": "done", "mode": "file", "url": f"/stream/{job_id}"})
        else:
            tmpdir = tempfile.mkdtemp(prefix="ytdl_")
            job["tmpdir"] = tmpdir
            queue.put_nowait({"type": "status", "message": "다운로드 준비 중..."})
            file_path = await merge_and_path(vd_dir, tmpdir, on_line=handler)
            job["file_path"] = file_path
            job["filename"] = os.path.basename(file_path)
            queue.put_nowait({"type": "done", "mode": "file", "url": f"/result/{job_id}"})
    except HTTPException as e:
        queue.put_nowait({"type": "error", "message": str(e.detail)})
    except Exception as e:
        queue.put_nowait({"type": "error", "message": f"{e}"})
    finally:
        queue.put_nowait({"type": "_end"})
        # 안전망: 일정 시간 후 작업/임시폴더 정리 (file 모드는 /result에서 우선 정리됨)
        asyncio.create_task(_expire_job(job_id, 600))


async def _expire_job(job_id: str, delay: int):
    await asyncio.sleep(delay)
    job = jobs.pop(job_id, None)
    if job and job.get("tmpdir"):
        shutil.rmtree(job["tmpdir"], ignore_errors=True)


# 작업 시작 — job_id 즉시 반환, 다운로드는 백그라운드로 진행
@app.post("/start")
async def start(vd_dir: str = Form(...), quality: str = Form("fast")):
    job_id = uuid.uuid4().hex
    jobs[job_id] = {"queue": asyncio.Queue(), "tmpdir": None, "file_path": None,
                    "filename": None, "direct_url": None}
    asyncio.create_task(_run_job(job_id, vd_dir, quality))
    return {"job_id": job_id}


# 진행 상황 SSE 스트림
@app.get("/progress/{job_id}")
async def progress(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")

    async def event_stream():
        queue: asyncio.Queue = job["queue"]
        while True:
            event = await queue.get()
            if event.get("type") == "_end":
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


# 완성된 파일 다운로드 (경로 B 고화질)
@app.get("/result/{job_id}")
async def result(job_id: str, background_tasks: BackgroundTasks):
    job = jobs.get(job_id)
    if not job or not job.get("file_path") or not os.path.isfile(job["file_path"]):
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")

    file_path = job["file_path"]
    filename = job["filename"]
    tmpdir = job.get("tmpdir")

    def cleanup():
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
        jobs.pop(job_id, None)

    background_tasks.add_task(cleanup)
    return FileResponse(
        file_path,
        filename=filename,
        media_type="video/mp4",
        content_disposition_type="attachment",
        background=background_tasks,
    )


# 빠름 모드 다운로드 — 직링크를 같은 출처로 프록시하며 첨부 헤더를 붙인다.
# iOS Safari는 same-origin + Content-Disposition: attachment 일 때만 "파일에 저장"이 동작한다.
@app.get("/stream/{job_id}")
async def stream(job_id: str, request: Request):
    job = jobs.get(job_id)
    if not job or not job.get("direct_url"):
        raise HTTPException(status_code=404, detail="스트림을 찾을 수 없습니다.")

    src = job["direct_url"]
    filename = job.get("filename") or "video.mp4"

    # 클라이언트(특히 iOS 다운로드 매니저)의 Range 요청을 그대로 상위로 전달
    fwd = {"User-Agent": "Mozilla/5.0"}
    if "range" in request.headers:
        fwd["Range"] = request.headers["range"]

    client = httpx.AsyncClient(timeout=httpx.Timeout(None), follow_redirects=True)
    upstream = await client.send(
        client.build_request("GET", src, headers=fwd), stream=True
    )

    headers = {
        "Content-Disposition": _content_disposition(filename),
        "Accept-Ranges": "bytes",
    }
    for h in ("content-length", "content-range"):
        if h in upstream.headers:
            headers[h] = upstream.headers[h]
    media_type = upstream.headers.get("content-type", "video/mp4")

    async def body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=headers,
        media_type=media_type,
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
