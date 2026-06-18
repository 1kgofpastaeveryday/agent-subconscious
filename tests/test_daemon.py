from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from agent_subconscious import daemon, hook_probe, llm_engine


ROOT = Path(__file__).resolve().parents[1]


def write_stop_observation(state_dir: Path, session_id: str = "sess_1", turn_id: str = "turn1") -> dict:
    payload = {
        "hook_event_name": "Stop",
        "session_id": session_id,
        "turn_id": turn_id,
        "cwd": str(ROOT),
        "last_assistant_message": "implemented hook_probe.py changes. tests were not run.",
    }
    observation = hook_probe.observation_from_payload(payload, "Stop")
    hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)
    return observation


def write_stop_observation_with_transcript(state_dir: Path, session_id: str = "sess_1", turn_id: str = "turn1") -> dict:
    transcript_path = state_dir / "rollout.jsonl"
    rows = [
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": turn_id},
        },
        {
            "type": "event_msg",
            "payload": {"type": "user_message", "turn_id": turn_id, "message": "please implement the hook change"},
        },
        {
            "type": "response_item",
            "payload": {"type": "function_call", "name": "functions.shell_command", "call_id": "call_1"},
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "turn_id": turn_id,
                "message": "implemented hook_probe.py changes. tests were not run.",
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_complete", "turn_id": turn_id},
        },
    ]
    transcript_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    payload = {
        "hook_event_name": "Stop",
        "session_id": session_id,
        "turn_id": turn_id,
        "cwd": str(ROOT),
        "transcript_path": str(transcript_path),
        "last_assistant_message": "implemented hook_probe.py changes. tests were not run.",
    }
    observation = hook_probe.observation_from_payload(payload, "Stop")
    hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)
    hook_probe.append_transcript_ref_if_new(state_dir, payload)
    return observation


