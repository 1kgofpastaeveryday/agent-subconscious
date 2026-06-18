from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from agent_subconscious import cli, llm_engine


class CliTests(unittest.TestCase):
    def test_login_starts_oauth_and_enables_backend_without_browser(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(cli.main(["--state-dir", str(state_dir), "login", "--no-browser"]), 0)

            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "login_pending")
            self.assertEqual(result["provider"], llm_engine.CHATGPT_PLAN_PROVIDER)
            self.assertFalse(result["opened_browser"])
            self.assertIn("authorize_url", result)
            self.assertTrue(llm_engine.chatgpt_plan_backend_enabled(state_dir))

    def test_login_waits_for_callback_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            completed = {
                "schema_version": 1,
                "provider": llm_engine.CHATGPT_PLAN_PROVIDER,
                "auth_route": llm_engine.CHATGPT_PLAN_AUTH_ROUTE,
                "state": "ready",
            }

            with mock.patch("agent_subconscious.cli.webbrowser.open", return_value=True) as opened:
                with mock.patch("agent_subconscious.cli.OAuthCallbackServer", FakeCallbackServer):
                    with mock.patch("agent_subconscious.cli.llm_engine.complete_chatgpt_plan_login", return_value=completed) as complete:
                        with redirect_stdout(StringIO()) as output:
                            self.assertEqual(cli.main(["--state-dir", str(state_dir), "login", "--timeout", "1"]), 0)

            result = json.loads(output.getvalue())
            self.assertEqual(result["state"], "ready")
            self.assertTrue(result["opened_browser"])
            opened.assert_called_once()
            self.assertIn("code=auth_code", complete.call_args.args[1])

    def test_oauth_callback_server_captures_redirect_url(self) -> None:
        server = cli.OAuthCallbackServer("http://localhost:0/auth/callback", timeout_seconds=2)
        with server:
            port = server.server.server_address[1]
            with urllib.request.urlopen(f"http://localhost:{port}/auth/callback?code=auth_code&state=oauth_state", timeout=2) as response:
                self.assertIn("Authentication successful", response.read().decode("utf-8"))

            callback = server.wait_for_callback()

        self.assertIn("code=auth_code", callback)
        self.assertIn("state=oauth_state", callback)

    def test_login_complete_reports_failure_without_pending_login(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            with redirect_stdout(StringIO()) as output:
                self.assertEqual(cli.main(["--state-dir", str(state_dir), "login", "--complete", "code"]), 1)

            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "login_failed")
            self.assertIn("no pending", result["redacted_message"])

    def test_status_redacts_pending_login_secret_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            llm_engine.begin_chatgpt_plan_login(state_dir)

            with redirect_stdout(StringIO()) as output:
                self.assertEqual(cli.main(["--state-dir", str(state_dir), "status"]), 0)

            text = output.getvalue()
            result = json.loads(text)
            self.assertEqual(result["login"]["state"], "login_pending")
            self.assertNotIn("code_verifier", text)
            self.assertNotIn("oauth_state", text)

    def test_sub_notes_sets_global_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config_path = Path(temp) / "global.json"
            with mock.patch.dict(os.environ, {"SUBCONSCIOUS_GLOBAL_CONFIG_PATH": str(config_path)}):
                with redirect_stdout(StringIO()) as output:
                    self.assertEqual(cli.main(["sub-notes", "none"]), 0)

                result = json.loads(output.getvalue())
                self.assertEqual(result["sub_notes_mode"], "none")
                self.assertEqual(json.loads(config_path.read_text(encoding="utf-8"))["sub_notes_mode"], "none")

    def test_run_delegates_to_daemon_chatgpt_plan_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            with mock.patch("agent_subconscious.cli.daemon.main", return_value=0) as daemon_main:
                self.assertEqual(cli.main(["--state-dir", str(state_dir), "run", "--once", "--quiet"]), 0)

            argv = daemon_main.call_args.args[0]
            self.assertIn("--once", argv)
            self.assertIn("--quiet", argv)
            self.assertIn("subconscious_chatgpt_plan", argv)


class FakeCallbackServer:
    def __init__(self, redirect_uri: str, *, timeout_seconds: float) -> None:
        self.redirect_uri = redirect_uri
        self.timeout_seconds = timeout_seconds

    def __enter__(self) -> "FakeCallbackServer":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def wait_for_callback(self) -> str:
        return "http://localhost:1455/auth/callback?code=auth_code&state=oauth_state"


if __name__ == "__main__":
    unittest.main()
