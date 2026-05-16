from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from agent_subconscious import daemon, hook_probe


ROOT = Path(__file__).resolve().parents[1]


def write_stop_observation(state_dir: Path, session_id: str = "sess_1", turn_id: str = "turn1") -> dict:
    payload = {
        "hook_event_name": "Stop",
        "session_id": session_id,
        "turn_id": turn_id,
        "cwd": str(ROOT),
        "last_assistant_message": "done",
    }
    observation = hook_probe.observation_from_payload(payload, "Stop")
    hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)
    return observation


class DaemonTests(unittest.TestCase):
    def test_process_once_creates_verified_feedback_for_stop_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            observation = write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {hook_probe.ALLOW_FIXTURE_KEY_ENV: "1"}):
                status = daemon.process_once(state_dir)
                feedback = hook_probe.read_jsonl(state_dir / hook_probe.FEEDBACK_FILE, strict=True)
                ok, reason = hook_probe.verify_feedback_item(
                    feedback[0],
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "sess_1",
                        "turn_id": "turn2",
                        "cwd": str(ROOT),
                        "prompt": "continue",
                    },
                )

            self.assertEqual(status["created_feedback"], 1)
            self.assertEqual(feedback[0]["source_observation_ids"], [observation["record_id"]])
            self.assertTrue(ok, reason)
            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]
            self.assertEqual(latest["state"], "processed")
            self.assertEqual([event["state"] for event in hook_probe.read_jsonl(state_dir / hook_probe.EVENTS_FILE, strict=True)], ["enqueued", "processing", "processed"])

    def test_process_once_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {hook_probe.ALLOW_FIXTURE_KEY_ENV: "1"}):
                daemon.process_once(state_dir)
                second = daemon.process_once(state_dir)

            feedback_lines = (state_dir / hook_probe.FEEDBACK_FILE).read_text(encoding="utf-8").splitlines()
            self.assertEqual(second["created_feedback"], 0)
            self.assertEqual(len([line for line in feedback_lines if line.strip()]), 1)
            self.assertEqual(len(hook_probe.read_jsonl(state_dir / hook_probe.EVENTS_FILE, strict=True)), 3)

    def test_process_once_marks_existing_feedback_observation_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            observation = write_stop_observation(state_dir)
            with mock.patch.dict(os.environ, {hook_probe.ALLOW_FIXTURE_KEY_ENV: "1"}):
                item = daemon.feedback_item_from_observation(observation)
                hook_probe.append_jsonl(state_dir / hook_probe.FEEDBACK_FILE, item)
                status = daemon.process_once(state_dir)

            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(latest["state"], "processed")

    def test_process_once_recovers_expired_processing_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            observation = write_stop_observation(state_dir)
            processing = daemon.transition_observation(observation, "processing")
            processing["lease_expires_at"] = (
                hook_probe.dt.datetime.now(hook_probe.dt.UTC) - hook_probe.dt.timedelta(seconds=1)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, processing)

            with mock.patch.dict(os.environ, {hook_probe.ALLOW_FIXTURE_KEY_ENV: "1"}):
                status = daemon.process_once(state_dir)

            events = hook_probe.read_jsonl(state_dir / hook_probe.EVENTS_FILE, strict=True)
            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]
            self.assertEqual(status["created_feedback"], 1)
            self.assertEqual(latest["state"], "processed")
            self.assertEqual([event["state"] for event in events], ["enqueued", "processing", "enqueued", "processing", "processed"])

    def test_process_once_disables_without_signing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {}, clear=True):
                status = daemon.process_once(state_dir)

            self.assertEqual(status["status"], "disabled")
            self.assertIn("signing key", status["disabled_reason"])
            self.assertFalse((state_dir / hook_probe.FEEDBACK_FILE).exists())

    def test_cli_once_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch("sys.argv", ["daemon", "--state-dir", str(state_dir), "--once", "--debug-allow-fixture-key"]):
                    with redirect_stdout(StringIO()):
                        self.assertEqual(daemon.main(None), 0)
            self.assertTrue(json.loads((state_dir / hook_probe.FEEDBACK_FILE).read_text(encoding="utf-8").splitlines()[0]))

    def test_cli_help_does_not_resolve_default_state_dir(self) -> None:
        with mock.patch.object(hook_probe, "default_state_dir", side_effect=AssertionError("should not resolve")):
            with redirect_stdout(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    daemon.main(["--help"])

        self.assertEqual(raised.exception.code, 0)

    def test_daemon_status_is_live_requires_fresh_heartbeat_and_state_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp) / "state"
            other_dir = Path(temp) / "other"
            hook_probe.prepare_state_dir(state_dir)
            hook_probe.prepare_state_dir(other_dir)
            status = daemon.daemon_status_record(state_dir, state="running", pid=os.getpid(), started_at=None)
            stale = dict(status)
            stale["last_heartbeat_at"] = (
                hook_probe.dt.datetime.now(hook_probe.dt.UTC) - hook_probe.dt.timedelta(seconds=120)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            wrong_process = dict(status)
            wrong_process["process_start_key"] = "procstat:not-this-process"

            self.assertTrue(daemon.daemon_status_is_live(status, state_dir))
            self.assertFalse(daemon.daemon_status_is_live(stale, state_dir))
            self.assertFalse(daemon.daemon_status_is_live(status, other_dir))
            self.assertFalse(daemon.daemon_status_is_live(wrong_process, state_dir))
            self.assertIsInstance(status["process_start_key"], str)
            self.assertIsInstance(status["executable_path_id"], str)

    def test_daemon_status_snapshot_round_trips_without_temp_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            status = daemon.daemon_status_record(state_dir, state="running", pid=os.getpid(), started_at=None)

            with hook_probe.locked_state(state_dir):
                daemon.write_daemon_status(state_dir, status)

            self.assertEqual(daemon.read_daemon_status(state_dir), status)
            self.assertEqual(list(state_dir.glob(f".{hook_probe.DAEMON_STATUS_FILE}.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
