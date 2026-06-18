from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import traceback
from io import StringIO
from pathlib import Path

DAEMON_IDLE_TIMEOUT_ENV = "SUBCONSCIOUS_DAEMON_IDLE_TIMEOUT_SECONDS"
DAEMON_INTERVAL_ENV = "SUBCONSCIOUS_DAEMON_INTERVAL_SECONDS"


def add_import_roots() -> None:
    plugin_root = Path(__file__).resolve().parents[1]
    bundled_package_root = plugin_root / "agent_subconscious"
    if bundled_package_root.is_dir():
        sys.path.insert(0, str(plugin_root))
        return
    development_root = Path(__file__).resolve().parents[3]
    if (development_root / "agent_subconscious").is_dir():
        sys.path.insert(0, str(development_root))


def extract_state_dir_args(args: list[str]) -> list[str]:
    for index, arg in enumerate(args):
        if arg == "--state-dir" and index + 1 < len(args):
            return ["--state-dir", args[index + 1]]
        if arg.startswith("--state-dir="):
            return ["--state-dir", arg.split("=", 1)[1]]
    return []


def state_dir_args(args: list[str], cwd_hint: str | None = None) -> list[str]:
    explicit = extract_state_dir_args(args)
    if explicit:
        return explicit
    if not cwd_hint:
        return []
    from agent_subconscious import hook_probe

    return ["--state-dir", str(hook_probe.default_state_dir(cwd_hint))]


def state_dir_from_args(args: list[str], cwd_hint: str | None = None) -> Path:
    from agent_subconscious import hook_probe

    explicit = extract_state_dir_args(args)
    raw = Path(explicit[1]) if explicit else hook_probe.default_state_dir(cwd_hint)
    return hook_probe.resolve_state_dir(raw, cwd_hint)


def float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def daemon_process_kwargs() -> dict:
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
        kwargs["creationflags"] = flags
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True
    return kwargs


def spawn_daemon_once(args: list[str], cwd_hint: str | None = None) -> None:
    command = [sys.executable, str(Path(__file__).resolve()), "daemon-once", "--quiet", *state_dir_args(args, cwd_hint)]
    subprocess.Popen(command, **daemon_process_kwargs())


def capability_records_exist(state_dir: Path, payload: dict | None) -> bool:
    from agent_subconscious import hook_probe

    if not isinstance(payload, dict):
        return False
    path = hook_probe.safe_state_file(state_dir, hook_probe.CAPABILITIES_FILE)
    if not path.exists():
        return False
    try:
        return any(hook_probe.verify_capability(record, payload) for record in hook_probe.read_jsonl(path, strict=True))
    except (OSError, hook_probe.HookProbeError):
        return False


def ensure_workspace_daemon(args: list[str], cwd_hint: str | None = None, payload: dict | None = None) -> None:
    from agent_subconscious import daemon, hook_probe

    state_dir = state_dir_from_args(args, cwd_hint)
    hook_probe.prepare_state_dir(state_dir)
    if not capability_records_exist(state_dir, payload):
        return
    interval = max(float_env(DAEMON_INTERVAL_ENV, daemon.DEFAULT_INTERVAL_SECONDS), 0.25)
    idle_timeout = float_env(DAEMON_IDLE_TIMEOUT_ENV, daemon.DEFAULT_IDLE_TIMEOUT_SECONDS)
    engine_args = desired_engine_args(state_dir)
    watcher_args = rollout_watcher_args(args, cwd_hint)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "daemon",
        "--quiet",
        *watcher_args,
        "--interval",
        str(interval),
        "--idle-timeout",
        str(idle_timeout),
        *engine_args,
        *state_dir_args(args, cwd_hint),
    ]
    with hook_probe.locked_state(state_dir):
        status = daemon.read_daemon_status(state_dir)
        needs_watcher = bool(watcher_args)
        has_required_watcher = not needs_watcher or (isinstance(status, dict) and status.get("rollout_watcher_enabled") is True)
        if daemon.daemon_status_is_live(status, state_dir) and has_required_watcher:
            return
        stop_stale_daemon(status)
        proc = subprocess.Popen(command, **daemon_process_kwargs())
        daemon.write_daemon_status(
            state_dir,
            daemon.daemon_status_record(
                state_dir,
                state="starting",
                pid=proc.pid,
                started_at=None,
            ),
        )


