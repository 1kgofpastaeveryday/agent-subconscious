"""User-facing CLI for local Agent Subconscious setup and operation."""

from __future__ import annotations

import argparse
import http.server
import queue
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

from . import daemon
from . import diagnostics
from . import hook_probe
from . import llm_engine
from . import rollout_watcher


def print_json(value: dict[str, Any]) -> None:
    print(hook_probe.dumps_json(value))


def state_dir_from_arg(value: Path | None) -> Path:
    return value or hook_probe.default_state_dir()


def login(args: argparse.Namespace) -> int:
    state_dir = state_dir_from_arg(args.state_dir)
    if args.complete:
        try:
            print_json(llm_engine.complete_chatgpt_plan_login(state_dir, args.complete))
            return 0
        except llm_engine.LlmEngineError as exc:
            print_json(
                {
                    "status": "login_failed",
                    "provider": llm_engine.CHATGPT_PLAN_PROVIDER,
                    "auth_route": llm_engine.CHATGPT_PLAN_AUTH_ROUTE,
                    "redacted_message": str(exc),
                }
            )
            return 1
    record = llm_engine.write_chatgpt_plan_backend_config(state_dir)
    status = llm_engine.begin_chatgpt_plan_login(state_dir)
    if args.no_browser:
        print_json(
            {
                "status": "login_pending",
                "provider": record["provider"],
                "auth_route": record["auth_route"],
                "opened_browser": False,
                "authorize_url": status["authorize_url"],
                "next_step": "Open the authorization URL, then run `subconscious login --complete <redirect-url>` if automatic callback capture is unavailable.",
            }
        )
        return 0
    try:
        callback_server = OAuthCallbackServer(str(status["redirect_uri"]), timeout_seconds=args.timeout)
    except OSError as exc:
        print_json(
            {
                "status": "login_pending",
                "provider": record["provider"],
                "auth_route": record["auth_route"],
                "opened_browser": False,
                "authorize_url": status["authorize_url"],
                "redacted_message": f"OAuth callback server could not start: {type(exc).__name__}",
                "next_step": "Open the authorization URL, then run `subconscious login --complete <redirect-url>`.",
            }
        )
        return 1
    opened = False
    with callback_server:
        try:
            opened = webbrowser.open(str(status["authorize_url"]), new=2)
        except webbrowser.Error:
            opened = False
        try:
            callback_url = callback_server.wait_for_callback()
            complete_status = llm_engine.complete_chatgpt_plan_login(state_dir, callback_url)
        except TimeoutError:
            print_json(
                {
                    "status": "login_pending",
                    "provider": record["provider"],
                    "auth_route": record["auth_route"],
                    "opened_browser": opened,
                    "authorize_url": status["authorize_url"],
                    "redacted_message": "Timed out waiting for the OAuth callback.",
                    "next_step": "If the browser reached the localhost callback URL, run `subconscious login --complete <redirect-url>`.",
                }
            )
            return 1
        except llm_engine.LlmEngineError as exc:
            print_json(
                {
                    "status": "login_failed",
                    "provider": record["provider"],
                    "auth_route": record["auth_route"],
                    "opened_browser": opened,
                    "redacted_message": str(exc),
                }
            )
            return 1
    complete_status = dict(complete_status)
    complete_status["opened_browser"] = opened
    print_json(complete_status)
    return 0


def status(args: argparse.Namespace) -> int:
    state_dir = state_dir_from_arg(args.state_dir)
    backend = llm_engine.read_backend_workspace_config(state_dir)
    login_status = llm_engine.read_login_status(state_dir)
    if login_status is not None:
        if login_status.get("state") == "ready":
            profile = llm_engine.read_auth_profiles(state_dir).get(llm_engine.CHATGPT_PLAN_PROFILE_ID)
            if isinstance(profile, dict):
                login_status = llm_engine.ready_login_status(profile)
        login_status = llm_engine.public_login_status(login_status)
    print_json(
        {
            "status": "ok",
            "backend_enabled": llm_engine.chatgpt_plan_backend_enabled(state_dir),
            "backend": backend,
            "global_config": hook_probe.read_global_config(),
            "login": login_status
            or {
                "schema_version": 1,
                "provider": llm_engine.CHATGPT_PLAN_PROVIDER,
                "auth_route": llm_engine.CHATGPT_PLAN_AUTH_ROUTE,
                "state": "not_started",
            },
        }
    )
    return 0


def sub_notes(args: argparse.Namespace) -> int:
    record = hook_probe.write_global_config({"sub_notes_mode": args.mode})
    print_json({"status": "configured", "sub_notes_mode": record["sub_notes_mode"]})
    return 0


def enable(args: argparse.Namespace) -> int:
    state_dir = state_dir_from_arg(args.state_dir)
    record = llm_engine.write_chatgpt_plan_backend_config(state_dir)
    print_json({"status": "configured", "provider": record["provider"], "auth_route": record["auth_route"]})
    return 0


def run(args: argparse.Namespace) -> int:
    state_dir = state_dir_from_arg(args.state_dir)
    argv = [
        "--state-dir",
        str(state_dir),
        "--engine",
        "subconscious_chatgpt_plan",
        "--interval",
        str(args.interval),
        "--idle-timeout",
        str(args.idle_timeout),
    ]
    if args.once:
        argv.append("--once")
    if args.quiet:
        argv.append("--quiet")
    if args.enable_rollout_watcher:
        argv.extend(["--enable-rollout-watcher", "--cwd", str(args.cwd)])
    return daemon.main(argv)


