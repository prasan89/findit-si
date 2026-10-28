#!/usr/bin/env python3
"""
RealityTrace evaluation script.

Usage:
    # From project root, with backend venv active:
    python eval/run_eval.py

    # With a running backend:
    BACKEND_URL=http://localhost:8000 python eval/run_eval.py

    # Upload clips and run (requires backend running):
    python eval/run_eval.py --upload --backend http://localhost:8000

Measures:
- Event detection per ground-truth event (recall)
- False event rate (precision proxy)
- Timestamp error for events with reliable timestamps
- Evidence frame availability
- Query retrieval quality (precision@k, recall@k)
- Cross-camera association precision
- End-to-end latency
"""
import argparse
import json
import sys
import time
import os
from pathlib import Path
from typing import Optional
import urllib.request
import urllib.error
import urllib.parse

EVAL_DIR = Path(__file__).parent
GT_FILE = EVAL_DIR / "ground_truth.json"
RESULTS_FILE = EVAL_DIR / "eval_results.json"

DEFAULT_BACKEND = "http://localhost:8000"


def http_get(url: str) -> dict:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def http_post_json(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def upload_clip(backend: str, clip_path: Path, camera_name: str, camera_location: str,
                recording_datetime: str, recording_timezone: str) -> str:
    """Upload a clip and return recording_id."""
    import http.client
    import mimetypes
    import uuid

    # Multipart form upload
    boundary = uuid.uuid4().hex
    host = backend.replace("http://", "").replace("https://", "")
    conn = http.client.HTTPConnection(host, timeout=60)

    with open(clip_path, "rb") as f:
        video_data = f.read()

    fields = {
        "camera_name": camera_name,
        "camera_location": camera_location,
        "recording_datetime": recording_datetime,
        "recording_timezone": recording_timezone,
    }

    body_parts = []
    for name, value in fields.items():
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}")

    filename = clip_path.name
    body_parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: video/mp4\r\n\r\n"
    )
    body = "\r\n".join(body_parts).encode("utf-8") + video_data + f"\r\n--{boundary}--\r\n".encode("utf-8")

    conn.request("POST", "/api/recordings/upload",
                 body=body,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    resp = conn.getresponse()
    result = json.loads(resp.read())
    if resp.status not in (200, 202):
        raise RuntimeError(f"Upload failed {resp.status}: {result}")
    return result["recording_id"]


def wait_for_indexed(backend: str, recording_id: str, timeout: int = 120) -> dict:
    """Poll until recording is INDEXED or PARTIAL."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = http_get(f"{backend}/api/recordings/{recording_id}")
        s = status.get("processing_status", "")
        if s in ("INDEXED", "PARTIAL", "FAILED"):
            return status
        print(f"  [{recording_id[:8]}] status={s} … waiting")
        time.sleep(3)
    raise TimeoutError(f"Recording {recording_id} did not finish indexing in {timeout}s")


def run_investigation(backend: str, query: str, recording_ids: Optional[list[str]] = None) -> dict:
    body = {"query": query}
    if recording_ids:
        body["recording_ids"] = recording_ids
    return http_post_json(f"{backend}/api/investigations/", body)


def score_event_recall(gt_event: dict, actual_events: list[dict]) -> dict:
    """
    Check if a ground-truth event was detected.
    Returns {found: bool, best_match_ts_error: float|None, has_evidence_frames: bool}.
    """
    cam = gt_event["camera"]
    ts_range = gt_event["timestamp_seconds_range"]
    keywords = gt_event["expected_keywords"]

    best_ts_error = None
    has_evidence = False

    for ev in actual_events:
        # Match camera
        if ev.get("camera_name") != cam:
            continue

        # Check timestamp overlap (event midpoint within extended range)
        ev_ts = ev.get("timestamp_seconds", -1)
        range_mid = (ts_range[0] + ts_range[1]) / 2
        ts_tolerance = (ts_range[1] - ts_range[0]) / 2 + 2.0  # window half + 2s slack
        ts_err = abs(ev_ts - range_mid)

        if ts_err > ts_tolerance * 2:
            continue

        # Check keyword overlap in action_description + observed_facts
        text = " ".join([
            ev.get("action_description", ""),
            " ".join(ev.get("observed_facts", [])),
            " ".join(ev.get("object_descriptions", [])),
            ev.get("scene_description", "") or "",
        ]).lower()

        keyword_hits = sum(1 for k in keywords if k in text)
        if keyword_hits == 0:
            continue

        if best_ts_error is None or ts_err < best_ts_error:
            best_ts_error = ts_err
        if ev.get("evidence_frames"):
            has_evidence = True

    found = best_ts_error is not None
    return {
        "found": found,
        "best_timestamp_error_seconds": best_ts_error,
        "has_evidence_frames": has_evidence,
    }


def run_query_eval(backend: str, query_def: dict, recording_ids: list[str],
                   gt_events: list[dict], cam_to_rid: dict) -> dict:
    """Run a query and measure precision/recall."""
    t0 = time.time()
    result = run_investigation(backend, query_def["query"], recording_ids)
    latency = time.time() - t0

    returned_events = result.get("events", [])
    expected_ids = query_def.get("expected_events", [])
    not_expected = query_def.get("expected_not_returned", [])

    # Map GT ids to camera names
    gt_map = {e["id"]: e for e in gt_events}

    # True positives: expected GT events that appear in results
    true_positives = []
    false_negatives = []
    for gt_id in expected_ids:
        gt = gt_map.get(gt_id, {})
        cam = gt.get("camera", "")
        found_in_results = any(e.get("camera_name") == cam for e in returned_events)
        if found_in_results:
            true_positives.append(gt_id)
        else:
            false_negatives.append(gt_id)

    # False positives: GT events that should NOT appear but do
    false_positives = []
    for gt_id in not_expected:
        gt = gt_map.get(gt_id, {})
        cam = gt.get("camera", "")
        if any(e.get("camera_name") == cam for e in returned_events):
            false_positives.append(gt_id)

    precision = len(true_positives) / max(len(returned_events), 1)
    recall = len(true_positives) / max(len(expected_ids), 1) if expected_ids else 1.0

    return {
        "query_id": query_def["id"],
        "query": query_def["query"],
        "latency_seconds": round(latency, 2),
        "events_returned": len(returned_events),
        "true_positives": true_positives,
        "false_negatives": false_negatives,
        "false_positives": false_positives,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "narrative_length": len(result.get("narrative", "")),
        "evidence_gaps": result.get("evidence_gaps", []),
    }


def run_multi_agent_investigation(backend: str, query: str, recording_ids: Optional[list[str]] = None) -> dict:
    body = {"query": query, "budget_usd": 5.0}
    if recording_ids:
        body["recording_ids"] = recording_ids
    return http_post_json(f"{backend}/api/investigations/multi-agent", body)


def run_multi_agent_benchmark(backend: str, queries: list[dict], recording_ids: list[str]) -> None:
    print(f"\n{'─'*40}")
    print("Multi-Agent Investigation Benchmark")
    print(f"{'─'*40}")

    for q in queries[:3]:
        print(f"\n  Query: {q['query']}")
        t0 = time.time()
        try:
            result = run_multi_agent_investigation(backend, q["query"], recording_ids)
            latency = time.time() - t0

            agents = result.get("agents", [])
            completed = sum(1 for a in agents if a["status"] == "completed")
            skipped = sum(1 for a in agents if a["status"] == "skipped")
            failed = sum(1 for a in agents if a["status"] == "failed")
            total_cost = result.get("total_cost_usd", 0)

            print(f"  Agents: {completed} completed / {skipped} skipped / {failed} failed")
            print(f"  Total cost: ${total_cost:.5f}")
            print(f"  Total latency: {latency:.2f}s")

            for agent in agents:
                status_sym = "✓" if agent["status"] == "completed" else ("⊘" if agent["status"] == "skipped" else "✗")
                ms = f"{agent['duration_ms'] / 1000:.2f}s" if agent["duration_ms"] > 0 else "—"
                print(f"    {status_sym} {agent['agent_name']}: {agent['status']} ({ms})")

            fr = result.get("final_report")
            if fr:
                print(f"  Key findings: {len(fr.get('key_findings', []))}")
                print(f"  Evidence citations: {len(fr.get('evidence_citations', []))}")
        except Exception as e:
            print(f"  ERROR: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default=DEFAULT_BACKEND)
    parser.add_argument("--upload", action="store_true",
                        help="Upload clips from eval/clips/ before evaluating")
    parser.add_argument("--recording-ids", nargs="+",
                        help="Use existing recording IDs instead of uploading")
    parser.add_argument("--multi-agent", action="store_true",
                        help="Also run multi-agent benchmark")
    args = parser.parse_args()

    gt = json.loads(GT_FILE.read_text())
    cameras = gt["cameras"]
    gt_events = gt["ground_truth_events"]
    queries = gt["queries"]

    print(f"\n{'='*60}")
    print("RealityTrace Evaluation")
    print(f"Backend: {args.backend}")
    print(f"Ground truth events: {len(gt_events)}")
    print(f"Queries: {len(queries)}")
    print(f"{'='*60}\n")

    # Check backend health
    try:
        http_get(f"{args.backend}/api/recordings/")
    except Exception as e:
        print(f"ERROR: Backend not reachable at {args.backend}: {e}")
        print("Start the backend with: cd backend && uvicorn app.main:app --reload --port 8000")
        sys.exit(1)

    recording_ids = []
    cam_to_rid = {}

    if args.recording_ids:
        recording_ids = args.recording_ids
        print(f"Using provided recording IDs: {recording_ids}")
    elif args.upload:
        print("Uploading evaluation clips…")
        for cam in cameras:
            clip = EVAL_DIR / cam["clip"]
            if not clip.exists():
                print(f"  SKIP: {clip} not found")
                continue
            print(f"  Uploading {cam['camera_name']}…")
            try:
                rid = upload_clip(
                    args.backend, clip,
                    cam["camera_name"], cam["camera_location"],
                    cam["recording_datetime"], cam["recording_timezone"],
                )
                recording_ids.append(rid)
                cam_to_rid[cam["camera_name"]] = rid
                print(f"    → {rid}")
            except Exception as e:
                print(f"  ERROR uploading {cam['clip']}: {e}")

        if not recording_ids:
            print("No clips uploaded. Exiting.")
            sys.exit(1)

        print("\nWaiting for indexing…")
        indexed_statuses = {}
        for rid in recording_ids:
            try:
                status = wait_for_indexed(args.backend, rid)
                indexed_statuses[rid] = status
                print(f"  [{rid[:8]}] {status['processing_status']} — {status.get('frame_count', 0)} frames")
            except Exception as e:
                print(f"  [{rid[:8]}] ERROR: {e}")
    else:
        print("No --upload flag and no --recording-ids given.")
        print("Fetching existing indexed recordings…")
        try:
            all_recs = http_get(f"{args.backend}/api/recordings/")
            for r in all_recs:
                if r["processing_status"] in ("INDEXED", "PARTIAL"):
                    recording_ids.append(r["recording_id"])
                    cam_name = r.get("camera_name", "")
                    if cam_name:
                        cam_to_rid[cam_name] = r["recording_id"]
            print(f"Found {len(recording_ids)} indexed recording(s).")
        except Exception as e:
            print(f"ERROR fetching recordings: {e}")
            sys.exit(1)

    if not recording_ids:
        print("No recordings to evaluate against. Use --upload to upload eval clips.")
        sys.exit(0)

    # Refresh cam_to_rid from actual recordings
    try:
        all_recs = http_get(f"{args.backend}/api/recordings/")
        for r in all_recs:
            if r["recording_id"] in recording_ids and r.get("camera_name"):
                cam_to_rid[r["camera_name"]] = r["recording_id"]
    except Exception:
        pass

    # --- Event recall evaluation ---
    print(f"\n{'─'*40}")
    print("Event Detection (Recall)")
    print(f"{'─'*40}")
    print("Fetching all events via broad query…")
    try:
        broad_result = run_investigation(args.backend, "show all events", recording_ids)
        all_events = broad_result.get("events", [])
        print(f"Total events returned: {len(all_events)}")
    except Exception as e:
        print(f"ERROR: {e}")
        all_events = []

    recall_results = []
    for gt_ev in gt_events:
        r = score_event_recall(gt_ev, all_events)
        recall_results.append({"gt_id": gt_ev["id"], **r})
        status = "✓ FOUND" if r["found"] else "✗ MISSED"
        ts_info = f"  ts_err={r['best_timestamp_error_seconds']:.1f}s" if r["best_timestamp_error_seconds"] is not None else ""
        ev_info = "  evidence_frames=YES" if r["has_evidence_frames"] else "  evidence_frames=NO"
        print(f"  {status} {gt_ev['id']} ({gt_ev['camera']}){ts_info}{ev_info}")

    found_count = sum(1 for r in recall_results if r["found"])
    print(f"\nRecall: {found_count}/{len(gt_events)} = {found_count/len(gt_events):.0%}")
    if found_count == 0:
        print("  → All events missed. This is expected with MockMetaClient (returns empty objects).")
        print("  → With a real vision model, colored shapes should produce detections.")

    # --- Query evaluation ---
    print(f"\n{'─'*40}")
    print("Query Retrieval Quality")
    print(f"{'─'*40}")
    query_results = []
    for q in queries:
        try:
            qr = run_query_eval(args.backend, q, recording_ids, gt_events, cam_to_rid)
            query_results.append(qr)
            print(f"  {q['id']}: P={qr['precision']:.2f} R={qr['recall']:.2f}  "
                  f"returned={qr['events_returned']}  latency={qr['latency_seconds']:.1f}s")
        except Exception as e:
            print(f"  {q['id']}: ERROR {e}")

    # --- Cross-camera link quality ---
    print(f"\n{'─'*40}")
    print("Cross-Camera Correlation")
    print(f"{'─'*40}")
    cc_links = [(ev, lnk)
                for ev in all_events
                for lnk in ev.get("cross_camera_links", [])]
    print(f"Total cross-camera links: {len(cc_links)}")
    correct_links = 0
    false_assoc = 0
    for ev, lnk in cc_links:
        src_cam = ev.get("camera_name", "")
        tgt_cam = lnk.get("target_camera_name", "")
        link_type = lnk.get("link_type", "")
        conf = lnk.get("confidence", 0)
        # Determine if this link is expected based on GT
        expected = False
        for gt_ev in gt_events:
            if gt_ev["camera"] == src_cam and gt_ev.get("cross_camera_candidate"):
                cand_id = gt_ev["cross_camera_candidate"]
                cand_gt = next((e for e in gt_events if e["id"] == cand_id), None)
                if cand_gt and cand_gt["camera"] == tgt_cam:
                    expected = True
                    break
        if expected:
            correct_links += 1
        else:
            false_assoc += 1
        print(f"  {src_cam} → {tgt_cam}: {link_type} ({conf:.0%}) {'[expected]' if expected else '[false association]'}")

    if cc_links:
        cc_precision = correct_links / len(cc_links)
        print(f"\nCross-camera link precision: {correct_links}/{len(cc_links)} = {cc_precision:.0%}")
    else:
        print("No cross-camera links formed (expected with empty mock responses).")

    # --- Usage summary ---
    print(f"\n{'─'*40}")
    print("AI Credit Usage")
    print(f"{'─'*40}")
    try:
        usage = http_get(f"{args.backend}/api/usage/")
        print(f"  Spent: ${usage['total_spent_usd']:.4f} / ${usage['budget_limit_usd']:.2f}")
        print(f"  Remaining: ${usage['remaining_usd']:.4f}")
        for row in usage.get("by_model", []):
            print(f"  {row['provider']}/{row['model']}: {row['calls']} calls, "
                  f"in={row['tokens_in']} out={row['tokens_out']}, ${row['cost_usd']:.4f}")
    except Exception as e:
        print(f"  Could not fetch usage: {e}")

    # --- Summary ---
    avg_precision = sum(q["precision"] for q in query_results) / max(len(query_results), 1)
    avg_recall = sum(q["recall"] for q in query_results) / max(len(query_results), 1)
    avg_latency = sum(q["latency_seconds"] for q in query_results) / max(len(query_results), 1)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Event recall:          {found_count}/{len(gt_events)} = {found_count/len(gt_events):.0%}")
    print(f"Avg query precision:   {avg_precision:.2f}")
    print(f"Avg query recall:      {avg_recall:.2f}")
    print(f"Avg query latency:     {avg_latency:.1f}s")
    print(f"False CC associations: {false_assoc}")

    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "recording_ids": recording_ids,
        "event_recall": {
            "found": found_count,
            "total": len(gt_events),
            "rate": found_count / len(gt_events),
            "details": recall_results,
        },
        "queries": query_results,
        "cross_camera": {
            "total_links": len(cc_links),
            "correct": correct_links,
            "false_associations": false_assoc,
        },
        "avg_query_precision": avg_precision,
        "avg_query_recall": avg_recall,
        "avg_latency_seconds": avg_latency,
    }
    RESULTS_FILE.write_text(json.dumps(results, indent=2))
    print(f"\nDetailed results written to: {RESULTS_FILE}")

    if args.multi_agent and recording_ids:
        run_multi_agent_benchmark(args.backend, queries, recording_ids)


if __name__ == "__main__":
    main()
