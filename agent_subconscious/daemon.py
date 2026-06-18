"""Minimal local reviewer daemon for the hook spike.

The daemon only reads and writes Subconscious-owned state files. The reviewer is
deliberately conservative: it emits one static, signed advisory from completed
turn observations so the next-prompt injection path can be exercised end to end.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import os
import secrets
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from . import hook_probe
from . import llm_engine
from . import rollout_watcher

DEFAULT_BODY = "verification gap: status unknown."
PROCESSOR_ID = "daemon_minimal_reviewer"
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_IDLE_TIMEOUT_SECONDS = 15 * 60
LIVE_HEARTBEAT_MAX_AGE_SECONDS = 60.0
DEFAULT_PROCESS_MAX_ITEMS = 10
DEFAULT_PROCESS_MAX_SECONDS = 1.0
DEFAULT_RETRY_WAIT_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 3
DAEMON_STATUS_STATES = {"starting", "running", "stopped", "disabled"}
MAX_TRANSCRIPT_TAIL_BYTES = 512_000
MAX_TRANSCRIPT_ROWS = 240
MAX_TRANSCRIPT_TEXT_BYTES = 320
MAX_TRANSCRIPT_CONTEXT_BYTES = 1_000
IPC_HOST = "127.0.0.1"


def daemon_code_id() -> str:
    digest = hashlib.sha256()
    for module_path in (Path(__file__), Path(llm_engine.__file__), Path(hook_probe.__file__)):
        try:
            stat = module_path.stat()
        except OSError:
            material = str(module_path)
        else:
            material = f"{module_path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}"
        digest.update(material.encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return "code_" + digest.hexdigest()[:24]


LOADED_DAEMON_CODE_ID = daemon_code_id()


def daemon_status_record(
    state_dir: Path,
    *,
    state: str,
    pid: int,
    started_at: str | None,
    rollout_watcher_enabled: bool = False,
    process_start_key: str | None = None,
    executable_path_id: str | None = None,
    last_process_result: dict[str, Any] | None = None,
    last_error_code: str | None = None,
    stopped_at: str | None = None,
    ipc_url: str | None = None,
    ipc_token: str | None = None,
    pending_sub_notes: int | None = None,
    pending_sub_note_routes: dict[str, int] | None = None,
) -> dict[str, Any]:
    if state not in DAEMON_STATUS_STATES:
        raise ValueError(f"unsupported daemon state: {state}")
    now = hook_probe.utc_now()
    return {
        "schema_version": 1,
        "state": state,
        "processor_id": PROCESSOR_ID,
        "code_id": LOADED_DAEMON_CODE_ID,
        "pid": pid,
        "workspace_state_dir_id": hook_probe.stable_id("state", str(state_dir.resolve())),
        "process_start_key": process_start_key or running_process_start_key(pid),
        "executable_path_id": executable_path_id or running_process_executable_path_id(pid),
        "started_at": started_at or now,
        "last_heartbeat_at": now,
        "stopped_at": stopped_at,
        "rollout_watcher_enabled": rollout_watcher_enabled,
        "last_process_result": last_process_result,
        "last_error_code": last_error_code,
        "ipc_url": ipc_url,
        "ipc_token": ipc_token,
        "pending_sub_notes": pending_sub_notes,
        "pending_sub_note_routes": pending_sub_note_routes or {},
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
        for attempt in range(6):
            try:
                os.replace(temp_path, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05)
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
    rollout_watcher_enabled: bool = False,
    ipc_url: str | None = None,
    ipc_token: str | None = None,
    pending_sub_notes: int | None = None,
    pending_sub_note_routes: dict[str, int] | None = None,
) -> None:
    with hook_probe.locked_state(state_dir):
        write_daemon_status(
            state_dir,
            daemon_status_record(
                state_dir,
                state=state,
                pid=pid,
                started_at=started_at,
                rollout_watcher_enabled=rollout_watcher_enabled,
                last_process_result=last_process_result,
                last_error_code=last_error_code,
                stopped_at=stopped_at,
                ipc_url=ipc_url,
                ipc_token=ipc_token,
                pending_sub_notes=pending_sub_notes,
                pending_sub_note_routes=pending_sub_note_routes,
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
    if status.get("code_id") != daemon_code_id():
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


def feedback_item_from_observation(
    observation: dict[str, Any],
    body: str = DEFAULT_BODY,
    *,
    confidence: str = "medium",
    risk: str = "verification",
) -> dict[str, Any]:
    if not hook_probe.safe_feedback_body(body) or any(pattern.search(body) for pattern in hook_probe.INSTRUCTION_LIKE_PATTERNS):
        raise hook_probe.HookProbeError("unsafe feedback body")
    created_at = hook_probe.utc_now()
    record_id = hook_probe.stable_id("fb", str(observation["record_id"]), body)
    confidence = capped_confidence_for_observation(observation, confidence)
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
        "confidence": confidence,
        "risk": risk,
        "body": body,
        "evidence_refs": [observation["record_id"]],
        "content_digest": "sha256:" + hook_probe.sha256_hex(body),
        "signature": "",
    }
    item["signature"] = hook_probe.sign_feedback_item(item)
    return item


def sub_note_from_feedback_item(item: dict[str, Any]) -> dict[str, Any]:
    body = str(item["body"])
    digest = hook_probe.sha256_hex(" ".join(body.split()))
    dedupe_key = hook_probe.sub_note_dedupe_key(
        str(item["workspace_id"]),
        str(item["session_id"]),
        str(item["risk"]),
        str(item["confidence"]),
        body,
    )
    return {
        "schema_version": 1,
        "message_id": hook_probe.stable_id(
            "submsg",
            str(item["workspace_id"]),
            str(item["session_id"]),
            str(item["derived_from_turn_id"]),
            str(item["risk"]),
            str(item["confidence"]),
            digest,
        ),
        "dedupe_key": dedupe_key,
        "workspace_id": item["workspace_id"],
        "generation": 1,
        "session_id": item["session_id"],
        "derived_from_turn_id": item["derived_from_turn_id"],
        "source": "daemon_review",
        "sequence": int(hook_probe.dt.datetime.now(hook_probe.dt.UTC).timestamp() * 1000),
        "created_at": item["created_at"],
        "expires_at": item["expires_at"],
        "risk": item["risk"],
        "confidence": item["confidence"],
        "body": item["body"],
    }


def sub_note_from_review(observation: dict[str, Any], review: llm_engine.ReviewResult) -> dict[str, Any]:
    if review.body is None:
        raise hook_probe.HookProbeError("missing sub note body")
    body = hook_probe.normalize_sub_note_body(review.body)
    if not hook_probe.safe_sub_note_body(body):
        raise hook_probe.HookProbeError("unsafe sub note body")
    digest = hook_probe.sha256_hex(" ".join(body.split()))
    workspace = str(observation["workspace_id"])
    session_id = str(observation["session_id"])
    turn_id = str(observation["turn_id"])
    risk = review.risk
    confidence = review.confidence
    created_at = hook_probe.utc_now()
    expires_at = (
        hook_probe.dt.datetime.now(hook_probe.dt.UTC).replace(microsecond=0) + hook_probe.dt.timedelta(minutes=30)
    ).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "message_id": hook_probe.stable_id("submsg", workspace, session_id, turn_id, risk, confidence, digest),
        "dedupe_key": hook_probe.sub_note_dedupe_key(workspace, session_id, risk, confidence, body),
        "workspace_id": workspace,
        "generation": 1,
        "session_id": session_id,
        "derived_from_turn_id": turn_id,
        "source": "daemon_review",
        "sequence": int(hook_probe.dt.datetime.now(hook_probe.dt.UTC).timestamp() * 1000),
        "created_at": created_at,
        "expires_at": expires_at,
        "risk": risk,
        "confidence": confidence,
        "body": body,
    }


def sub_note_audit_record(note: dict[str, Any], *, status: str = "pending") -> dict[str, Any]:
    body = str(note.get("body") or "")
    return {
        "schema_version": 2,
        "message_id": note["message_id"],
        "dedupe_key": note["dedupe_key"],
        "workspace_id": note["workspace_id"],
        "generation": note["generation"],
        "session_id": note["session_id"],
        "derived_from_turn_id": note["derived_from_turn_id"],
        "source": note["source"],
        "sequence": note["sequence"],
        "created_at": note["created_at"],
        "expires_at": note["expires_at"],
        "risk": note["risk"],
        "confidence": note["confidence"],
        "body_digest": "sha256:" + hook_probe.sha256_hex(" ".join(body.split())),
        "body_stored": False,
        "status": status,
    }


def existing_sub_note_dedupe_keys(state_dir: Path) -> set[str]:
    path = state_dir / hook_probe.SUB_NOTES_FILE
    if not path.exists():
        return set()
    keys: set[str] = set()
    for item in hook_probe.read_jsonl(path, strict=True):
        if item.get("dedupe_key") is not None:
            keys.add(str(item.get("dedupe_key")))
            continue
        if all(item.get(field) is not None for field in ("workspace_id", "session_id", "risk", "confidence", "body")):
            keys.add(
                hook_probe.sub_note_dedupe_key(
                    str(item["workspace_id"]),
                    str(item["session_id"]),
                    str(item["risk"]),
                    str(item["confidence"]),
                    str(item["body"]),
                )
            )
    return keys


class PendingSubNoteQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._notes_by_route: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
        self._dedupe_keys_by_route: dict[tuple[str, str, str | None], set[str]] = {}

    def __len__(self) -> int:
        with self._lock:
            return sum(len(notes) for notes in self._notes_by_route.values())

    @staticmethod
    def _route_for_note(note: dict[str, Any]) -> tuple[str, str, str | None]:
        thread_id = note.get("thread_id")
        return (
            str(note.get("workspace_id") or ""),
            str(note.get("session_id") or ""),
            str(thread_id) if isinstance(thread_id, str) else None,
        )

    @staticmethod
    def _route_for_prompt(payload: dict[str, Any]) -> tuple[str, str, str | None]:
        thread_id = payload.get("thread_id")
        return (
            str(payload.get("workspace_id") or ""),
            str(payload.get("session_id") or ""),
            str(thread_id) if isinstance(thread_id, str) else None,
        )

    @staticmethod
    def _note_is_fresh(note: dict[str, Any]) -> bool:
        expires = hook_probe.parse_time(str(note.get("expires_at")))
        return expires is not None and expires > hook_probe.dt.datetime.now(hook_probe.dt.UTC)

    def add(self, note: dict[str, Any]) -> bool:
        with self._lock:
            if not self._note_is_fresh(note):
                return False
            route = self._route_for_note(note)
            dedupe_key = str(note["dedupe_key"])
            route_dedupes = self._dedupe_keys_by_route.setdefault(route, set())
            if dedupe_key in route_dedupes:
                return False
            notes = self._notes_by_route.setdefault(route, [])
            notes.append(dict(note))
            route_dedupes.add(dedupe_key)
            notes.sort(key=lambda item: (int(item.get("sequence", 0)), str(item.get("created_at")), str(item.get("message_id"))))
            return True

    def expire_stale(self) -> int:
        removed = 0
        with self._lock:
            for route in list(self._notes_by_route):
                fresh_notes = [note for note in self._notes_by_route[route] if self._note_is_fresh(note)]
                removed += len(self._notes_by_route[route]) - len(fresh_notes)
                if fresh_notes:
                    self._notes_by_route[route] = fresh_notes
                    self._dedupe_keys_by_route[route] = {str(note.get("dedupe_key")) for note in fresh_notes}
                else:
                    self._notes_by_route.pop(route, None)
                    self._dedupe_keys_by_route.pop(route, None)
        return removed

    def drop_inactive_routes_for_prompt(self, payload: dict[str, Any]) -> int:
        active_route = self._route_for_prompt(payload)
        active_routes = {active_route}
        if active_route[2] is not None:
            active_routes.add((active_route[0], active_route[1], None))
        removed = 0
        with self._lock:
            for route in list(self._notes_by_route):
                if route[0] != active_route[0] or route in active_routes:
                    continue
                removed += len(self._notes_by_route[route])
                self._notes_by_route.pop(route, None)
                self._dedupe_keys_by_route.pop(route, None)
        return removed

    def route_counts(self) -> dict[str, int]:
        with self._lock:
            return {"/".join(part if part is not None else "-" for part in route): len(notes) for route, notes in self._notes_by_route.items()}

    def pop_for_prompt(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        turn_id = str(payload.get("turn_id") or "")
        with self._lock:
            route = self._route_for_prompt(payload)
            routes = [route]
            if route[2] is not None:
                routes.append((route[0], route[1], None))
            selected: dict[str, Any] | None = None
            for candidate_route in routes:
                notes = self._notes_by_route.get(candidate_route, [])
                kept: list[dict[str, Any]] = []
                for note in notes:
                    if not self._note_is_fresh(note):
                        continue
                    if selected is None and note.get("derived_from_turn_id") != turn_id:
                        selected = note
                        continue
                    kept.append(note)
                if kept:
                    self._notes_by_route[candidate_route] = kept
                    self._dedupe_keys_by_route[candidate_route] = {str(note.get("dedupe_key")) for note in kept}
                else:
                    self._notes_by_route.pop(candidate_route, None)
                    self._dedupe_keys_by_route.pop(candidate_route, None)
                if selected is not None:
                    break
            return dict(selected) if selected is not None else None


def delivery_for_pending_note(note: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    delivery = hook_probe.delivery_for_sub_note(
        note,
        {
            "cwd": str(payload.get("cwd") or os.getcwd()),
            "session_id": str(payload["session_id"]),
            "turn_id": str(payload["turn_id"]),
            "thread_id": payload.get("thread_id"),
        },
    )
    return hook_probe.emitted_sub_note_delivery(delivery)


class SubNoteIpcServer:
    def __init__(self, state_dir: Path, pending: PendingSubNoteQueue, token: str) -> None:
        self.state_dir = state_dir
        self.pending = pending
        self.token = token
        self.server = self._make_server()
        host, port = self.server.server_address
        self.url = f"http://{host}:{port}"
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def _make_server(self) -> http.server.ThreadingHTTPServer:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send_json(self, status: int, value: dict[str, Any]) -> None:
                body = hook_probe.dumps_json(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                return self.headers.get("X-Subconscious-Token") == outer.token

            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/sub-note/status" or not self._authorized():
                    self._send_json(404, {"status": "not_found"})
                    return
                self._send_json(200, {"status": "ok", "pending_count": len(outer.pending)})

            def do_POST(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != "/sub-note/pop" or not self._authorized():
                    self._send_json(404, {"status": "not_found"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    value = hook_probe.loads_json(self.rfile.read(min(length, 8192)).decode("utf-8"))
                    if not isinstance(value, dict):
                        raise ValueError("payload")
                    if not hook_probe.safe_token(value.get("workspace_id")):
                        raise ValueError("workspace")
                    if not hook_probe.safe_token(value.get("session_id")) or not hook_probe.safe_token(value.get("turn_id")):
                        raise ValueError("scope")
                    thread_id = value.get("thread_id")
                    if thread_id is not None and not hook_probe.safe_token(thread_id):
                        raise ValueError("thread")
                    if value.get("prompt_kind") != "real_user_prompt":
                        self._send_json(200, {"status": "empty", "reason": "not-real-user-prompt"})
                        return
                    outer.pending.expire_stale()
                    outer.pending.drop_inactive_routes_for_prompt(value)
                    note = outer.pending.pop_for_prompt(value)
                    if note is None:
                        status = read_daemon_status(outer.state_dir)
                        if isinstance(status, dict):
                            status["pending_sub_notes"] = len(outer.pending)
                            status["pending_sub_note_routes"] = outer.pending.route_counts()
                            status["last_heartbeat_at"] = hook_probe.utc_now()
                            write_daemon_status(outer.state_dir, status)
                        self._send_json(200, {"status": "empty"})
                        return
                    delivery = delivery_for_pending_note(note, value)
                    hook_probe.append_jsonl(outer.state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE, delivery)
                    status = read_daemon_status(outer.state_dir)
                    if isinstance(status, dict):
                        status["pending_sub_notes"] = len(outer.pending)
                        status["pending_sub_note_routes"] = outer.pending.route_counts()
                        status["last_heartbeat_at"] = hook_probe.utc_now()
                        write_daemon_status(outer.state_dir, status)
                    self._send_json(200, {"status": "note", "note": note, "delivery": delivery})
                except Exception as exc:
                    hook_probe.emit_diagnostic(f"sub-note-ipc-error-{type(exc).__name__}")
                    self._send_json(400, {"status": "error", "error_type": type(exc).__name__})

            def log_message(self, format: str, *args: object) -> None:
                return

        return http.server.ThreadingHTTPServer((IPC_HOST, 0), Handler)


def append_llm_activity(
    state_dir: Path,
    *,
    event: str,
    provider: str,
    observation: dict[str, Any],
    model: str | None = None,
    outcome: str | None = None,
    error: str | None = None,
) -> None:
    record = {
        "schema_version": 1,
        "record_id": hook_probe.stable_id("llmact", event, provider, str(observation.get("record_id")), hook_probe.utc_now()),
        "created_at": hook_probe.utc_now(),
        "event": event,
        "provider": provider,
        "model": model,
        "observation_id": observation.get("record_id"),
        "observation_source": observation.get("source"),
        "observation_turn_id": observation.get("turn_id"),
        "outcome": outcome,
        "error_type": error,
        "raw_prompt_stored": False,
        "raw_response_stored": False,
    }
    hook_probe.append_jsonl(state_dir / hook_probe.LLM_ACTIVITY_FILE, record)


def fallback_review_for_engine_error(error: llm_engine.LlmEngineError) -> llm_engine.ReviewResult | None:
    if error.retryable:
        return None
    if "unsafe feedback body" not in str(error) and "unsafe Sub notes body" not in str(error):
        return None
    return llm_engine.ReviewResult(
        body="Subconscious tried to produce a note that failed local safety checks; keep the next turn focused on concrete evidence rather than using that draft.",
        confidence="medium",
        risk="review",
    )


def review_has_actionable_signal(review: llm_engine.ReviewResult, observation: dict[str, Any]) -> bool:
    if review.body is None:
        return False
    body = review.body.lower()
    if body in hook_probe.LOW_VALUE_SUB_NOTE_BODIES:
        return False
    if not body.startswith("evidence:"):
        return True
    summary = str(observation.get("summary") or "").lower()
    useful_markers = (
        "change_signal=true",
        "verification=passed",
        "verification=failed",
        "verification=not_run",
        "blocker=true",
        "prompt_signals=sub_notes",
        "sub notes",
        "tests were not run",
        "tests failed",
        "failures were reported",
        "code changed",
        "implemented ",
    )
    return any(marker in summary for marker in useful_markers)


def capped_confidence_for_observation(observation: dict[str, Any], confidence: str) -> str:
    if confidence != "high":
        return confidence
    if int(observation.get("fidelity_tier") or 0) > 0 and observation.get("refs") and not observation.get("omitted"):
        return "high"
    return "medium"


def transcript_records_by_id(state_dir: Path) -> dict[str, dict[str, Any]]:
    path = state_dir / hook_probe.TRANSCRIPT_REFS_FILE
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for record in hook_probe.read_jsonl(path, strict=True):
        record_id = record.get("id")
        if isinstance(record_id, str):
            records[record_id] = record
    return records


def read_transcript_tail(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() != ".jsonl" or not path.exists() or not path.is_file() or hook_probe.is_reparse_or_symlink(path):
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > MAX_TRANSCRIPT_TAIL_BYTES:
                handle.seek(size - MAX_TRANSCRIPT_TAIL_BYTES)
                handle.readline()
            raw = handle.read(MAX_TRANSCRIPT_TAIL_BYTES)
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[-MAX_TRANSCRIPT_ROWS:]:
        try:
            value = hook_probe.loads_json(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def text_from_content_blocks(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()


def safe_excerpt(value: Any, max_bytes: int = MAX_TRANSCRIPT_TEXT_BYTES) -> str:
    text, _omitted = hook_probe.bounded_text(value, max_bytes)
    text = " ".join(text.split())
    return text


def rows_for_turn(rows: list[dict[str, Any]], turn_id: str) -> list[dict[str, Any]]:
    start = None
    end = None
    for index, row in enumerate(rows):
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "event_msg" and payload.get("turn_id") == turn_id:
            if payload.get("type") == "task_started":
                start = index
            elif payload.get("type") == "task_complete" and start is not None:
                end = index + 1
    if start is None:
        return rows[-80:]
    return rows[start : end or len(rows)]


def rows_match_session(rows: list[dict[str, Any]], session_id: str) -> bool:
    if not session_id:
        return True
    saw_meta = False
    for row in rows:
        payload = row.get("payload")
        if row.get("type") != "session_meta" or not isinstance(payload, dict):
            continue
        saw_meta = True
        meta_id = payload.get("id") or payload.get("session_id")
        if isinstance(meta_id, str) and meta_id == session_id:
            return True
    return not saw_meta


def transcript_context_from_rows(rows: list[dict[str, Any]], turn_id: str, session_id: str = "") -> str:
    if not rows_match_session(rows, session_id):
        return ""
    turn_rows = rows_for_turn(rows, turn_id)
    user_text = ""
    assistant_text = ""
    injected_notes: list[str] = []
    tool_names: list[str] = []
    tool_result_count = 0
    for row in turn_rows:
        row_type = row.get("type")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row_type == "event_msg":
            event_type = payload.get("type")
            if event_type == "user_message" and isinstance(payload.get("message"), str):
                user_text = payload["message"]
            elif event_type == "agent_message" and isinstance(payload.get("message"), str):
                assistant_text = payload["message"]
        if row_type != "response_item":
            continue
        payload_type = payload.get("type")
        if payload_type == "message":
            role = payload.get("role")
            content_text = text_from_content_blocks(payload.get("content"))
            if role == "developer" and content_text.startswith(hook_probe.SUB_NOTES_PREFIX):
                injected_notes.append(content_text)
            elif role == "assistant" and content_text:
                assistant_text = content_text
            elif role == "user" and content_text:
                user_text = content_text
        elif isinstance(payload_type, str) and payload_type not in {"reasoning"}:
            name = payload.get("name") or payload.get("tool_name") or payload_type
            if isinstance(name, str) and name not in tool_names:
                tool_names.append(name[:80])
            if payload_type in {"function_call_output", "tool_result"}:
                tool_result_count += 1
    pieces: list[str] = []
    if user_text:
        pieces.append(f"user_excerpt={safe_excerpt(user_text)}")
    if assistant_text:
        pieces.append(f"assistant_excerpt={safe_excerpt(assistant_text)}")
    if injected_notes:
        pieces.append(f"injected_sub_notes={safe_excerpt(injected_notes[-1], 160)}")
    if tool_names:
        pieces.append(f"tools={','.join(tool_names[:6])}")
    if tool_result_count:
        pieces.append(f"tool_results={min(tool_result_count, 9)}")
    if not pieces:
        return ""
    context = "transcript_tail: " + "; ".join(pieces)
    context, _omitted = hook_probe.bounded_text(context, MAX_TRANSCRIPT_CONTEXT_BYTES)
    return context


def transcript_context_for_observation(observation: dict[str, Any], state_dir: Path) -> str:
    ref_ids = [
        ref.get("id")
        for ref in observation.get("refs", [])
        if isinstance(ref, dict) and ref.get("kind") == "transcript" and isinstance(ref.get("id"), str)
    ]
    if not ref_ids:
        return ""
    records = transcript_records_by_id(state_dir)
    for ref_id in ref_ids:
        record = records.get(ref_id)
        if not isinstance(record, dict) or record.get("workspace_id") != observation.get("workspace_id"):
            continue
        try:
            path = Path(str(record.get("path") or "")).resolve()
        except (OSError, RuntimeError):
            continue
        if not transcript_path_allowed(path, state_dir):
            continue
        context = transcript_context_from_rows(
            read_transcript_tail(path),
            str(observation.get("turn_id") or ""),
            str(observation.get("session_id") or ""),
        )
        if context:
            return context
    return ""


def transcript_path_allowed(path: Path, state_dir: Path) -> bool:
    try:
        resolved = path.resolve()
        roots = [state_dir.resolve()]
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        roots.append((codex_home / "sessions").resolve())
        for root in roots:
            if resolved == root or root in resolved.parents:
                return True
    except (OSError, RuntimeError):
        return False
    return False


def review_observation_for_engine(observation: dict[str, Any], state_dir: Path) -> dict[str, Any]:
    context = transcript_context_for_observation(observation, state_dir)
    if not context:
        return observation
    enriched = dict(observation)
    summary = f"{observation.get('summary')}; {context}"
    summary, _omitted = hook_probe.bounded_text(summary, hook_probe.MAX_SUMMARY_BYTES)
    enriched["summary"] = summary
    enriched["fidelity_tier"] = max(int(observation.get("fidelity_tier") or 0), 1)
    return enriched


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
        and (
            observation.get("state") == "enqueued"
            or stale_processing_observation(observation)
            or retry_ready_observation(observation)
        )
    ]


def stale_processing_observation(observation: dict[str, Any]) -> bool:
    if observation.get("state") != "processing":
        return False
    lease_expires = hook_probe.parse_time(str(observation.get("lease_expires_at")))
    return lease_expires is not None and lease_expires <= hook_probe.dt.datetime.now(hook_probe.dt.UTC)


def retry_ready_observation(observation: dict[str, Any]) -> bool:
    if observation.get("state") != "retry_wait":
        return False
    next_attempt = hook_probe.parse_time(str(observation.get("next_attempt_at")))
    return next_attempt is not None and next_attempt <= hook_probe.dt.datetime.now(hook_probe.dt.UTC)


def transition_observation(
    observation: dict[str, Any],
    state: str,
    *,
    last_error: str | None = None,
    lease_seconds: int = 30,
) -> dict[str, Any]:
    transitioned = dict(observation)
    transitioned["state"] = state
    transitioned["next_attempt_at"] = None
    transitioned["last_error"] = last_error
    if state == "processing":
        transitioned["attempt"] = int(observation["attempt"]) + 1
        transitioned["processing_by"] = PROCESSOR_ID
        transitioned["lease_expires_at"] = (
            hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(seconds=max(lease_seconds, 30))
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    elif state == "processed":
        transitioned["processing_by"] = observation["processing_by"]
        transitioned["lease_expires_at"] = None
    elif state == "enqueued":
        transitioned["processing_by"] = None
        transitioned["lease_expires_at"] = None
        transitioned["last_error"] = None
    elif state == "retry_wait":
        transitioned["processing_by"] = observation["processing_by"]
        transitioned["lease_expires_at"] = None
        transitioned["next_attempt_at"] = (
            hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(seconds=DEFAULT_RETRY_WAIT_SECONDS)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    elif state == "dead_letter":
        transitioned["processing_by"] = observation["processing_by"]
        transitioned["lease_expires_at"] = None
    else:
        raise ValueError(f"unsupported observation transition: {state}")
    return transitioned


def failure_transition(observation: dict[str, Any], error: str, *, retryable: bool = True) -> dict[str, Any]:
    if not retryable or int(observation.get("attempt") or 0) >= DEFAULT_MAX_ATTEMPTS:
        return transition_observation(observation, "dead_letter", last_error=error[:120])
    return transition_observation(observation, "retry_wait", last_error=error[:120])


def process_once(
    state_dir: Path,
    *,
    max_items: int = DEFAULT_PROCESS_MAX_ITEMS,
    max_seconds: float = DEFAULT_PROCESS_MAX_SECONDS,
    engine: llm_engine.LlmEngine | None = None,
    enable_rollout_watcher: bool = False,
    cwd: Path | None = None,
    pending_sub_notes: PendingSubNoteQueue | None = None,
) -> dict[str, Any]:
    state_dir = hook_probe.resolve_state_dir(state_dir)
    hook_probe.prepare_state_dir(state_dir)
    if enable_rollout_watcher:
        try:
            rollout_path = rollout_watcher.latest_rollout_for_cwd(cwd or Path.cwd())
            if rollout_path is not None:
                rollout_watcher.scan_rollout_once(state_dir, rollout_path, cwd=cwd or Path.cwd())
        except Exception:
            hook_probe.emit_diagnostic("rollout-watcher-scan-error")
    try:
        engine = engine or llm_engine.selected_engine(DEFAULT_BODY, state_dir=state_dir)
    except llm_engine.LlmEngineError as exc:
        return {
            "status": "disabled",
            "created_feedback": 0,
            "created_sub_notes": 0,
            "processed_observations": 0,
            "disabled_reason": str(exc),
            "engine": "unavailable",
        }
    if engine.provider == llm_engine.CHATGPT_PLAN_PROVIDER and not llm_engine.chatgpt_plan_backend_enabled(state_dir):
        return {
            "status": "disabled",
            "created_feedback": 0,
            "created_sub_notes": 0,
            "processed_observations": 0,
            "disabled_reason": "subconscious_chatgpt_plan backend requires explicit workspace opt-in config",
            "engine": llm_engine.CHATGPT_PLAN_PROVIDER,
        }
    created = 0
    created_sub_notes = 0
    disabled_reason = None
    degraded = False
    processed = 0
    started = time.monotonic()
    while processed < max(max_items, 1) and time.monotonic() - started < max(max_seconds, 0.1):
        observation = None
        processing = None
        with hook_probe.locked_state(state_dir, timeout_seconds=min(max_seconds, hook_probe.DEFAULT_LOCK_TIMEOUT_SECONDS)):
            for candidate in eligible_observations(state_dir):
                observation = candidate
                break
            if observation is None:
                break
            if stale_processing_observation(observation):
                observation = transition_observation(observation, "enqueued")
                hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)
            elif retry_ready_observation(observation):
                observation = transition_observation(observation, "enqueued")
                hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)
            processing = transition_observation(
                observation,
                "processing",
                lease_seconds=int(max(getattr(engine, "lease_seconds", 30), 30)),
            )
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, processing)

        try:
            review_input = review_observation_for_engine(observation, state_dir)
            append_llm_activity(
                state_dir,
                event="review_started",
                provider=engine.provider,
                model=getattr(engine, "model", None),
                observation=review_input,
            )
            review = engine.review_observation(review_input, state_dir=state_dir)
            append_llm_activity(
                state_dir,
                event="review_completed",
                provider=engine.provider,
                model=getattr(engine, "model", None),
                observation=review_input,
                outcome="body" if review.body is not None else "abstain",
            )
            if not review_has_actionable_signal(review, review_input):
                with hook_probe.locked_state(state_dir, timeout_seconds=min(max_seconds, hook_probe.DEFAULT_LOCK_TIMEOUT_SECONDS)):
                    hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, transition_observation(processing, "processed"))
                processed += 1
                continue
            sub_note = sub_note_from_review(observation, review)
            item = (
                feedback_item_from_observation(
                    observation,
                    review.body,
                    confidence=review.confidence,
                    risk=review.risk,
                )
                if review.body is not None and hook_probe.safe_feedback_body(review.body)
                else None
            )
        except hook_probe.HookProbeError as exc:
            disabled_reason = str(exc)
            append_llm_activity(
                state_dir,
                event="review_failed",
                provider=engine.provider,
                model=getattr(engine, "model", None),
                observation=observation,
                error=type(exc).__name__,
            )
            with hook_probe.locked_state(state_dir, timeout_seconds=min(max_seconds, hook_probe.DEFAULT_LOCK_TIMEOUT_SECONDS)):
                hook_probe.append_jsonl(
                    state_dir / hook_probe.EVENTS_FILE,
                    failure_transition(processing, disabled_reason, retryable=False),
                )
            processed += 1
            degraded = True
            break
        except llm_engine.LlmEngineError as exc:
            append_llm_activity(
                state_dir,
                event="review_failed",
                provider=engine.provider,
                model=getattr(engine, "model", None),
                observation=observation,
                error=type(exc).__name__,
            )
            fallback_review = fallback_review_for_engine_error(exc)
            if fallback_review is None:
                disabled_reason = str(exc)
                with hook_probe.locked_state(state_dir, timeout_seconds=min(max_seconds, hook_probe.DEFAULT_LOCK_TIMEOUT_SECONDS)):
                    hook_probe.append_jsonl(
                        state_dir / hook_probe.EVENTS_FILE,
                        failure_transition(processing, disabled_reason, retryable=exc.retryable),
                    )
                processed += 1
                degraded = True
                break
            disabled_reason = str(exc)
            sub_note = sub_note_from_review(observation, fallback_review)
            item = (
                feedback_item_from_observation(
                    observation,
                    fallback_review.body,
                    confidence=fallback_review.confidence,
                    risk=fallback_review.risk,
                )
                if fallback_review.body is not None and hook_probe.safe_feedback_body(fallback_review.body)
                else None
            )
            degraded = True

        with hook_probe.locked_state(state_dir, timeout_seconds=min(max_seconds, hook_probe.DEFAULT_LOCK_TIMEOUT_SECONDS)):
            seen_sub_notes = existing_sub_note_dedupe_keys(state_dir)
            if sub_note["dedupe_key"] not in seen_sub_notes:
                hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, sub_note_audit_record(sub_note))
                if pending_sub_notes is not None:
                    pending_sub_notes.add(sub_note)
                seen_sub_notes.add(sub_note["dedupe_key"])
                created_sub_notes += 1
            if item is None:
                hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, transition_observation(processing, "processed"))
                processed += 1
                continue
            seen = existing_feedback_ids(state_dir)
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
        "status": "degraded" if degraded else "ok",
        "created_feedback": created,
        "created_sub_notes": created_sub_notes,
        "processed_observations": processed,
        "disabled_reason": disabled_reason,
        "engine": engine.provider,
    }


def run_loop(
    state_dir: Path,
    interval: float,
    idle_timeout: float | None,
    *,
    engine: llm_engine.LlmEngine | None = None,
    enable_rollout_watcher: bool = False,
    cwd: Path | None = None,
) -> None:
    state_dir = hook_probe.resolve_state_dir(state_dir)
    hook_probe.prepare_state_dir(state_dir)
    started_at = hook_probe.utc_now()
    pending_sub_notes = PendingSubNoteQueue()
    ipc_token = secrets.token_urlsafe(24)
    ipc_server = SubNoteIpcServer(state_dir, pending_sub_notes, ipc_token)
    ipc_server.start()
    last_activity = time.monotonic()
    update_daemon_status(
        state_dir,
        state="running",
        pid=os.getpid(),
        started_at=started_at,
        rollout_watcher_enabled=enable_rollout_watcher,
        ipc_url=ipc_server.url,
        ipc_token=ipc_token,
        pending_sub_notes=len(pending_sub_notes),
        pending_sub_note_routes=pending_sub_notes.route_counts(),
    )
    result: dict[str, Any] | None = None
    last_error_code = None
    try:
        while True:
            try:
                pending_sub_notes.expire_stale()
                result = process_once(
                    state_dir,
                    engine=engine,
                    enable_rollout_watcher=enable_rollout_watcher,
                    cwd=cwd,
                    pending_sub_notes=pending_sub_notes,
                )
                if (
                    int(result.get("created_feedback") or 0) > 0
                    or int(result.get("created_sub_notes") or 0) > 0
                    or pending_observation_count(state_dir) > 0
                ):
                    last_activity = time.monotonic()
                update_daemon_status(
                    state_dir,
                    state="disabled" if result.get("status") == "disabled" else "running",
                    pid=os.getpid(),
                    started_at=started_at,
                    rollout_watcher_enabled=enable_rollout_watcher,
                    last_process_result=result,
                    ipc_url=ipc_server.url,
                    ipc_token=ipc_token,
                    pending_sub_notes=len(pending_sub_notes),
                    pending_sub_note_routes=pending_sub_notes.route_counts(),
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
                    rollout_watcher_enabled=enable_rollout_watcher,
                    last_process_result=result,
                    last_error_code=last_error_code,
                    ipc_url=ipc_server.url,
                    ipc_token=ipc_token,
                    pending_sub_notes=len(pending_sub_notes),
                    pending_sub_note_routes=pending_sub_notes.route_counts(),
                )
            if idle_timeout is not None and time.monotonic() - last_activity >= idle_timeout:
                break
            time.sleep(interval)
    finally:
        ipc_server.stop()
        update_daemon_status(
            state_dir,
            state="stopped",
            pid=os.getpid(),
            started_at=started_at,
            rollout_watcher_enabled=enable_rollout_watcher,
            last_process_result=result,
            last_error_code=last_error_code,
            stopped_at=hook_probe.utc_now(),
            ipc_url=None,
            ipc_token=None,
            pending_sub_notes=0,
            pending_sub_note_routes={},
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the minimal Agent Subconscious reviewer daemon")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--idle-timeout", type=float, default=DEFAULT_IDLE_TIMEOUT_SECONDS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug-allow-fixture-key", action="store_true")
    parser.add_argument("--enable-rollout-watcher", action="store_true")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument(
        "--engine",
        choices=("fake_local", "subconscious_chatgpt_plan"),
        default=None,
        help="Reviewer engine. Defaults to SUBCONSCIOUS_LLM_ENGINE or fake_local.",
    )
    parser.add_argument(
        "--enable-chatgpt-plan-backend-config",
        action="store_true",
        help="Write explicit workspace opt-in for the Subconscious ChatGPT Plan backend and exit.",
    )
    parser.add_argument("--subconscious-login-status", action="store_true", help="Print Subconscious login status and exit.")
    parser.add_argument("--begin-subconscious-login", action="store_true", help="Start Subconscious Codex OAuth login and print the authorization URL.")
    parser.add_argument("--complete-subconscious-login", default=None, help="Complete Subconscious Codex OAuth login with a redirect URL or authorization code.")
    args = parser.parse_args(argv)
    if args.debug_allow_fixture_key:
        hook_probe.os.environ[hook_probe.ALLOW_FIXTURE_KEY_ENV] = "1"
    state_dir = args.state_dir or hook_probe.default_state_dir()
    if args.enable_chatgpt_plan_backend_config:
        record = llm_engine.write_chatgpt_plan_backend_config(state_dir)
        if not args.quiet:
            print(hook_probe.dumps_json({"status": "configured", "provider": record["provider"], "auth_route": record["auth_route"]}))
        return 0
    if args.subconscious_login_status:
        status = llm_engine.read_login_status(state_dir) or {
            "schema_version": 1,
            "provider": llm_engine.CHATGPT_PLAN_PROVIDER,
            "auth_route": llm_engine.CHATGPT_PLAN_AUTH_ROUTE,
            "state": "not_started",
            "redacted_message": "Subconscious ChatGPT Plan OAuth login has not been started.",
        }
        if not args.quiet:
            print(hook_probe.dumps_json(llm_engine.public_login_status(status)))
        return 0
    if args.begin_subconscious_login:
        status = llm_engine.begin_chatgpt_plan_login(state_dir)
        if not args.quiet:
            print(hook_probe.dumps_json(status))
        return 0
    if args.complete_subconscious_login is not None:
        try:
            status = llm_engine.complete_chatgpt_plan_login(state_dir, args.complete_subconscious_login)
        except llm_engine.LlmEngineError as exc:
            status = {
                "schema_version": 1,
                "provider": llm_engine.CHATGPT_PLAN_PROVIDER,
                "auth_route": llm_engine.CHATGPT_PLAN_AUTH_ROUTE,
                "state": "login_failed",
                "redacted_message": str(exc),
            }
        if not args.quiet:
            print(hook_probe.dumps_json(status))
        return 0
    engine: llm_engine.LlmEngine | None = None
    if args.engine == "fake_local":
        engine = llm_engine.FakeLocalEngine(DEFAULT_BODY)
    elif args.engine == "subconscious_chatgpt_plan":
        try:
            if not llm_engine.chatgpt_plan_backend_enabled(state_dir):
                raise llm_engine.LlmEngineError("subconscious_chatgpt_plan backend requires explicit workspace opt-in config")
            engine = llm_engine.SubconsciousChatGptPlanEngine()
        except llm_engine.LlmEngineError as exc:
            if not args.quiet:
                print(
                    hook_probe.dumps_json(
                        {
                            "status": "disabled",
                            "created_feedback": 0,
                            "disabled_reason": str(exc),
                            "engine": llm_engine.CHATGPT_PLAN_PROVIDER,
                        }
                    )
                )
            return 0
    if args.once:
        try:
            result = process_once(state_dir, engine=engine, enable_rollout_watcher=args.enable_rollout_watcher, cwd=args.cwd)
        except Exception as exc:
            hook_probe.emit_diagnostic(f"daemon-once-error-{type(exc).__name__}")
            result = {
                "status": "disabled",
                "created_feedback": 0,
                "disabled_reason": f"{type(exc).__name__}",
                "engine": engine.provider if engine else None,
            }
        if not args.quiet:
            print(hook_probe.dumps_json(result))
        return 0
    idle_timeout = None if args.idle_timeout < 0 else max(args.idle_timeout, 0.0)
    run_loop(
        state_dir,
        max(args.interval, 0.25),
        idle_timeout,
        engine=engine,
        enable_rollout_watcher=args.enable_rollout_watcher,
        cwd=args.cwd,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
