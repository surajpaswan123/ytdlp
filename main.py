import os
import re
import time
import urllib.parse
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import yt_dlp
import httpx

app = FastAPI(title="Church App yt-dlp Stream Extractor")

# CORS headers to allow app & admin portal requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def health_check():
    return {"status": "online", "service": "yt-dlp stream extractor"}

@app.get("/extract")
def extract_stream(
    url: str = Query(..., description="YouTube URL or video ID"),
    type: str = Query("video", description="Type of stream: 'video' or 'audio'"),
    request: Request = None
):
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

    # Optimized yt-dlp options for fast stream extraction
    ydl_opts = {
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'mweb', 'web']
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
            else:
                formats = info.get('formats', [])
                playable_formats = []
                
                if type == "audio":
                    # Filter for audio-only streams (vcodec == 'none' and acodec != 'none')
                    playable_formats = [
                        f for f in formats
                        if f.get('url')
                        and f.get('vcodec') == 'none'
                        and f.get('acodec') != 'none'
                        and f.get('ext') in ['m4a', 'mp3', 'webm', 'aac', 'opus']
                    ]
                    # Sort by audio bitrate (abr)
                    playable_formats.sort(key=lambda x: x.get('abr') or 0)
                else:
                    # Filter for progressive combined formats (both audio and video exist)
                    playable_formats = [
                        f for f in formats
                        if f.get('url')
                        and f.get('vcodec') != 'none'
                        and f.get('acodec') != 'none'
                        and f.get('ext') in ['mp4', 'm3u8', 'webm']
                    ]
                    # Sort by resolution/height
                    playable_formats.sort(key=lambda x: x.get('height') or 0)

                # Fallback to standard best formats if no matching filtered progressive/audio formats
                if not playable_formats:
                    playable_formats = [
                        f for f in formats
                        if f.get('url')
                        and not f.get('url', '').endswith('.jpg')
                        and not f.get('url', '').endswith('.png')
                        and 'storyboard' not in f.get('url', '')
                        and 'i.ytimg.com' not in f.get('url', '')
                        and f.get('ext') in ['mp4', 'm4a', 'webm', 'mp3', 'aac', 'm3u8', 'opus']
                    ]

                if playable_formats:
                    # Pick the highest quality matched format (last element after sorting)
                    stream_url = playable_formats[-1].get('url')
                elif not stream_url and 'formats' in info and len(info['formats']) > 0:
                    stream_url = info['formats'][-1].get('url')

            if not stream_url:
                raise HTTPException(status_code=404, detail="Could not extract direct stream URL")

            # Check if we should return a proxy url (especially for googlevideo URLs to bypass 403 IP lock)
            # Live HLS (.m3u8) streams do not need proxying as their segments are distributed differently
            proxy_url = stream_url
            if request and "googlevideo.com" in stream_url:
                encoded_url = urllib.parse.quote(stream_url)
                proxy_url = f"{request.base_url}stream?url={encoded_url}"

            return {
                "success": True,
                "title": title,
                "isLive": is_live,
                "streamUrl": proxy_url,
                "thumbnail": thumbnail,
                "extractedAt": int(time.time())
            }
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"YouTube extraction error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.get("/stream")
async def stream_media(url: str, request: Request):
    if not url:
        raise HTTPException(status_code=400, detail="Missing url parameter")

    # Decode URL if double encoded
    decoded_url = urllib.parse.unquote(url)
    while decoded_url != url:
        url = decoded_url
        decoded_url = urllib.parse.unquote(url)

    # Forward Range headers
    headers = {}
    range_header = request.headers.get("range")
    if range_header:
        headers["Range"] = range_header

    # Mask headers to resemble a standard browser request
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    headers["Referer"] = "https://www.youtube.com/"

    try:
        # We set follow_redirects=True to handle Google Video CDN redirects automatically
        client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        
        # Build and send the request
        req = client.build_request("GET", url, headers=headers)
        response = await client.send(req, stream=True)

        if response.status_code not in [200, 206]:
            # Close the response stream and release the connection
            await response.aclose()
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Source stream returned status {response.status_code}"
            )

        # Prepare headers to return to client (Content-Type, Content-Length, Content-Range, Accept-Ranges)
        response_headers = {}
        for header_name in ["content-type", "content-length", "content-range", "accept-ranges"]:
            val = response.headers.get(header_name)
            if val:
                response_headers[header_name] = val

        # Ensure Accept-Ranges is returned so players know they can seek
        if "accept-ranges" not in response_headers:
            response_headers["accept-ranges"] = "bytes"

        async def generate_chunks():
            try:
                # Stream content in chunks (e.g. 64KB)
                async for chunk in response.iter_bytes(chunk_size=65536):
                    yield chunk
            except Exception as e:
                # Handle connection issues during streaming gracefully
                print(f"Streaming exception: {e}")
            finally:
                # Ensure the client connection to YouTube is closed
                await response.aclose()

        return StreamingResponse(
            generate_chunks(),
            status_code=response.status_code,
            headers=response_headers
        )

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to source: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")

@app.get("/test-fetch")
async def test_fetch(url: str):
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=5.0) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            return {
                "success": True,
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body_length": len(resp.content)
            }
    except Exception as e:
        return {
            "success": False,
            "error_type": type(e).__name__,
            "error_message": str(e)
        }

@app.get("/test-stream")
async def test_stream(url: str, request: Request):
    try:
        decoded_url = urllib.parse.unquote(url)
        while decoded_url != url:
            url = decoded_url
            decoded_url = urllib.parse.unquote(url)

        headers = {}
        range_header = request.headers.get("range")
        if range_header:
            headers["Range"] = range_header
        headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        headers["Referer"] = "https://www.youtube.com/"

        client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        req = client.build_request("GET", url, headers=headers)
        response = await client.send(req, stream=True)
        
        response_headers = {}
        for h in ["content-type", "content-length", "content-range", "accept-ranges"]:
            val = response.headers.get(h)
            if val:
                response_headers[h] = val
        
        await response.aclose()
        await client.aclose()
        
        return {
            "success": True,
            "status_code": response.status_code,
            "headers": response_headers
        }
    except Exception as e:
        return {
            "success": False,
            "error_type": type(e).__name__,
            "error_message": str(e)
        }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)


