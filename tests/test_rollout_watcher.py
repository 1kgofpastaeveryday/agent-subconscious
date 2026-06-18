from __future__ import annotations

import json
import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from agent_subconscious import diagnostics, hook_probe, rollout_watcher


ROOT = Path(__file__).resolve().parents[1]


def write_rollout(path: Path) -> None:
    rows = [
        {
            "timestamp": "2026-05-17T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": "sess_rollout", "cwd": str(ROOT), "cli_version": "0.131.0-alpha.9"},
        },
        {
            "timestamp": "2026-05-17T00:00:01Z",
            "type": "event_msg",
            "payload": {"type": "user_message", "turn_id": "turn1", "message": "please update the hook"},
        },
        {
            "timestamp": "2026-05-17T00:00:02Z",
            "type": "response_item",
            "payload": {"type": "function_call", "name": "shell_command", "call_id": "call1"},
        },
        {
            "timestamp": "2026-05-17T00:00:03Z",
            "type": "event_msg",
            "payload": {"type": "agent_message", "turn_id": "turn1", "message": "implemented code. tests were not run."},
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


class RolloutWatcherTests(unittest.TestCase):
    def test_fallback_rollout_watcher_parses_session_prompt_and_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp) / "state"
            rollout = Path(temp) / "rollout.jsonl"
            write_rollout(rollout)

            with mock.patch("agent_subconscious.rollout_watcher.latest_rollout_for_cwd", return_value=rollout):
                status = rollout_watcher.scan_rollout_once(state_dir, rollout, cwd=ROOT)

            self.assertEqual(status["state"], "active")
            events = hook_probe.read_jsonl(state_dir / hook_probe.EVENTS_FILE, strict=True)
            live = hook_probe.read_jsonl(state_dir / hook_probe.LIVE_EVENT_LOG_FILE, strict=True)
            self.assertIn("SessionStart", {event["source"] for event in events})
            self.assertIn("UserPromptSubmit", {event["source"] for event in events})
            self.assertIn("Stop", {event["source"] for event in events})
            self.assertEqual(live[-1]["source_file_path"], str(rollout.resolve()))
            self.assertEqual(rollout_watcher.read_status(state_dir)["injection_surface"], "hook-dependent")
            self.assertFalse(rollout_watcher.read_status(state_dir)["stale"])

    def test_watcher_loop_re_resolves_latest_rollout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "sessions"
            day = root / "2026" / "05" / "18"
            day.mkdir(parents=True)
            old = day / "old.jsonl"
            new = day / "new.jsonl"
            write_rollout(old)
            write_rollout(new)
            os.utime(old, (1, 1))
            os.utime(new, None)

            with mock.patch("agent_subconscious.rollout_watcher.codex_sessions_root", return_value=root):
                self.assertEqual(rollout_watcher.latest_rollout_for_cwd(ROOT), new)

    def test_diagnostic_reports_hook_watcher_note_and_injection_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            prompt_observation = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn2",
                    "cwd": str(ROOT),
                    "prompt": "continue",
                },
                "UserPromptSubmit",
            )
            hook_probe.append_live_event(
                state_dir,
                prompt_observation,
                injection={"received": True, "kind": "none"},
            )
            stop_observation = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "Stop",
                    "session_id": "sess_1",
                    "turn_id": "turn1",
                    "cwd": str(ROOT),
                    "last_assistant_message": "implemented code. tests were not run.",
                },
                "Stop",
            )
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, stop_observation)
            hook_probe.append_live_event(state_dir, stop_observation)
            note = hook_probe.make_sub_note(str(ROOT), "sess_1", "turn1", "Main Agent changed code without verification; prove the changed path before expanding scope.")
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, note)
            rollout_watcher.write_status(
                state_dir,
                {
                    "schema_version": 1,
                    "state": "active",
                    "updated_at": hook_probe.utc_now(),
                    "rollout_path": "rollout.jsonl",
                    "parsed_rows": 4,
                    "observed_events": 3,
                    "injection_surface": "hook-dependent",
                },
            )

            with mock.patch("agent_subconscious.rollout_watcher.codex_binary_status", return_value={"path": "codex", "version": "codex 0.131.0"}):
                with mock.patch("agent_subconscious.rollout_watcher.watcher_is_stale", return_value=(False, None)):
                    status = diagnostics.current_status(state_dir, cwd=ROOT)

            self.assertEqual(status["codex"]["version"], "codex 0.131.0")
            self.assertTrue(status["hook_events_firing"]["UserPromptSubmit"])
            self.assertEqual(status["rollout_watching"]["state"], "active")
            self.assertEqual(status["latest_prompt_produced_note"]["status"], "note")
            self.assertEqual(status["latest_prompt_received_injection"]["status"], "empty_none")
            self.assertEqual(status["status"], "NOT_READY")

    def test_diagnostic_reports_real_non_empty_delivery_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            note = hook_probe.make_sub_note(
                str(ROOT),
                "sess_1",
                "turn1",
                "Main Agent changed code without verification; prove the changed path before expanding scope.",
            )
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, note)
            payload = {"session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)}
            delivery = hook_probe.delivery_for_sub_note(note, payload)
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE, delivery)
            emitted = hook_probe.emitted_sub_note_delivery(delivery)
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE, emitted)
            prompt_observation = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn2",
                    "cwd": str(ROOT),
                    "prompt": "continue",
                },
                "UserPromptSubmit",
            )
            hook_probe.append_live_event(
                state_dir,
                prompt_observation,
                injection=hook_probe.injection_record(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "sess_1",
                        "turn_id": "turn2",
                        "cwd": str(ROOT),
                    },
                    hook_probe.render_sub_note(note),
                    note=note,
                    delivery=delivery,
                ),
            )
            rollout_watcher.write_status(
                state_dir,
                {
                    "schema_version": 1,
                    "state": "active",
                    "updated_at": hook_probe.utc_now(),
                    "rollout_path": str(Path(temp) / "rollout.jsonl"),
                    "parsed_rows": 4,
                    "observed_events": 3,
                    "injection_surface": "hook-dependent",
                },
            )

            with mock.patch("agent_subconscious.rollout_watcher.watcher_is_stale", return_value=(False, None)):
                with mock.patch("agent_subconscious.daemon.daemon_status_is_live", return_value=True):
                    status = diagnostics.current_status(state_dir, cwd=ROOT)

            self.assertEqual(status["status"], "READY")
            proof = status["readiness"]["latest_non_empty_delivery_proof"]
            self.assertEqual(proof["note_created_at"], note["created_at"])
            self.assertEqual(proof["note_body_digest"], "sha256:" + hook_probe.sha256_hex(note["body"]))
            self.assertEqual(proof["derived_from_turn_id"], "turn1")
            self.assertEqual(proof["claimed_prompt_turn_id"], "turn2")
            self.assertEqual(proof["delivery_state"], "emitted")
            self.assertTrue(proof["visible_injected_sub_notes"])
            self.assertEqual(status["latest_valid_non_empty_delivery_proof"]["status"], "valid")

    def test_rollout_prompt_after_real_non_empty_injection_does_not_clear_ready_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            note = hook_probe.make_sub_note(
                str(ROOT),
                "sess_1",
                "turn1",
                "Main Agent changed code without verification; prove the changed path before expanding scope.",
            )
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, note)
            payload = {"session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)}
            delivery = hook_probe.delivery_for_sub_note(note, payload)
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE, delivery)
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE, hook_probe.emitted_sub_note_delivery(delivery))
            hook_prompt = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn2",
                    "cwd": str(ROOT),
                    "prompt": "real hook prompt",
                },
                "UserPromptSubmit",
            )
            hook_probe.append_live_event(
                state_dir,
                hook_prompt,
                injection=hook_probe.injection_record(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "sess_1",
                        "turn_id": "turn2",
                        "cwd": str(ROOT),
                    },
                    hook_probe.render_sub_note(note),
                    note=note,
                    delivery=delivery,
                ),
            )
            rollout_prompt = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "rollout-user-2",
                    "cwd": str(ROOT),
                    "prompt": "same prompt observed from rollout",
                    "event_id": "rollout-2",
                },
                "UserPromptSubmit",
            )
            hook_probe.append_live_event(
                state_dir,
                rollout_prompt,
                source_file_path=str(Path(temp) / "rollout.jsonl"),
                injection=None,
            )
            rollout_watcher.write_status(
                state_dir,
                {
                    "schema_version": 1,
                    "state": "active",
                    "updated_at": hook_probe.utc_now(),
                    "rollout_path": str(Path(temp) / "rollout.jsonl"),
                    "parsed_rows": 2,
                    "observed_events": 1,
                    "injection_surface": "hook-dependent",
                },
            )

            with mock.patch("agent_subconscious.rollout_watcher.watcher_is_stale", return_value=(False, None)):
                with mock.patch("agent_subconscious.daemon.daemon_status_is_live", return_value=True):
                    status = diagnostics.current_status(state_dir, cwd=ROOT)

            self.assertEqual(status["status"], "READY")
            self.assertEqual(status["latest_prompt_received_injection"]["status"], "not_received")
            self.assertEqual(status["latest_hook_injection"]["status"], "real_non_empty")
            self.assertEqual(status["latest_rollout_observed_prompt"]["turn_id"], "rollout-user-2")
            proof = status["latest_valid_non_empty_delivery_proof"]
            self.assertEqual(proof["status"], "valid")
            self.assertEqual(proof["note_created_at"], note["created_at"])
            self.assertEqual(proof["note_body_digest"], "sha256:" + hook_probe.sha256_hex(note["body"]))
            self.assertEqual(proof["derived_from_turn_id"], "turn1")
            self.assertEqual(proof["claimed_prompt_turn_id"], "turn2")
            self.assertEqual(proof["emitted_at"], delivery["claimed_at"])
            self.assertEqual(proof["prompt_kind"], "real_user_prompt")
            self.assertTrue(proof["visible_injected_sub_notes"])

    def test_diagnostic_flags_stale_visible_sub_notes_separately(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            note = hook_probe.make_sub_note(
                str(ROOT),
                "sess_1",
                "turn1",
                "Main Agent changed code without verification; prove the changed path before expanding scope.",
            )
            note["created_at"] = "2026-05-10T00:00:00Z"
            note["expires_at"] = "2026-05-10T00:01:00Z"
            payload = {"session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)}
            delivery = hook_probe.delivery_for_sub_note(note, payload)
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE, hook_probe.emitted_sub_note_delivery(delivery))
            prompt = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn2",
                    "cwd": str(ROOT),
                    "prompt": "continue",
                },
                "UserPromptSubmit",
            )
            hook_probe.append_live_event(
                state_dir,
                prompt,
                injection=hook_probe.injection_record(
                    {"hook_event_name": "UserPromptSubmit", "session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)},
                    hook_probe.render_sub_note(note),
                    note=note,
                    delivery=delivery,
                ),
            )

            with mock.patch("agent_subconscious.rollout_watcher.watcher_is_stale", return_value=(False, None)):
                with mock.patch("agent_subconscious.daemon.daemon_status_is_live", return_value=True):
                    status = diagnostics.current_status(state_dir, cwd=ROOT)

            self.assertEqual(status["historical_delivery_proof"]["status"], "stale_expired")
            self.assertEqual(status["stale_visible_sub_notes"]["status"], "stale_visible_sub_notes")

    def test_diagnostic_detects_and_ignores_historical_developer_context_sub_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            note = hook_probe.make_sub_note(
                str(ROOT),
                "sess_1",
                "turn1",
                "Main Agent changed code without verification; prove the changed path before expanding scope.",
            )
            delivery_payload = {"session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)}
            delivery = hook_probe.emitted_sub_note_delivery(hook_probe.delivery_for_sub_note(note, delivery_payload))
            prompt2 = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn2",
                    "cwd": str(ROOT),
                    "prompt": "continue",
                },
                "UserPromptSubmit",
            )
            prompt3 = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn3",
                    "cwd": str(ROOT),
                    "prompt": "next",
                },
                "UserPromptSubmit",
            )
            hook_probe.append_live_event(
                state_dir,
                prompt2,
                injection=hook_probe.injection_record(
                    {"hook_event_name": "UserPromptSubmit", "session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)},
                    hook_probe.render_sub_note(note, delivery_payload),
                    note=note,
                    delivery=delivery,
                ),
            )
            hook_probe.append_live_event(state_dir, prompt3, injection=hook_probe.injection_record(delivery_payload | {"turn_id": "turn3"}, None))

            with mock.patch("agent_subconscious.rollout_watcher.watcher_is_stale", return_value=(False, None)):
                with mock.patch("agent_subconscious.daemon.daemon_status_is_live", return_value=True):
                    status = diagnostics.current_status(state_dir, cwd=ROOT)

            self.assertEqual(status["status"], "READY")
            self.assertEqual(status["stale_visible_sub_notes"]["status"], "missing")
            self.assertEqual(status["historical_developer_context_sub_notes"]["status"], "visible_historical")
            self.assertTrue(status["historical_developer_context_sub_notes"]["ignored"])

    def test_diagnostic_quarantines_legacy_pre_marker_historical_sub_note(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            note = hook_probe.make_sub_note(
                str(ROOT),
                "sess_1",
                "turn1",
                "Main Agent changed code without verification; prove the changed path before expanding scope.",
            )
            legacy_delivery = hook_probe.emitted_sub_note_delivery(
                hook_probe.delivery_for_sub_note(note, {"session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)})
            )
            for field in ("target_session_id", "target_turn_id", "target_thread_id"):
                legacy_delivery.pop(field, None)
            hook_probe.append_jsonl(state_dir / hook_probe.SUB_NOTE_DELIVERIES_FILE, legacy_delivery)
            legacy_injection = hook_probe.injection_record(
                {"hook_event_name": "UserPromptSubmit", "session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)},
                hook_probe.render_sub_note(note, {"session_id": "sess_1", "turn_id": "turn2", "cwd": str(ROOT)}),
                note=note,
                delivery=legacy_delivery,
            )
            for field in ("marker", "target_workspace_id", "target_session_id", "target_turn_id", "target_thread_id"):
                legacy_injection.pop(field, None)
            prompt2 = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn2",
                    "cwd": str(ROOT),
                    "prompt": "continue",
                },
                "UserPromptSubmit",
            )
            prompt3 = hook_probe.observation_from_payload(
                {
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": "sess_1",
                    "turn_id": "turn3",
                    "cwd": str(ROOT),
                    "prompt": "next clean turn",
                },
                "UserPromptSubmit",
            )
            hook_probe.append_live_event(state_dir, prompt2, injection=legacy_injection)
            hook_probe.append_live_event(state_dir, prompt3, injection=hook_probe.injection_record({"session_id": "sess_1", "turn_id": "turn3", "cwd": str(ROOT)}, None))

            with mock.patch("agent_subconscious.rollout_watcher.watcher_is_stale", return_value=(False, None)):
                with mock.patch("agent_subconscious.daemon.daemon_status_is_live", return_value=True):
                    status = diagnostics.current_status(state_dir, cwd=ROOT)

            self.assertEqual(status["stale_visible_sub_notes"]["status"], "missing")
            self.assertEqual(status["historical_developer_context_sub_notes"]["status"], "legacy_historical_stale_note")
            self.assertTrue(status["historical_developer_context_sub_notes"]["quarantined"])
            self.assertEqual(status["legacy_historical_delivery_records"]["status"], "present")
            self.assertEqual(status["legacy_historical_delivery_records"]["count"], 1)
            self.assertEqual(status["latest_valid_non_empty_delivery_proof"]["status"], "legacy_historical")


if __name__ == "__main__":
    unittest.main()
