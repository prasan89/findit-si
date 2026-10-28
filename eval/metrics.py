#!/usr/bin/env python3
"""Deterministic evaluation metrics for RealityTrace (Phase 5).

Pure functions: no I/O, no model calls, no randomness. All metrics are
computed from explicit ground truth + explicit predictions. Undefined values
(zero denominators, missing timestamps, unmeasured provider usage) are
returned as None — never fabricated.

Metric definitions are exported via METRIC_DEFINITIONS for inclusion in
machine-readable reports.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

METRIC_DEFINITIONS: dict[str, str] = {
    "event_matching": (
        "Greedy 1:1 assignment. A prediction matches a GT event when: "
        "(a) camera matches (when require_camera), "
        "(b) |pred_ts - gt_midpoint| <= gt_half_window + time_tolerance_seconds, "
        "(c) prediction text contains >= keyword_overlap_min GT keywords "
        "(when GT lists keywords), "
        "(d) event_type matches (only when require_event_type). "
        "GT events in id order take the lowest-timestamp-error unmatched prediction."
    ),
    "precision": "TP / (TP + FP); None when the denominator is 0.",
    "recall": "TP / (TP + FN); None when the denominator is 0.",
    "f1": ("2*P*R / (P + R); 0.0 when P and R are defined but P + R == 0; "
             "None when P or R is undefined (zero denominator)."),
    "timestamp_error": (
        "For matched events: |pred_ts - gt_midpoint| in video-relative seconds. "
        "Absolute-timestamp error uses parsed ISO timestamps when BOTH sides provide "
        "valid absolute times; otherwise the case is reported under unavailable counts."
    ),
    "boundary_error": (
        "Signed (pred_point - gt_start) and (pred_point - gt_end) in seconds, where "
        "pred_point is the prediction timestamp (or predicted start/end when provided). "
        "None when GT provides no boundaries."
    ),
    "evidence_coverage": "Claims with >= 1 syntactically valid evidence reference / total claims.",
    "evidence_reference_validity": (
        "References whose recording exists AND 0 <= timestamp <= recording duration / total references. "
        "Validity is existence only — it does NOT imply the evidence supports the claim."
    ),
    "evidence_support_rate": (
        "Human-annotated supported claims / human-annotated claims. "
        "Computed ONLY from explicit human annotations (supported true/false); "
        "unannotated claims are excluded and counted separately."
    ),
    "unsupported_claim_rate": "Human-annotated unsupported claims / human-annotated claims.",
    "contradiction_rate": "Human-annotated contradicted claims / human-annotated claims.",
    "cross_camera_precision": "Predicted pairs in the VALID set / predicted pairs in (VALID + INVALID + UNLABELED). Ambiguous/excluded pairs never count.",
    "cross_camera_recall": "Predicted pairs in the VALID set / all VALID pairs; None when no valid pairs are labeled.",
    "false_association_count": "Predicted pairs in the INVALID set (unordered comparison).",
    "false_association_rate": "False association count / total predicted pairs; None when no pairs predicted.",
}

LOW_SAMPLE_THRESHOLD = 5


# ---------------------------------------------------------------- precision --
def prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    """Precision/recall/F1 with explicit denominators; None when undefined."""
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is None or recall is None:
        f1 = None
    elif (precision + recall) == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "precision_denominator": tp + fp,
        "recall_denominator": tp + fn,
    }


# ------------------------------------------------------------ event matching --
def _prediction_text(pred: dict[str, Any]) -> str:
    parts = [str(pred.get("action_description", "") or "")]
    parts.extend(str(x) for x in (pred.get("observed_facts") or []))
    parts.extend(str(x) for x in (pred.get("object_descriptions") or []))
    parts.append(str(pred.get("scene_description", "") or ""))
    return " ".join(parts).lower()


def _as_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def match_events(
    gt_events: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    time_tolerance_seconds: float = 2.0,
    require_camera: bool = True,
    require_event_type: bool = False,
    keyword_overlap_min: int = 1,
) -> dict[str, Any]:
    """Greedy 1:1 GT-to-prediction matching. See METRIC_DEFINITIONS['event_matching']."""
    matches: list[dict[str, Any]] = []
    used_pred: set[int] = set()
    matchable_gt = 0

    for gt in sorted(gt_events, key=lambda g: str(g.get("id", ""))):
        gt_id = gt.get("id", "")
        start = _as_float(gt.get("start_seconds",
                                 (gt.get("timestamp_seconds_range") or [None, None])[0]
                                 if isinstance(gt.get("timestamp_seconds_range"), (list, tuple)) else None))
        end = _as_float(gt.get("end_seconds",
                               (gt.get("timestamp_seconds_range") or [None, None])[1]
                               if isinstance(gt.get("timestamp_seconds_range"), (list, tuple)) else None))
        if start is None or end is None:
            continue
        matchable_gt += 1
        midpoint = (start + end) / 2.0
        window = (end - start) / 2.0 + time_tolerance_seconds
        keywords = [str(k).lower() for k in (gt.get("expected_keywords") or [])]
        gt_cam = gt.get("camera_name") or gt.get("camera")
        gt_type = gt.get("event_type")

        best_idx: int | None = None
        best_err: float | None = None
        for idx, pred in enumerate(predictions):
            if idx in used_pred:
                continue
            pred_ts = _as_float(pred.get("timestamp_seconds"))
            if pred_ts is None:
                continue
            if require_camera and gt_cam is not None:
                if pred.get("camera_name") != gt_cam:
                    continue
            if require_event_type and gt_type is not None:
                if pred.get("event_type") != gt_type:
                    continue
            err = abs(pred_ts - midpoint)
            if err > window:
                continue
            if keywords and keyword_overlap_min > 0:
                text = _prediction_text(pred)
                if sum(1 for k in keywords if k and k in text) < keyword_overlap_min:
                    continue
            if best_err is None or err < best_err:
                best_err = err
                best_idx = idx

        if best_idx is not None:
            used_pred.add(best_idx)
            pred = predictions[best_idx]
            matches.append({
                "gt_id": gt_id,
                "pred_index": best_idx,
                "pred_event_id": pred.get("event_id"),
                "timestamp_error_seconds": best_err,
                "pred_timestamp_seconds": _as_float(pred.get("timestamp_seconds")),
                "gt_midpoint_seconds": midpoint,
                "gt_window_seconds": [start, end],
            })

    matched_gt_ids = {m["gt_id"] for m in matches}
    false_negatives = [g.get("id", "") for g in gt_events if g.get("id", "") not in matched_gt_ids]
    false_positives = [i for i in range(len(predictions)) if i not in used_pred]
    return {
        "matches": matches,
        "matched_gt_ids": sorted(matched_gt_ids),
        "false_negative_gt_ids": false_negatives,
        "false_positive_pred_indices": false_positives,
        "tp": len(matches),
        "fp": len(false_positives),
        "fn": len(false_negatives),
        "n_gt_matchable": matchable_gt,
        "n_gt_unmatchable": len(gt_events) - matchable_gt,
    }


def score_event_detection(
    gt_events: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    **match_kwargs: Any,
) -> dict[str, Any]:
    """Event-level P/R/F1 plus per-event-type breakdown."""
    matching = match_events(gt_events, predictions, **match_kwargs)
    overall = prf(matching["tp"], matching["fp"], matching["fn"])

    by_type: dict[str, dict[str, Any]] = {}
    gt_by_id = {g.get("id"): g for g in gt_events}
    matched_by_gt = {m["gt_id"]: m for m in matching["matches"]}
    fp_by_type: dict[str, int] = {}
    for idx in matching["false_positive_pred_indices"]:
        ptype = predictions[idx].get("event_type", "unknown") or "unknown"
        fp_by_type[ptype] = fp_by_type.get(ptype, 0) + 1
    types = sorted({(g.get("event_type") or "unknown") for g in gt_events}
                   | set(fp_by_type))
    for etype in types:
        gt_of_type = [g for g in gt_events if (g.get("event_type") or "unknown") == etype]
        tp = sum(1 for g in gt_of_type if g.get("id") in matched_by_gt)
        fn = len(gt_of_type) - tp
        fp = fp_by_type.get(etype, 0)
        entry = prf(tp, fp, fn)
        entry["n_gt"] = len(gt_of_type)
        entry["low_sample"] = len(gt_of_type) < LOW_SAMPLE_THRESHOLD
        by_type[etype] = entry

    return {"matching": matching, "overall": overall, "by_event_type": by_type}


# ------------------------------------------------------------------ temporal --
def _parse_abs(value: Any) -> float | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _summary_stats(errors: list[float]) -> dict[str, Any]:
    if not errors:
        return {"n": 0, "mean": None, "median": None, "p90": None,
                "min": None, "max": None}
    ordered = sorted(errors)
    p90 = None
    if len(ordered) >= 10:
        # Nearest-rank p90; needs n>=10 for a meaningful high percentile.
        import math
        p90 = ordered[min(math.ceil(0.9 * len(ordered)) - 1, len(ordered) - 1)]
    return {
        "n": len(errors),
        "mean": statistics.fmean(errors),
        "median": statistics.median(errors),
        "p90": p90,
        "p90_note": None if p90 is not None else "n<10: high percentile not meaningful",
        "min": ordered[0],
        "max": ordered[-1],
    }


def score_temporal_accuracy(
    matching: dict[str, Any],
    gt_events: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Timestamp/boundary errors over matched events; missing data counted, not filled."""
    gt_by_id = {g.get("id"): g for g in gt_events}
    rel_errors: list[float] = []
    abs_errors: list[float] = []
    start_errors: list[float] = []
    end_errors: list[float] = []
    missing_abs = 0
    malformed_abs = 0
    missing_boundary = 0
    per_match: list[dict[str, Any]] = []

    for m in matching.get("matches", []):
        gt = gt_by_id.get(m["gt_id"], {})
        pred = predictions[m["pred_index"]] if 0 <= m["pred_index"] < len(predictions) else {}
        rel_errors.append(float(m["timestamp_error_seconds"]))

        # Absolute error requires valid ISO timestamps on BOTH sides.
        gt_abs_raw = gt.get("absolute_start") or gt.get("timestamp_absolute")
        pred_abs_raw = pred.get("timestamp_absolute")
        gt_abs = _parse_abs(gt_abs_raw)
        pred_abs = _parse_abs(pred_abs_raw)
        abs_err: float | None = None
        if gt_abs_raw is None or pred_abs_raw is None:
            missing_abs += 1
        elif gt_abs is None or pred_abs is None:
            malformed_abs += 1
        else:
            abs_err = abs(pred_abs - gt_abs)
            abs_errors.append(abs_err)

        # Boundary errors.
        gt_start = _as_float(gt.get("start_seconds"))
        gt_end = _as_float(gt.get("end_seconds"))
        pred_point = _as_float(pred.get("timestamp_seconds"))
        start_err = end_err = None
        if gt_start is None or gt_end is None or pred_point is None:
            missing_boundary += 1
        else:
            start_err = pred_point - gt_start
            end_err = pred_point - gt_end
            start_errors.append(abs(start_err))
            end_errors.append(abs(end_err))

        per_match.append({
            "gt_id": m["gt_id"],
            "relative_error_seconds": float(m["timestamp_error_seconds"]),
            "absolute_error_seconds": abs_err,
            "start_boundary_error_seconds": start_err,
            "end_boundary_error_seconds": end_err,
        })

    return {
        "relative_error": _summary_stats(rel_errors),
        "absolute_error": _summary_stats(abs_errors),
        "start_boundary_error": _summary_stats(start_errors),
        "end_boundary_error": _summary_stats(end_errors),
        "n_matched": len(matching.get("matches", [])),
        "n_missing_absolute": missing_abs,
        "n_malformed_absolute": malformed_abs,
        "n_missing_boundary": missing_boundary,
        "per_match": per_match,
    }


