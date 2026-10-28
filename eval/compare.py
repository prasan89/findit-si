#!/usr/bin/env python3
"""Standard vs Multi-Agent evaluation comparison (Phase 5).

Offline (default): scores the checked-in deterministic reference predictions
(eval/reference_predictions.json) against the versioned dataset. No backend,
no model calls, zero cost.

    backend/.venv/bin/python eval/compare.py
    backend/.venv/bin/python eval/compare.py --dataset v1   # upgraded v1 GT

Live (explicit opt-in, spends real API credits):
    REALITYTRACE_LIVE_EVAL=1 backend/.venv/bin/python eval/compare.py --live \\
        --backend http://localhost:8000 --recording-ids <id> [<id> ...]

Live safeguards: requires BOTH --live AND REALITYTRACE_LIVE_EVAL=1, checks
the /api/usage budget floor before running, caps queries/recordings, runs
sequentially, and reports measured usage deltas.

Outputs (always rewritten):
    eval/comparison_report.json  — machine-readable report
    eval/comparison_report.md    — human-readable summary
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).parent
REPO_ROOT = EVAL_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from eval.dataset import composition, load_v1_as_v2, load_v2
    from eval.metrics import (METRIC_DEFINITIONS, match_events, prf,
                              score_cross_camera, score_event_detection,
                              score_evidence_quality, score_temporal_accuracy,
                              summarize_agent_runs)
except ImportError:  # script-mode fallback when eval/ is on sys.path directly
    sys.path.insert(0, str(EVAL_DIR))
    from dataset import composition, load_v1_as_v2, load_v2  # type: ignore
    from metrics import (METRIC_DEFINITIONS, match_events, prf,  # type: ignore
                         score_cross_camera, score_event_detection,
                         score_evidence_quality, score_temporal_accuracy,
                         summarize_agent_runs)

REFERENCE_FILE = EVAL_DIR / "reference_predictions.json"
REPORT_JSON = EVAL_DIR / "comparison_report.json"
REPORT_MD = EVAL_DIR / "comparison_report.md"

LIVE_ENV_VAR = "REALITYTRACE_LIVE_EVAL"
DEFAULT_MIN_REMAINING_USD = 5.0

MATCH_KWARGS = ("time_tolerance_seconds", "require_camera",
                "require_event_type", "keyword_overlap_min")


def _code_revision() -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5,
                              cwd=str(REPO_ROOT))
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def _match_kwargs(ds: dict) -> dict:
    rules = ds.get("match_rules", {})
    return {k: rules[k] for k in MATCH_KWARGS if k in rules}


def _score_query(query_def: dict, preds: list[dict], ds: dict) -> dict:
    """Query-level P/R against expected/ambiguous sets (predictions pre-matched)."""
    expected = set(query_def.get("expected_events", []))
    ambiguous = set(query_def.get("ambiguous_events", []))
    matching = match_events(ds["events"], preds, **_match_kwargs(ds))
    gt_of_pred = {m["pred_index"]: m["gt_id"] for m in matching["matches"]}

    tp_ids: list[str] = []
    fp_pred: list[int] = []
    ambiguous_hits: list[dict] = []
    for m in matching["matches"]:
        if m["gt_id"] in expected:
            tp_ids.append(m["gt_id"])
        elif m["gt_id"] in ambiguous:
            ambiguous_hits.append({"gt_id": m["gt_id"],
                                   "pred_index": m["pred_index"]})
        else:
            fp_pred.append(m["pred_index"])
    # Unmatched predictions are false positives (returned but irrelevant).
    fp_pred.extend(matching["false_positive_pred_indices"])
    fn_ids = sorted(expected - set(tp_ids))
    scores = prf(len(tp_ids), len(fp_pred), len(fn_ids))
    kind = query_def.get("kind", "positive")
    passed: bool | None = None
    if kind == "negative":
        passed = len(fp_pred) == 0 and len(ambiguous_hits) == 0
    return {
        "query_id": query_def["id"],
        "query": query_def["query"],
        "kind": kind,
        "expected_events": sorted(expected),
        "n_returned": len(preds),
        "true_positives": sorted(tp_ids),
        "false_negatives": fn_ids,
        "false_positive_pred_indices": sorted(fp_pred),
        "ambiguous_hits": ambiguous_hits,
        "precision": scores["precision"],
        "recall": scores["recall"],
        "f1": scores["f1"],
        "negative_case_passed": passed,
    }


def _union_events(per_query_preds: dict[str, list[dict]]) -> list[dict]:
    seen: dict[str, dict] = {}
    order: list[dict] = []
    for preds in per_query_preds.values():
        for pred in preds:
            key = str(pred.get("event_id", f"idx:{len(order)}"))
            if key not in seen:
                seen[key] = pred
                order.append(pred)
    return order


def _claims_from_events(mode: str, qid: str, preds: list[dict]) -> list[dict]:
    claims = []
    for pred in preds:
        refs = []
        for frame in pred.get("evidence_frames", []) or []:
            if isinstance(frame, dict):
                refs.append({"recording_id": pred.get("recording_id"),
                             "timestamp_seconds": frame.get("timestamp_seconds")})
        claims.append({
            "claim_id": f"{mode}:{qid}:{pred.get('event_id', '?')}",
            "text": str(pred.get("action_description", "")),
            # No human annotations exist in Phase 5: support is unmeasurable.
            "supported": None,
            "contradicted": None,
            "evidence": refs,
        })
    return claims


def _claims_from_final_reports(mode: str, per_query: dict[str, dict]) -> list[dict]:
    claims = []
    for qid, payload in per_query.items():
        report = (payload.get("final_report") or {})
        for item in report.get("claim_evidence", []) or []:
            claims.append({
                "claim_id": f"{mode}:{qid}:report:{len(claims)}",
                "text": str(item.get("claim", "")),
                "supported": None,
                "contradicted": None,
                "evidence": list(item.get("evidence_refs", []) or []),
                "auditor_verified": item.get("verified"),
            })
    return claims


def _cc_pairs_from_events(preds: list[dict]) -> tuple[list[list[str]], dict[str, dict]]:
    """Return (unordered GT-mappable pairs pending mapping, event index).

    Links are directed in storage; scoring dedups to unordered pairs.
    Mapping pred event IDs -> GT IDs happens in score_mode via matching.
    """
    index = {str(p.get("event_id")): p for p in preds if p.get("event_id")}
    pairs: list[list[str]] = []
    for pred in preds:
        src = str(pred.get("event_id", ""))
        for link in pred.get("cross_camera_links", []) or []:
            tgt = link.get("target_event_id")
            if tgt and str(tgt) in index:
                pairs.append([src, str(tgt)])
    return pairs, index


def score_mode(
    mode: str,
    per_query_preds: dict[str, list[dict]],
    per_query_payloads: dict[str, dict],
    ds: dict,
    recording_index: list[dict],
) -> dict:
    """Score one mode end-to-end. Returns the mode's report section."""
    query_scores = []
    failed_queries: list[dict] = []
    for q in ds["queries"]:
        try:
            preds = per_query_preds.get(q["id"], [])
            query_scores.append(_score_query(q, preds, ds))
        except Exception as exc:  # never let one query kill the report
            failed_queries.append({"query_id": q["id"], "error": str(exc)[:300]})

    union = _union_events(per_query_preds)
    detection = score_event_detection(ds["events"], union, **_match_kwargs(ds))
    temporal = score_temporal_accuracy(detection["matching"], ds["events"], union)

    # Cross-camera: map predicted endpoint IDs to GT IDs through matching.
    pairs, index = _cc_pairs_from_events(union)
    gt_of_pred_event: dict[str, str] = {}
    for m in detection["matching"]["matches"]:
        pred = union[m["pred_index"]]
        if pred.get("event_id"):
            gt_of_pred_event[str(pred["event_id"])] = m["gt_id"]
    gt_pairs = []
    unmapped_links = 0
    for src, tgt in pairs:
        if src in gt_of_pred_event and tgt in gt_of_pred_event:
            gt_pairs.append([gt_of_pred_event[src], gt_of_pred_event[tgt]])
        else:
            unmapped_links += 1
    # Dedup directed links to unordered pairs for scoring (count kept).
    seen_pairs: set[tuple[str, str]] = set()
    deduped: list[list[str]] = []
    for a, b in gt_pairs:
        key = tuple(sorted((a, b)))
        if key not in seen_pairs:
            seen_pairs.add(key)
            deduped.append([a, b])
    cc = ds.get("cross_camera_associations", {})
    cross_camera = score_cross_camera(deduped, cc.get("valid", []),
                                      cc.get("invalid", []), cc.get("ambiguous", []))
    cross_camera["n_directed_links"] = len(pairs)
    cross_camera["n_unmapped_links"] = unmapped_links

    # Evidence quality: retrieval claims (+ report claims for multi-agent).
    claims: list[dict] = []
    for qid, preds in per_query_preds.items():
        claims.extend(_claims_from_events(mode, qid, preds))
    report_claims = _claims_from_final_reports(mode, per_query_payloads)
    claims.extend(report_claims)
    evidence = score_evidence_quality(claims, recording_index)
    evidence["n_report_claims"] = len(report_claims)
    evidence["auditor_verified_true"] = sum(
        1 for c in report_claims if c.get("auditor_verified") is True)
    evidence["auditor_verified_false"] = sum(
        1 for c in report_claims if c.get("auditor_verified") is False)
    evidence["auditor_verified_null"] = sum(
        1 for c in report_claims if c.get("auditor_verified") is None)

    # Query-level macro averages over defined values only.
    def _avg(key: str) -> dict:
        vals = [q[key] for q in query_scores if q.get(key) is not None]
        return {"mean": (sum(vals) / len(vals)) if vals else None,
                "n_defined": len(vals), "n_queries": len(query_scores)}

    # Observability (multi-agent payloads carry agent runs; standard: none).
    agent_runs: list[dict] = []
    for payload in per_query_payloads.values():
        agent_runs.extend(payload.get("agents", []) or [])
    observability = (summarize_agent_runs(agent_runs) if agent_runs
                     else {"available": False,
                           "note": "Standard mode runs no agents; no agent observability."})

    latencies = [p.get("latency_ms_noncomparable", p.get("latency_seconds", 0) * 1000
                       if "latency_seconds" in p else None)
                 for p in per_query_payloads.values()]
    latencies = [float(v) for v in latencies if v is not None]

    return {
        "queries": query_scores,
        "failed_queries": failed_queries,
        "query_precision_macro": _avg("precision"),
        "query_recall_macro": _avg("recall"),
        "event_detection": detection,
        "temporal_accuracy": temporal,
        "cross_camera": cross_camera,
        "evidence_quality": evidence,
        "observability": observability,
        "latency_ms": {"per_query_noncomparable": latencies,
                       "note": "Wall-clock on the local machine; not comparable across machines."},
    }


