import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

# ── yt-dlp 음악 ──────────────────────────────────────────────────
PLAYLIST_ID = "PLAPySWyRggBE8Clp8tdoZtY6JisKa0uRR"
MUSIC_DIR = DATA_DIR / "music"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)


def _download_audio(video_id: str) -> Path:
    """서버에 오디오 파일을 다운로드한다. 이미 있으면 캐시된 파일 반환."""
    existing = list(MUSIC_DIR.glob(f"{video_id}.*"))
    if existing:
        return existing[0]

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(MUSIC_DIR / "%(id)s.%(ext)s"),
    }
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=True
        )
        ext = info.get("ext", "m4a")
        return MUSIC_DIR / f"{video_id}.{ext}"

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


@app.get("/api/music/stream")
def stream_audio(video_id: str = Query(..., min_length=1)):
    """오디오 파일을 서버에서 다운로드 후 직접 서빙한다."""
    try:
        file_path = _download_audio(video_id)
        return FileResponse(file_path, media_type="audio/mp4")
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"오디오 다운로드 실패: {exc}"
        ) from exc


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
