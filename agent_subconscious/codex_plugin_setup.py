"""Install and inspect the local Codex plugin hook spike.

This module mirrors the Codex hook trust identity enough for the local
agent-subconscious plugin. It intentionally manages only this plugin's cache
and config entries under CODEX_HOME.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from . import hook_probe
    from . import llm_engine
except ImportError:  # pragma: no cover - direct script execution fallback.
    from agent_subconscious import hook_probe
    from agent_subconscious import llm_engine

PLUGIN_NAME = "agent-subconscious"
MARKETPLACE_NAME = "agent-subconscious-local"
PLUGIN_KEY = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
DEFAULT_PLUGIN_VERSION = "local"
POSIX_HOOKS_FILE = "hooks.json"
WINDOWS_HOOKS_FILE = "hooks.windows.json"
HOOK_SOURCE_RELATIVE_PATH = "hooks/hooks.json"
CODEX_EXECUTABLE_ENV = "SUBCONSCIOUS_CODEX_EXECUTABLE"

USER_MESSAGES = {
    "codex_missing": "Codex CLI was not found in this environment.",
    "plugin_missing": "Subconscious is not enabled in Codex. Enable the plugin before using hooks.",
    "hooks_unavailable": "This Codex surface does not support the required Subconscious hooks.",
    "wrong_environment": "Codex setup was checked in a different environment than the one that will run hooks.",
    "login_required": 'Subconscious is not logged in. To continue, ask Codex: "log in to subconscious". Codex will start Subconscious OAuth and open the ChatGPT login URL.',
    "fixture_only": "Subconscious hook fixtures are ready, but this Codex surface has no proven host attestation/login signal yet.",
    "unknown": "Subconscious Codex setup status could not be determined from redacted diagnostics.",
}

LOGIN_REQUIRED_NEXT_ACTION = {
    "type": "ask_codex",
    "prompt": "log in to subconscious",
    "expected_codex_action": "start_subconscious_oauth_and_open_chatgpt_login_url",
}

EVENT_KEY_LABELS = {
    "PreToolUse": "pre_tool_use",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "Stop": "stop",
}


@dataclass(frozen=True)
class HookTrustEntry:
    key: str
    event_name: str
    trusted_hash: str


def dumps_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected object JSON at {path}")
    return value


def hook_command_for_platform(
    platform: str,
    python_executable: Path | None = None,
    *,
    fixture_mode: bool = False,
    plugin_root: Path | None = None,
) -> str:
    executable = str((python_executable or Path(sys.executable)).expanduser().resolve())
    root = str((plugin_root or default_plugin_root()).expanduser().resolve())
    fixture_args = " --debug-assume-main-agent --debug-allow-fixture-key" if fixture_mode else ""
    if platform == "windows":
        return f'{executable} "{root}\\bin\\subconscious_hook.py" with-daemon{fixture_args}'
    if platform == "posix":
        return f'"{executable}" "{root}/bin/subconscious_hook.py" with-daemon{fixture_args}'
    raise ValueError("platform must be 'posix' or 'windows'")


def render_hooks_for_platform(
    hooks: dict[str, Any],
    *,
    platform: str,
    python_executable: Path | None = None,
    fixture_mode: bool = False,
    plugin_root: Path | None = None,
) -> dict[str, Any]:
    rendered = json.loads(json.dumps(hooks))
    command = hook_command_for_platform(platform, python_executable, fixture_mode=fixture_mode, plugin_root=plugin_root)
    groups_by_event = rendered.get("hooks")
    if not isinstance(groups_by_event, dict):
        raise ValueError("missing hooks object")
    for groups in groups_by_event.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                continue
            for handler in handlers:
                if isinstance(handler, dict) and handler.get("type") == "command":
                    handler["command"] = command
    return rendered


def repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[1]


def default_plugin_root() -> Path:
    return repo_root_from_module() / "plugins" / PLUGIN_NAME


def default_codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def canonical_sha256(value: dict[str, Any]) -> str:
    digest = hashlib.sha256(dumps_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def event_key_label(event_name: str) -> str:
    try:
        return EVENT_KEY_LABELS[event_name]
    except KeyError as exc:
        raise ValueError(f"unsupported hook event: {event_name}") from exc


def normalized_command_handler(handler: dict[str, Any]) -> dict[str, Any]:
    if handler.get("type") != "command":
        raise ValueError("only command hooks are supported")
    command = handler.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("hook command is required")
    timeout = handler.get("timeout", 600)
    if not isinstance(timeout, int):
        raise ValueError("hook timeout must be an integer")
    normalized: dict[str, Any] = {
        "type": "command",
        "command": command,
        "timeout": max(timeout, 1),
        "async": bool(handler.get("async", False)),
    }
    status_message = handler.get("statusMessage")
    if status_message is not None:
        if not isinstance(status_message, str):
            raise ValueError("hook statusMessage must be a string")
        normalized["statusMessage"] = status_message
    return normalized


def command_hook_hash(event_name: str, group: dict[str, Any], handler: dict[str, Any]) -> str:
    normalized_group: dict[str, Any] = {}
    matcher = group.get("matcher")
    if matcher is not None:
        if not isinstance(matcher, str):
            raise ValueError("hook matcher must be a string")
        normalized_group["matcher"] = matcher
    normalized_group["hooks"] = [normalized_command_handler(handler)]
    identity = {"event_name": event_key_label(event_name), **normalized_group}
    return canonical_sha256(identity)


def hook_file_for_platform(plugin_root: Path, platform: str) -> Path:
    hooks_dir = plugin_root / "hooks"
    if platform == "windows":
        return hooks_dir / WINDOWS_HOOKS_FILE
    if platform == "posix":
        return hooks_dir / POSIX_HOOKS_FILE
    raise ValueError("platform must be 'posix' or 'windows'")


def plugin_version(plugin_root: Path) -> str:
    manifest = load_json(plugin_root / ".codex-plugin" / "plugin.json")
    version = manifest.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return DEFAULT_PLUGIN_VERSION


def hook_trust_entries(plugin_root: Path, *, platform: str = "posix", fixture_mode: bool = False) -> list[HookTrustEntry]:
    hooks_file = hook_file_for_platform(plugin_root, platform)
    hooks_doc = render_hooks_for_platform(load_json(hooks_file), platform=platform, fixture_mode=fixture_mode, plugin_root=plugin_root)
    hooks = hooks_doc.get("hooks")
    if not isinstance(hooks, dict):
        raise ValueError(f"missing hooks object in {hooks_file}")
    entries: list[HookTrustEntry] = []
    for event_name, groups in hooks.items():
        if event_name not in EVENT_KEY_LABELS:
            raise ValueError(f"unsupported hook event: {event_name}")
        if not isinstance(groups, list):
            raise ValueError(f"hook groups must be a list for {event_name}")
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise ValueError(f"hook group must be an object for {event_name}")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                raise ValueError(f"hook handlers must be a list for {event_name}")
            for handler_index, handler in enumerate(handlers):
                if not isinstance(handler, dict):
                    raise ValueError(f"hook handler must be an object for {event_name}")
                event_label = event_key_label(event_name)
                key = f"{PLUGIN_KEY}:{HOOK_SOURCE_RELATIVE_PATH}:{event_label}:{group_index}:{handler_index}"
                entries.append(HookTrustEntry(key, event_name, command_hook_hash(event_name, group, handler)))
    return entries


def plugin_cache_root(codex_home: Path, version: str) -> Path:
    return codex_home / "plugins" / "cache" / MARKETPLACE_NAME / PLUGIN_NAME / version


def ensure_relative_to(path: Path, base: Path) -> None:
    resolved = path.resolve()
    resolved_base = base.resolve()
    try:
        resolved.relative_to(resolved_base)
    except ValueError as exc:
        raise ValueError(f"{resolved} is outside {resolved_base}") from exc


def copy_plugin_to_cache(plugin_root: Path, codex_home: Path, *, platform: str = "posix", fixture_mode: bool = False) -> Path:
    version = plugin_version(plugin_root)
    target = plugin_cache_root(codex_home, version)
    cache_base = codex_home / "plugins" / "cache" / MARKETPLACE_NAME / PLUGIN_NAME
    ensure_relative_to(target, cache_base)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.parent / f".{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    backup_target = target.parent / f".{target.name}.previous"
    for stale in (temp_target, backup_target):
        if stale.exists():
            shutil.rmtree(stale)
    shutil.copytree(plugin_root, temp_target)
    repo_package = plugin_root.parents[1] / "agent_subconscious"
    if repo_package.is_dir() and not (temp_target / "agent_subconscious").exists():
        shutil.copytree(repo_package, temp_target / "agent_subconscious", ignore=shutil.ignore_patterns("__pycache__"))
    if platform == "windows":
        shutil.copy2(temp_target / "hooks" / WINDOWS_HOOKS_FILE, temp_target / "hooks" / POSIX_HOOKS_FILE)
    rendered_hooks = render_hooks_for_platform(
        load_json(temp_target / "hooks" / POSIX_HOOKS_FILE),
        platform=platform,
        fixture_mode=fixture_mode,
        plugin_root=plugin_root,
    )
    (temp_target / "hooks" / POSIX_HOOKS_FILE).write_text(json.dumps(rendered_hooks, indent=2) + "\n", encoding="utf-8", newline="\n")
    if target.exists():
        target.rename(backup_target)
    temp_target.rename(target)
    if backup_target.exists():
        shutil.rmtree(backup_target)
    return target


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def minutes_from_now(minutes: int) -> str:
    import datetime as dt

    return (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(parts).encode('utf-8')).hexdigest()[:24]}"


def path_id(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        material = str(path.expanduser().resolve())
    except OSError:
        material = str(path)
    if os.name == "nt":
        material = material.lower()
    return "path_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def codex_executable() -> Path | None:
    configured = os.environ.get(CODEX_EXECUTABLE_ENV)
    if configured:
        path = Path(configured).expanduser()
        return path if path.exists() else None
    found = shutil.which("codex")
    return Path(found) if found else None


def codex_version(codex_path: Path | None) -> str | None:
    if codex_path is None:
        return None
    try:
        proc = subprocess.run(
            [str(codex_path), "--version"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = (proc.stdout or proc.stderr).strip().splitlines()
    return version[0][:120] if version else None


def auth_is_ready(state_dir: Path | None = None) -> bool:
    try:
        resolved_state_dir = hook_probe.resolve_state_dir(state_dir or hook_probe.default_state_dir())
        return (
            llm_engine.chatgpt_plan_backend_enabled(resolved_state_dir)
            and llm_engine.has_fresh_chatgpt_plan_access_token(resolved_state_dir)
        )
    except (OSError, ValueError, hook_probe.HookProbeError):
        return False


def cache_matches_source(
    plugin_root: Path,
    installed_path: Path,
    *,
    platform: str = "posix",
    fixture_mode: bool = False,
) -> bool:
    if not installed_path.exists():
        return False
    pairs = [
        (plugin_root / ".codex-plugin" / "plugin.json", installed_path / ".codex-plugin" / "plugin.json"),
        (plugin_root / "bin" / "subconscious_hook.py", installed_path / "bin" / "subconscious_hook.py"),
    ]
    expected_hooks = render_hooks_for_platform(
        load_json(hook_file_for_platform(plugin_root, platform)),
        platform=platform,
        fixture_mode=fixture_mode,
        plugin_root=plugin_root,
    )
    installed_hooks_path = installed_path / "hooks" / POSIX_HOOKS_FILE
    if not installed_hooks_path.exists() or load_json(installed_hooks_path) != expected_hooks:
        return False
    repo_package = plugin_root.parents[1] / "agent_subconscious"
    installed_package = installed_path / "agent_subconscious"
    if repo_package.is_dir():
        for source in sorted(repo_package.glob("*.py")):
            pairs.append((source, installed_package / source.name))
    for source, cached in pairs:
        if not source.exists() or not cached.exists():
            return False
        if file_sha256(source) != file_sha256(cached):
            return False
    return True


def table_header(table: str) -> str:
    return f"[{table}]"


def set_toml_value(text: str, table: str, key: str, rendered_value: str) -> str:
    lines = text.splitlines()
    header = table_header(table)
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([header, f"{key} = {rendered_value}"])
        return "\n".join(lines) + "\n"
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start + 1, end):
        if key_pattern.match(lines[index]):
            lines[index] = f"{key} = {rendered_value}"
            return "\n".join(lines) + "\n"
    lines.insert(end, f"{key} = {rendered_value}")
    return "\n".join(lines) + "\n"


def read_config(config_path: Path) -> str:
    if not config_path.exists():
        return ""
    return config_path.read_text(encoding="utf-8")


def get_toml_value(text: str, table: str, key: str) -> str | None:
    lines = text.splitlines()
    header = table_header(table)
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == header:
            start = index
            break
    if start is None:
        return None
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            end = index
            break
    key_pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for index in range(start + 1, end):
        stripped = lines[index].strip()
        if key_pattern.match(stripped):
            return stripped.split("=", 1)[1].strip()
    return None


def write_config_settings(codex_home: Path, entries: list[HookTrustEntry]) -> None:
    codex_home.mkdir(parents=True, exist_ok=True)
    config_path = codex_home / "config.toml"
    text = read_config(config_path)
    for key in ("hooks", "plugins", "plugin_hooks"):
        text = set_toml_value(text, "features", key, "true")
    text = set_toml_value(text, f'plugins."{PLUGIN_KEY}"', "enabled", "true")
    for entry in entries:
        text = set_toml_value(text, f'hooks.state."{entry.key}"', "trusted_hash", json.dumps(entry.trusted_hash))
    atomic_write_text(config_path, text)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    with temp_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def install(plugin_root: Path, codex_home: Path, *, platform: str = "posix", fixture_mode: bool = False) -> dict[str, Any]:
    plugin_root = plugin_root.resolve()
    codex_home = codex_home.expanduser().resolve()
    entries = hook_trust_entries(plugin_root, platform=platform, fixture_mode=fixture_mode)
    installed_path = copy_plugin_to_cache(plugin_root, codex_home, platform=platform, fixture_mode=fixture_mode)
    write_config_settings(codex_home, entries)
    return {
        "status": "installed",
        "plugin_key": PLUGIN_KEY,
        "installed_path": str(installed_path),
        "trusted_hook_count": len(entries),
        "platform": platform,
        "fixture_mode": fixture_mode,
    }


def config_contains_required_state(text: str, entries: list[HookTrustEntry]) -> bool:
    if get_toml_value(text, f'plugins."{PLUGIN_KEY}"', "enabled") != "true":
        return False
    for feature in ("hooks", "plugins", "plugin_hooks"):
        if get_toml_value(text, "features", feature) != "true":
            return False
    for entry in entries:
        rendered_hash = json.dumps(entry.trusted_hash)
        if get_toml_value(text, f'hooks.state."{entry.key}"', "trusted_hash") != rendered_hash:
            return False
    return True


def doctor(plugin_root: Path, codex_home: Path, *, platform: str = "posix", fixture_mode: bool = False) -> dict[str, Any]:
    plugin_root = plugin_root.resolve()
    codex_home = codex_home.expanduser().resolve()
    version = plugin_version(plugin_root)
    entries = hook_trust_entries(plugin_root, platform=platform, fixture_mode=fixture_mode)
    installed_path = plugin_cache_root(codex_home, version)
    config_text = read_config(codex_home / "config.toml")
    created_at = utc_now()
    codex_path = codex_executable()
    codex_ver = codex_version(codex_path)
    installed = (installed_path / ".codex-plugin" / "plugin.json").exists()
    configured = config_contains_required_state(config_text, entries)
    cache_matches = cache_matches_source(plugin_root, installed_path, platform=platform, fixture_mode=fixture_mode)
    fixture_signing_ready = fixture_mode or bool(os.environ.get("SUBCONSCIOUS_HOOK_FIXTURE_SIGNING_KEY") or os.environ.get("SUBCONSCIOUS_HOOK_PROBE_ALLOW_FIXTURE_KEY") == "1")
    fixture_main_agent_assumption = fixture_mode or os.environ.get("SUBCONSCIOUS_CODEX_ADAPTER_ASSUME_MAIN_AGENT") == "1"
    fixture_ready = fixture_signing_ready and fixture_main_agent_assumption
    auth_ready = auth_is_ready()
    hooks_available = configured
    host_setup_ready = codex_path is not None and installed and configured and cache_matches
    host_surface_ready = host_setup_ready and auth_ready
    if codex_path is None:
        mode = "codex_missing"
        status = "unsupported"
    elif not installed or not cache_matches:
        mode = "plugin_missing"
        status = "needs_setup"
    elif not configured:
        mode = "hooks_unavailable"
        status = "unsupported"
    elif fixture_ready:
        mode = "unknown"
        status = "fixture_ready"
    elif not auth_ready:
        mode = "login_required"
        status = "login_required"
    else:
        mode = "ready"
        status = "ok"
    findings = []
    if mode == "codex_missing":
        findings.append({"code": "codex_missing", "severity": "unsupported", "message": USER_MESSAGES["codex_missing"], "next_action": None})
    elif not installed or not cache_matches:
        findings.append({"code": "plugin_missing", "severity": "action_required", "message": USER_MESSAGES["plugin_missing"], "next_action": None})
    elif not configured:
        findings.append({"code": "hooks_unavailable", "severity": "unsupported", "message": USER_MESSAGES["hooks_unavailable"], "next_action": None})
    elif fixture_ready:
        findings.append({"code": "fixture_only", "severity": "degraded", "message": USER_MESSAGES["fixture_only"], "next_action": None})
    elif not auth_ready:
        findings.append(
            {
                "code": "login_required",
                "severity": "action_required",
                "message": USER_MESSAGES["login_required"],
                "next_action": LOGIN_REQUIRED_NEXT_ACTION,
            }
        )
    exit_code = exit_code_for_status(status)
    status_message = None if status == "ok" else (USER_MESSAGES["fixture_only"] if fixture_ready else USER_MESSAGES.get(mode, USER_MESSAGES["unknown"]))
    report = {
        "schema_version": 1,
        "record_id": stable_id("doctor", str(codex_home), str(plugin_root), created_at),
        "created_at": created_at,
        "command": ["subconscious", "doctor", "host-setup"],
        "exit_code": exit_code,
        "status": status,
        "mode": mode,
        "provider": "codex_plugin",
        "plugin_key": PLUGIN_KEY,
        "installed": installed,
        "configured": configured,
        "cache_matches_source": cache_matches,
        "auth_ready": auth_ready,
        "fixture_ready": fixture_ready,
        "fixture_signing_ready": fixture_signing_ready,
        "fixture_main_agent_assumption": fixture_main_agent_assumption,
        "host_setup_ready": host_setup_ready,
        "host_surface_ready": host_surface_ready,
        "hook_surface_ready": hooks_available,
        "login_required": mode == "login_required",
        "codex_executable_id": path_id(codex_path),
        "codex_version": codex_ver,
        "codex_home_id": path_id(codex_home),
        "config_digest": "sha256:" + hashlib.sha256(config_text.encode("utf-8")).hexdigest(),
        "last_checked_at": created_at,
        "valid_until": minutes_from_now(5),
        "last_error_code": findings[0]["code"] if findings else None,
        "redacted_message": status_message,
        "auth_message": status_message,
        "next_action": LOGIN_REQUIRED_NEXT_ACTION if mode == "login_required" else None,
        "findings": findings,
        "installed_path": str(installed_path),
        "trusted_hook_count": len(entries),
        "platform": platform,
        "fixture_mode": fixture_mode,
    }
    return report


def exit_code_for_status(status: str) -> int:
    if status in {"ok", "installed"}:
        return 0
    if status == "fixture_ready":
        return 1
    if status in {"needs_setup", "login_required"}:
        return 2
    if status == "unsupported":
        return 3
    if status == "error":
        return 4
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install or inspect the Agent Subconscious Codex plugin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "install"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--codex-home", type=Path, default=default_codex_home())
        sub.add_argument("--plugin-root", type=Path, default=default_plugin_root())
        sub.add_argument("--platform", choices=("posix", "windows"), default="posix")
        sub.add_argument(
            "--fixture-mode",
            action="store_true",
            help="Install or inspect the explicit local fixture hook path; not MVP readiness.",
        )
        sub.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            result = install(args.plugin_root, args.codex_home, platform=args.platform, fixture_mode=args.fixture_mode)
        else:
            result = doctor(args.plugin_root, args.codex_home, platform=args.platform, fixture_mode=args.fixture_mode)
    except (OSError, ValueError) as exc:
        result = {"status": "error", "error": str(exc)}
        print(dumps_json(result) if args.json else f"error: {exc}", file=sys.stderr)
        return exit_code_for_status("error")
    if args.json:
        print(dumps_json(result))
    else:
        print(f"{result['status']}: {result['plugin_key']} ({result['trusted_hook_count']} hooks)")
        if result.get("auth_message"):
            print(result["auth_message"])
    return exit_code_for_status(str(result["status"]))


if __name__ == "__main__":
    raise SystemExit(main())