# ------------------------------------------------------------------ evidence --
def _parse_evidence_ref(ref: Any) -> tuple[str | None, float | None]:
    """Parse 'recording_id@Ts' / 'event_id@recording_id:Ts' style references.

    Returns (recording_key_or_id, timestamp_seconds). Unknown shapes yield
    (None, None) and are counted invalid by the caller.
    """
    if isinstance(ref, dict):
        rec = ref.get("recording_id") or ref.get("recording_key")
        ts = _as_float(ref.get("timestamp_seconds"))
        return (str(rec) if rec else None, ts)
    if not isinstance(ref, str):
        return (None, None)
    text = ref.strip()
    # 'event@rec:Ts' -> rec part; 'rec@Ts' -> rec part.
    ts_part: str | None = None
    rec_part: str | None = None
    if "@" in text:
        left, right = text.rsplit("@", 1)
        if ":" in left and "@" not in left:
            # 'event@...' already split; handle 'event@rec:Ts' via left side
            pass
        if ":" in right:
            rec_part, ts_part = right.rsplit(":", 1)
        else:
            # 'rec@Ts' or 'event@Ts'
            rec_part, ts_part = left, right
        if not rec_part:
            rec_part = None
    elif ":" in text:
        rec_part, ts_part = text.rsplit(":", 1)
    else:
        return (None, None)
    ts_part = (ts_part or "").strip().rstrip("sS")
    ts = _as_float(ts_part)
    return (rec_part.strip() if rec_part else None, ts)


