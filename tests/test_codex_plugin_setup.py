from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest import mock

from agent_subconscious import codex_plugin_setup
from agent_subconscious import hook_probe
from agent_subconscious import llm_engine


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "agent-subconscious"


class CodexPluginSetupTests(unittest.TestCase):
    def test_posix_hook_trust_hashes_match_codex_identity(self) -> None:
        entries = codex_plugin_setup.hook_trust_entries(PLUGIN_ROOT, platform="posix")

        self.assertEqual(len(entries), 3)
        self.assertTrue(all(entry.key.startswith("agent-subconscious@agent-subconscious-local:hooks/hooks.json:") for entry in entries))
        self.assertTrue(all(entry.trusted_hash.startswith("sha256:") for entry in entries))

    def test_install_copies_plugin_and_writes_idempotent_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            result = codex_plugin_setup.install(PLUGIN_ROOT, codex_home, platform="posix")
            first_config = (codex_home / "config.toml").read_text(encoding="utf-8")
            second = codex_plugin_setup.install(PLUGIN_ROOT, codex_home, platform="posix")
            second_config = (codex_home / "config.toml").read_text(encoding="utf-8")

            self.assertEqual(result["trusted_hook_count"], 3)
            self.assertEqual(second["status"], "installed")
            self.assertEqual(first_config, second_config)
            self.assertTrue((codex_home / "plugins" / "cache" / "agent-subconscious-local" / "agent-subconscious" / "0.1.0" / ".codex-plugin" / "plugin.json").exists())
            self.assertTrue((codex_home / "plugins" / "cache" / "agent-subconscious-local" / "agent-subconscious" / "0.1.0" / "agent_subconscious" / "hook_probe.py").exists())
            cached_hook = codex_home / "plugins" / "cache" / "agent-subconscious-local" / "agent-subconscious" / "0.1.0" / "hooks" / "hooks.json"
            cached_command = next(iter(codex_plugin_setup.load_json(cached_hook)["hooks"].values()))[0]["hooks"][0]["command"]
            cached_timeout = next(iter(codex_plugin_setup.load_json(cached_hook)["hooks"].values()))[0]["hooks"][0]["timeout"]
            self.assertIn(str(Path(codex_plugin_setup.sys.executable).resolve()), cached_command)
            self.assertEqual(cached_timeout, 30)
            self.assertIn('[plugins."agent-subconscious@agent-subconscious-local"]', first_config)
            self.assertIn("plugin_hooks = true", first_config)

    def test_fixture_mode_install_renders_debug_hook_and_doctor_is_fixture_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            codex_path = Path(temp) / "codex"
            with mock.patch.object(codex_plugin_setup, "codex_executable", return_value=codex_path):
                with mock.patch.object(codex_plugin_setup, "codex_version", return_value="codex-cli test"):
                    with mock.patch.dict(os.environ, {}, clear=True):
                        result = codex_plugin_setup.install(PLUGIN_ROOT, codex_home, platform="windows", fixture_mode=True)
                        status = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="windows", fixture_mode=True)
                        normal_status = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="windows")

            cached_hook = codex_home / "plugins" / "cache" / "agent-subconscious-local" / "agent-subconscious" / "0.1.0" / "hooks" / "hooks.json"
            cached_command = next(iter(codex_plugin_setup.load_json(cached_hook)["hooks"].values()))[0]["hooks"][0]["command"]
            self.assertEqual(result["fixture_mode"], True)
            self.assertIn("--debug-assume-main-agent", cached_command)
            self.assertIn("--debug-allow-fixture-key", cached_command)
            self.assertEqual(status["status"], "fixture_ready")
            self.assertEqual(status["mode"], "unknown")
            self.assertTrue(status["fixture_ready"])
            self.assertTrue(status["fixture_signing_ready"])
            self.assertTrue(status["fixture_main_agent_assumption"])
            self.assertTrue(status["host_setup_ready"])
            self.assertFalse(status["host_surface_ready"])
            self.assertEqual(normal_status["status"], "needs_setup")
            self.assertFalse(normal_status["cache_matches_source"])

    def test_doctor_reports_needs_setup_login_required_and_fixture_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            codex_path = Path(temp) / "codex"
            with mock.patch.object(codex_plugin_setup, "codex_executable", return_value=codex_path):
                with mock.patch.object(codex_plugin_setup, "codex_version", return_value="codex-cli test"):
                    with mock.patch.dict(os.environ, {}, clear=True):
                        before = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="posix")
                        codex_plugin_setup.install(PLUGIN_ROOT, codex_home, platform="posix")
                        after = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="posix")
                    with mock.patch.dict(
                        os.environ,
                        {
                            "SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY": "1",
                            "SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT": "1",
                        },
                        clear=True,
                    ):
                        fixture = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="posix")
                    with mock.patch.dict(os.environ, {"SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY": "1"}, clear=True):
                        signing_only = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="posix")

            self.assertEqual(before["status"], "needs_setup")
            self.assertEqual(before["mode"], "plugin_missing")
            self.assertEqual(after["status"], "login_required")
            self.assertEqual(after["mode"], "login_required")
            self.assertEqual(after["schema_version"], 1)
            self.assertEqual(after["command"], ["subconscious", "doctor", "host-setup"])
            self.assertEqual(after["exit_code"], 2)
            self.assertTrue(after["installed"])
            self.assertTrue(after["configured"])
            self.assertFalse(after["auth_ready"])
            self.assertTrue(after["cache_matches_source"])
            self.assertIn("log in to subconscious", after["auth_message"])
            self.assertIn("open the ChatGPT login URL", after["auth_message"])
            self.assertEqual(after["next_action"]["type"], "ask_codex")
            self.assertEqual(after["next_action"]["prompt"], "log in to subconscious")
            self.assertEqual(
                after["next_action"]["expected_codex_action"],
                "start_subconscious_oauth_and_open_chatgpt_login_url",
            )
            self.assertEqual(after["findings"][0]["code"], "login_required")
            self.assertEqual(after["findings"][0]["next_action"], after["next_action"])
            self.assertEqual(fixture["status"], "fixture_ready")
            self.assertEqual(fixture["mode"], "unknown")
            self.assertFalse(fixture["auth_ready"])
            self.assertTrue(fixture["fixture_ready"])
            self.assertFalse(fixture["host_surface_ready"])
            self.assertEqual(fixture["findings"][0]["code"], "fixture_only")
            self.assertEqual(signing_only["status"], "login_required")
            self.assertFalse(signing_only["fixture_ready"])
            self.assertTrue(signing_only["fixture_signing_ready"])
            self.assertFalse(signing_only["fixture_main_agent_assumption"])

    def test_doctor_exit_code_requires_login_when_auth_missing(self) -> None:
        self.assertEqual(codex_plugin_setup.exit_code_for_status("ok"), 0)
        self.assertEqual(codex_plugin_setup.exit_code_for_status("installed"), 0)
        self.assertEqual(codex_plugin_setup.exit_code_for_status("fixture_ready"), 1)
        self.assertEqual(codex_plugin_setup.exit_code_for_status("login_required"), 2)
        self.assertEqual(codex_plugin_setup.exit_code_for_status("needs_setup"), 2)
        self.assertEqual(codex_plugin_setup.exit_code_for_status("unsupported"), 3)

    def test_doctor_reports_ready_when_chatgpt_plan_login_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            codex_path = Path(temp) / "codex"
            state_dir = Path(temp) / "state"
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
                secret=True,
            )
            with mock.patch.object(codex_plugin_setup, "codex_executable", return_value=codex_path):
                with mock.patch.object(codex_plugin_setup, "codex_version", return_value="codex-cli test"):
                    with mock.patch.dict(os.environ, {hook_probe.STATE_DIR_ENV: str(state_dir)}, clear=True):
                        codex_plugin_setup.install(PLUGIN_ROOT, codex_home, platform="posix")
                        status = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="posix")

        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["mode"], "ready")
        self.assertTrue(status["auth_ready"])
        self.assertTrue(status["host_setup_ready"])
        self.assertTrue(status["host_surface_ready"])
        self.assertFalse(status["login_required"])
        self.assertEqual(status["findings"], [])

    def test_doctor_main_returns_probe_failure_exit_code_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stderr = StringIO()
            with redirect_stderr(stderr):
                status = codex_plugin_setup.main(
                    [
                        "doctor",
                        "--plugin-root",
                        str(Path(temp) / "missing-plugin"),
                        "--codex-home",
                        str(Path(temp) / ".codex"),
                        "--json",
                    ]
                )

        self.assertEqual(status, 4)
        self.assertIn('"status":"error"', stderr.getvalue())

    def test_doctor_detects_stale_installed_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            codex_path = Path(temp) / "codex"
            with mock.patch.object(codex_plugin_setup, "codex_executable", return_value=codex_path):
                with mock.patch.object(codex_plugin_setup, "codex_version", return_value="codex-cli test"):
                    codex_plugin_setup.install(PLUGIN_ROOT, codex_home, platform="posix")
                    cached_hook = codex_home / "plugins" / "cache" / "agent-subconscious-local" / "agent-subconscious" / "0.1.0" / "hooks" / "hooks.json"
                    cached_hook.write_text('{"hooks":{}}\n', encoding="utf-8")

                    status = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="posix")

            self.assertEqual(status["status"], "needs_setup")
            self.assertFalse(status["cache_matches_source"])
            self.assertEqual(status["findings"][0]["code"], "plugin_missing")

    def test_doctor_config_check_is_scoped_to_expected_tables(self) -> None:
        entries = codex_plugin_setup.hook_trust_entries(PLUGIN_ROOT, platform="posix")
        misleading = """
[features]
hooks=true
plugins = true
plugin_hooks = true

[plugins."agent-subconscious@agent-subconscious-local"]
enabled = false

[plugins."other@market"]
enabled = true
"""

        self.assertFalse(codex_plugin_setup.config_contains_required_state(misleading, entries))

    def test_doctor_reports_codex_missing_before_login_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            codex_home = Path(temp) / ".codex"
            codex_plugin_setup.install(PLUGIN_ROOT, codex_home, platform="posix")
            with mock.patch.object(codex_plugin_setup, "codex_executable", return_value=None):
                status = codex_plugin_setup.doctor(PLUGIN_ROOT, codex_home, platform="posix")

        self.assertEqual(status["status"], "unsupported")
        self.assertEqual(status["mode"], "codex_missing")
        self.assertEqual(status["exit_code"], 3)
        self.assertEqual(status["findings"][0]["code"], "codex_missing")

    def test_config_writer_replaces_keys_without_requiring_spaces(self) -> None:
        updated = codex_plugin_setup.set_toml_value("[features]\nhooks=true\n", "features", "hooks", "true")

        self.assertEqual(updated.count("hooks"), 1)
        self.assertIn("hooks = true", updated)


if __name__ == "__main__":
    unittest.main()