class DaemonTests(unittest.TestCase):
    def test_process_once_creates_verified_feedback_for_stop_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            observation = write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {hook_probe.ALLOW_FIXTURE_KEY_ENV: "1"}):
                status = daemon.process_once(state_dir)
                sub_notes = hook_probe.read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, strict=True)
                activity = hook_probe.read_jsonl(state_dir / hook_probe.LLM_ACTIVITY_FILE, strict=True)

            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 1)
            self.assertEqual(status["engine"], "fake_local")
            self.assertEqual(sub_notes[0]["derived_from_turn_id"], observation["turn_id"])
            self.assertNotIn("body", sub_notes[0])
            self.assertFalse(sub_notes[0]["body_stored"])
            self.assertTrue(sub_notes[0]["body_digest"].startswith("sha256:"))
            self.assertEqual([entry["event"] for entry in activity], ["review_started", "review_completed"])
            self.assertEqual(activity[-1]["outcome"], "body")
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

            sub_note_lines = (state_dir / hook_probe.SUB_NOTES_FILE).read_text(encoding="utf-8").splitlines()
            self.assertEqual(second["created_feedback"], 0)
            self.assertEqual(second["created_sub_notes"], 0)
            self.assertEqual(len([line for line in sub_note_lines if line.strip()]), 1)
            self.assertEqual(len(hook_probe.read_jsonl(state_dir / hook_probe.EVENTS_FILE, strict=True)), 3)

    def test_process_once_marks_existing_feedback_observation_processed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            observation = write_stop_observation(state_dir)
            with mock.patch.dict(os.environ, {hook_probe.ALLOW_FIXTURE_KEY_ENV: "1"}):
                item = daemon.feedback_item_from_observation(
                    observation,
                    "verification gap: tests were not run after code changes.",
                )
                hook_probe.append_jsonl(state_dir / hook_probe.FEEDBACK_FILE, item)
                status = daemon.process_once(state_dir)

            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 1)
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
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 1)
            self.assertEqual(latest["state"], "processed")
            self.assertEqual([event["state"] for event in events], ["enqueued", "processing", "enqueued", "processing", "processed"])

    def test_process_once_creates_default_feedback_signing_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {}, clear=True):
                status = daemon.process_once(state_dir)

            self.assertEqual(status["status"], "ok")
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 1)
            self.assertFalse((state_dir / hook_probe.FEEDBACK_FILE).exists())
            self.assertTrue((state_dir / hook_probe.SUB_NOTES_FILE).exists())

    def test_process_once_abstains_for_low_signal_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            payload = {
                "hook_event_name": "Stop",
                "session_id": "sess_1",
                "turn_id": "turn1",
                "cwd": str(ROOT),
                "last_assistant_message": "done",
            }
            observation = hook_probe.observation_from_payload(payload, "Stop")
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)

            status = daemon.process_once(state_dir)

            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 0)
            self.assertEqual(latest["state"], "processed")
            self.assertFalse((state_dir / hook_probe.FEEDBACK_FILE).exists())
            activity = hook_probe.read_jsonl(state_dir / hook_probe.LLM_ACTIVITY_FILE, strict=True)
            self.assertEqual(activity[-1]["outcome"], "abstain")

    def test_process_once_abstains_for_generic_code_unknown_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            payload = {
                "hook_event_name": "Stop",
                "session_id": "sess_1",
                "turn_id": "turn1",
                "cwd": str(ROOT),
                "last_assistant_message": "updated agent_subconscious/hook_probe.py.",
            }
            observation = hook_probe.observation_from_payload(payload, "Stop")
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)

            status = daemon.process_once(state_dir)

            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 0)
            self.assertEqual(latest["state"], "processed")
            self.assertFalse((state_dir / hook_probe.SUB_NOTES_FILE).exists())

    def test_process_once_ignores_user_prompt_submit_for_note_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            payload = {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "sess_1",
                "turn_id": "turn1",
                "cwd": str(ROOT),
                "prompt": "second test prompt: this turn should produce some sub note",
            }
            observation = hook_probe.observation_from_payload(payload, "UserPromptSubmit")
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)

            status = daemon.process_once(state_dir)
            state = (state_dir / hook_probe.EVENTS_FILE).read_text(encoding="utf-8")

            self.assertEqual(status["created_sub_notes"], 0)
            self.assertEqual(status["processed_observations"], 0)
            self.assertFalse((state_dir / hook_probe.SUB_NOTES_FILE).exists())
            self.assertNotIn("second test prompt", state)

    def test_process_once_dedupes_same_sub_note_body_within_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            for index in range(2):
                payload = {
                    "hook_event_name": "Stop",
                    "session_id": "sess_1",
                    "turn_id": f"turn{index}",
                    "cwd": str(ROOT),
                    "last_assistant_message": "implemented hook_probe.py changes. tests were not run.",
                }
                hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, hook_probe.observation_from_payload(payload, "Stop"))

            status = daemon.process_once(state_dir)
            sub_notes = hook_probe.read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, strict=True)

            self.assertEqual(status["created_sub_notes"], 1)
            self.assertEqual(len(sub_notes), 1)

    def test_process_once_filters_low_signal_live_evidence(self) -> None:
        class EvidenceEngine(llm_engine.LlmEngine):
            provider = llm_engine.CHATGPT_PLAN_PROVIDER

            def review_observation(self, observation: dict, *, state_dir: Path) -> llm_engine.ReviewResult:
                return llm_engine.ReviewResult(
                    body="evidence: assistant complied with a simple acknowledgment request.",
                    confidence="medium",
                    risk="verification",
                )

        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            llm_engine.write_chatgpt_plan_backend_config(state_dir)
            payload = {
                "hook_event_name": "Stop",
                "session_id": "sess_1",
                "turn_id": "turn1",
                "cwd": str(ROOT),
                "last_assistant_message": "done",
            }
            observation = hook_probe.observation_from_payload(payload, "Stop")
            hook_probe.append_jsonl(state_dir / hook_probe.EVENTS_FILE, observation)

            status = daemon.process_once(state_dir, engine=EvidenceEngine())

            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 0)
            self.assertEqual(latest["state"], "processed")
            self.assertFalse((state_dir / hook_probe.FEEDBACK_FILE).exists())

    def test_process_once_falls_back_when_live_reviewer_returns_unsafe_feedback(self) -> None:
        class UnsafeEngine(llm_engine.LlmEngine):
            provider = llm_engine.CHATGPT_PLAN_PROVIDER

            def review_observation(self, observation: dict, *, state_dir: Path) -> llm_engine.ReviewResult:
                raise llm_engine.LlmEngineError("Codex OAuth model returned unsafe feedback body", retryable=False)

        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            llm_engine.write_chatgpt_plan_backend_config(state_dir)
            observation = write_stop_observation(state_dir)

            status = daemon.process_once(state_dir, engine=UnsafeEngine())
            sub_notes = hook_probe.read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, strict=True)
            latest = hook_probe.read_observations_by_id(state_dir)[observation["record_id"]]

            self.assertEqual(status["status"], "degraded")
            self.assertEqual(status["created_feedback"], 0)
            self.assertEqual(status["created_sub_notes"], 1)
            self.assertEqual(status["disabled_reason"], "Codex OAuth model returned unsafe feedback body")
            self.assertEqual(sub_notes[0]["risk"], "review")
            self.assertNotIn("body", sub_notes[0])
            self.assertFalse(sub_notes[0]["body_stored"])
            self.assertEqual(latest["state"], "processed")

    def test_process_once_enriches_live_review_with_transient_transcript_tail(self) -> None:
        class InspectingEngine(llm_engine.LlmEngine):
            provider = "inspecting"

            def __init__(self) -> None:
                self.summary = ""

            def review_observation(self, observation: dict, *, state_dir: Path) -> llm_engine.ReviewResult:
                self.summary = observation["summary"]
                return llm_engine.ReviewResult(
                    body="verification gap: tests were not run after code changes.",
                    confidence="medium",
                    risk="verification",
                )

        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            observation = write_stop_observation_with_transcript(state_dir)
            engine = InspectingEngine()

            status = daemon.process_once(state_dir, engine=engine)
            feedback = hook_probe.read_jsonl(state_dir / hook_probe.FEEDBACK_FILE, strict=True)
            sub_notes = hook_probe.read_jsonl(state_dir / hook_probe.SUB_NOTES_FILE, strict=True)

            self.assertEqual(status["created_feedback"], 1)
            self.assertEqual(status["created_sub_notes"], 1)
            self.assertIn("transcript_tail:", engine.summary)
            self.assertIn("assistant_excerpt=implemented hook_probe.py changes. tests were not run.", engine.summary)
            self.assertEqual(feedback[0]["body"], "verification gap: tests were not run after code changes.")
            self.assertNotIn("body", sub_notes[0])
            self.assertEqual(
                sub_notes[0]["body_digest"],
                "sha256:" + hook_probe.sha256_hex("verification gap: tests were not run after code changes."),
            )
            self.assertEqual(feedback[0]["source_observation_ids"], [observation["record_id"]])

    def test_chatgpt_plan_engine_requires_workspace_opt_in_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {llm_engine.ENGINE_ENV: "subconscious_chatgpt_plan"}, clear=True):
                status = daemon.process_once(state_dir)

            self.assertEqual(status["status"], "disabled")
            self.assertEqual(status["created_sub_notes"], 0)
            self.assertIn("explicit workspace opt-in", status["disabled_reason"])

    def test_chatgpt_plan_backend_opt_in_still_requires_login_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            write_stop_observation(state_dir)
            llm_engine.write_chatgpt_plan_backend_config(state_dir)

            with mock.patch.dict(os.environ, {llm_engine.ENGINE_ENV: "subconscious_chatgpt_plan"}, clear=True):
                status = daemon.process_once(state_dir)

            self.assertEqual(status["status"], "degraded")
            self.assertEqual(status["created_sub_notes"], 0)
            self.assertIn("login is not ready", status["disabled_reason"])
            self.assertFalse((state_dir / hook_probe.FEEDBACK_FILE).exists())
            activity = hook_probe.read_jsonl(state_dir / hook_probe.LLM_ACTIVITY_FILE, strict=True)
            self.assertEqual(activity[-1]["event"], "review_failed")

    def test_cli_can_write_chatgpt_plan_backend_config_and_login_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)

            with redirect_stdout(StringIO()) as output:
                self.assertEqual(
                    daemon.main(["--state-dir", str(state_dir), "--enable-chatgpt-plan-backend-config"]),
                    0,
                )
            configured = json.loads(output.getvalue())
            self.assertEqual(configured["provider"], "subconscious_chatgpt_plan")

            with redirect_stdout(StringIO()) as output:
                self.assertEqual(daemon.main(["--state-dir", str(state_dir), "--begin-subconscious-login"]), 0)
            login = json.loads(output.getvalue())
            self.assertEqual(login["state"], "login_pending")
            self.assertIn("authorize_url", login)
            self.assertNotIn("code_verifier", login)

    def test_complete_chatgpt_plan_login_stores_profile_without_printing_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            pending = llm_engine.begin_chatgpt_plan_login(state_dir)
            login = llm_engine.read_login_status(state_dir)
            access = fake_jwt({"exp": int((hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(hours=1)).timestamp()), "account_id": "acct_123", "email": "user@example.com"})
            token = {"access_token": access, "refresh_token": "rt_secret", "expires_in": 3600}

            def fake_exchange(code: str, verifier: str) -> dict:
                self.assertEqual(code, "auth_code")
                self.assertEqual(verifier, login["code_verifier"])
                return token

            callback = "http://localhost:1455/auth/callback?" + urllib.parse.urlencode(
                {"code": "auth_code", "state": login["oauth_state"]}
            )
            with mock.patch("agent_subconscious.llm_engine.exchange_code_for_token", side_effect=fake_exchange):
                status = llm_engine.complete_chatgpt_plan_login(state_dir, callback)

            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["account_id"], "acct_123")
            self.assertNotIn("access_token", hook_probe.dumps_json(status))
            self.assertNotIn("refresh_token", hook_probe.dumps_json(status))
            profiles = llm_engine.read_auth_profiles(state_dir)
            self.assertEqual(profiles[llm_engine.CHATGPT_PLAN_PROFILE_ID]["refresh_token"], "rt_secret")
            self.assertNotEqual(pending["authorize_url"], "")

    def test_chatgpt_plan_account_id_uses_nested_auth_claim_id(self) -> None:
        access = fake_jwt(
            {
                "exp": int((hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(hours=1)).timestamp()),
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct_nested",
                    "chatgpt_user_id": "user_nested",
                    "localhost": True,
                },
            }
        )

        profile = llm_engine.token_response_to_profile({"access_token": access, "refresh_token": "rt_secret"})
        status = llm_engine.ready_login_status({"access_token": access, "refresh_token": "rt_secret", "account_id": str({"bad": "dict"})})

        self.assertEqual(profile["account_id"], "acct_nested")
        self.assertEqual(status["account_id"], "acct_nested")

    def test_chatgpt_plan_access_token_refreshes_expired_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            expired_access = fake_jwt({"exp": int((hook_probe.dt.datetime.now(hook_probe.dt.UTC) - hook_probe.dt.timedelta(hours=1)).timestamp()), "account_id": "acct_123"})
            fresh_access = fake_jwt({"exp": int((hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(hours=1)).timestamp()), "account_id": "acct_123"})
            profile = llm_engine.token_response_to_profile({"access_token": expired_access, "refresh_token": "rt_secret", "expires_in": 1})
            profile["expires_at"] = (
                hook_probe.dt.datetime.now(hook_probe.dt.UTC) - hook_probe.dt.timedelta(hours=1)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            llm_engine.write_auth_profiles(state_dir, {llm_engine.CHATGPT_PLAN_PROFILE_ID: profile})

            with mock.patch("agent_subconscious.llm_engine.refresh_chatgpt_plan_token", return_value={"access_token": fresh_access, "refresh_token": "rt_new", "expires_in": 3600}):
                token = llm_engine.resolve_chatgpt_plan_access_token(state_dir)

            self.assertEqual(token, fresh_access)
            profiles = llm_engine.read_auth_profiles(state_dir)
            self.assertEqual(profiles[llm_engine.CHATGPT_PLAN_PROFILE_ID]["refresh_token"], "rt_new")

    def test_chatgpt_plan_engine_posts_sanitized_codex_responses_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            observation = write_stop_observation(state_dir)
            observation["summary"] = "assistant turn completed; token=sk-thisshouldnotleave"
            (state_dir / hook_probe.EVENTS_FILE).write_text(hook_probe.dumps_json(observation) + "\n", encoding="utf-8", newline="\n")
            access = fake_jwt({"exp": int((hook_probe.dt.datetime.now(hook_probe.dt.UTC) + hook_probe.dt.timedelta(hours=1)).timestamp()), "account_id": "acct_123"})
            profile = llm_engine.token_response_to_profile({"access_token": access, "refresh_token": "rt_secret", "expires_in": 3600})
            llm_engine.write_auth_profiles(state_dir, {llm_engine.CHATGPT_PLAN_PROFILE_ID: profile})
            engine = llm_engine.SubconsciousChatGptPlanEngine(model="gpt-5.5", timeout_seconds=1)
            seen = {}

            def fake_urlopen(request: urllib.request.Request, timeout: float):
                seen["url"] = request.full_url
                seen["headers"] = dict(request.header_items())
                seen["body"] = json.loads(request.data.decode("utf-8"))
                return FakeHttpResponse(
                    "\n".join(
                        [
                            "event: response.output_text.delta",
                            "data: "
                            + json.dumps(
                                {
                                    "type": "response.output_text.delta",
                                    "delta": json.dumps(
                                        {
                                            "body": "privacy risk: sanitized observation omitted sensitive material.",
                                            "confidence": "medium",
                                            "risk": "privacy",
                                        }
                                    ),
                                }
                            ),
                            "",
                            "data: [DONE]",
                            "",
                        ]
                    )
                )

            with mock.patch("agent_subconscious.llm_engine.urllib.request.urlopen", side_effect=fake_urlopen):
                review = engine.review_observation(observation, state_dir=state_dir)

            self.assertEqual(review.risk, "privacy")
            self.assertEqual(seen["url"], llm_engine.CHATGPT_PLAN_RESPONSES_URL)
            self.assertEqual(seen["headers"]["Authorization"], f"Bearer {access}")
            self.assertEqual(seen["body"]["model"], "gpt-5.5")
            self.assertFalse(seen["body"]["store"])
            self.assertTrue(seen["body"]["stream"])
            self.assertIn("instructions", seen["body"])
            input_text = seen["body"]["input"][0]["content"][0]["text"]
            self.assertIn("[REDACTED]", input_text)
            self.assertNotIn("sk-thisshouldnotleave", input_text)
            self.assertIn("Return exactly ABSTAIN", input_text)

    def test_review_result_from_text_allows_abstain(self) -> None:
        review = llm_engine.review_result_from_text('{"body":null,"confidence":"low","risk":"review"}')

        self.assertIsNone(review.body)
        self.assertEqual(review.confidence, "low")
        self.assertEqual(review.risk, "review")

    def test_review_result_from_text_accepts_freeform_sub_note(self) -> None:
        review = llm_engine.review_result_from_text(
            "Main Agent went straight to plumbing; another useful angle is to keep Subconscious as a delayed idea generator before adding more review logic."
        )

        self.assertIn("delayed idea generator", review.body)
        self.assertEqual(review.risk, "review")

    def test_cli_once_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            hook_probe.prepare_state_dir(state_dir)
            write_stop_observation(state_dir)

            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch("sys.argv", ["daemon", "--state-dir", str(state_dir), "--once", "--debug-allow-fixture-key"]):
                    with redirect_stdout(StringIO()):
                        self.assertEqual(daemon.main(None), 0)
            self.assertTrue(json.loads((state_dir / hook_probe.SUB_NOTES_FILE).read_text(encoding="utf-8").splitlines()[0]))

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

    def test_pending_sub_note_queue_does_not_leak_across_sessions(self) -> None:
        queue = daemon.PendingSubNoteQueue()
        note = hook_probe.make_sub_note(
            str(ROOT),
            "sess_A",
            "turn0",
            "verification gap: pending note must not cross sessions.",
        )

        self.assertTrue(queue.add(note))
        self.assertIsNone(
            queue.pop_for_prompt(
                {
                    "workspace_id": hook_probe.workspace_id(str(ROOT)),
                    "session_id": "sess_B",
                    "turn_id": "turn1",
                    "thread_id": None,
                }
            )
        )
        self.assertIsNotNone(
            queue.pop_for_prompt(
                {
                    "workspace_id": hook_probe.workspace_id(str(ROOT)),
                    "session_id": "sess_A",
                    "turn_id": "turn1",
                    "thread_id": None,
                }
            )
        )

    def test_pending_sub_note_queue_expires_old_notes(self) -> None:
        queue = daemon.PendingSubNoteQueue()
        note = hook_probe.make_sub_note(
            str(ROOT),
            "sess_A",
            "turn0",
            "verification gap: expired pending note must be dropped.",
        )
        note["expires_at"] = (
            hook_probe.dt.datetime.now(hook_probe.dt.UTC) - hook_probe.dt.timedelta(seconds=1)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        self.assertFalse(queue.add(note))
        self.assertEqual(len(queue), 0)

    def test_pending_sub_note_queue_drops_other_workspace_sessions_for_active_prompt(self) -> None:
        queue = daemon.PendingSubNoteQueue()
        note_a = hook_probe.make_sub_note(
            str(ROOT),
            "sess_A",
            "turn0",
            "verification gap: session a stale pending note must be dropped.",
        )
        note_b = hook_probe.make_sub_note(
            str(ROOT),
            "sess_B",
            "turn0",
            "verification gap: session b active pending note should remain.",
        )

        self.assertTrue(queue.add(note_a))
        self.assertTrue(queue.add(note_b))
        removed = queue.drop_inactive_routes_for_prompt(
            {
                "workspace_id": hook_probe.workspace_id(str(ROOT)),
                "session_id": "sess_B",
                "turn_id": "turn1",
                "thread_id": None,
            }
        )

        self.assertEqual(removed, 1)
        self.assertEqual(queue.route_counts(), {f"{hook_probe.workspace_id(str(ROOT))}/sess_B/-": 1})
        self.assertIsNotNone(
            queue.pop_for_prompt(
                {
                    "workspace_id": hook_probe.workspace_id(str(ROOT)),
                    "session_id": "sess_B",
                    "turn_id": "turn1",
                    "thread_id": None,
                }
            )
        )

def fake_jwt(payload: dict) -> str:
    header = base64_url_json({"alg": "none", "typ": "JWT"})
    body = base64_url_json(payload)
    return f"{header}.{body}.sig"


def base64_url_json(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return __import__("base64").urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class FakeHttpResponse:
    def __init__(self, payload: dict | str) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.payload, str):
            return self.payload.encode("utf-8")
        return json.dumps(self.payload).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
