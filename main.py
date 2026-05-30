from fastapi import FastAPI, HTTPException, Form, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

# 파일이 저장된 폴더 경로
SHARED_FOLDER = r"./video"

# 디렉토리 존재 여부 확인
if not os.path.exists(SHARED_FOLDER):
    os.makedirs(SHARED_FOLDER)


@app.get("/", response_class=HTMLResponse)
async def main():
    html_content = """
    <html>
        <head>
            <title>FastAPI HTML Example</title>
        </head>
        <body>
            <form action = "/filedown" method = "post">
                <label>유튜브 주소 입력 : <input type="text" name="vd_dir"></label><br>
                <button type="submit">제출</button>
            </form>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

# 파일 다운로드용 엔드포인트
@app.post("/filedown")
async def down_yt(vd_dir:str = Form(...), background_tasks: BackgroundTasks = None):
    os.system(rf'yt-dlp -f "bv*[vcodec^=avc1][ext=mp4]+ba[acodec^=mp4a][ext=m4a]/best[vcodec^=avc1][ext=mp4]" --merge-output-format mp4 -P "./video" {vd_dir}')
    filename = os.listdir(SHARED_FOLDER)[0]
    file_path = os.path.join(SHARED_FOLDER, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    #add callback that video will be removed after download
    background_tasks.add_task(os.remove, file_path)
    return FileResponse(file_path, filename=filename, media_type="application/octet-stream")


# 선택: 정적 파일 서빙 (브라우저에서 바로 열리는 파일들도 접근 가능하게 하려면)
app.mount("/files", StaticFiles(directory=SHARED_FOLDER), name="files")



import socket
# 호스트 이름을 기준으로 IP 주소 가져오기
hostname = socket.gethostname()
ip_address = socket.gethostbyname(hostname)
print("="*50, f"\nIP 주소: {ip_address}\n", "="*50)