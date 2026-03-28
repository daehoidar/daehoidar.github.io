import os
import sqlite3
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

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

# ── 실시간 방문자 추적 ───────────────────────────────────────────
_visitors: dict[str, float] = {}  # {visitor_id: last_heartbeat_timestamp}
VISITOR_TTL = 60  # 60초 동안 heartbeat 없으면 퇴장 처리

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


# ── 방문자 API ────────────────────────────────────────────────────
@app.post("/api/visitors/heartbeat")
def visitor_heartbeat(visitor_id: str):
    """방문자 heartbeat 등록. 30초마다 호출."""
    now = time.time()
    _visitors[visitor_id] = now
    # 만료된 방문자 정리
    expired = [k for k, v in _visitors.items() if now - v > VISITOR_TTL]
    for k in expired:
        del _visitors[k]
    return {"count": len(_visitors)}


@app.get("/api/visitors/count")
def visitor_count():
    """현재 접속자 수 반환."""
    now = time.time()
    active = sum(1 for v in _visitors.values() if now - v <= VISITOR_TTL)
    return {"count": active}


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