def score_evidence_quality(
    claims: list[dict[str, Any]],
    recordings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evidence coverage/validity/support. Support comes ONLY from human annotations.

    claim = {"claim_id": str, "text": str, "evidence": [refs],
             "supported": True/False/None, "contradicted": True/False/None}
    recordings = dataset recording entries (key + duration_seconds). Live
    recording UUIDs are accepted when the claim carries resolved metadata —
    see `recording_index` entries {"key"|"recording_id", "duration_seconds"}.
    """
    durations: dict[str, float | None] = {}
    for rec in recordings:
        dur = _as_float(rec.get("duration_seconds"))
        for k in (rec.get("key"), rec.get("recording_id"), rec.get("camera_name")):
            if k:
                durations[str(k)] = dur

    n_claims = len(claims)
    claims_with_ref = 0
    total_refs = 0
    valid_refs = 0
    invalid_refs: list[dict[str, Any]] = []
    annotated = 0
    supported = 0
    unsupported = 0
    contradicted = 0
    unannotated = 0
    claims_without_evidence: list[str] = []

    for claim in claims:
        refs = claim.get("evidence") or []
        syntactically_valid = [r for r in refs if _parse_evidence_ref(r) != (None, None)]
        if syntactically_valid:
            claims_with_ref += 1
        else:
            claims_without_evidence.append(str(claim.get("claim_id", "?")))
        for ref in refs:
            total_refs += 1
            rec_key, ts = _parse_evidence_ref(ref)
            dur = durations.get(rec_key or "")
            if rec_key is not None and ts is not None and dur is not None and 0 <= ts <= dur:
                valid_refs += 1
            else:
                reason = "unknown_recording" if rec_key not in durations else (
                    "timestamp_out_of_range" if ts is None or dur is None or not (0 <= ts <= dur)
                    else "malformed_reference")
                invalid_refs.append({"claim_id": str(claim.get("claim_id", "?")),
                                     "ref": ref, "reason": reason})
        ann = claim.get("supported")
        if ann is None:
            unannotated += 1
        else:
            annotated += 1
            if ann is True:
                supported += 1
            else:
                unsupported += 1
        if claim.get("contradicted") is True:
            contradicted += 1

    return {
        "n_claims": n_claims,
        "claims_with_evidence": claims_with_ref,
        "claims_without_evidence": claims_without_evidence,
        "evidence_coverage": (claims_with_ref / n_claims) if n_claims else None,
        "n_references": total_refs,
        "n_valid_references": valid_refs,
        "reference_validity_rate": (valid_refs / total_refs) if total_refs else None,
        "invalid_references": invalid_refs,
        "n_annotated": annotated,
        "n_unannotated": unannotated,
        "evidence_support_rate": (supported / annotated) if annotated else None,
        "unsupported_claim_rate": (unsupported / annotated) if annotated else None,
        "contradiction_rate": (contradicted / annotated) if annotated else None,
        "support_note": ("Support/contradiction computed from human annotations only; "
                         "reference existence is NOT treated as support."),
    }


# -------------------------------------------------------------- cross-camera --
def _norm_pair(a: Any, b: Any) -> tuple[str, str]:
    return tuple(sorted((str(a), str(b))))  # type: ignore[return-value]


def score_cross_camera(
    predicted_pairs: list[tuple[Any, Any] | list[Any]],
    valid_pairs: list[tuple[Any, Any] | list[Any] | dict[str, Any]],
    invalid_pairs: list[tuple[Any, Any] | list[Any] | dict[str, Any]],
    ambiguous_pairs: list[tuple[Any, Any] | list[Any] | dict[str, Any]] | None = None,
    excluded_pairs: list[tuple[Any, Any] | list[Any]] | None = None,
) -> dict[str, Any]:
    """Cross-camera link scoring over unordered GT-id pairs.

    dict entries of the form {"pair": [a, b]} are accepted (dataset shape).
    Ambiguous pairs (e.g. unsynchronized clocks, unverifiable identity) are
    reported separately and excluded from precision/recall.
    """
    def _to_set(items: list[Any] | None) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for item in items or []:
            pair = item.get("pair") if isinstance(item, dict) else item
            if pair and len(pair) == 2:
                out.add(_norm_pair(pair[0], pair[1]))
        return out

    valid = _to_set(valid_pairs)
    invalid = _to_set(invalid_pairs)
    ambiguous = _to_set(ambiguous_pairs)
    excluded = _to_set(excluded_pairs)
    predicted = {_norm_pair(a, b) for a, b in (list(p) for p in predicted_pairs if len(p) == 2)}
    predicted = {p for p in predicted if p not in excluded}

    tp = sorted(predicted & valid)
    false_assoc = sorted(predicted & invalid)
    ambiguous_hits = sorted(predicted & ambiguous)
    unlabeled = sorted(predicted - valid - invalid - ambiguous)
    missed = sorted(valid - predicted)

    # Conservative precision: unlabeled predictions count as false but are
    # itemized separately so readers can see the composition.
    prec_denom = len(tp) + len(false_assoc) + len(unlabeled)
    precision = len(tp) / prec_denom if prec_denom else None
    recall = len(tp) / len(valid) if valid else None
    if precision is None or recall is None:
        f1 = None
    elif (precision + recall) == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)

    return {
        "n_predicted": len(predicted),
        "n_valid_labeled": len(valid),
        "n_invalid_labeled": len(invalid),
        "n_ambiguous_labeled": len(ambiguous),
        "n_excluded": len(excluded),
        "true_positives": [list(p) for p in tp],
        "false_associations": [list(p) for p in false_assoc],
        "false_association_count": len(false_assoc),
        "false_association_rate": (len(false_assoc) / len(predicted)) if predicted else None,
        "false_association_denominator": len(predicted),
        "ambiguous_hits": [list(p) for p in ambiguous_hits],
        "unlabeled_predictions": [list(p) for p in unlabeled],
        "false_negatives": [list(p) for p in missed],
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


# -------------------------------------------------------------- observability --
AGENT_STATUSES = ("completed", "skipped", "failed", "mocked")


def summarize_agent_runs(agent_dicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize per-agent execution states, latency, usage, cache, retries.

    Provider tokens/cost count as MEASURED only when the agent record carries
    usage_measured=true; otherwise they are estimates/unavailable. Missing
    cache/retry fields are reported unavailable, never zero-filled silently:
    counts use 0 only when the field is present.
    """
    by_status: dict[str, int] = {s: 0 for s in AGENT_STATUSES}
    unknown_statuses: list[str] = []
    skip_reasons: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    durations: dict[str, float] = {}
    total_ms = 0.0

    tokens_in_measured: int | None = 0
    tokens_out_measured: int | None = 0
    cost_measured: float | None = 0.0
    tokens_estimated = 0
    cost_estimated = 0.0
    usage_unavailable_agents: list[str] = []

    cache_hits: int | None = 0
    cache_misses: int | None = 0
    cache_unavailable = False
    retries = 0
    retries_unavailable = False
    timeouts = 0
    timeouts_unavailable = False
    event_ids: set[str] = set()
    evidence_ids: set[str] = set()

    for agent in agent_dicts:
        name = str(agent.get("agent_name", "?"))
        status = str(agent.get("status", "?"))
        if status in by_status:
            by_status[status] += 1
        else:
            unknown_statuses.append(f"{name}:{status}")
        dur = _as_float(agent.get("duration_ms")) or 0.0
        durations[name] = dur
        total_ms += dur

        if status == "skipped":
            skip_reasons.append({"agent": name, "reason": str(agent.get("error") or agent.get("skipped_reason") or "unknown")})
        if status == "failed":
            failures.append({"agent": name, "reason": str(agent.get("error") or "unknown")})

        measured = agent.get("usage_measured") is True
        tin = agent.get("tokens_in")
        tout = agent.get("tokens_out")
        cost = _as_float(agent.get("cost_usd"))
        if measured and isinstance(tin, int) and isinstance(tout, int):
            assert tokens_in_measured is not None and tokens_out_measured is not None
            tokens_in_measured += tin
            tokens_out_measured += tout
            cost_measured = (cost_measured or 0.0) + (cost or 0.0)
        elif measured:
            usage_unavailable_agents.append(name)
        else:
            tokens_estimated += int(agent.get("tokens_used") or 0)
            cost_estimated += cost or 0.0
            if tin is None and tout is None and not agent.get("tokens_used"):
                usage_unavailable_agents.append(name)

        ch, cm = agent.get("cache_hits"), agent.get("cache_misses")
        if ch is None or cm is None:
            cache_unavailable = True
        else:
            assert cache_hits is not None and cache_misses is not None
            cache_hits += int(ch)
            cache_misses += int(cm)
        if agent.get("retries") is None:
            retries_unavailable = True
        else:
            retries += int(agent.get("retries") or 0)
        if agent.get("timeouts") is None:
            timeouts_unavailable = True
        else:
            timeouts += int(agent.get("timeouts") or 0)

        for eid in agent.get("event_ids") or []:
            event_ids.add(str(eid))
        for evid in agent.get("evidence_ids") or []:
            evidence_ids.add(str(evid))

    if cache_unavailable:
        cache_hits = cache_misses = None
    cache_total = None if cache_hits is None else cache_hits + cache_misses

    return {
        "n_agents": len(agent_dicts),
        "by_status": by_status,
        "unknown_statuses": unknown_statuses,
        "skip_reasons": skip_reasons,
        "failures": failures,
        "total_duration_ms": total_ms,
        "duration_by_agent_ms": durations,
        "tokens_in_measured": tokens_in_measured,
        "tokens_out_measured": tokens_out_measured,
        "cost_measured_usd": cost_measured,
        "tokens_estimated": tokens_estimated,
        "cost_estimated_usd": cost_estimated,
        "usage_unavailable_agents": sorted(set(usage_unavailable_agents)),
        "usage_note": "Measured = provider-reported usage payloads only; estimates are heuristic and reported separately.",
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_total": cache_total,
        "cache_hit_rate": (cache_hits / cache_total) if cache_total else None,
        "cache_unavailable": cache_unavailable,
        "retries": None if retries_unavailable else retries,
        "timeouts": None if timeouts_unavailable else timeouts,
        "event_ids": sorted(event_ids),
        "evidence_ids": sorted(evidence_ids),
    }
