# RealityTrace — Independent Video Event Verification Report

Date: 2026-10-29. Method: direct frame inspection with ffmpeg (uniform + targeted
sampling, cropped enlargements). No model API calls; no reliance on overlays,
captions, or prior summaries. All timestamps are clip-relative (HH:MM:SS.mmm),
measured from each file's start. Overlays (NDTV/CN9 captions, TikTok text, red
circles/arrows) are burned into the sources and were treated as untrusted.

## 1. Executive summary

- **Video 1 (gold-chain allegation):** a sudden run-up, physical contact, flight,
  and chase at ~00:00:41 are VERIFIED; the chain itself and any item changing
  possession are NOT visible — the snatching allegation is PARTIALLY VERIFIED.
  Suspects flee on foot; no vehicle departure is visible.
- **Video 2 (motorcycle/pedestrian):** a two-rider motorcycle stops beside a
  standing woman (~00:00:08–14) and leaves; proximity is VERIFIED but the nature
  of the interaction is UNVERIFIED — no contact, threat, or exchange is visible.
- **Video 3 (parking):** only normal pedestrian movement across the full clip;
  no entry, tampering, collision, or anomaly of any kind is visible.
- **Video 4 (entrance):** the woman walks steadily throughout; no trip, stumble,
  or fall occurs. Footwear sits off her path. Accident allegation CONTRADICTED.
- **Video 5 (fire):** genuine fire VERIFIED — onset ~00:00:14–15, intermittent
  flares, heavy smoke, burning through clip end. No people, evacuation, or
  response visible. Ignition source not determinable from video.
- **Video 6 (bovines):** horn-locked pushing VERIFIED; both animals run off with
  no collision, injury, or threat to any person visible.
- **Cross-video:** no shared people, vehicles, locations, or connected events
  can be established; all six appear unrelated.

## 2. File and timeline validation

| File | Recording | Duration | FPS | Resolution | Audio | Coverage |
|---|---|---|---|---|---|---|
| videoplayback.mp4 | VC1 c7f89dc6 | 48.44s | 30 | 360x640 | yes, present (content not assessed) | full @2s + t=8–18 @0.23s + t=38–48 @0.5s + contact crop |
| videoplayback (1).mp4 | VC2 357d217a | 27.14s | 30 | 360x640 | yes, present (content not assessed) | full @2s + t=6–14 @0.27s + proximity crop |
| videoplayback (2) (1).mp4 | VC3 e47a8cb2 | 36.36s | 25 | 360x640 | yes, present (content not assessed) | full @2s + t=14–24 @0.4s + t=24–36 @1s |
| videoplayback (3).mp4 | VC4 112850a1 | 13.82s | 30 | 360x640 | yes, present (content not assessed) | full @1s + t=3–9 @0.33s |
| videoplayback (4).mp4 | VC5 12254763 | 315.52s | 29.97 | 426x240 | none | full @15s + t=0–30 @1s + t=85–110 @1s + reconciled t=88–120 + exact t=90/105 + t=298–315 @1s |
| videoplayback (5).mp4 | VC6 7c3d7bc4 | 9.47s | 30 | 240x426 | none | full @1s + t=0–2 detail |

All files decoded without errors; no corrupted segments. One framing note: VC1 cuts
to a second camera angle at ~00:00:42.5 (CCTV stamp 19/03/2025 22:37:13); VC3 framing
shifts right at ~00:00:28–29 (possible pan or edit). Temporal relation of VC1's second
angle to the first is unknown. Audio transcription was not possible with available
tooling — a stated limitation, not a finding.

## 3. Per-video reports

### Video 1 — VC1: street table, alleged chain snatching (48.44s)

- 00:00:00–04: Person A (striped shirt) stands at round street table, leaning over
  a paper/menu. Motorcycle parked background with rider (yellow/blue) + passenger (red).
- 00:00:02–06: passenger dismounts; stands by motorcycle.
- 00:00:06–40: Person A stays at table (shifts, looks around ~00:00:24). Red-shirt
  Person B stands/wanders near motorcycle, never closer than ~3m to Person A
  (verified on 0.23s grid, t=8–18).
- 00:00:40.500: Person B suddenly sprints from motorcycle toward the table.
- 00:00:41.000–41.500: contact — bodies overlap in motion-blurred frames; Person A
  recoils/turns. Hand, neck, and chain area not discernible at this resolution.
- 00:00:41.500–42.500: Person B flees on foot toward background (white-car
  direction); Person A runs after him (chase). Neither heads to the motorcycle,
  which stays parked with its rider.
- 00:00:42.500–48.440: second camera angle — empty courtyard/benches, nobody;
  CCTV stamp runs 22:37:13–22:37:18. No people, no vehicle, no further action.
- Ambiguity: the object (chain), the exact contact point, and possession change
  are below visibility limits; angle-2 timing vs angle-1 is unknown; audio not assessed.

### Video 2 — VC2: lane, motorcycle and pedestrian (27.14s; footage ends ~00:00:24)

