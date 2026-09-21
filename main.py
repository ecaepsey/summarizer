import os
import shutil
import tempfile

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask





app = FastAPI()


class VideoRequest(BaseModel):
    url: str


class ChunkRequest(BaseModel):
    url: str
    start: int = 0
    duration: int = 600


def cleanup(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


def cleanup_directory(path: str):
    shutil.rmtree(path, ignore_errors=True)


# OLD endpoint — keep it for testing short videos
@app.post("/audio")
def get_audio(request: VideoRequest):
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, "audio.%(ext)s")

    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }
        ],

        "quiet": False,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([request.url])

    except Exception as e:
        cleanup_directory(temp_dir)

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )

    audio_path = os.path.join(temp_dir, "audio.m4a")

    if not os.path.exists(audio_path):
        cleanup_directory(temp_dir)

        raise HTTPException(
            status_code=500,
            detail="Audio file was not created"
        )

    return FileResponse(
        audio_path,
        media_type="audio/mp4",
        filename="audio.m4a",
        background=BackgroundTask(
            cleanup_directory,
            temp_dir
        ),
    )


# NEW endpoint — download only part of the VOD
@app.post("/audio/chunk")
def get_audio_chunk(request: ChunkRequest):
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(
        temp_dir,
        "audio.%(ext)s"
    )

    end = request.start + request.duration

    print(
        f"Downloading chunk: "
        f"{request.start}s → {end}s"
    )

    options = {
        "format": "bestaudio/best",

        "outtmpl": output_template,

        # Download only requested part
        "download_ranges": yt_dlp.utils.download_range_func(
            None,
            [(request.start, end)]
        ),

        # IMPORTANT:
        # no force_keyframes_at_cuts

        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }
        ],

        "quiet": False,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([request.url])

        files = os.listdir(temp_dir)

        print("Generated files:", files)

        audio_files = [
            file
            for file in files
            if file.endswith(".m4a")
        ]

        if not audio_files:
            raise HTTPException(
                status_code=500,
                detail="Audio chunk was not created"
            )

        audio_path = os.path.join(
            temp_dir,
            audio_files[0]
        )

        return FileResponse(
            audio_path,
            media_type="audio/mp4",
            filename=f"chunk_{request.start}.m4a",
            background=BackgroundTask(
                cleanup_directory,
                temp_dir
            ),
        )

    except HTTPException:
        cleanup_directory(temp_dir)
        raise

    except Exception as e:
        cleanup_directory(temp_dir)

        raise HTTPException(
            status_code=400,
            detail=str(e)
        )


class VideoInfoRequest(BaseModel):

    url: str



import subprocess

@app.post("/video/info")
def get_video_info(request: VideoInfoRequest):
    options = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                request.url,
                download=False
            )

        stream_url = info.get("url")

        if not stream_url:
            raise HTTPException(
                status_code=500,
                detail="Could not get stream URL"
            )

        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                stream_url,
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        print("FFPROBE:", result.stdout)
        print("FFPROBE ERROR:", result.stderr)

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail="ffprobe failed"
            )

        duration_text = result.stdout.strip()

        if not duration_text:
            raise HTTPException(
                status_code=500,
                detail="Could not determine duration"
            )

        duration = int(float(duration_text))

        return {
            "title": info.get("title"),
            "duration": duration
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )



import requests

TWITCH_CLIENT_ID = "jk4mes0y212gdrmohdha067bp50ggv"
TWITCH_CLIENT_SECRET = "4vlp07dzj2i1rhgia7zzgdbozchprt"

TWITCH_API = "https://api.twitch.tv/helix"

twitch_access_token = None


def get_twitch_token():
    global twitch_access_token

    if twitch_access_token:
        return twitch_access_token

    response = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
    )

    response.raise_for_status()

    twitch_access_token = response.json()["access_token"]

    return twitch_access_token


@app.get("/streamers")
def get_streamers(
    language: str = "ru",
    limit: int = 20,
):
    limit = min(max(limit, 1), 100)

    response = requests.get(
        f"{TWITCH_API}/streams",
        headers={
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {get_twitch_token()}",
        },
        params={
            "language": language,
            "first": limit,
        },
    )

    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    data = response.json()["data"]

    return [
        {
            "id": stream["user_id"],
            "login": stream["user_login"],
            "name": stream["user_name"],
            "title": stream["title"],
            "game": stream["game_name"],
            "viewers": stream["viewer_count"],
            "thumbnail": stream["thumbnail_url"]
                .replace("{width}", "640")
                .replace("{height}", "360"),
        }
        for stream in data
    ]


@app.get("/streamers/{user_id}/videos")
def get_streamer_videos(
    user_id: str,
    limit: int = 20,
):
    limit = min(max(limit, 1), 100)

    response = requests.get(
        f"{TWITCH_API}/videos",
        headers={
            "Client-ID": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {get_twitch_token()}",
        },
        params={
            "user_id": user_id,
            "type": "archive",
            "first": limit,
        },
    )

    if not response.ok:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text,
        )

    data = response.json()["data"]

    return [
        {
            "id": video["id"],
            "title": video["title"],
            "url": video["url"],
            "thumbnail": video["thumbnail_url"]
                .replace("%{width}", "640")
                .replace("%{height}", "360"),
            "duration": video["duration"],
            "created_at": video["created_at"],
            "views": video["view_count"],
        }
        for video in data
    ]