def watch_rollout(args: argparse.Namespace) -> int:
    state_dir = state_dir_from_arg(args.state_dir)
    argv = ["--state-dir", str(state_dir), "--cwd", str(args.cwd)]
    if args.rollout:
        argv.extend(["--rollout", str(args.rollout)])
    if args.once:
        argv.append("--once")
    return rollout_watcher.main(argv)


def diagnose(args: argparse.Namespace) -> int:
    print_json(diagnostics.current_status(state_dir_from_arg(args.state_dir), cwd=args.cwd))
    return 0


class OAuthCallbackServer:
    def __init__(self, redirect_uri: str, *, timeout_seconds: float) -> None:
        parsed = urllib.parse.urlparse(redirect_uri)
        if parsed.scheme != "http" or not parsed.hostname or parsed.port is None:
            raise OSError("unsupported redirect URI")
        self.redirect_uri = redirect_uri
        self.host = parsed.hostname
        self.port = parsed.port
        self.path = parsed.path or "/"
        self.timeout_seconds = max(float(timeout_seconds), 1.0)
        self.callbacks: queue.Queue[str] = queue.Queue(maxsize=1)
        self.server = self._make_server()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "OAuthCallbackServer":
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def wait_for_callback(self) -> str:
        try:
            return self.callbacks.get(timeout=self.timeout_seconds)
        except queue.Empty as exc:
            raise TimeoutError("timed out waiting for OAuth callback") from exc

    def _make_server(self) -> http.server.ThreadingHTTPServer:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != outer.path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not found")
                    return
                full_url = urllib.parse.urlunparse(
                    ("http", self.headers.get("Host", f"{outer.host}:{outer.port}"), parsed.path, "", parsed.query, "")
                )
                if outer.callbacks.empty():
                    outer.callbacks.put_nowait(full_url)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    title = "Authentication successful"
                    body = "Subconscious login is complete. You can close this tab and return to Codex."
                else:
                    title = "Authentication callback received"
                    body = "Subconscious received the callback, but it did not include an authorization code."
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                html = f"<!doctype html><title>{title}</title><h1>{title}</h1><p>{body}</p>"
                self.wfile.write(html.encode("utf-8"))

            def log_message(self, format: str, *args: object) -> None:
                return

        return http.server.ThreadingHTTPServer((self.host, self.port), Handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="subconscious", description="Agent Subconscious local control CLI")
    parser.add_argument("--state-dir", type=Path, default=None)
    subcommands = parser.add_subparsers(dest="command", required=True)

    login_parser = subcommands.add_parser("login", help="Start or complete Subconscious ChatGPT Plan login")
    login_parser.add_argument("--complete", default=None, help="Redirect URL or authorization code from the browser")
    login_parser.add_argument("--no-browser", action="store_true", help="Print the authorization URL without opening a browser")
    login_parser.add_argument("--timeout", type=float, default=300.0, help="Seconds to wait for the OAuth callback")
    login_parser.set_defaults(func=login)

    status_parser = subcommands.add_parser("status", help="Show redacted backend/login status")
    status_parser.set_defaults(func=status)

    sub_notes_parser = subcommands.add_parser("sub-notes", help="Set global Sub notes display mode")
    sub_notes_parser.add_argument("mode", choices=sorted(hook_probe.SUB_NOTES_MODES), help="Global Sub notes mode")
    sub_notes_parser.set_defaults(func=sub_notes)

    enable_parser = subcommands.add_parser("enable", help="Enable the ChatGPT Plan backend for this workspace")
    enable_parser.set_defaults(func=enable)

    run_parser = subcommands.add_parser("run", help="Run the Subconscious reviewer daemon")
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument("--quiet", action="store_true")
    run_parser.add_argument("--enable-rollout-watcher", action="store_true")
    run_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    run_parser.add_argument("--interval", type=float, default=daemon.DEFAULT_INTERVAL_SECONDS)
    run_parser.add_argument("--idle-timeout", type=float, default=daemon.DEFAULT_IDLE_TIMEOUT_SECONDS)
    run_parser.set_defaults(func=run)

    watch_parser = subcommands.add_parser("watch-rollout", help="Observe Codex rollout JSONL as fallback input")
    watch_parser.add_argument("--once", action="store_true")
    watch_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    watch_parser.add_argument("--rollout", type=Path, default=None)
    watch_parser.set_defaults(func=watch_rollout)

    diagnose_parser = subcommands.add_parser("diagnose", help="Show live dogfood status")
    diagnose_parser.add_argument("--cwd", type=Path, default=Path.cwd())
    diagnose_parser.set_defaults(func=diagnose)
    return parser


def normalize_global_args(argv: list[str]) -> list[str]:
    normalized = list(argv)
    state_dir_args: list[str] = []
    index = 0
    while index < len(normalized):
        arg = normalized[index]
        if arg == "--state-dir" and index + 1 < len(normalized):
            state_dir_args = [arg, normalized[index + 1]]
            del normalized[index : index + 2]
            continue
        if arg.startswith("--state-dir="):
            state_dir_args = ["--state-dir", arg.split("=", 1)[1]]
            del normalized[index]
            continue
        index += 1
    return [*state_dir_args, *normalized]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_global_args(sys.argv[1:] if argv is None else argv))
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