- 00:00:00–05: Person A (woman, grey dress) stands left side of lane holding an
  object (phone-like). CCTV stamp "Thu 06:55:xx".
- 00:00:04–08: motorcycle with 2 riders (blue shirts) enters from background-right,
  slows approaching the woman.
- 00:00:08–13: motorcycle stopped beside the woman; all parties adjacent and
  facing each other (within arm's reach). No grabbing, striking, pushing,
  collision, fall, or visible item exchange in any frame (0.27s grid).
- 00:00:13–15: motorcycle departs toward foreground-left at low speed; woman
  stays, watches, then looks at the object in her hands.
- 00:00:15–24: woman alone, standing/walking slowly, calm posture throughout.
- 00:00:24–27: TikTok end cards, no footage.
- Ambiguity: the interaction's nature (conversation vs confrontation vs anything
  else) is not visually distinguishable; no distress, flight, or aggression
  markers either way. Audio not assessed.

### Video 3 — VC3: apartment parking, night (36.36s)

- 00:00:00–12: woman in pink saree (Person A) near red car, left; white SUV parked.
  Nothing moves except Person A shifting position.
- 00:00:12–24: man in dark clothing (Person B) enters from background, walks
  across the lot past Person A (closest ~2m, 0.4s grid), continues right and
  exits. No contact, no pause, no gesture, no approach to any vehicle.
- 00:00:24–36: Person A walks in background near gate; no other people. Neither
  person touches any vehicle, door, window, or object at any point.
- Result: only normal pedestrian movement. No entry, tampering, removal,
  collision, obstruction, or anomaly visible in any sampled frame.

### Video 4 — VC4: house entrance, courtyard (13.82s)

- 00:00:00–13: woman (green blouse, cream saree) walks steadily from courtyard
  left across tiled ground to the house entrance and steps up. Gait continuous
  on 1s full coverage plus 0.33s grid (t=3–9): no stumble, trip, fall, balance
  loss, or sudden movement anywhere.
- Footwear (slippers) lies near the white car at frame right — off her walking
  line throughout; she never steps near, over, or around it.
- No injury, no consequence, no second person involved. Red circles/white arrows
  are source overlays tracking her feet and the slippers, not evidence.

### Video 5 — VC5: locker room fire (315.52s, CCTV "Camera Lockers", no audio)

- 00:00:00–14: normal room: lockers left, staircase right, bags on floor. No anomaly.
- 00:00:14–15: onset — sudden bright flash burst at locker base (left) with an
  immediate smoke puff (CCTV ~01:29:28–29). Appearance consistent with an
  electrical arc flash; the unit/cause inside the locker is not visible.
- 00:00:15–85: small persistent flames at locker base + growing smoke layer;
  intermittent brighter flares (e.g. ~00:01:37–45 region shows flare/lull cycling
  on seconds timescale with camera auto-exposure swings — sparse samples alias
  the intensity, verified by CCTV-anchored reconciliation frames).
- 00:01:30–03:00: fully involved: repeated large flares/whiteouts, heavy smoke
  filling the room, sustained glow at locker base. Visibility near zero at times.
- 00:04:58–05:15: fire continues through clip end (flames + dense smoke, CCTV
  01:33:10–27). No people, evacuation, alarm response, or firefighting appears
  in any of ~70 viewed frames spanning the full duration.
- Cannot be determined from video: ignition source/equipment fault (unseen),
  anything outside this camera's view, events after the clip ends.

### Video 6 — VC6: two bovines by a road (9.47s, no audio)

- 00:00:00–05: brown bovine (Animal A) and black bovine (Animal B) head-to-head,
  horns visibly interlocked/crossed (confirmed on enlarged t=0–2 frames), pushing.
- 00:00:05–08: animals separate slightly, keep pushing, then turn and run down
  the road away from camera. Parked red car on the right untouched; bystanders
  remain distant (background only).
- 00:00:08–09.5: both animals running off; no person, vehicle, or object struck;
  no fall or injury visible; outcome beyond the clip unknown.
- No collision, road accident, or human injury occurs in the footage.

## 4. Timestamped event log (clip-relative)

- VC1 00:00:02–06: passenger dismounts motorcycle, stands by it.
- VC1 00:00:40.500: red-shirt person sprints toward table man.
- VC1 00:00:41.000–41.500: physical contact (blurred), victim recoils.
- VC1 00:00:41.500–42.500: suspect flees on foot; victim chases toward background.
- VC1 00:00:42.500–48.440: second angle, empty courtyard, no action.
- VC2 00:00:04–08: two-rider motorcycle approaches standing woman, slows.
- VC2 00:00:08–13: stopped adjacent; facing interaction of unknown nature.
- VC2 00:00:13–15: motorcycle departs; woman remains, calm.
- VC3 00:00:12–24: man crosses lot past woman; no contact, no vehicle interaction.
- VC4 00:00:00–13: woman walks steadily to entrance; no incident.
- VC5 00:00:14–15: flash onset at locker base + smoke puff.
- VC5 00:00:15–05:15: growing fire, intermittent flares, heavy smoke to clip end.
- VC6 00:00:00–05: horn-locked pushing; 00:00:05–09.5: separation and flight, no impact.