def stop_stale_daemon(status: dict | None) -> None:
    if not isinstance(status, dict):
        return
    pid = status.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            os.kill(pid, 15)
    except OSError:
        return


def desired_engine_args(state_dir: Path) -> list[str]:
    try:
        from agent_subconscious import llm_engine

        if llm_engine.chatgpt_plan_backend_enabled(state_dir) and llm_engine.has_fresh_chatgpt_plan_access_token(state_dir):
            return ["--engine", llm_engine.CHATGPT_PLAN_PROVIDER]
    except Exception:
        return []
    return []


def rollout_watcher_args(args: list[str], cwd_hint: str | None) -> list[str]:
    if "--debug-allow-fixture-key" in args or os.environ.get("SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY") == "1":
        return []
    return ["--enable-rollout-watcher", "--cwd", str(Path(cwd_hint or os.getcwd()))]


def payload_from_stdin(raw_stdin: str) -> dict | None:
    from agent_subconscious import hook_probe

    try:
        payload = hook_probe.loads_json(raw_stdin)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def cwd_hint_from_payload(payload: dict | None) -> str | None:
    if not isinstance(payload, dict) or not payload.get("cwd"):
        return None
    return str(payload.get("cwd"))


def run_adapter_with_stdin(args: list[str], raw_stdin: str) -> int:
    from agent_subconscious.codex_hook_adapter import main as adapter_main

    original_stdin = sys.stdin
    try:
        sys.stdin = StringIO(raw_stdin)
        return adapter_main(args)
    finally:
        sys.stdin = original_stdin


def main() -> int:
    add_import_roots()
    if len(sys.argv) > 1 and sys.argv[1] in {"login", "status", "enable", "run", "sub-notes", "watch-rollout", "diagnose"}:
        from agent_subconscious.cli import main as cli_main

        return cli_main(sys.argv[1:])
    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        from agent_subconscious.daemon import main as daemon_main

        return daemon_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "daemon-once":
        from agent_subconscious.daemon import main as daemon_main

        return daemon_main(["--once", *sys.argv[2:]])
    if len(sys.argv) > 1 and sys.argv[1] in {"with-daemon", "stop-with-daemon"}:
        adapter_args = sys.argv[2:]
        raw_stdin = sys.stdin.read()
        payload = payload_from_stdin(raw_stdin)
        cwd_hint = cwd_hint_from_payload(payload)
        adapter_status = run_adapter_with_stdin(adapter_args, raw_stdin)
        if adapter_status != 0:
            return adapter_status
        try:
            ensure_workspace_daemon(adapter_args, cwd_hint, payload)
        except Exception as exc:
            try:
                from agent_subconscious import hook_probe

                hook_probe.emit_diagnostic(f"daemon-start-error-{type(exc).__name__}")
            except Exception:
                pass
            return 0
        return 0
    from agent_subconscious.codex_hook_adapter import main as adapter_main

    return adapter_main()


def fail_closed_main() -> int:
    try:
        return main()
    except BaseException as exc:
        if len(sys.argv) > 1 and sys.argv[1] in {"with-daemon", "stop-with-daemon", "daemon", "daemon-once"}:
            write_emergency_diagnostic(exc)
            try:
                print("{}")
            except OSError:
                pass
            return 0
        raise


def write_emergency_diagnostic(exc: BaseException) -> None:
    try:
        path = Path(tempfile.gettempdir()) / "agent-subconscious-hook-emergency.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"argv={sys.argv!r}\n")
            handle.write(f"error={type(exc).__name__}: {exc}\n")
            handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            handle.write("\n")
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(fail_closed_main())
