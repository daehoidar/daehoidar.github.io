import os
import sqlite3
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from yt_dlp import YoutubeDL

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "guestbook.db"

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "https://daehoidar.github.io",
]


class GuestbookEntryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    message: str = Field(min_length=1, max_length=300)


class GuestbookEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    name: str
    message: str
    timestamp: int


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guestbook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def sanitize_text(value: str) -> str:
    cleaned = value.strip()
    if "<" in cleaned or ">" in cleaned:
        raise ValueError("부등호 문자는 사용할 수 없습니다.")
    return cleaned


app = FastAPI(title="Cafe Minu API", version="1.0.0")

# ── yt-dlp 음악 스트리밍 ─────────────────────────────────────────
PLAYLIST_ID = "PLAPySWyRggBE8Clp8tdoZtY6JisKa0uRR"

# 캐시: {video_id: {"url": ..., "expires": epoch}}
_stream_cache: dict[str, dict] = {}
CACHE_TTL = 3600  # 1시간 (YouTube URL은 보통 6시간 유효)


def _extract_audio_url(video_id: str) -> str:
    """yt-dlp로 YouTube 영상에서 오디오 스트림 URL을 추출한다."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=False
        )
        return info["url"]


def _get_cached_url(video_id: str) -> str:
    """캐시된 URL이 유효하면 반환, 아니면 새로 추출한다."""
    cached = _stream_cache.get(video_id)
    if cached and cached["expires"] > time.time():
        return cached["url"]
    url = _extract_audio_url(video_id)
    _stream_cache[video_id] = {"url": url, "expires": time.time() + CACHE_TTL}
    return url

origins_env = os.getenv("ALLOWED_ORIGINS", "")
allowed_origins = [origin.strip() for origin in origins_env.split(",") if origin.strip()]
if not allowed_origins:
    allowed_origins = DEFAULT_ALLOWED_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ── 음악 API ─────────────────────────────────────────────────────
@app.get("/api/music/playlist")
def get_playlist():
    """플레이리스트의 모든 곡 정보(id, title, thumbnail)를 반환한다."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/playlist?list={PLAYLIST_ID}",
            download=False,
        )
    tracks = []
    for entry in info.get("entries", []):
        vid = entry.get("id", "")
        tracks.append({
            "id": vid,
            "title": entry.get("title", "Unknown"),
            "thumbnail": f"https://i.ytimg.com/vi/{vid}/default.jpg",
        })
    return {"tracks": tracks}


@app.head("/api/music/stream")
@app.get("/api/music/stream")
def proxy_stream(video_id: str = Query(..., min_length=1), request: Request = None):
    """백엔드가 오디오를 대신 가져와서 브라우저에 중계한다 (Range 요청 지원)."""
    try:
        url = _get_cached_url(video_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"스트림 URL 추출 실패: {exc}"
        ) from exc

    is_head = request and request.method == "HEAD"

    # HEAD 요청: 메타데이터만 반환 (본문 없음)
    if is_head:
        upstream = httpx.head(url, timeout=10.0, follow_redirects=True)
        return StreamingResponse(
            iter([b""]),
            headers={
                "Accept-Ranges": "bytes",
                "Content-Type": upstream.headers.get("content-type", "audio/mp4"),
                "Content-Length": upstream.headers.get("content-length", "0"),
            },
        )

    # GET 요청: Range 헤더를 그대로 전달하고 청크 단위로 스트리밍
    req_headers = {}
    range_header = request.headers.get("range") if request else None
    if range_header:
        req_headers["Range"] = range_header

    client = httpx.Client(follow_redirects=True)
    upstream = client.send(
        client.build_request("GET", url, headers=req_headers),
        stream=True,
    )

    resp_headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": upstream.headers.get("content-type", "audio/mp4"),
    }
    if "content-length" in upstream.headers:
        resp_headers["Content-Length"] = upstream.headers["content-length"]
    if "content-range" in upstream.headers:
        resp_headers["Content-Range"] = upstream.headers["content-range"]

    status_code = 206 if upstream.status_code == 206 else 200

    def _iter():
        try:
            yield from upstream.iter_bytes(chunk_size=64 * 1024)
        finally:
            upstream.close()
            client.close()

    return StreamingResponse(
        _iter(),
        status_code=status_code,
        headers=resp_headers,
    )


@app.get("/api/guestbook", response_model=list[GuestbookEntry])
def list_guestbook() -> list[GuestbookEntry]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, name, message, timestamp FROM guestbook ORDER BY timestamp DESC"
        ).fetchall()

    return [
        GuestbookEntry(
            key=str(row["id"]),
            name=row["name"],
            message=row["message"],
            timestamp=row["timestamp"],
        )
        for row in rows
    ]


@app.post("/api/guestbook", response_model=GuestbookEntry, status_code=201)
def create_guestbook(entry: GuestbookEntryCreate) -> GuestbookEntry:
    try:
        name = sanitize_text(entry.name)
        message = sanitize_text(entry.message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not name or not message:
        raise HTTPException(status_code=400, detail="이름/메시지를 입력해주세요.")

    timestamp = int(__import__("time").time() * 1000)

    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO guestbook(name, message, timestamp) VALUES (?, ?, ?)",
            (name, message, timestamp),
        )
        conn.commit()
        entry_id = cursor.lastrowid

    return GuestbookEntry(
        key=str(entry_id),
        name=name,
        message=message,
        timestamp=timestamp,
    )
