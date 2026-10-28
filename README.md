# RealityTrace

Evidence-backed CCTV event investigation system. Upload multiple camera recordings, ask natural-language questions, and get a structured, chronological evidence timeline with before/during/after frames, observed facts vs inferences, and cross-camera candidate links.

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- ffmpeg (`brew install ffmpeg` or `apt install ffmpeg`)

### Setup

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env — add MODEL_API_KEY for real AI analysis (optional; falls back to mock)

# 2. Backend
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
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

## Configuration

`.env` variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `MODEL_API_KEY` | — | Meta Model API key (optional — mock client used if absent) |
| `META_API_BASE_URL` | `https://api.meta.ai/v1` | Vision API base URL |
| `FINDIT_DATA_DIR` | `./data` | Storage root for videos, frames, DB |
| `FINDIT_FRAME_EXTRACTION_FPS` | `1.0` | Frames per second to extract |
| `FINDIT_MAX_FRAMES_PER_VIDEO` | `300` | Cap on frames extracted per video |
| `FINDIT_MAX_API_CALLS_PER_VIDEO` | `300` | Cap on vision API calls per video |
| `FINDIT_MAX_VIDEO_SIZE_MB` | `500` | Upload size limit |

## Architecture and Data Flow

```
Upload video
    │
    ▼
recordings table (SQLite)
    │
    ▼ (background task)
FFmpeg: extract frames → frames/{recording_id}/
    │
    ▼
Vision model (MuseSpark or Mock): analyze each frame
    │
    ▼
observations table — one row per detected object per frame
    │
    ▼
event_extractor.py — groups nearby observations into Events
    │
    ▼
events table — structured events with evidence_frames JSON
    │
    ▼
correlator.py — finds cross-camera candidate links
    │
    ▼
event_links table
    │
    ▼
investigate.py — ranks events by query relevance, builds timeline
    │
    ▼
POST /api/investigations/ → InvestigationResult
```

### Key modules

| Module | Role |
|---|---|
| `app/processor.py` | Frame extraction, model calls, observation storage |
| `app/event_extractor.py` | Groups observations → Events with before/during/after frames |
| `app/investigate.py` | NL query engine — retrieves events, builds timeline |
| `app/correlator.py` | Conservative cross-camera candidate links |
| `app/meta_client.py` | Vision model provider (MuseSpark + Mock) |
| `app/routes/investigations.py` | POST /api/investigations/ |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/api/recordings/upload` | Upload video with optional camera name/location/datetime |
| GET | `/api/recordings/` | List all recordings |
| GET | `/api/recordings/{id}` | Get recording status |
| DELETE | `/api/recordings/{id}` | Delete recording |
| GET | `/api/search/?q=…` | Legacy object search |
| POST | `/api/investigations/` | Investigate an incident |

### Investigation request/response

```json
POST /api/investigations/
{
  "query": "What happened before the person left the building?",
  "recording_ids": ["optional-filter"],
  "time_start_seconds": null,
  "time_end_seconds": null
}
```

Response includes:
- `events[]` — chronological list, each with `observed_facts`, `inferred_claims`, `uncertainty_notes`, `evidence_frames` (before/during/after), `cross_camera_links`
- `narrative` — text summary assembled from retrieved evidence only
- `evidence_gaps` — recordings with no matching events
- `timestamp_ordering_reliable` — whether absolute ordering is available

## Testing

```bash
cd backend
source .venv/bin/activate
pytest                    # all tests (115)
pytest -v tests/test_investigation.py   # investigation-specific tests
pytest -m live_api        # live API smoke test (requires MODEL_API_KEY)
```

Frontend type check:

```bash
cd frontend
npm run build
```

**Tests never call external APIs.** The `MockMetaClient` provides deterministic responses keyed by frame content hash.

## Limitations

1. **No real-time processing** — videos are processed after upload; there is no live stream support.
2. **Vision model accuracy** — depends entirely on what the vision model reports; no ground-truth validation has been run.
3. **Cross-camera correlation is conservative** — all links are labeled "candidate" unless confidence exceeds 0.85. Similar appearance is explicitly noted as insufficient to confirm identity.
4. **No facial recognition** — the system does not identify individuals. Object and appearance descriptions are used only for relevance scoring.
5. **Unsynchronized clocks** — when recordings lack absolute timestamps, chronological ordering is best-effort and is flagged clearly.
6. **Scale** — tested with small video sets. The SQLite DB and synchronous processing are not designed for large camera networks.
7. **Mock client** — without `MODEL_API_KEY`, the mock client returns deterministic empty responses; no actual scene analysis occurs.

## Hackathon Demo Script

### Setup (one time)
```bash
cp .env.example .env
# Optionally add MODEL_API_KEY for real AI results
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000 &
cd frontend && npm run dev &
```

### Demo flow

1. Open http://localhost:3000 — shows **RealityTrace** header.

2. **Cameras tab** → Upload two short video clips:
   - First clip: set Camera name = "Entrance Camera", location = "Front Door", datetime = now
   - Second clip: set Camera name = "Hallway Camera", location = "Main Corridor", datetime = (now + 2 minutes)

3. Wait for status to show **INDEXED** (polls every 3s).

4. **Investigate tab** → Click the example query:
   > "Find when the red bag was left near the door"

5. Results appear with:
   - Chronological event timeline
   - Before / During / After frame navigation on each card
   - Green "Observed facts" vs amber "Inferences (unconfirmed)"
   - Cross-camera candidate links (if any appearance matches found)
   - Evidence gaps for recordings with no matching events
   - Confidence percentage and uncertainty notes

6. Try the time filter query:
   > "Show events between 00:10 and 01:00"

7. Switch to **Search tab** for the original object-search experience (backward compatible).

### Key talking points
- Every event is traceable to a source frame and original video timestamp.
- The system never invents events — if evidence is absent, it says so.
- Observed facts and inferences are explicitly labeled at different trust levels.
- Cross-camera links are "candidate" by default — appearance alone never confirms identity.
- Works without a live API key using the deterministic mock client.
