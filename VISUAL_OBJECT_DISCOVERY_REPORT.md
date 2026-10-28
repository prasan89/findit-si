# RealityTrace — Visual Object Discovery Report (P0)

Date: 2026-10-28. Tree: `main` (committed with this report).

## 1. Confirmed root cause

**Case B: the object was described in stored observations, but query retrieval ignored it.**
No other case applied:

- Indexing recognized everything: the 30s video (`checi`, 30.016s) has 70 observations across
  t=0–29s (1 fps over the full duration) including `white grub`, `cream-colored larva`,
  `small black fly`; the 10s video (`test2`) has 14 observations including 6 ant descriptions.
  Frame files, timestamps, and evidence references are intact — not cases A, C, or D.
- The retrieval gap: `any insect detected` → tokens `[insect]` (generic words are stopwords),
  but no insect vocabulary existed, so the token matched nothing and both insect-bearing
  events scored 0.0. A secondary precision trap was found and closed during the fix: naive
  class-level matching would have returned the ants-only event for a `grub` query, so
  hypernym (`insect`) and hyponym (`grub`, `ants`, …) matching were separated.

## 2. Stored evidence for the example videos

- `checi` event (t=14.5, `bird_detected`, 25 descriptions): grub/larva/fly evidence quoted
  verbatim in `object_descriptions` (e.g. *"white grub (curved segmented body)"*).
- `test2` event (t=4.5, `bird_detected`): 5 ant descriptions (*"group of ants …"*), no grub
  words — correctly excluded from `grub` queries, correctly included for `insect`.
- Neither event's text contains the word "insect", which is exactly why hypernym expansion
  was required. Primary Bird categories were preserved (insect evidence is secondary).

## 3. Issue classification

Retrieval defect (plus a reporting gap: never-indexed/failed recordings were silently
skipped). Indexing, model output, persistence, API serialization, and UI rendering were all
verified correct and left untouched. No model change, no re-indexing, no re-extraction.

## 4. Files and architecture changed

- `backend/app/event_extractor.py` — `INSECT_SUBJECTS` (true insects + larval forms;
  spiders/`bug` excluded as ambiguous), `INSECT_HYPERNYMS`, `INSECT_LEMMA` pairs.
  Retrieval-class only: never re-types the primary subject.
- `backend/app/investigate.py` — insect hypernym gate + hyponym lemma-precision scoring;
  `_ineligible_notes()` reports never-indexed/failed recordings in `evidence_gaps`
  (both result paths), instead of silently skipping them.
- `backend/app/investigation_cache.py` — `CACHE_VERSION` 2→3 (retrieval + gaps changed).
- `backend/tests/test_insect_discovery.py` — 10 tests: grub retrieval, ants/larvae,
  bird-without-insect abstention, holding/carrying questions, ambiguous-object
  abstention without invented identity, paired person/dog/vehicle guards, grub
  lemma precision, never-indexed + failed reporting.

## 5. Tests added and actual results

- New suite: 10/10 (4 failed pre-fix, all pass post-fix).
- Full backend: **433 passed, 1 deselected** (`live_api`).
- Frontend: **15/15**, `tsc` clean; production build green (prior gate; no build-affecting
  files changed since).
- E2E (system Chrome, seeded mock backend): **6/6**.
- Live prod-DB matrix: `insect`→both, `grub`→checi only, `ants`→test2 only,
  `birds`→both, `human`/`dog`→[].

## 6. Browser verification results

Live `:3000`/`:8000`, system Chrome — **PASSED**: insect query returns 2 Bird events
quoting grub/larva/ants evidence; Bird categories intact; evidence preview plays the
correct recording; `grub` precise to checi; human/dog queries clean.
Screenshot: `/tmp/verify-insect-timeline.png`.

## 7. Model calls and measured cost

Zero live calls, $0. Indexing evidence was already complete; the fix is retrieval-only.

## 8. Remaining limitations

- Insect vocabulary covers common true insects + larval forms; rare species names and
  ambiguous words (`bug`, `spider`, `fly` as a verb in prose) are deliberately unmatched.
- Person/vehicle/bird classes remain pure synonym sets (e.g. `car` matches `truck`
  evidence); only the insect class has hypernym/hyponym precision, where evidence demanded it.
- No claim is made about insect detection accuracy beyond these two videos; the model
  must still name the creature for retrieval to find it.
