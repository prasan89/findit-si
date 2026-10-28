# RealityTrace — Forensic Vision Accuracy Audit (P0)

Date: 2026-10-28. Tree: `main` at `a901f52` (clean). Audited live against local backend `:8000` + frontend `:3000`.

## 1. Executive summary

All three reported failures were **application defects, not model failures**. The vision model
(`meta/muse-spark-1.3`) correctly identified the bird in every piece of retained evidence, including
one new controlled live call made during this audit. The pipeline corrupted correct model output in
two places — heuristic category inference and query relevance scoring — and then cached a wrong
answer. Three minimal fixes (commits `2284cb3`, `37c998d`, `a901f52`, all already on `main` and
re-verified here) resolve every observed failure. This audit added the full Rule 5 regression matrix
and the end-to-end frame trace below. No production-code changes were required by this audit itself.

## 2. Confirmed root causes

| # | Failure observed | Layer | Root cause (evidenced) | Fix commit |
|---|---|---|---|---|
| 1 | `any human detected` → bird labeled `Person`, 94% | Indexing/persistence (`_infer_event_type`) | Substring keyword match: the ants' *"multiple **individuals**"* matched `"individual"` → stored `person_detected` | `2284cb3` |
| 2 | Same query returned that row | Query matching (`_score_event`) | Generic token `"detected"` matched stored type text `"person detected"` (0.63) with zero human evidence | `2284cb3` (subject gate) + `a901f52` (stopwords) |
| 3 | `any birds detected?` → 0 events | Query matching | Plural `"birds"` never substring-matches singular `"bird"`; no bird category existed | `37c998d` (`bird_detected` + synonym coverage) |
| 4 | `any dog detected?` → 2 bird events | Query matching | `"detected"` matched `"bird detected"` type text (0.63); `"dog"` matched nothing | `a901f52` |
| 5 | Wrong answers persisted after code fixes | Indexing/persistence | `investigation_cache` served stale rows (poisoned dog/human answers observed) | Cache clear + `CACHE_VERSION` 1→2 |

The 94% was never a person probability: it is the **mean of the model's per-object
`detection_score`s** (0.96/0.92/… for bird/ants frames). It was left untouched; the false category
was fixed instead. UI rendering was faithful throughout (it renders whatever `event_type` the API
returns) — no API/frontend defect found.

## 3. End-to-end trace, representative frame (test2, t=6.0s, visibly a black bird)

Recording `af826d8f` (`test2`, camera_location `bird`).

1. **Frame:** `frames/af826d…/af826d…_6_000.jpg`, ~200 KB, sha256 `6474ccb6…`. Visually: black bird holding an insect, dark soil (viewed directly).
2. **Model request:** `POST {META_API_BASE_URL}/chat/completions`, `model: muse-spark-1.3`, temperature 0.1, `detail: high`, JPEG data URL + `ANALYSIS_PROMPT` (code `meta_client.py`; Bearer auth, redacted). Stored rows were indexed under prompt v1 (recovered from git); current code sends prompt v2.
3. **Raw model response:** **not retained** — only the parsed analysis dict is stored in `frame_cache`. No raw HTTP body exists anywhere. Stated, not invented.
4. **Parsed output (frame_cache, muse-spark-1.3, prompt v1):** `label: "black bird"`, `confidence: 0.98`, *"standing stationary on dark soil … holding an insect in its beak"*. Correct.
5. **Stored observation** (`54fb3b31…`): `object_label: "black bird"`, `detection_score: 0.98`, same scene text — faithful copy of (4).
6. **Event:** `person_detected` (substring bug) → `scene_change` (after `2284cb3` re-extraction) → `bird_detected` (current, after `37c998d` re-extraction). Confidence 0.944 = group mean, unchanged by every repair.
7. **Retrieval (current code, measured):** `birds`→hit (synonym coverage), `human`/`dog`→0.0 (subject gate / no match).
8. **API (live curl):** `any birds detected?` → both `bird_detected` events; `any human detected` → `[]`; `any dog detected?` → `[]`.
9. **UI (live browser):** 2 × `Bird` badges, 0 × `Person`; evidence preview plays the correct recording; timeline/playback intact (screenshot `/tmp/audit-bird-timeline.png`).

