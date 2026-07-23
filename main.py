import os
import re
import time
import subprocess
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI(title="Church App yt-dlp Stream Extractor")

# CORS headers to allow app & admin portal requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LAST_UPDATE_CHECK = 0

def check_auto_update():
    global LAST_UPDATE_CHECK
    now = time.time()
    # Check for yt-dlp updates once every 24 hours (86400 seconds)
    if now - LAST_UPDATE_CHECK > 86400:
        try:
            subprocess.run(["pip", "install", "--upgrade", "yt-dlp"], check=False)
            LAST_UPDATE_CHECK = now
        except Exception as e:
            print(f"Auto-update check warning: {e}")

@app.get("/")
def health_check():
    return {"status": "online", "service": "yt-dlp stream extractor"}

@app.get("/extract")
def extract_stream(url: str = Query(..., description="YouTube URL or video ID")):
    check_auto_update()

    if not url or not url.strip():
        raise HTTPException(status_code=400, detail="Missing or empty url parameter")

    clean_input = url.strip()

    # Extract 11-character YouTube Video ID if a full URL was provided
    video_id = None
    match = re.search(r'(?:youtu\.be\/|youtube\.com\/(?:embed\/|v\/|watch\?v=|watch\?.+&v=|live\/|shorts\/))([\w-]{11})', clean_input)
    if match:
        video_id = match.group(1)
    elif len(clean_input) == 11 and re.match(r'^[\w-]{11}$', clean_input):
        video_id = clean_input

    if video_id:
        target_url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        target_url = clean_input

    # Optimized yt-dlp options for fast stream extraction bypassing bot checks
    ydl_opts = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'allow_unplayable_formats': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb', 'tv']
            }
        }
    }

    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = "cookies.txt"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=False)
            if not info:
                raise HTTPException(status_code=404, detail="No video information found for URL")
            
            stream_url = info.get('url')
            is_live = info.get('is_live', False) or info.get('was_live', False)
            title = info.get('title', 'Church Stream')
            thumbnail = info.get('thumbnail')
            
            # If HLS live manifest (.m3u8) is present
            if info.get('hls_url'):
                stream_url = info.get('hls_url')
                is_live = True

            # Fallback to available formats array if direct URL wasn't at top level
            if not stream_url and 'formats' in info and len(info['formats']) > 0:
                mp4_formats = [f for f in info['formats'] if f.get('url') and (f.get('ext') == 'mp4' or 'mp4' in f.get('vcodec', ''))]
                if mp4_formats:
                    stream_url = mp4_formats[-1].get('url')
                else:
                    stream_url = info['formats'][-1].get('url')

            if not stream_url:
                raise HTTPException(status_code=404, detail="Could not extract direct stream URL")

            return {
                "success": True,
                "title": title,
                "isLive": is_live,
                "streamUrl": stream_url,
                "thumbnail": thumbnail,
                "extractedAt": int(time.time())
            }
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"YouTube extraction error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
