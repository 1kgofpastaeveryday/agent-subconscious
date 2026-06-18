from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_subconscious import hook_probe


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_SEEDED_BODY = "Main Agent changed code without verification; before continuing design work, it may be worth proving the changed path with the smallest focused check."
LOW_VALUE_BODIES = {
    "evidence: sub notes pipeline activity was observed.",
    "verification gap: verification status was not visible after a code related turn.",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def codex_base_args(cwd: Path, model: str) -> list[str]:
    return [
        codex_command(),
        "exec",
        "-C",
        str(cwd),
        "--sandbox",
        "danger-full-access",
        "--json",
        "-m",
        model,
    ]


def codex_command() -> str:
    return "codex.cmd" if os.name == "nt" else "codex"


def run_codex(args: list[str], output_file: Path, prompt: str, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    command = [*args, "-o", str(output_file), prompt]
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout_seconds)


def latest_session_id(state_dir: Path, workspace_id: str, started_at: float) -> str:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        events = [
            item
            for item in read_jsonl(state_dir / hook_probe.EVENTS_FILE)
            if item.get("workspace_id") == workspace_id and item.get("session_id")
        ]
        if events:
            events.sort(key=lambda item: str(item.get("created_at") or ""))
            candidate = str(events[-1]["session_id"])
            if candidate:
                return candidate
        time.sleep(0.5)
    raise RuntimeError(f"no Codex hook session appeared after {started_at}")


def append_seed_note(state_dir: Path, cwd: Path, session_id: str, body: str) -> dict[str, Any]:
    note = hook_probe.make_sub_note(
        str(cwd),
        session_id,
        "harness_seed_turn",
        body,
        risk="verification",
        confidence="medium",
        source="codex_exec_harness",
    )
    existing = {
        str(item.get("dedupe_key"))
        for item in read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE)
        if item.get("workspace_id") == note["workspace_id"] and item.get("session_id") == session_id
    }
    if note["dedupe_key"] not in existing:
        hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, note)
    return note


def sub_notes_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip().lower().startswith("sub notes:")]


def evaluate_note_body(body: str) -> dict[str, Any]:
    if body.lower().startswith("sub notes:"):
        body = body[len("Sub notes:") :].strip()
    lowered = " ".join(body.lower().split())
    if lowered in LOW_VALUE_BODIES:
        return {"body": body, "quality": "fail", "reason": "low-value pipeline or generic verification note"}
    if lowered.startswith(("verification gap:", "correctness risk:", "safety risk:", "scope risk:", "privacy risk:", "operations risk:", "review quality risk:", "intent mismatch:")):
        return {"body": body, "quality": "pass", "reason": "actionable risk framing"}
    if lowered.startswith("evidence:"):
        return {"body": body, "quality": "warn", "reason": "evidence-only note; useful only if concrete"}
    if 40 <= len(body) <= hook_probe.MAX_SUB_NOTES_BODY_CHARS and hook_probe.safe_sub_note_body(body):
        return {"body": body, "quality": "pass", "reason": "freeform alternate angle"}
    return {"body": body, "quality": "fail", "reason": "unsafe or too short freeform note"}


def delivery_count(state_dir: Path, session_id: str, body: str) -> int:
    notes = {
        str(item.get("message_id"))
        for item in read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE)
        if item.get("session_id") == session_id and item.get("body") == body
    }
    return sum(
        1
        for item in read_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE)
        if item.get("session_id") == session_id and item.get("message_id") in notes and item.get("state") == "emitted"
    )


def line_evaluations(lines: list[str]) -> list[dict[str, Any]]:
    return [evaluate_note_body(line) for line in lines if line.strip().lower() != "sub notes: none"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a real Codex CLI exec/resume check for Subconscious injection.")
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--body", default=DEFAULT_SEEDED_BODY)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    cwd = args.cwd.resolve()
    state_dir = hook_probe.default_state_dir(str(cwd))
    workspace_id = hook_probe.workspace_id(str(cwd))
    hook_probe.prepare_state_dir(state_dir)
    before = time.time()

    with tempfile.TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        first_output = temp_dir / "first.txt"
        second_output = temp_dir / "second.txt"
        third_output = temp_dir / "third.txt"

        base = codex_base_args(cwd, args.model)
        first = run_codex(
            base,
            first_output,
            "Subconscious harness turn 1. Reply exactly: HARNESS_TURN_1_DONE. Do not modify files.",
            args.timeout,
        )
        if first.returncode != 0:
            print(json.dumps({"status": "fail", "phase": "first_exec", "stderr": first.stderr[-2000:]}, ensure_ascii=False))
            return 1

        session_id = latest_session_id(state_dir, workspace_id, before)
        note = append_seed_note(state_dir, cwd, session_id, args.body)

        resume_base = [
            codex_command(),
            "exec",
            "resume",
            session_id,
            "--json",
            "-m",
            args.model,
        ]
        second = run_codex(
            resume_base,
            second_output,
            "Harness turn 2. If a Sub notes line is present in your prompt context, begin with that exact line, then print HARNESS_TURN_2_DONE. Do not modify files.",
            args.timeout,
        )
        third = run_codex(
            resume_base,
            third_output,
            "Harness turn 3. If a Sub notes line is present in your prompt context, begin with that exact line, then print HARNESS_TURN_3_DONE. Do not modify files.",
            args.timeout,
        )

        first_text = first_output.read_text(encoding="utf-8", errors="replace") if first_output.exists() else ""
        second_text = second_output.read_text(encoding="utf-8", errors="replace") if second_output.exists() else ""
        third_text = third_output.read_text(encoding="utf-8", errors="replace") if third_output.exists() else ""
        second_lines = sub_notes_lines(second_text)
        third_lines = sub_notes_lines(third_text)
        seed_line = f"Sub notes: {args.body}"
        emitted = delivery_count(state_dir, session_id, args.body)
        evaluations = {
            "first": line_evaluations(sub_notes_lines(first_text)),
            "second": line_evaluations(second_lines),
            "third": line_evaluations(third_lines),
        }
        all_quality_ok = all(
            evaluation["quality"] in {"pass", "warn"}
            for phase_evaluations in evaluations.values()
            for evaluation in phase_evaluations
        )
        result = {
            "status": "pass"
            if second.returncode == 0
            and third.returncode == 0
            and seed_line in second_lines
            and seed_line not in third_lines
            and emitted == 1
            and evaluate_note_body(args.body)["quality"] == "pass"
            and all_quality_ok
            else "fail",
            "workspace_id": workspace_id,
            "state_dir": str(state_dir),
            "session_id": session_id,
            "note_quality": evaluate_note_body(args.body),
            "sub_notes_quality": evaluations,
            "first_sub_notes_lines": sub_notes_lines(first_text),
            "second_sub_notes_lines": second_lines,
            "third_sub_notes_lines": third_lines,
            "seed_note_message_id": note["message_id"],
            "seed_note_delivery_count": emitted,
            "codex_returncodes": [first.returncode, second.returncode, third.returncode],
            "stderr_tail": {
                "first": first.stderr[-1200:],
                "second": second.stderr[-1200:],
                "third": third.stderr[-1200:],
            },
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
