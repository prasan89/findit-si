# FindIt

Search across multiple videos to find where you last saw an object.

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- ffmpeg (install via `brew install ffmpeg` or `apt install ffmpeg`)

### Setup

```bash
# 1. Copy env file
cp .env.example .env
# Edit .env and add your META_API_KEY

# 2. Backend
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Frontend
cd ../frontend
npm install

# 4. Run (two terminals)
# Terminal 1 — backend:
cd backend && uvicorn app.main:app --reload --port 8000
# Terminal 2 — frontend:
cd frontend && npm run dev
```

Open http://localhost:3000

## Testing

```bash
cd backend
pytest
```
