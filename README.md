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
| **Phase 6 — Reliability & Security** | | |
| `META_CONNECT_TIMEOUT_S` | `10.0` | Meta API connect timeout (seconds) |
| `META_READ_TIMEOUT_S` | `60.0` | Meta API vision read timeout (seconds) |
| `META_TEXT_READ_TIMEOUT_S` | `45.0` | Meta API text-LLM read timeout (seconds) |
| `MAX_API_ATTEMPTS` | `3` | Max attempts per Meta API call (transient failures only) |
| `BASE_BACKOFF_SECONDS` | `1.0` | Retry backoff base; exponential with jitter, honors `Retry-After` |
| `META_BACKOFF_MAX_S` | `8.0` | Backoff cap (seconds) |
| `VISION_MAX_OUTPUT_TOKENS` | `2000` | Vision `max_completion_tokens` cap |
| `TEXT_MAX_OUTPUT_TOKENS` | `1500` | Text-LLM `max_completion_tokens` cap |
| `AGENT_TIMEOUT_SECONDS` | `30.0` | Per-agent execution timeout; exceeded agents are `timed_out` |
| `INVESTIGATION_TIMEOUT_SECONDS` | `120.0` | Overall investigation deadline; expiry yields a `PARTIAL` trace |
| `INVESTIGATION_MAX_CONCURRENT` | `4` | Max concurrent investigations (`503` when full) |
| `INVESTIGATION_MAX_LLM_CALLS` | `8` | Max model calls per multi-agent investigation |
| `UPLOAD_MAX_RECORDINGS` | `0` | Recording quota (`0` = unlimited; `429` when reached) |
| `FFMPEG_TIMEOUT_S` | `300.0` | Decoder timeout for frame extraction (ffprobe capped at 60s) |
| `FRAME_CACHE_RETENTION_DAYS` | `0` | Frame-cache retention (`0` = keep; prune via `prune_frame_cache`) |
| `API_TOKEN` | _(empty)_ | When set, mutating endpoints require `X-API-Token`; reads stay open |
| `RATE_LIMIT_PER_MINUTE` | `240` | Per-IP limit on expensive POST routes (`0` = off) |
| `MAX_QUERY_CHARS` | `2000` | Max investigation query length |
| `MAX_RECORDING_IDS` | `50` | Max recording IDs per investigation |
| **Phase 8 — Cost & Performance** | | |
| `LLM_MAX_CONCURRENT` | `2` | Max parallel Meta API requests per process (`0` = unbounded) |
| `AGENT_PARALLEL_PAIRS` | `true` | Run independent LLM-agent pairs concurrently |
| `INVESTIGATION_CACHE_TTL_S` | `3600` | Investigation-result cache TTL (`0` = disabled) |
| `INVESTIGATION_CACHE_MAX_ENTRIES` | `200` | Result-cache size cap (oldest trimmed past the cap) |

### Reliability behavior (Phase 6)

- **Retries**: only transient failures are retried — HTTP `408`/`429`/`5xx`, connect/read/write errors, and timeouts. `401`/`403`/`400`/`422`, malformed model responses, and budget breaches fail fast. Backoff is exponential with full jitter and honors the server's `Retry-After` header (capped).
- **Pipeline**: each agent has its own timeout and the investigation has an overall deadline. Timed-out agents are marked `timed_out` (never rewritten as successful); on deadline expiry the trace persists as `PARTIAL` with a best-effort report only when retrieval finished.
- **Spending**: per-video frame/call caps, per-investigation budget + model-call cap, global budget reserve, and bounded retries. `/api/usage` reports provider-reported (measured) totals; per-call figures are estimates and labeled as such.
- **Caching**: frame analyses are cached by content hash + model + prompt version, so results are never reused across incompatible models or prompts. `indexing_status_detail` reports API calls vs cache hits; prune old rows with `prune_frame_cache(db_path, days)`.

### Security model and data retention (Phase 6)

- **No user authentication**: anyone who can reach the backend can read recordings (UUIDs are unguessable but listable via `GET /api/recordings/`). For sensitive footage, run on an isolated network. Setting `API_TOKEN` gates mutating endpoints (upload, delete, investigations) behind `X-API-Token`; reads stay open so the local demo works without shipping a secret to the browser.
- **Keys stay server-side**: `MODEL_API_KEY`/`META_API_KEY` are never sent to the browser, never logged, and never persisted in traces, usage rows, or error messages (covered by leakage tests).
- **Uploads**: extension + magic-bytes validation, streamed size cap, optional quota, filename sanitization, UUID storage, traversal-safe paths, ISO-8601 datetime validation.
- **Rate limiting**: fixed-window per-IP limit on the three expensive POST routes (`429` + `Retry-After` when exceeded).
- **Retention**: `DELETE /api/recordings/{id}` removes the video, frames, thumbnail, and DB rows (observations/events cascade). Content-hash model analyses in `frame_cache` are retained per `FRAME_CACHE_RETENTION_DAYS` (`0` = keep indefinitely; prune manually).
- **LLM output is untrusted**: model responses are shape-validated, defensively parsed, and never executed — they flow only into text fields, never into shell commands, file paths, or SQL.

### Cost and performance behavior (Phase 8)

- **Parallelism**: the two independent LLM-agent pairs (Video+Object, Contradiction+Evidence) run concurrently; all other stages stay ordered. Results assemble in fixed order; failures stay per-agent. Disable with `AGENT_PARALLEL_PAIRS=false` for strict sequential execution.
- **API concurrency**: all Meta requests share one process-wide semaphore (`LLM_MAX_CONCURRENT`, default 2), acquired per attempt and never held across backoff.
- **Result cache**: successful investigation responses are cached by query + recording versions + config fingerprint (`INVESTIGATION_CACHE_TTL_S`, default 1h). Warm hits skip all work and execute zero model calls; recording changes invalidate automatically. Results with failed/timed-out agents are never cached. Repeat `Idempotency-Key` values replay the same response, or `422` on parameter mismatch. Concurrent duplicates coalesce onto one computation.
- **Benchmarks**: `backend/.venv/bin/python eval/benchmark.py` runs both modes over the dataset via in-process ASGI and writes `eval/benchmark_report.json` (latencies, call counts, cold/warm). Offline latencies measure framework overhead with mocked models — not production capacity.

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
