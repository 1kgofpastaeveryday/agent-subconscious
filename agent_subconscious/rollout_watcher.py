"""Fallback observer for Codex rollout/session JSONL files."""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from . import hook_probe

WATCHER_STATUS_FILE = "rollout_watcher_status.json"
WATCHER_CURSOR_FILE = "rollout_watcher_cursor.json"
MAX_SCAN_ROWS = 2_000
STALE_WATCHER_SECONDS = 120.0

hook_probe.SAFE_STATE_FILES.update({WATCHER_STATUS_FILE, WATCHER_CURSOR_FILE})


def codex_sessions_root() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return (Path(configured).expanduser() if configured else Path.home() / ".codex") / "sessions"


def latest_rollout_for_cwd(cwd: Path, root: Path | None = None) -> Path | None:
    root = root or codex_sessions_root()
    if not root.exists():
        return None
    wanted = str(cwd.resolve())
    if os.name == "nt":
        wanted = wanted.lower()
    candidates = sorted(root.rglob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in candidates[:40]:
        meta = session_meta_from_file(path)
        meta_cwd = str(meta.get("cwd") or "")
        if os.name == "nt":
            meta_cwd = meta_cwd.lower()
        if meta_cwd == wanted:
            return path
    return None


def file_mtime_utc(path: Path) -> str | None:
    try:
        return hook_probe.dt.datetime.fromtimestamp(path.stat().st_mtime, hook_probe.dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except OSError:
        return None


def rollout_identity(path: Path) -> dict[str, Any]:
    meta = session_meta_from_file(path)
    return {
        "path": str(path.resolve()),
        "session_id": meta.get("id"),
        "cwd": meta.get("cwd"),
        "cli_version": meta.get("cli_version"),
        "mtime": file_mtime_utc(path),
    }


def session_meta_from_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = hook_probe.loads_json(line)
                if isinstance(row, dict) and row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
                    return row["payload"]
                break
    except (OSError, ValueError, TypeError):
        return {}
    return {}


def row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def event_from_rollout_row(row: dict[str, Any], meta: dict[str, Any], line_number: int, path: Path) -> dict[str, Any] | None:
    timestamp = str(row.get("timestamp") or hook_probe.utc_now())
    session_id = str(meta.get("id") or path.stem)
    cwd = str(meta.get("cwd") or os.getcwd())
    payload = row_payload(row)
    row_type = row.get("type")
    if row_type == "session_meta":
        return {
            "hook_event_name": "SessionStart",
            "session_id": session_id,
            "cwd": cwd,
            "source": "startup",
            "event_id": f"rollout-{line_number}",
            "created_at": timestamp,
        }
    if payload.get("type") == "user_message":
        turn_id = str(payload.get("turn_id") or f"rollout-user-{line_number}")
        message = content_text(payload.get("message"))
        return {
            "hook_event_name": "UserPromptSubmit",
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": cwd,
            "prompt": message,
            "event_id": f"rollout-{line_number}",
        }
    if payload.get("type") == "agent_message":
        turn_id = str(payload.get("turn_id") or f"rollout-agent-{line_number}")
        message = content_text(payload.get("message"))
        return {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "turn_id": turn_id,
            "cwd": cwd,
            "last_assistant_message": message or "assistant turn observed from rollout log.",
            "transcript_path": str(path),
            "event_id": f"rollout-{line_number}",
        }
    if row_type == "response_item" and payload.get("type") == "function_call":
        call_name = str(payload.get("name") or "tool")
        call_id = str(payload.get("call_id") or line_number)
        return {
            "hook_event_name": "Stop",
            "session_id": session_id,
            "turn_id": f"tool-{call_id}",
            "cwd": cwd,
            "last_assistant_message": f"tool step observed from rollout log: {call_name}.",
            "transcript_path": str(path),
            "event_id": f"rollout-{line_number}",
        }
    return None


def write_status(state_dir: Path, record: dict[str, Any]) -> None:
    path = hook_probe.safe_state_file(state_dir, WATCHER_STATUS_FILE)
    path.write_text(hook_probe.dumps_json(record) + "\n", encoding="utf-8", newline="\n")


def read_status(state_dir: Path) -> dict[str, Any] | None:
    path = hook_probe.safe_state_file(state_dir, WATCHER_STATUS_FILE)
    if not path.exists():
        return None
    try:
        value = hook_probe.loads_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def append_rollout_observation(state_dir: Path, event: dict[str, Any], source_path: Path) -> bool:
    source = hook_probe.event_name(event)
    observation = hook_probe.observation_from_payload(event, source)
    if not hook_probe.ensure_observation_appendable(state_dir, observation):
        return False
    hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)
    hook_probe.append_live_event(state_dir, observation, source_file_path=str(source_path.resolve()))
    if source == "Stop":
        hook_probe.append_transcript_ref_if_new(state_dir, event)
    return True


def scan_rollout_once(state_dir: Path, rollout_path: Path, *, cwd: Path | None = None) -> dict[str, Any]:
    state_dir = hook_probe.resolve_state_dir(state_dir, str(cwd or os.getcwd()))
    hook_probe.prepare_state_dir(state_dir)
    meta = session_meta_from_file(rollout_path)
    observed = 0
    parsed = 0
    with hook_probe.locked_state(state_dir):
        with rollout_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if line_number > MAX_SCAN_ROWS:
                    break
                if not line.strip():
                    continue
                try:
                    row = hook_probe.loads_json(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                parsed += 1
                if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
                    meta = row["payload"]
                event = event_from_rollout_row(row, meta, line_number, rollout_path)
                if event is None:
                    continue
                try:
                    if append_rollout_observation(state_dir, event, rollout_path):
                        observed += 1
                except hook_probe.HookProbeError:
                    continue
        latest = latest_rollout_for_cwd(cwd or Path(str(meta.get("cwd") or os.getcwd())))
        status = {
            "schema_version": 1,
            "state": "active",
            "updated_at": hook_probe.utc_now(),
            "rollout_path": str(rollout_path.resolve()),
            "rollout_session_id": meta.get("id"),
            "rollout_mtime": file_mtime_utc(rollout_path),
            "latest_rollout_path": str(latest.resolve()) if latest is not None else None,
            "latest_rollout_mtime": file_mtime_utc(latest) if latest is not None else None,
            "stale": latest is not None and latest.resolve() != rollout_path.resolve(),
            "parsed_rows": parsed,
            "observed_events": observed,
            "injection_surface": "hook-dependent",
        }
        write_status(state_dir, status)
    return status


def codex_binary_status() -> dict[str, Any]:
    import shutil

    path = shutil.which("codex")
    version = None
    if path:
        try:
            proc = subprocess.run([path, "--version"], text=True, capture_output=True, timeout=5, check=False)
            version = (proc.stdout or proc.stderr).strip() or None
        except (OSError, subprocess.SubprocessError):
            version = None
    return {"path": path, "version": version}


def watcher_is_stale(status: dict[str, Any] | None, cwd: Path) -> tuple[bool, str | None]:
    if not isinstance(status, dict):
        return True, "inactive"
    updated = hook_probe.parse_time(str(status.get("updated_at")))
    if updated is None:
        return True, "missing-updated-at"
    age = (hook_probe.dt.datetime.now(hook_probe.dt.UTC) - updated).total_seconds()
    if age > STALE_WATCHER_SECONDS:
        return True, "stale-updated-at"
    latest = latest_rollout_for_cwd(cwd)
    if latest is None:
        return True, "no-current-rollout"
    watched = status.get("rollout_path")
    try:
        if not isinstance(watched, str) or Path(watched).resolve() != latest.resolve():
            return True, "not-following-latest-rollout"
    except OSError:
        return True, "invalid-watched-rollout"
    return False, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe Codex rollout JSONL as a fallback input")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--rollout", type=Path, default=None)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args(argv)
    state_dir = args.state_dir or hook_probe.default_state_dir(str(args.cwd))
    initial_rollout_path = args.rollout or latest_rollout_for_cwd(args.cwd)
    if initial_rollout_path is None:
        print(hook_probe.dumps_json({"status": "disabled", "reason": "no rollout file found"}))
        return 0
    while True:
        rollout_path = args.rollout or latest_rollout_for_cwd(args.cwd) or initial_rollout_path
        status = scan_rollout_once(state_dir, rollout_path, cwd=args.cwd)
        if args.once:
            print(hook_probe.dumps_json(status))
            return 0
        time.sleep(max(args.interval, 0.25))


if __name__ == "__main__":
    raise SystemExit(main())