# ------------------------------------------------------------- offline mode --
def run_offline(ds: dict, reference_path: Path = REFERENCE_FILE) -> dict:
    """Score checked-in reference predictions. Zero backend/model usage."""
    if not reference_path.exists():
        raise FileNotFoundError(
            f"{reference_path} not found. Regenerate with: "
            "backend/.venv/bin/python eval/build_reference.py")
    ref = json.loads(reference_path.read_text())
    if ref.get("dataset_version") != ds.get("version"):
        raise ValueError(
            f"Reference dataset_version {ref.get('dataset_version')!r} != "
            f"dataset {ds.get('version')!r}; regenerate the reference.")

    fixture_ids: dict[str, str] = (ref.get("fixture", {}).get("recording_ids", {}) or {})
    id_to_key = {v: k for k, v in fixture_ids.items()}
    recording_index = list(ds.get("recordings", []))
    for rec in ds.get("recordings", []):
        fid = fixture_ids.get(rec["key"])
        if fid:
            recording_index.append({"key": fid, "recording_id": fid,
                                    "duration_seconds": rec["duration_seconds"]})

    modes: dict[str, dict] = {}
    for mode in ("standard", "multi_agent"):
        payloads: dict[str, dict] = ref.get(mode, {}) or {}
        preds_key = "events" if mode == "standard" else "retrieved_events"
        per_query_preds = {qid: (p.get(preds_key, []) or []) for qid, p in payloads.items()}
        modes[mode] = score_mode(mode, per_query_preds, payloads, ds, recording_index)

    return {
        "evaluation_mode": "offline",
        "dataset": composition(ds),
        "code_revision": _code_revision(),
        "model_config": ref.get("model_config", {}),
        "evaluation_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cache": {"cold_warm_separated": False,
                  "note": ("Offline fixture performs no frame processing; "
                           "frame-cache hits/misses unavailable.")},
        "usage": {"live_api_calls": 0, "tokens_in": None, "tokens_out": None,
                  "cost_usd_measured": 0.0,
                  "note": "Offline run: no live model calls; cost $0."},
        "modes": modes,
        "metric_definitions": METRIC_DEFINITIONS,
        "match_rules": ds.get("match_rules", {}),
        "known_limitations": ds.get("known_limitations", []),
    }