## 5. Claim verification matrix

| Video | Timestamp | Observed event | Claim tested | Status | Confidence | Evidence / limitation |
|---|---|---|---|---|---|---|
| VC1 | 00:00:41 | run-up + contact + flight + chase | gold-chain snatching | PARTIALLY VERIFIED | Medium | act sequence fits; chain/object never visible |
| VC1 | 00:00:41 | blurred overlap at table | neck contact / chain removal | UNVERIFIED | Low | below resolution; no frame shows hand/neck/chain |
| VC1 | 00:00:41.5 | flight on foot, victim chases | suspect flees, incl. by vehicle | PARTIALLY VERIFIED | Medium | foot flight+chase seen; no vehicle departure |
| VC2 | 00:00:08–13 | bike stops beside woman, facing | theft attempt / confrontation | UNVERIFIED | Low | proximity only; no contact/threat/exchange seen |
| VC2 | 00:00:04–15 | slow approach, stop, slow exit | ordinary movement alternative | PARTIALLY VERIFIED | Medium | speeds calm, woman calm after; nature unknown |
| VC3 | full clip | normal walking only | any parking anomaly | CONTRADICTED | High | full coverage, nothing abnormal occurs |
| VC4 | full clip | steady walk, no fall | trip/fall on footwear | CONTRADICTED | High | 0.33s grid; slippers off-path; no accident |
| VC5 | 00:00:14–15 | flash + smoke onset at lockers | real fire starts | VERIFIED | High | onset frame + 5min progression to heavy fire |
| VC5 | 00:00:14 | bright burst character | electrical arcing | PARTIALLY VERIFIED | Medium | flash look fits; source unit unseen |
| VC5 | 00:00:14 | unseen interior of locker | ignition source / equipment fault | UNVERIFIED | Low | cannot see inside; no testing possible |
| VC5 | 00:01:30–05:15 | flares, dense smoke to end | fire continues, no response | VERIFIED | High | flames+glow in end frames; zero people seen |
| VC6 | 00:00:00–05 | interlocked horns, pushing | bovines fight, horns lock | VERIFIED | High | enlarged frames show crossed horns + pushing |
| VC6 | 00:00:05–09.5 | separation, flight, no impact | road accident / human injury | CONTRADICTED | High | car untouched, people distant, no fall |

## 6. Confirmed observations

- VC1: sudden approach, contact, foot flight, and immediate chase at ~00:00:41.
- VC2: motorcycle stops adjacent to pedestrian; calm departure; calm aftermath.
- VC3: two pedestrians cross paths without contact; vehicles untouched.
- VC4: steady uninterrupted walk; footwear present but off-path.
- VC5: genuine developing fire from ~00:00:14 through clip end; empty room throughout.
- VC6: horn-locked bovine pushing ending in flight with no impact.

## 7. Unverified or contradicted allegations

- Unverified: chain visible/removed, item possession change (VC1); interaction
  nature (VC2); ignition source/equipment fault (VC5).
- Contradicted: vehicle getaway (VC1 — flight is on foot); any parking anomaly
  (VC3); trip/fall (VC4); road accident or human injury (VC6).

## 8. Alternative explanations

- VC2: asking directions, known persons conversing, or brief confrontation — all
  fit the silent proximity; none is supported over the others.
- VC5: initial flash could be arcing equipment or another igniting fault; only
  inspection/testing of the locker unit can distinguish.
- VC1: contact + flight + chase fit snatch-and-run, but without a visible object
  an attempted (failed) grab or another dispute cannot be excluded.

## 9. Evidence gaps and follow-up

- Audio exists (VC1–VC4) but was not transcribed — shouting/alarms could clarify
  VC1/VC2; needs a listener or ASR pass.
- VC1: original unedited CCTV (both angles, full timestamps) + any police report
  on the alleged chain; angle-2 timing is unknown.
- VC5: fire-service/incident records, locker-unit inspection, cameras covering
  response; footage after 05:15.
- VC2/VC3/VC4/VC6: no video-internal gaps; nothing further required barring new claims.

## 10. Final assessment and review priority

1. **VC5 fire (highest):** confirmed genuine developing fire in an empty room with
   no visible response — warrants immediate human review of alarm/monitoring
   coverage and incident follow-up.
2. **VC1 contact + chase:** confirmed physical incident consistent with the
   allegation; needs human review with audio/original footage for the object question.
3. **VC2 proximity stop:** ambiguous by nature; low priority unless audio or a
   complaint adds context.
4. **VC3/VC4/VC6:** no incident, no injury, no action required from this footage.

What the video shows vs suggests vs unknown is separated per claim above. No
names, no legal conclusions, no intent attribution. No cross-video connections
found: distinct locations (street eatery, lane, parking, house, locker room,
roadside), no recurring identifiable person/vehicle/object across clips.
