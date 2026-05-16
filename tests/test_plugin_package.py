from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


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
                self.assertIn("${PLUGIN_ROOT}", command)
                self.assertIn("subconscious_hook.py", command)
                self.assertIn("with-daemon", command)
                self.assertNotIn("--debug-allow-fixture-key", command)
                self.assertNotIn("--debug-assume-main-agent", command)

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
                        "last_assistant_message": "done",
                    }
                ),
                text=True,
                capture_output=True,
                env=env,
                check=True,
            )
            wait_for_path(state_dir / "feedback_items.jsonl")
            events = [json.loads(line) for line in (state_dir / "observation_events.jsonl").read_text(encoding="utf-8").splitlines()]
            feedback = [json.loads(line) for line in (state_dir / "feedback_items.jsonl").read_text(encoding="utf-8").splitlines()]
            daemon_status = json.loads((state_dir / "daemon_status.json").read_text(encoding="utf-8"))
            stopped_status = wait_for_daemon_stopped(state_dir)

        self.assertEqual(json.loads(proc.stdout), {})
        self.assertEqual(proc.stderr, "")
        self.assertEqual([event["state"] for event in events], ["enqueued", "enqueued", "processing", "processed"])
        self.assertEqual(feedback[0]["source_observation_ids"], [events[1]["record_id"]])
        self.assertEqual(daemon_status["processor_id"], "daemon_minimal_reviewer")
        self.assertIn(daemon_status["state"], {"running", "stopped"})
        self.assertIsInstance(daemon_status["pid"], int)
        self.assertEqual(stopped_status["state"], "stopped")

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


if __name__ == "__main__":
    unittest.main()
