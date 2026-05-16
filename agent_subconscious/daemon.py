"""Minimal local reviewer daemon for the hook spike.

The daemon only reads and writes Subconscious-owned state files. The reviewer is
deliberately conservative: it emits one static, signed advisory from completed
turn observations so the next-prompt injection path can be exercised end to end.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

from . import hook_probe

DEFAULT_BODY = "verification gap: status unknown."
PROCESSOR_ID = "daemon_minimal_reviewer"
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_IDLE_TIMEOUT_SECONDS = 15 * 60
LIVE_HEARTBEAT_MAX_AGE_SECONDS = 60.0
DEFAULT_PROCESS_MAX_ITEMS = 10
DEFAULT_PROCESS_MAX_SECONDS = 1.0
DAEMON_STATUS_STATES = {"starting", "running", "stopped", "disabled"}


def daemon_status_record(
    state_dir: Path,
    *,
    state: str,
    pid: int,
    started_at: str | None,
    process_start_key: str | None = None,
    executable_path_id: str | None = None,
    last_process_result: dict[str, Any] | None = None,
    last_error_code: str | None = None,
    stopped_at: str | None = None,
) -> dict[str, Any]:
    if state not in DAEMON_STATUS_STATES:
        raise ValueError(f"unsupported daemon state: {state}")
    now = hook_probe.utc_now()
    return {
        "schema_version": 1,
        "state": state,
        "processor_id": PROCESSOR_ID,
        "pid": pid,
        "workspace_state_dir_id": hook_probe.stable_id("state", str(state_dir.resolve())),
        "process_start_key": process_start_key or running_process_start_key(pid),
        "executable_path_id": executable_path_id or running_process_executable_path_id(pid),
        "started_at": started_at or now,
        "last_heartbeat_at": now,
        "stopped_at": stopped_at,
        "last_process_result": last_process_result,
        "last_error_code": last_error_code,
    }


def daemon_status_path(state_dir: Path) -> Path:
    return hook_probe.safe_state_file(state_dir, hook_probe.DAEMON_STATUS_FILE)


def read_daemon_status(state_dir: Path) -> dict[str, Any] | None:
    path = daemon_status_path(state_dir)
    if not path.exists():
        return None
    try:
        value = hook_probe.loads_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != 1 or value.get("processor_id") != PROCESSOR_ID:
        return None
    if value.get("state") not in DAEMON_STATUS_STATES:
        return None
    if not isinstance(value.get("pid"), int) or value["pid"] <= 0:
        return None
    return value


def write_daemon_status(state_dir: Path, record: dict[str, Any]) -> None:
    path = daemon_status_path(state_dir)
    temp_path = state_dir / f".{hook_probe.DAEMON_STATUS_FILE}.tmp-{os.getpid()}-{time.time_ns()}"
    if hook_probe.is_reparse_or_symlink(temp_path):
        raise hook_probe.HookProbeError("daemon status temp file cannot be a link or reparse point")
    try:
        if temp_path.resolve().parent != state_dir.resolve():
            raise hook_probe.HookProbeError("daemon status temp file escapes state directory")
    except FileNotFoundError:
        if temp_path.parent.resolve() != state_dir.resolve():
            raise hook_probe.HookProbeError("daemon status temp file escapes state directory")
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(hook_probe.dumps_json(record))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def heartbeat_is_fresh(status: dict[str, Any], max_age_seconds: float = LIVE_HEARTBEAT_MAX_AGE_SECONDS) -> bool:
    heartbeat = hook_probe.parse_time(str(status.get("last_heartbeat_at")))
    if heartbeat is None:
        return False
    age = hook_probe.dt.datetime.now(hook_probe.dt.UTC) - heartbeat
    return age.total_seconds() <= max_age_seconds


def state_dir_matches_status(status: dict[str, Any], state_dir: Path | None) -> bool:
    if state_dir is None:
        return True
    try:
        expected = hook_probe.stable_id("state", str(state_dir.resolve()))
    except OSError:
        return False
    return status.get("workspace_state_dir_id") == expected


def path_id(path: Path) -> str:
    try:
        material = str(path.expanduser().resolve())
    except OSError:
        material = str(path)
    if os.name == "nt":
        material = material.lower()
    return hook_probe.stable_id("path", material)


def running_process_executable_path_id(pid: int) -> str | None:
    if pid == os.getpid():
        return path_id(Path(sys.executable))
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return None
            try:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return None
                return path_id(Path(buffer.value))
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
    exe_path = Path("/proc") / str(pid) / "exe"
    try:
        return path_id(exe_path.resolve())
    except OSError:
        return None


def running_process_start_key(pid: int) -> str | None:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return None
            try:
                creation = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not ctypes.windll.kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
                value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return f"winfiletime:{value}"
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        stat = stat_path.read_text(encoding="utf-8").split()
    except OSError:
        return None
    if len(stat) < 22:
        return None
    return f"procstat:{stat[21]}"


def process_identity_matches_status(status: dict[str, Any]) -> bool:
    pid = status.get("pid")
    if not isinstance(pid, int):
        return False
    expected_start = status.get("process_start_key")
    expected_executable = status.get("executable_path_id")
    if not isinstance(expected_start, str) or not expected_start:
        return False
    if not isinstance(expected_executable, str) or not expected_executable:
        return False
    return running_process_start_key(pid) == expected_start and running_process_executable_path_id(pid) == expected_executable


def update_daemon_status(
    state_dir: Path,
    *,
    state: str,
    pid: int,
    started_at: str | None,
    last_process_result: dict[str, Any] | None = None,
    last_error_code: str | None = None,
    stopped_at: str | None = None,
) -> None:
    with hook_probe.locked_state(state_dir):
        write_daemon_status(
            state_dir,
            daemon_status_record(
                state_dir,
                state=state,
                pid=pid,
                started_at=started_at,
                last_process_result=last_process_result,
                last_error_code=last_error_code,
                stopped_at=stopped_at,
            ),
        )


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def daemon_status_is_live(
    status: dict[str, Any] | None,
    state_dir: Path | None = None,
    *,
    max_heartbeat_age_seconds: float = LIVE_HEARTBEAT_MAX_AGE_SECONDS,
) -> bool:
    if status is None:
        return False
    if status.get("state") not in {"starting", "running"}:
        return False
    pid = status.get("pid")
    return (
        isinstance(pid, int)
        and pid_is_running(pid)
        and heartbeat_is_fresh(status, max_heartbeat_age_seconds)
        and state_dir_matches_status(status, state_dir)
        and process_identity_matches_status(status)
    )


def pending_observation_count(state_dir: Path) -> int:
    return len(eligible_observations(state_dir))


def feedback_item_from_observation(observation: dict[str, Any], body: str = DEFAULT_BODY) -> dict[str, Any]:
    created_at = hook_probe.utc_now()
    record_id = hook_probe.stable_id("fb", str(observation["record_id"]), body)
    item = {
        "schema_version": 1,
        "record_id": record_id,
        "workspace_id": observation["workspace_id"],
        "generation": 1,
        "session_id": observation["session_id"],
        "source_observation_ids": [observation["record_id"]],
        "derived_from_turn_id": observation["turn_id"],
        "eligible_after_observation_id": observation["record_id"],
        "eligible_session_id": observation["session_id"],
        "created_at": created_at,
        "expires_at": (hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(hours=1))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "confidence": "medium",
        "risk": "verification",
        "body": body,
        "evidence_refs": [observation["record_id"]],
        "content_digest": "sha256:" + hook_probe.sha256_hex(body),
        "signature": "",
    }
    item["signature"] = hook_probe.sign_feedback_item(item)
    return item


def existing_feedback_ids(state_dir: Path) -> set[str]:
    path = state_dir / hook_probe.FEEDBACK_FILE
    if not path.exists():
        return set()
    return {str(item.get("record_id")) for item in hook_probe.read_jsonl(path, strict=True)}


def eligible_observations(state_dir: Path) -> list[dict[str, Any]]:
    observations = hook_probe.read_observations_by_id(state_dir)
    return [
        observation
        for observation in observations.values()
        if observation.get("source") == "Stop"
        and (observation.get("state") == "enqueued" or stale_processing_observation(observation))
    ]


def stale_processing_observation(observation: dict[str, Any]) -> bool:
    if observation.get("state") != "processing":
        return False
    lease_expires = hook_probe.parse_time(str(observation.get("lease_expires_at")))
    return lease_expires is not None and lease_expires <= hook_probe.dt.datetime.now(hook_probe.dt.UTC)


def transition_observation(observation: dict[str, Any], state: str) -> dict[str, Any]:
    transitioned = dict(observation)
    transitioned["state"] = state
    transitioned["next_attempt_at"] = None
    transitioned["last_error"] = None
    if state == "processing":
        transitioned["attempt"] = int(observation["attempt"]) + 1
        transitioned["processing_by"] = PROCESSOR_ID
        transitioned["lease_expires_at"] = (
            hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(seconds=30)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    elif state == "processed":
        transitioned["processing_by"] = observation["processing_by"]
        transitioned["lease_expires_at"] = None
    elif state == "enqueued":
        transitioned["processing_by"] = None
        transitioned["lease_expires_at"] = None
    else:
        raise ValueError(f"unsupported observation transition: {state}")
    return transitioned


def process_once(
    state_dir: Path,
    *,
    max_items: int = DEFAULT_PROCESS_MAX_ITEMS,
    max_seconds: float = DEFAULT_PROCESS_MAX_SECONDS,
) -> dict[str, Any]:
    state_dir = hook_probe.resolve_state_dir(state_dir)
    hook_probe.prepare_state_dir(state_dir)
    created = 0
    disabled_reason = None
    processed = 0
    started = time.monotonic()
    with hook_probe.locked_state(state_dir, timeout_seconds=min(max_seconds, hook_probe.DEFAULT_LOCK_TIMEOUT_SECONDS)):
        seen = existing_feedback_ids(state_dir)
        for observation in eligible_observations(state_dir):
            if processed >= max(max_items, 1):
                break
            if time.monotonic() - started >= max(max_seconds, 0.1):
                break
            try:
                item = feedback_item_from_observation(observation)
            except hook_probe.HookProbeError as exc:
                disabled_reason = str(exc)
                break
            if stale_processing_observation(observation):
                observation = transition_observation(observation, "enqueued")
                hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)
            processing = transition_observation(observation, "processing")
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, processing)
            if item["record_id"] in seen:
                hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, transition_observation(processing, "processed"))
                processed += 1
                continue
            hook_probe.append_jsonl(state_dir / hook_probe.FEEDBACK_FILE, item)
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, transition_observation(processing, "processed"))
            seen.add(item["record_id"])
            created += 1
            processed += 1
    return {
        "status": "disabled" if disabled_reason else "ok",
        "created_feedback": created,
        "processed_observations": processed,
        "disabled_reason": disabled_reason,
    }


def run_loop(state_dir: Path, interval: float, idle_timeout: float | None) -> None:
    state_dir = hook_probe.resolve_state_dir(state_dir)
    hook_probe.prepare_state_dir(state_dir)
    started_at = hook_probe.utc_now()
    last_activity = time.monotonic()
    update_daemon_status(state_dir, state="running", pid=os.getpid(), started_at=started_at)
    while True:
        result: dict[str, Any] | None = None
        last_error_code = None
        try:
            result = process_once(state_dir)
            if int(result.get("created_feedback") or 0) > 0 or pending_observation_count(state_dir) > 0:
                last_activity = time.monotonic()
            update_daemon_status(
                state_dir,
                state="disabled" if result.get("status") == "disabled" else "running",
                pid=os.getpid(),
                started_at=started_at,
                last_process_result=result,
            )
            if result.get("status") == "disabled":
                break
        except Exception as exc:
            last_error_code = f"daemon-loop-error-{type(exc).__name__}"
            hook_probe.emit_diagnostic(last_error_code)
            update_daemon_status(
                state_dir,
                state="running",
                pid=os.getpid(),
                started_at=started_at,
                last_process_result=result,
                last_error_code=last_error_code,
            )
        if idle_timeout is not None and time.monotonic() - last_activity >= idle_timeout:
            break
        time.sleep(interval)
    update_daemon_status(
        state_dir,
        state="stopped",
        pid=os.getpid(),
        started_at=started_at,
        last_process_result=result,
        last_error_code=last_error_code,
        stopped_at=hook_probe.utc_now(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the minimal Agent Subconscious reviewer daemon")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT_SECONDS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug-allow-fixture-key", action="store_true")
    args = parser.parse_args(argv)
    if args.debug_allow_fixture_key:
        hook_probe.os.environ[hook_probe.ALLOW_FIXTURE_KEY_ENV] = "1"
    state_dir = args.state_dir or hook_probe.default_state_dir()
    if args.once:
        try:
            result = process_once(state_dir)
        except Exception as exc:
            hook_probe.emit_diagnostic(f"daemon-once-error-{type(exc).__name__}")
            result = {
                "status": "disabled",
                "created_feedback": 0,
                "disabled_reason": f"{type(exc).__name__}",
            }
        if not args.quiet:
            print(hook_probe.dumps_json(result))
        return 0
    idle_timeout = None if args.idle_timeout < 0 else max(args.idle_timeout, 0.0)
    run_loop(state_dir, max(args.interval, 0.25), idle_timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
