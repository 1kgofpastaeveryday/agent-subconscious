from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "agent-subconscious"
HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "Stop"}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def wait_for_path(path: Path, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def load_launcher_module():
    path = PLUGIN_ROOT / "bin" / "subconscious_hook.py"
    spec = importlib.util.spec_from_file_location("subconscious_hook_test_module", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load launcher module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_for_daemon_stopped(state_dir: Path, timeout: float = 5.0) -> dict:
    path = state_dir / "daemon_status.json"
    deadline = time.time() + timeout
    last_status = None
    while time.time() < deadline:
        if path.exists():
            last_status = json.loads(path.read_text(encoding="utf-8"))
            if last_status.get("state") == "stopped":
                return last_status
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for daemon stop; last={last_status}")


class PluginPackageTests(unittest.TestCase):
    def test_plugin_manifest_points_to_hook_bundle(self) -> None:
        manifest = load_json(PLUGIN_ROOT / ".codex-plugin" / "plugin.json")

        self.assertEqual(manifest["name"], "agent-subconscious")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["hooks"], "./hooks/hooks.json")
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)

    def test_repo_marketplace_exposes_local_plugin(self) -> None:
        marketplace = load_json(ROOT / ".agents" / "plugins" / "marketplace.json")

        self.assertEqual(marketplace["name"], "agent-subconscious-local")
        entries = [item for item in marketplace["plugins"] if item.get("name") == "agent-subconscious"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source"], {"source": "local", "path": "./plugins/agent-subconscious"})
        self.assertEqual(entries[0]["policy"], {"installation": "AVAILABLE", "authentication": "ON_USE"})

    def test_hooks_bundle_covers_required_codex_events(self) -> None:
        hooks = load_json(PLUGIN_ROOT / "hooks" / "hooks.json")["hooks"]

        self.assertEqual(set(hooks), HOOK_EVENTS)
        for event_name in HOOK_EVENTS:
            handlers = hooks[event_name][0]["hooks"]
            self.assertEqual(len(handlers), 1)
            for handler in handlers:
                command = handler["command"]
                self.assertIn(str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"), command)
                self.assertIn("subconscious_hook.py", command)
                self.assertIn("with-daemon", command)
                self.assertIn("--debug-assume-main-agent", command)
                self.assertIn("--debug-allow-fixture-key", command)

    def test_adapter_debug_flags_enable_fixture_spike_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            env = os.environ.copy()
            env.pop("SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT", None)
            env.pop("SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY", None)

            subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                    "--state-dir",
                    str(state_dir),
                    "--debug-assume-main-agent",
                    "--debug-allow-fixture-key",
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                        "cwd": str(ROOT),
                        "source": "startup",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "agent_subconscious.codex_hook_adapter",
                    "--state-dir",
                    str(state_dir),
                    "--debug-assume-main-agent",
                    "--debug-allow-fixture-key",
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                        "cwd": str(ROOT),
                        "model": "gpt-5.4-mini",
                        "permission_mode": "default",
                        "source": "startup",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            capability_file_exists = (state_dir / "session_capabilities.jsonl").exists()

        self.assertEqual(json.loads(proc.stdout), {"hookSpecificOutput": {"hookEventName": "SessionStart"}})
        self.assertEqual(proc.stderr, "")
        self.assertTrue(capability_file_exists)

    def test_plugin_launcher_exposes_login_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            proc = subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                    "login",
                    "--state-dir",
                    str(state_dir),
                    "--no-browser",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=True,
            )

            result = json.loads(proc.stdout)
            self.assertEqual(result["status"], "login_pending")
            self.assertIn("authorize_url", result)
            self.assertNotIn("code_verifier", proc.stdout)

    def test_stop_launcher_runs_adapter_before_daemon_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            env = os.environ.copy()
            env.pop("SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT", None)
            env.pop("SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY", None)
            env["SUBCONSCIOUS_DAEMON_IDLE_TIMEOUT_SECONDS"] = "0.25"
            env["SUBCONSCIOUS_DAEMON_INTERVAL_SECONDS"] = "0.05"

            subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                    "with-daemon",
                    "--state-dir",
                    str(state_dir),
                    "--debug-assume-main-agent",
                    "--debug-allow-fixture-key",
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                        "cwd": str(ROOT),
                        "source": "startup",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                    "with-daemon",
                    "--state-dir",
                    str(state_dir),
                    "--debug-assume-main-agent",
                    "--debug-allow-fixture-key",
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "hook_event_name": "Stop",
                        "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                        "turn_id": "019e1730-72b2-7c07-84d7-586439661044",
                        "cwd": str(ROOT),
                        "last_assistant_message": "implemented hook change. tests were not run.",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            wait_for_path(state_dir / "sub_notes.jsonl")
            events = [json.loads(line) for line in (state_dir / "observation_events.jsonl").read_text(encoding="utf-8").splitlines()]
            sub_notes = [json.loads(line) for line in (state_dir / "sub_notes.jsonl").read_text(encoding="utf-8").splitlines()]
            daemon_status = json.loads((state_dir / "daemon_status.json").read_text(encoding="utf-8"))
            stopped_status = wait_for_daemon_stopped(state_dir)

        self.assertEqual(json.loads(proc.stdout), {})
        self.assertEqual(proc.stderr, "")
        self.assertEqual([event["state"] for event in events], ["enqueued", "enqueued", "processing", "processed"])
        self.assertEqual(sub_notes[0]["derived_from_turn_id"], events[1]["turn_id"])
        self.assertEqual(daemon_status["processor_id"], "daemon_minimal_reviewer")
        self.assertIn(daemon_status["state"], {"running", "stopped"})
        self.assertIsInstance(daemon_status["pid"], int)
        self.assertEqual(stopped_status["state"], "stopped")

    def test_launcher_selects_chatgpt_plan_engine_when_login_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            from agent_subconscious import hook_probe, llm_engine

            hook_probe.prepare_state_dir(state_dir)
            llm_engine.write_chatgpt_plan_backend_config(state_dir)
            llm_engine.write_state_json(
                state_dir,
                hook_probe.AUTH_PROFILES_FILE,
                {
                    "schema_version": 1,
                    "profiles": {
                        llm_engine.CHATGPT_PLAN_PROFILE_ID: {
                            "schema_version": 1,
                            "access_token": "fresh-access-token",
                            "refresh_token": "refresh-token",
                            "expires_at": "2999-01-01T00:00:00Z",
                        }
                    },
                },
            )
            launcher = load_launcher_module()

            self.assertEqual(launcher.desired_engine_args(state_dir), ["--engine", "subconscious_chatgpt_plan"])

    def test_launcher_does_not_select_chatgpt_plan_for_stale_ready_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            from agent_subconscious import hook_probe, llm_engine

            hook_probe.prepare_state_dir(state_dir)
            llm_engine.write_chatgpt_plan_backend_config(state_dir)
            llm_engine.write_state_json(
                state_dir,
                hook_probe.LOGIN_STATUS_FILE,
                {
                    "schema_version": 1,
                    "provider": llm_engine.CHATGPT_PLAN_PROVIDER,
                    "auth_route": llm_engine.CHATGPT_PLAN_AUTH_ROUTE,
                    "state": "ready",
                },
            )
            launcher = load_launcher_module()

            self.assertEqual(launcher.desired_engine_args(state_dir), [])

    def test_stop_launcher_fails_closed_when_daemon_state_is_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            env = os.environ.copy()
            env["SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT"] = "1"
            env["SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY"] = "1"
            env["SUBCONSCIOUS_DAEMON_IDLE_TIMEOUT_SECONDS"] = "0.25"
            env["SUBCONSCIOUS_DAEMON_INTERVAL_SECONDS"] = "0.05"
            subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                    "with-daemon",
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "hook_event_name": "SessionStart",
                        "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                        "cwd": str(ROOT),
                        "source": "startup",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            (state_dir / "feedback_items.jsonl").write_text("{not-json\n", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                    "with-daemon",
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "hook_event_name": "Stop",
                        "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                        "turn_id": "019e1730-72b2-7c07-84d7-586439661044",
                        "cwd": str(ROOT),
                        "last_assistant_message": "done",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            wait_for_daemon_stopped(state_dir)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})
        self.assertEqual(proc.stderr, "")

    def test_launcher_does_not_start_daemon_for_invalid_capability_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            state_dir.mkdir(exist_ok=True)
            (state_dir / "session_capabilities.jsonl").write_text('{"not":"a-capability"}\n', encoding="utf-8")
            env = os.environ.copy()
            env["SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT"] = "1"
            env["SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY"] = "1"

            proc = subprocess.run(
                [
                    sys.executable,
                    str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                    "with-daemon",
                    "--state-dir",
                    str(state_dir),
                ],
                cwd=ROOT,
                input=json.dumps(
                    {
                        "hook_event_name": "Stop",
                        "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                        "turn_id": "019e1730-72b2-7c07-84d7-586439661044",
                        "cwd": str(ROOT),
                        "last_assistant_message": "done",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            daemon_started = (state_dir / "daemon_status.json").exists()

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), {})
        self.assertFalse(daemon_started)

    def test_launcher_reports_daemon_start_failure_diagnostic(self) -> None:
        env = os.environ.copy()
        env["SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT"] = "1"
        env["SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY"] = "1"

        proc = subprocess.run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "bin" / "subconscious_hook.py"),
                "with-daemon",
                "--state-dir",
                str(ROOT),
            ],
            cwd=ROOT,
            input=json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "019e1730-6bcc-7a90-9465-a72be73e9549",
                    "cwd": str(ROOT),
                    "source": "startup",
                }
            ),
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )

        self.assertEqual(proc.returncode, 0)
        self.assertIn("daemon-start-error-HookProbeError", proc.stderr)

    def test_launcher_fails_closed_on_unhandled_hook_exception(self) -> None:
        module = load_launcher_module()
        original_argv = sys.argv
        try:
            sys.argv = ["subconscious_hook.py", "with-daemon"]
            with mock.patch.object(module, "main", side_effect=RuntimeError("boom")):
                with mock.patch.object(module, "write_emergency_diagnostic") as diagnostic:
                    self.assertEqual(module.fail_closed_main(), 0)
        finally:
            sys.argv = original_argv

        diagnostic.assert_called_once()


if __name__ == "__main__":
    unittest.main()