## 4. Raw-model vs stored-event vs displayed-result

| Stage | Bird content | Person content | Dog content |
|---|---|---|---|
| Visible frame | black bird + insect | none | none |
| Raw model response | **not retained** | — | — |
| Parsed analysis (cache) | "black bird", 0.98 | none | none |
| Stored observation | "black bird", 0.98 | none | none |
| Stored event (current) | `bird_detected`, 0.944 | none | none |
| Displayed (bird query) | 2 × Bird | none | none |
| Displayed (human/dog query) | correctly absent | correctly absent | correctly absent |

Live cross-check (this audit, 1 call): current prompt v2 on the same frame bytes →
`"black bird"`, 0.95, *"no people or vehicles visible"*. Stored v1 analysis, live v2 analysis, and
the visible pixels all agree. Model exonerated from two independent samples (not a proof of general
accuracy).

## 5. Changes made and why

This audit changed **test code only** — the application fixes were the three verified commits above:

- `backend/tests/test_vision_accuracy_matrix.py` (new, 10 tests): the Rule 5 matrix — bird/dog/person/vehicle ground-truth events × `any birds/dog/human/vehicle detected`, singular/plural/punctuation variants, absent-object (`zebra`) and empty-scene rows, identity-preservation checks.
- Prior verified fixes (unchanged by this audit): word-boundary subject inference, subject-gated retrieval with synonym coverage, `bird_detected` category + `Bird` label, contradiction guard surfacing uncertainty notes, generic-token stopwords, `CACHE_VERSION` bump.

No re-indexing of frames was needed: indexed evidence was always correct; only deterministic,
$0 re-extraction ran (with DB backups kept under `/tmp`).

## 6. Regression tests and actual results

- `backend/.venv/bin/python -m pytest -q` → **423 passed, 1 deselected** (`live_api`), 7.6s
- `npm test` → **15/15**; `npx tsc --noEmit` → clean; `npm run build` → green (prior run; no build-affecting files changed since, and rebuilds are withheld while the dev server serves)
- `npx playwright test` → **5/5 E2E** (last run; no prod changes since)
- Live browser matrix (this audit, system Chrome vs `:3000`/`:8000`): dog→0, birds→2 Birds, human→0 — **PASSED**

## 7. Model/API calls and measured cost

Exactly **one** live call in this audit: `muse-spark-1.3`, prompt v2, 9.4s latency,
4187 in / 1259 out tokens ≈ **$0.011** (from the repo's documented muse-spark-1.3 rates).
All other validation used mocks, stored data, and deterministic tests ($0).

## 8. Remaining limitations and unresolved failures

- One frame is diagnostic evidence, not model-accuracy proof; no claim is made about general detection accuracy (n=2 bird recordings locally).
- Raw model responses are never retained — future forensics depend on parsed cache/observations. Consider persisting raw bodies (with retention limits).
- `scene_change` remains the unknown-subject bucket; species beyond bird/person/vehicle (e.g. dog, cat) match by description text only, with no dedicated category.
- Bare generic queries (e.g. `detected` alone) take the pre-existing return-all pure-filter path.
- Three live text-agent `ValueError`s (non-JSON output) were observed in logs during multi-agent runs; the designed fallback path handled them (trace completed, `success=False` recorded). Unrelated to classification; left for a reliability pass.

## 9. Next steps

1. Keep the Rule 5 matrix green; extend it when new subject classes are added.
2. Optionally persist raw frame-analysis responses for future forensics.
3. Optionally add dedicated categories for other common species only with labeled evidence.
4. Consider a bounded live multi-frame evaluation before any production accuracy claim.

---

`APPLICATION BUG FIXED — REGRESSION TESTS PASS`
