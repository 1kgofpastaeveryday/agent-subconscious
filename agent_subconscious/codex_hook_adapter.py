"""Adapter from real Codex hook payloads to the local hook probe contract.

The probe intentionally rejects raw Codex hook payloads. This adapter is the
small host-facing shim: it validates the observed Codex fields, adds a local
host attestation, and then delegates to the fail-closed probe.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
from pathlib import Path
from typing import Any

from . import hook_probe


SESSION_START_SOURCES = {"startup", "resume", "clear"}
SUPPORTED_EVENTS = {"SessionStart", "UserPromptSubmit", "Stop"}
ASSUME_MAIN_AGENT_ENV = "SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT"


def adapter_diagnostic(reason: str, name: str | None = None) -> None:
    event = name if hook_probe.safe_token(name) else "unknown"
    diagnostic = {
        "subconscious_codex_hook_adapter": {
            "status": "disabled",
            "reason": reason,
            "hook_event_name": event,
        }
    }
    sys.stderr.write(hook_probe.dumps_json(diagnostic))
    sys.stderr.write("\n")
    sys.stderr.flush()


def require_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise hook_probe.HookProbeError(f"missing {field}")
    return value


def validate_real_codex_payload(payload: dict[str, Any]) -> str:
    name = hook_probe.event_name(payload)
    if name not in SUPPORTED_EVENTS:
        raise hook_probe.HookProbeError("unsupported event")
    session_id = require_text(payload, "session_id")
    if not hook_probe.safe_token(session_id):
        raise hook_probe.HookProbeError("invalid session")
    require_text(payload, "cwd")
    for optional_text in ("model", "permission_mode", "transcript_path", "thread_id", "threadId", "conversation_id", "conversationId"):
        value = payload.get(optional_text)
        if value is not None and not isinstance(value, str):
            raise hook_probe.HookProbeError(f"invalid {optional_text}")
        if isinstance(value, str) and value and optional_text in {"thread_id", "threadId", "conversation_id", "conversationId"} and not hook_probe.safe_token(value):
            raise hook_probe.HookProbeError(f"invalid {optional_text}")
    if name == "SessionStart":
        source = payload.get("source") or "startup"
        if not isinstance(source, str):
            raise hook_probe.HookProbeError("invalid session start source")
        if source not in SESSION_START_SOURCES:
            raise hook_probe.HookProbeError("unsupported session start source")
    if name in {"UserPromptSubmit", "Stop"}:
        turn_id = require_text(payload, "turn_id")
        if not hook_probe.safe_token(turn_id):
            raise hook_probe.HookProbeError("invalid turn")
    if name == "UserPromptSubmit" and not isinstance(payload.get("prompt"), str):
        raise hook_probe.HookProbeError("missing prompt")
    if name == "Stop":
        stop_hook_active = payload.get("stop_hook_active")
        if stop_hook_active is not None and not isinstance(stop_hook_active, bool):
            raise hook_probe.HookProbeError("invalid stop flag")
        assistant = payload.get("last_assistant_message")
        if assistant is not None and not isinstance(assistant, str):
            raise hook_probe.HookProbeError("invalid assistant message")
    return name


def host_attestation(payload: dict[str, Any], event_name: str) -> dict[str, Any]:
    expires = (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=30)).replace(microsecond=0)
    record = {
        "schema_version": 1,
        "workspace_id": hook_probe.workspace_id(str(payload.get("cwd") or os.getcwd())),
        "session_id": str(payload["session_id"]),
        "hook_event_name": event_name,
        "agent_role": "main",
        "supported": True,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "signature": "",
    }
    record["signature"] = hook_probe.capability_signature(record)
    return record


def adapt_codex_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_name = validate_real_codex_payload(payload)
    adapted = dict(payload)
    adapted["subconscious_hook_attestation"] = host_attestation(adapted, event_name)
    return adapted


def delegate_to_probe(payload: dict[str, Any], state_dir: Path | None) -> int:
    probe_argv: list[str] = []
    if state_dir is not None:
        probe_argv.extend(["--state-dir", str(state_dir)])
    original_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(hook_probe.dumps_json(payload))
        return hook_probe.main(probe_argv)
    finally:
        sys.stdin = original_stdin


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Subconscious real Codex hook adapter")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument(
        "--debug-assume-main-agent",
        action="store_true",
        help="Fixture spike only: treat this Codex hook stream as the main agent.",
    )
    parser.add_argument(
        "--debug-allow-fixture-key",
        action="store_true",
        help="Fixture spike only: allow the built-in test signing key.",
    )
    args = parser.parse_args(argv)
    if args.debug_assume_main_agent:
        os.environ[ASSUME_MAIN_AGENT_ENV] = "1"
    if args.debug_allow_fixture_key:
        os.environ[hook_probe.ALLOW_FIXTURE_KEY_ENV] = "1"
    try:
        payload = hook_probe.read_stdin_json()
        name = hook_probe.event_name(payload)
        adapted = adapt_codex_payload(payload)
        return delegate_to_probe(adapted, args.state_dir)
    except (hook_probe.HookProbeError, TypeError, ValueError):
        try:
            adapter_diagnostic("invalid-real-codex-hook-input", locals().get("name"))
            print(hook_probe.dumps_json(hook_probe.unsupported_output(locals().get("name", ""))))
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