# ---------------------------------------------------------------- live mode --
def _require_live_opt_in() -> None:
    if os.environ.get(LIVE_ENV_VAR) != "1":
        raise SystemExit(
            f"Live evaluation refused: set {LIVE_ENV_VAR}=1 in the environment "
            "AND pass --live (explicit opt-in required).")


def run_live(args: argparse.Namespace, ds: dict) -> dict:
    """Run both modes against a live backend. Spends real API credits."""
    _require_live_opt_in()
    sys.path.insert(0, str(EVAL_DIR))
    import run_eval  # noqa: E402  (reuse upload/wait/HTTP helpers)

    backend = args.backend.rstrip("/")
    try:
        run_eval.http_get(f"{backend}/api/recordings/")
    except Exception as exc:
        raise SystemExit(f"Backend not reachable at {backend}: {exc}")

    try:
        usage_before = run_eval.http_get(f"{backend}/api/usage/")
    except Exception as exc:
        raise SystemExit(f"Cannot read /api/usage for budget precheck: {exc}")
    remaining = float(usage_before.get("remaining_usd", 0.0))
    if remaining < args.min_remaining_usd:
        raise SystemExit(
            f"Live evaluation refused: remaining ${remaining:.2f} < floor "
            f"${args.min_remaining_usd:.2f}. Aborting before spending.")

    recording_ids: list[str] = list(args.recording_ids or [])
    if args.upload:
        print("Uploading evaluation clips…")
        for cam in ds["recordings"]:
            clip = EVAL_DIR / cam["clip"]
            if not clip.exists():
                print(f"  SKIP: {clip} not found")
                continue
            rid = run_eval.upload_clip(
                backend, clip, cam["camera_name"], cam.get("camera_location", ""),
                cam["recording_start"], cam["recording_timezone"])
            recording_ids.append(rid)
            print(f"  {cam['camera_name']} -> {rid}")
        for rid in recording_ids:
            status = run_eval.wait_for_indexed(backend, rid, timeout=args.index_timeout)
            print(f"  [{rid[:8]}] {status['processing_status']}")
    if not recording_ids:
        raise SystemExit("No recordings: pass --recording-ids or --upload.")
    recording_ids = recording_ids[:args.max_recordings]

    # Map live recording IDs -> dataset recording keys by camera_name.
    all_recs = run_eval.http_get(f"{backend}/api/recordings/")
    live_meta = {r["recording_id"]: r for r in all_recs if r["recording_id"] in recording_ids}
    cam_to_key = {r["camera_name"]: r["key"] for r in ds["recordings"]}
    recording_index = list(ds.get("recordings", []))
    for rid, meta in live_meta.items():
        recording_index.append({"key": rid, "recording_id": rid,
                                "duration_seconds": meta.get("video_duration_seconds")})

    queries = ds["queries"][:args.max_queries]
    passes = ["cold"] + (["warm"] if args.cache_passes > 1 else [])
    report_modes: dict[str, dict] = {}
    usage_after_modes: dict[str, dict] = {}

    for mode in ("standard", "multi_agent"):
        per_query_preds: dict[str, list[dict]] = {}
        per_query_payloads: dict[str, dict] = {}
        for pass_name in passes:
            for q in queries:
                t0 = time.time()
                try:
                    if mode == "standard":
                        resp = run_eval.run_investigation(backend, q["query"], recording_ids)
                        preds = resp.get("events", [])
                        payload = {"events": preds,
                                   "evidence_gaps": resp.get("evidence_gaps", []),
                                   "latency_seconds": time.time() - t0,
                                   "cache_pass": pass_name}
                    else:
                        resp = run_eval.run_multi_agent_investigation(
                            backend, q["query"], recording_ids)
                        preds = resp.get("retrieved_events", [])
                        payload = dict(resp)
                        payload["latency_seconds"] = time.time() - t0
                        payload["cache_pass"] = pass_name
                except Exception as exc:
                    preds, payload = [], {"error": str(exc)[:300],
                                          "latency_seconds": time.time() - t0,
                                          "cache_pass": pass_name}
                key = q["id"] if pass_name == "cold" else f"{q['id']}:warm"
                per_query_preds[key] = preds
                per_query_payloads[key] = payload
                n = len(preds)
                print(f"  [{mode}/{pass_name}] {q['id']}: {n} event(s)")
        # Score cold pass as canonical; warm pass reported for cache comparison.
        cold_preds = {qid: p for qid, p in per_query_preds.items() if ":" not in qid}
        cold_payloads = {qid: p for qid, p in per_query_payloads.items() if ":" not in qid}
        section = score_mode(mode, cold_preds, cold_payloads, ds, recording_index)
        if args.cache_passes > 1:
            warm_preds = {qid.split(":")[0]: p for qid, p in per_query_preds.items()
                          if ":" in qid}
            warm_payloads = {qid.split(":")[0]: p for qid, p in per_query_payloads.items()
                             if ":" in qid}
            section["warm_cache_pass"] = score_mode(
                mode, warm_preds, warm_payloads, ds, recording_index)
        report_modes[mode] = section
        try:
            usage_after_modes[mode] = run_eval.http_get(f"{backend}/api/usage/")
        except Exception:
            usage_after_modes[mode] = {}

    usage_after = usage_after_modes.get("multi_agent", {})
    spent_delta = (float(usage_after.get("total_spent_usd", 0.0))
                   - float(usage_before.get("total_spent_usd", 0.0)))
    return {
        "evaluation_mode": "live",
        "dataset": composition(ds),
        "code_revision": _code_revision(),
        "model_config": {"backend": backend, "recording_ids": recording_ids},
        "evaluation_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cache": {"cold_warm_separated": args.cache_passes > 1,
                  "passes": passes,
                  "note": ("Warm pass = immediate repeat run; frame-level cache "
                           "hits are not exposed remotely, so separation is "
                           "run-order-based and approximate.")},
        "usage": {"live_api_calls": None,
                  "note": "Call counts from /api/usage snapshots below.",
                  "usage_before": usage_before,
                  "usage_after_by_mode": usage_after_modes,
                  "spent_delta_usd_measured": round(spent_delta, 6)},
        "modes": report_modes,
        "metric_definitions": METRIC_DEFINITIONS,
        "match_rules": ds.get("match_rules", {}),
        "known_limitations": ds.get("known_limitations", []),
    }


