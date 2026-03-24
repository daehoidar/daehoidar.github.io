# Cafe Minu Guestbook Backend

`daehoidar.github.io` 방명록을 위한 FastAPI 백엔드입니다.

## 1) 로컬 실행

```bash
cd guestbook-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

확인:

```bash
curl http://localhost:8000/health
```

## 2) API

- `GET /api/guestbook`: 방명록 목록 조회(최신순)
- `POST /api/guestbook`: 방명록 작성

요청 예시:

```bash
curl -X POST http://localhost:8000/api/guestbook \
  -H 'Content-Type: application/json' \
  -d '{"name":"minu","message":"hello"}'
```

## 3) Fly.io 배포

```bash
cd guestbook-backend
fly launch --copy-config --ha=false
fly deploy
```

필수 환경변수:

- `ALLOWED_ORIGINS`: CORS 허용 도메인 목록(쉼표 구분)

예:

```text
https://daehoidar.github.io,http://localhost:5500,http://127.0.0.1:5500
```

> 참고: 기본 저장소는 컨테이너 로컬 파일(`data/guestbook.db`)입니다. 재배포 시 유지가 필요하면 Fly Volume/Postgres로 전환하세요.
