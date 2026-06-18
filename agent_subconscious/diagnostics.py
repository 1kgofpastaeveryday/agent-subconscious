"""Live environment diagnostics for Agent Subconscious dogfooding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import daemon, hook_probe, llm_engine, rollout_watcher

NOTE_STALE_SECONDS = 30 * 60


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return hook_probe.read_jsonl(path, strict=False)
    except hook_probe.HookProbeError:
        return []


def latest_by_event(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        event_type = record.get("event_type")
        if isinstance(event_type, str):
            latest[event_type] = record
    return latest


def latest_prompt(logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in reversed(logs):
        if record.get("event_type") == "UserPromptSubmit":
            return record
    return None


def latest_hook_prompt(logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in reversed(logs):
        if record.get("event_type") == "UserPromptSubmit" and record.get("source_file_path") is None:
            return record
    return None


def latest_rollout_prompt(logs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for record in reversed(logs):
        if record.get("event_type") == "UserPromptSubmit" and record.get("source_file_path") is not None:
            return record
    return None


def latest_turn_review_status(state_dir: Path, logs: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = latest_prompt(logs)
    prompt_time = hook_probe.parse_time(str(prompt.get("observed_at"))) if prompt else None
    notes = _read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE)
    if prompt_time is not None:
        later_notes = [
            item
            for item in notes
            if (created := hook_probe.parse_time(str(item.get("created_at")))) is not None and created >= prompt_time
        ]
        if later_notes:
            latest = later_notes[-1]
            return {"status": "note", "turn_id": latest.get("derived_from_turn_id"), "note_body_digest": latest.get("body_digest")}
    stops = [record for record in logs if record.get("event_type") == "Stop"]
    if not stops:
        return {"status": "missing", "reason": "no observed assistant/tool/final turn"}
    stop = stops[-1]
    turn_id = stop.get("turn_id")
    turn_notes = [item for item in notes if item.get("derived_from_turn_id") == turn_id]
    if turn_notes:
        return {"status": "note", "turn_id": turn_id, "note_body_digest": turn_notes[-1].get("body_digest")}
    activities = _read_jsonl(state_dir / hook_probe.LLM_ACTIVITY_FILE)
    related = [item for item in activities if item.get("observation_turn_id") == turn_id]
    if related:
        last = related[-1]
        if last.get("event") == "review_completed":
            return {"status": str(last.get("outcome") or "completed"), "turn_id": turn_id}
        if last.get("event") == "review_failed":
            return {"status": "error", "turn_id": turn_id, "error_type": last.get("error_type")}
    observations = hook_probe.read_observations_by_id(state_dir)
    observation = observations.get(str(stop.get("observation_id")))
    if observation and observation.get("last_error"):
        return {"status": "error", "turn_id": turn_id, "reason": observation.get("last_error")}
    return {"status": "pending", "turn_id": turn_id}


def injection_status_for_prompt(prompt: dict[str, Any] | None) -> dict[str, Any]:
    if prompt is None:
        return {"status": "missing", "reason": "no observed user prompt"}
    injection = prompt.get("injection")
    if isinstance(injection, dict) and injection.get("received") is True:
        status = "received"
        if injection.get("kind") == "none":
            status = "empty_none"
        elif injection.get("prompt_kind") == "synthetic_diagnostic":
            status = "synthetic_diagnostic"
        elif injection.get("kind") == "non_empty":
            status = "real_non_empty" if injection.get("prompt_kind") == "real_user_prompt" else "synthetic_non_empty"
        return {
            "status": status,
            "turn_id": prompt.get("turn_id"),
            "kind": injection.get("kind"),
            "prompt_kind": injection.get("prompt_kind"),
            "note_body_digest": injection.get("note_body_digest"),
            "note_created_at": injection.get("note_created_at"),
            "derived_from_turn_id": injection.get("derived_from_turn_id"),
            "delivery_record_id": injection.get("delivery_record_id"),
            "claimed_prompt_turn_id": injection.get("claimed_prompt_turn_id"),
            "marker": injection.get("marker"),
            "target_session_id": injection.get("target_session_id"),
            "target_turn_id": injection.get("target_turn_id"),
            "target_thread_id": injection.get("target_thread_id"),
        }
    return {"status": "not_received", "turn_id": prompt.get("turn_id")}


def latest_injection_status(logs: list[dict[str, Any]]) -> dict[str, Any]:
    return injection_status_for_prompt(latest_prompt(logs))


def latest_hook_injection_status(logs: list[dict[str, Any]]) -> dict[str, Any]:
    return injection_status_for_prompt(latest_hook_prompt(logs))


def latest_rollout_prompt_status(logs: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = latest_rollout_prompt(logs)
    if prompt is None:
        return {"status": "missing", "reason": "no rollout-observed prompt"}
    return {
        "status": "observed",
        "turn_id": prompt.get("turn_id"),
        "session_id": prompt.get("session_id"),
        "observed_at": prompt.get("observed_at"),
        "source_file_path": prompt.get("source_file_path"),
        "injection": prompt.get("injection"),
    }


def _latest_note(notes: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not notes:
        return None
    return sorted(notes, key=lambda item: (str(item.get("created_at")), int(item.get("sequence") or 0)))[-1]


def note_freshness(note: dict[str, Any] | None) -> dict[str, Any]:
    if note is None:
        return {"status": "missing"}
    now = hook_probe.dt.datetime.now(hook_probe.dt.UTC)
    created = hook_probe.parse_time(str(note.get("created_at")))
    expires = hook_probe.parse_time(str(note.get("expires_at")))
    if expires is None or expires <= now:
        return {"status": "stale_expired", "reason": "expired", "created_at": note.get("created_at"), "expires_at": note.get("expires_at")}
    if created is None:
        return {"status": "stale_expired", "reason": "invalid-created-at", "created_at": note.get("created_at")}
    age = (now - created).total_seconds()
    if age > NOTE_STALE_SECONDS:
        return {"status": "stale_expired", "reason": "too-old", "age_seconds": age, "created_at": note.get("created_at")}
    return {"status": "fresh", "age_seconds": age, "created_at": note.get("created_at"), "expires_at": note.get("expires_at")}


def note_freshness_from_fields(created_at: Any, expires_at: Any) -> dict[str, Any]:
    now = hook_probe.dt.datetime.now(hook_probe.dt.UTC)
    created = hook_probe.parse_time(str(created_at))
    expires = hook_probe.parse_time(str(expires_at))
    if expires is None or expires <= now:
        return {"status": "stale_expired", "reason": "expired", "created_at": created_at, "expires_at": expires_at}
    if created is None:
        return {"status": "stale_expired", "reason": "invalid-created-at", "created_at": created_at}
    age = (now - created).total_seconds()
    if age > NOTE_STALE_SECONDS:
        return {"status": "stale_expired", "reason": "too-old", "age_seconds": age, "created_at": created_at, "expires_at": expires_at}
    return {"status": "fresh", "age_seconds": age, "created_at": created_at, "expires_at": expires_at}


def emitted_deliveries_by_id(state_dir: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for delivery in _read_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE):
        record_id = delivery.get("record_id")
        if isinstance(record_id, str):
            latest[record_id] = delivery
    return latest


def has_delivery_markers(record: dict[str, Any] | None) -> bool:
    return isinstance(record, dict) and isinstance(record.get("target_session_id"), str) and isinstance(record.get("target_turn_id"), str)


def has_injection_markers(injection: dict[str, Any] | None) -> bool:
    return isinstance(injection, dict) and isinstance(injection.get("target_session_id"), str) and isinstance(injection.get("target_turn_id"), str)


def latest_delivery_proof(state_dir: Path, logs: list[dict[str, Any]]) -> dict[str, Any]:
    legacy_candidate: dict[str, Any] | None = None
    deliveries = emitted_deliveries_by_id(state_dir)
    for record in reversed(logs):
        injection = record.get("injection")
        if not isinstance(injection, dict) or injection.get("kind") != "non_empty":
            continue
        delivery_id = injection.get("delivery_record_id")
        delivery = deliveries.get(str(delivery_id))
        markerless = not has_injection_markers(injection) or (delivery is not None and not has_delivery_markers(delivery))
        freshness = note_freshness_from_fields(injection.get("note_created_at"), injection.get("note_expires_at"))
        proof = {
            "status": "legacy_historical" if markerless else ("valid" if freshness.get("status") == "fresh" else "stale_expired"),
            "prompt_turn_id": record.get("turn_id"),
            "prompt_kind": injection.get("prompt_kind"),
            "visible_injected_sub_notes": bool(injection.get("note_body_digest")),
            "note_created_at": injection.get("note_created_at"),
            "note_expires_at": injection.get("note_expires_at"),
            "note_body_digest": injection.get("note_body_digest"),
            "derived_from_turn_id": injection.get("derived_from_turn_id"),
            "delivery_record_id": delivery_id,
            "claimed_prompt_turn_id": injection.get("claimed_prompt_turn_id"),
            "target_session_id": injection.get("target_session_id"),
            "target_turn_id": injection.get("target_turn_id"),
            "target_thread_id": injection.get("target_thread_id"),
            "source_session_id": record.get("session_id"),
            "delivery_state": delivery.get("state") if delivery else injection.get("delivery_state"),
            "delivered_prompt_turn_id": delivery.get("delivered_prompt_turn_id") if delivery else None,
            "emitted_at": delivery.get("emitted_at") if delivery else None,
            "freshness": freshness,
            "legacy_markerless": markerless,
        }
        if markerless:
            legacy_candidate = proof
            continue
        if (
            proof["delivery_state"] != "emitted"
            or proof["prompt_kind"] != "real_user_prompt"
            or not proof["visible_injected_sub_notes"]
            or proof["target_session_id"] != record.get("session_id")
            or proof["target_turn_id"] != record.get("turn_id")
        ):
            proof["status"] = "invalid"
        return proof
    if legacy_candidate is not None:
        return legacy_candidate
    return {"status": "missing", "reason": "no non-empty injection recorded"}


def current_queued_fresh_note(state_dir: Path) -> dict[str, Any]:
    daemon_status = daemon.read_daemon_status(state_dir)
    if not daemon.daemon_status_is_live(daemon_status, state_dir):
        return {"status": "unknown", "reason": "daemon-not-live"}
    if not isinstance(daemon_status, dict):
        return {"status": "unknown", "reason": "daemon-status-missing"}
    pending = daemon_status.get("pending_sub_notes") if isinstance(daemon_status, dict) else None
    return {
        "status": "live",
        "pending_count": pending if isinstance(pending, int) else None,
        "route_counts": daemon_status.get("pending_sub_note_routes") if isinstance(daemon_status.get("pending_sub_note_routes"), dict) else {},
        "source": "daemon_ram_queue",
    }


def stale_visible_sub_notes(logs: list[dict[str, Any]]) -> dict[str, Any]:
    current = latest_hook_prompt(logs)
    if current is None:
        return {"status": "missing", "reason": "no current prompt"}
    injection = current.get("injection")
    if not isinstance(injection, dict) or injection.get("kind") != "non_empty":
        return {"status": "missing", "reason": "current prompt has no visible non-empty Sub notes", "current_turn_id": current.get("turn_id")}
    current_session = current.get("session_id")
    current_turn = current.get("turn_id")
    current_thread = current.get("thread_id")
    if not has_injection_markers(injection):
        return {
            "status": "current_legacy_visible_sub_note",
            "reason": "current visible Sub notes lack target markers",
            "source_session_id": current.get("session_id"),
            "current_session_id": current_session,
            "current_turn_id": current_turn,
        }
    target_session = injection.get("target_session_id")
    target_turn = injection.get("target_turn_id")
    target_thread = injection.get("target_thread_id")
    if target_session != current_session or target_turn != current_turn or (target_thread is not None and target_thread != current_thread):
        return {
            "status": "stale_visible_sub_note",
            "source_session_id": current.get("session_id"),
            "target_session_id": target_session,
            "target_turn_id": target_turn,
            "target_thread_id": target_thread,
            "current_session_id": current_session,
            "current_turn_id": current_turn,
            "current_thread_id": current_thread,
        }
    freshness = note_freshness_from_fields(injection.get("note_created_at"), injection.get("note_expires_at"))
    if freshness.get("status") == "fresh":
        return {"status": "not_stale", "latest_prompt_turn_id": current.get("turn_id"), "freshness": freshness}
    return {
        "status": "stale_visible_sub_notes",
        "latest_prompt_turn_id": current.get("turn_id"),
        "note_created_at": injection.get("note_created_at"),
        "note_expires_at": injection.get("note_expires_at"),
        "freshness": freshness,
    }


def current_hook_injection_for_turn(logs: list[dict[str, Any]]) -> dict[str, Any]:
    prompt = latest_hook_prompt(logs)
    status = injection_status_for_prompt(prompt)
    if prompt is None:
        return status
    injection = prompt.get("injection")
    if isinstance(injection, dict) and injection.get("kind") == "non_empty":
        matches = injection.get("target_session_id") == prompt.get("session_id") and injection.get("target_turn_id") == prompt.get("turn_id")
        status["marker_matches_current_turn"] = matches
    return status


def historical_developer_context_sub_notes(logs: list[dict[str, Any]]) -> dict[str, Any]:
    current = latest_hook_prompt(logs)
    if current is None:
        return {"status": "missing", "reason": "no current prompt"}
    for record in reversed(logs):
        if record is current:
            continue
        injection = record.get("injection")
        if isinstance(injection, dict) and injection.get("kind") == "non_empty":
            if not has_injection_markers(injection):
                return {
                    "status": "legacy_historical_stale_note",
                    "source_session_id": record.get("session_id"),
                    "source_turn_id": record.get("turn_id"),
                    "current_session_id": current.get("session_id"),
                    "current_turn_id": current.get("turn_id"),
                    "quarantined": True,
                    "ignored": True,
                }
            return {
                "status": "visible_historical",
                "source_session_id": record.get("session_id"),
                "target_session_id": injection.get("target_session_id"),
                "target_turn_id": injection.get("target_turn_id") or injection.get("claimed_prompt_turn_id"),
                "current_session_id": current.get("session_id"),
                "current_turn_id": current.get("turn_id"),
                "ignored": True,
            }
    return {"status": "missing", "reason": "no historical non-empty Sub notes recorded"}


def legacy_historical_delivery_records(state_dir: Path) -> dict[str, Any]:
    markerless = []
    for delivery in _read_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE):
        if delivery.get("state") == "emitted" and not has_delivery_markers(delivery):
            markerless.append(
                {
                    "record_id": delivery.get("record_id"),
                    "session_id": delivery.get("session_id"),
                    "claimed_prompt_turn_id": delivery.get("claimed_prompt_turn_id"),
                    "delivered_prompt_turn_id": delivery.get("delivered_prompt_turn_id"),
                    "emitted_at": delivery.get("emitted_at"),
                }
            )
    return {"status": "present" if markerless else "empty", "count": len(markerless), "latest": markerless[-1] if markerless else None, "quarantined": bool(markerless)}


def queued_notes_for_other_sessions(state_dir: Path, logs: list[dict[str, Any]]) -> dict[str, Any]:
    live = current_queued_fresh_note(state_dir)
    current = latest_hook_prompt(logs)
    if live.get("status") != "live" or current is None:
        return live
    route_counts = live.get("route_counts") if isinstance(live.get("route_counts"), dict) else {}
    current_ws = str(current.get("workspace_id") or "")
    current_route_prefix = f"{current_ws}/{current.get('session_id')}/"
    other = {route: count for route, count in route_counts.items() if not route.startswith(current_route_prefix)}
    return {"status": "present" if other else "empty", "route_counts": other}


def stale_or_mismatched_session_notes(state_dir: Path, logs: list[dict[str, Any]]) -> dict[str, Any]:
    current = latest_hook_prompt(logs)
    notes = _read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE)
    if current is None:
        return {"status": "unknown", "reason": "no current prompt"}
    now = hook_probe.dt.datetime.now(hook_probe.dt.UTC)
    current_session = current.get("session_id")
    stale = []
    mismatched = []
    for note in notes:
        expires = hook_probe.parse_time(str(note.get("expires_at")))
        if expires is None or expires <= now:
            stale.append(note.get("message_id"))
        elif note.get("session_id") != current_session:
            mismatched.append({"message_id": note.get("message_id"), "session_id": note.get("session_id")})
    return {"status": "present" if stale or mismatched else "empty", "stale_message_ids": stale, "mismatched_session_notes": mismatched}


def readiness(state_dir: Path, logs: list[dict[str, Any]], watcher_status: dict[str, Any] | None, cwd: Path) -> dict[str, Any]:
    watcher_stale, watcher_reason = rollout_watcher.watcher_is_stale(watcher_status, cwd)
    proof = latest_delivery_proof(state_dir, logs)
    daemon_status = daemon.read_daemon_status(state_dir)
    daemon_live = daemon.daemon_status_is_live(daemon_status, state_dir)
    daemon_error = isinstance(daemon_status, dict) and daemon_status.get("last_error_code")
    observation_path_fresh = not watcher_stale or latest_hook_prompt(logs) is not None
    stale_visible = stale_visible_sub_notes(logs)
    ready = (
        not watcher_stale
        and observation_path_fresh
        and daemon_live
        and not daemon_error
        and proof.get("status") == "valid"
        and stale_visible.get("status") not in {"stale_visible_sub_note", "stale_visible_sub_notes", "current_legacy_visible_sub_note"}
    )
    reasons: list[str] = []
    if watcher_stale:
        reasons.append(f"stale rollout watcher: {watcher_reason}")
    if not observation_path_fresh:
        reasons.append("no fresh observation path")
    if not daemon_live:
        reasons.append("daemon stopped")
    if daemon_error:
        reasons.append(f"daemon error: {daemon_error}")
    if proof.get("status") == "missing":
        reasons.append("no emitted non-empty delivery proof")
    elif proof.get("status") == "stale_expired":
        reasons.append(f"proof expired: {proof.get('freshness', {}).get('reason')}")
    elif proof.get("status") != "valid":
        reasons.append("invalid non-empty delivery proof")
    if stale_visible.get("status") in {"stale_visible_sub_note", "stale_visible_sub_notes", "current_legacy_visible_sub_note"}:
        reasons.append(str(stale_visible.get("status")))
    return {
        "status": "READY" if ready else "NOT_READY",
        "reasons": reasons,
        "latest_non_empty_delivery_proof": proof,
        "current_queued_fresh_note": current_queued_fresh_note(state_dir),
        "live_delivery_state": current_queued_fresh_note(state_dir),
        "stale_visible_sub_notes": stale_visible,
        "current_hook_injection_for_this_turn": current_hook_injection_for_turn(logs),
        "historical_developer_context_sub_notes": historical_developer_context_sub_notes(logs),
        "legacy_historical_delivery_records": legacy_historical_delivery_records(state_dir),
        "queued_notes_for_other_sessions": queued_notes_for_other_sessions(state_dir, logs),
        "stale_mismatched_session_notes": stale_or_mismatched_session_notes(state_dir, logs),
    }


def current_status(state_dir: Path | None = None, cwd: Path | None = None) -> dict[str, Any]:
    cwd = cwd or Path.cwd()
    state_dir = hook_probe.resolve_state_dir(state_dir or hook_probe.default_state_dir(str(cwd)), str(cwd))
    hook_probe.prepare_state_dir(state_dir)
    logs = _read_jsonl(state_dir / hook_probe.LIVE_EVENT_LOG_FILE)
    event_latest = latest_by_event(logs)
    watcher_status = rollout_watcher.read_status(state_dir)
    daemon_status = daemon.read_daemon_status(state_dir)
    readiness_status = readiness(state_dir, logs, watcher_status, cwd)
    return {
        "status": readiness_status["status"],
        "state_dir": str(state_dir),
        "codex": rollout_watcher.codex_binary_status(),
        "latest_rollout": str(rollout_watcher.latest_rollout_for_cwd(cwd) or ""),
        "hook_events_firing": {
            event: event_latest.get(event) is not None
            for event in ("SessionStart", "UserPromptSubmit", "Stop")
        },
        "latest_hook_events": event_latest,
        "rollout_watching": watcher_status or {"state": "inactive"},
        "daemon": daemon_status or {"state": "unknown"},
        "llm_engine": {
            "selected": llm_engine.ENGINE_ENV,
            "backend_enabled": llm_engine.chatgpt_plan_backend_enabled(state_dir),
        },
        "latest_prompt_produced_note": latest_turn_review_status(state_dir, logs),
        "latest_prompt_received_injection": latest_injection_status(logs),
        "latest_hook_injection": latest_hook_injection_status(logs),
        "current_hook_injection_for_this_turn": readiness_status["current_hook_injection_for_this_turn"],
        "latest_rollout_observed_prompt": latest_rollout_prompt_status(logs),
        "latest_valid_non_empty_delivery_proof": readiness_status["latest_non_empty_delivery_proof"],
        "current_queued_fresh_note": readiness_status["current_queued_fresh_note"],
        "live_delivery_state": readiness_status["live_delivery_state"],
        "historical_delivery_proof": readiness_status["latest_non_empty_delivery_proof"],
        "historical_developer_context_sub_notes": readiness_status["historical_developer_context_sub_notes"],
        "legacy_historical_delivery_records": readiness_status["legacy_historical_delivery_records"],
        "queued_notes_for_other_sessions": readiness_status["queued_notes_for_other_sessions"],
        "stale_mismatched_session_notes": readiness_status["stale_mismatched_session_notes"],
        "stale_visible_sub_notes": readiness_status["stale_visible_sub_notes"],
        "readiness": readiness_status,
    }