# ------------------------------------------------------------------ reports --
def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _top_failures(section: dict, mode: str) -> list[str]:
    failures: list[str] = []
    for q in section.get("queries", []):
        if q.get("false_negatives") or q.get("false_positive_pred_indices"):
            failures.append(
                f"{mode} {q['query_id']} ({q['kind']}): "
                f"FN={q['false_negatives'] or '-'} "
                f"FP_preds={q['false_positive_pred_indices'] or '-'} "
                f"P={_fmt(q.get('precision'))} R={_fmt(q.get('recall'))}")
        if q.get("kind") == "negative" and q.get("negative_case_passed") is False:
            failures.append(f"{mode} {q['query_id']}: NEGATIVE case returned events")
    cc = section.get("cross_camera", {})
    for pair in cc.get("false_associations", []):
        failures.append(f"{mode} cross-camera FALSE association: {pair[0]} <-> {pair[1]}")
    for pair in cc.get("false_negatives", []):
        failures.append(f"{mode} cross-camera MISSED valid link: {pair[0]} <-> {pair[1]}")
    ev = section.get("evidence_quality", {})
    for item in ev.get("invalid_references", [])[:5]:
        failures.append(f"{mode} invalid evidence ref {item['ref']!r} ({item['reason']})")
    return failures


def write_markdown(report: dict, path: Path = REPORT_MD) -> None:
    ds = report.get("dataset", {})
    modes = report.get("modes", {})
    lines = [
        "# RealityTrace Evaluation Report (Phase 5)",
        "",
        f"- Evaluation mode: **{report.get('evaluation_mode')}**",
        f"- Dataset: {ds.get('dataset_id')} v{ds.get('version')} "
        f"({ds.get('n_recordings_synthetic', 0)} synthetic / "
        f"{ds.get('n_recordings_real', 0)} real recordings, "
        f"{ds.get('n_events')} events, {ds.get('n_queries')} queries)",
        f"- Code revision: {report.get('code_revision') or 'unavailable'}",
        f"- Timestamp: {report.get('evaluation_timestamp')}",
        f"- Model/config: `{json.dumps(report.get('model_config', {}))}`",
        "",
        "> All footage is synthetic color blocks. Results do NOT represent "
        "real-world CCTV performance.",
        "",
        "## Query-level results",
        "",
        "| Query | Kind | Mode | P | R | F1 | Returned | Expected |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for mode, section in modes.items():
        for q in section.get("queries", []):
            lines.append(
                f"| {q['query_id']} | {q['kind']} | {mode} | "
                f"{_fmt(q.get('precision'))} | {_fmt(q.get('recall'))} | "
                f"{_fmt(q.get('f1'))} | {q.get('n_returned')} | "
                f"{','.join(q.get('expected_events', [])) or '-'} |")
    lines += [
        "",
        "## Event detection (union over queries)",
        "",
        "| Mode | P | R | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode, section in modes.items():
        overall = section.get("event_detection", {}).get("overall", {})
        lines.append(
            f"| {mode} | {_fmt(overall.get('precision'))} | "
            f"{_fmt(overall.get('recall'))} | {_fmt(overall.get('f1'))} | "
            f"{overall.get('tp')} | {overall.get('fp')} | {overall.get('fn')} |")
    lines += [
        "",
        "## Temporal accuracy (matched events)",
        "",
        "| Mode | Rel mean | Rel median | Abs mean (n) | Missing abs |",
        "|---|---|---|---|---|",
    ]
    for mode, section in modes.items():
        temporal = section.get("temporal_accuracy", {})
        rel = temporal.get("relative_error", {})
        abs_e = temporal.get("absolute_error", {})
        lines.append(
            f"| {mode} | {_fmt(rel.get('mean'))}s | {_fmt(rel.get('median'))}s | "
            f"{_fmt(abs_e.get('mean'))}s (n={abs_e.get('n')}) | "
            f"{temporal.get('n_missing_absolute')} missing / "
            f"{temporal.get('n_malformed_absolute')} malformed |")
    lines += [
        "",
        "## Cross-camera association",
        "",
        "| Mode | P | R | F1 | False assoc (rate) | Ambiguous hits | Unmapped |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode, section in modes.items():
        cc = section.get("cross_camera", {})
        lines.append(
            f"| {mode} | {_fmt(cc.get('precision'))} | {_fmt(cc.get('recall'))} | "
            f"{_fmt(cc.get('f1'))} | {cc.get('false_association_count')} "
            f"({_fmt(cc.get('false_association_rate'))} of {cc.get('false_association_denominator')}) | "
            f"{len(cc.get('ambiguous_hits', []))} | {cc.get('n_unmapped_links')} |")
    lines += [
        "",
        "## Evidence quality",
        "",
        "| Mode | Coverage | Ref validity | Support | Unsupported | Contradiction |",
        "|---|---|---|---|---|---|",
    ]
    for mode, section in modes.items():
        ev = section.get("evidence_quality", {})
        lines.append(
            f"| {mode} | {_fmt(ev.get('evidence_coverage'))} "
            f"({ev.get('claims_with_evidence')}/{ev.get('n_claims')}) | "
            f"{_fmt(ev.get('reference_validity_rate'))} "
            f"({ev.get('n_valid_references')}/{ev.get('n_references')}) | "
            f"{_fmt(ev.get('evidence_support_rate'))} | "
            f"{_fmt(ev.get('unsupported_claim_rate'))} | "
            f"{_fmt(ev.get('contradiction_rate'))} |")
    lines += ["",
              "_Support/contradiction are null without human annotations; "
              "reference existence is never treated as support._",
              "",
              "## Observability (multi-agent)",
              ""]
    ma_obs = modes.get("multi_agent", {}).get("observability", {})
    if ma_obs.get("available") is False:
        lines.append(ma_obs.get("note", "unavailable"))
    else:
        lines.append(f"- Agent runs: {ma_obs.get('n_agents')} "
                     f"({json.dumps(ma_obs.get('by_status', {}))})")
        lines.append(f"- Total agent time: {_fmt(ma_obs.get('total_duration_ms'), 1)}ms")
        lines.append(f"- Measured tokens: in={ma_obs.get('tokens_in_measured')} "
                     f"out={ma_obs.get('tokens_out_measured')}")
        lines.append(f"- Measured cost: ${ma_obs.get('cost_measured_usd')}")
        lines.append(f"- Cache: hits={ma_obs.get('cache_hits')} "
                     f"misses={ma_obs.get('cache_misses')} "
                     f"(unavailable={ma_obs.get('cache_unavailable')})")
        lines.append(f"- Retries: {ma_obs.get('retries')}, "
                     f"timeouts: {ma_obs.get('timeouts')}")
        if ma_obs.get("failures"):
            lines.append(f"- Failures: {json.dumps(ma_obs['failures'])}")
        if ma_obs.get("skip_reasons"):
            lines.append(f"- Skips: {len(ma_obs['skip_reasons'])} "
                         f"(see JSON for reasons)")
    lines += ["",
              "## Top failures",
              ""]
    failures: list[str] = []
    for mode, section in modes.items():
        failures.extend(_top_failures(section, mode))
    lines.extend(f"- {f}" for f in failures[:20] or ["None."])
    lines += ["",
              "## Usage",
              "",
              f"```json\n{json.dumps(report.get('usage', {}), indent=2)}\n```",
              "",
              "## Known limitations",
              ""]
    lines.extend(f"- {lim}" for lim in report.get("known_limitations", []))
    lines.append("")
    path.write_text("\n".join(lines))
    print(f"Wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RealityTrace Phase 5 comparison runner")
    parser.add_argument("--dataset", choices=["v1", "v2"], default="v2",
                        help="v2 native dataset or upgraded v1 ground truth")
    parser.add_argument("--live", action="store_true",
                        help="Run against a live backend (also needs REALITYTRACE_LIVE_EVAL=1)")
    parser.add_argument("--backend", default="http://localhost:8000")
    parser.add_argument("--recording-ids", nargs="+", default=[])
    parser.add_argument("--upload", action="store_true",
                        help="Upload dataset clips before live evaluation")
    parser.add_argument("--max-queries", type=int, default=8)
    parser.add_argument("--max-recordings", type=int, default=10)
    parser.add_argument("--cache-passes", type=int, choices=[1, 2], default=1)
    parser.add_argument("--min-remaining-usd", type=float,
                        default=DEFAULT_MIN_REMAINING_USD)
    parser.add_argument("--index-timeout", type=int, default=180)
    parser.add_argument("--out", default=str(REPORT_JSON))
    args = parser.parse_args()

    ds = load_v1_as_v2() if args.dataset == "v1" else load_v2()
    print(f"Dataset: {ds['dataset_id']} v{ds['version']} "
          f"({len(ds['events'])} events, {len(ds['queries'])} queries)")

    if args.live:
        report = run_live(args, ds)
    else:
        if os.environ.get(LIVE_ENV_VAR) == "1":
            print(f"NOTE: {LIVE_ENV_VAR}=1 is set but --live was not passed; "
                  "running OFFLINE (no model calls).")
        report = run_offline(ds)

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"Wrote {out_path}")
    write_markdown(report)

    # Stdout summary.
    print("\nSummary:")
    for mode, section in report["modes"].items():
        overall = section["event_detection"]["overall"]
        cc = section["cross_camera"]
        print(f"  {mode}: event P={_fmt(overall.get('precision'))} "
              f"R={_fmt(overall.get('recall'))} F1={_fmt(overall.get('f1'))} | "
              f"CC false_assoc={cc.get('false_association_count')}/"
              f"{cc.get('false_association_denominator')}")


if __name__ == "__main__":
    main()

