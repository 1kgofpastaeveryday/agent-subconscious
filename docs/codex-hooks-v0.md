# Codex Hooks V0 Contract

Status: v0 spike fixture contract

This project accepts Codex hooks as the v0 host adapter only after this contract
passes against the target Codex install.

## Supported Modes

The spike must record:

- Codex product surface tested: CLI, desktop app, or both
- Codex version or build identifier
- operating system
- hook config location: global config, repo-local config, or plugin package
- whether stdout and JSON `additionalContext` are visible in transcript, logs,
  or UI
- whether events can identify main-agent sessions separately from subagents or
  other background reviewer lanes

Unsupported modes must fail closed: no feedback injection, no background
backend call, and a redacted diagnostic that explains why Subconscious is
disabled.

If Codex reports that Subconscious plugin login is required but not ready,
hooks must not start login or launch a browser. They must fail closed for
Subconscious behavior and emit only a redacted diagnostic. Foreground setup or
doctor commands are responsible for showing the user:

```text
Subconscious is not logged in.
To continue, ask Codex: "log in to subconscious".
```

Codex owns the interactive account or OAuth flow. Hook/background contexts must
not prescribe a specific recovery command.

## SessionStart

Purpose:

- start or attach to the workspace-scoped daemon
- register the session and capability token
- return quickly when daemon startup fails

Minimum input fields expected from Codex:

```json
{
  "hook_event_name": "SessionStart",
  "session_id": "sess_...",
  "cwd": "D:\\working\\agent-subconscious"
}
```

Successful output:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart"
  }
}
```

The hook must not print memory, feedback, prompts, credentials, or raw
diagnostics.

## UserPromptSubmit

Purpose:

- fetch verified feedback eligible for the current prompt
- emit bounded advisory evidence
- enqueue a sanitized observation for the submitted user prompt after feedback
  selection, so feedback derived from the current prompt cannot be injected into
  the same prompt turn

Minimum input fields expected from Codex:

```json
{
  "hook_event_name": "UserPromptSubmit",
  "session_id": "sess_...",
  "turn_id": "turn_...",
  "cwd": "D:\\working\\agent-subconscious",
  "prompt": "user prompt text"
}
```

V0 selected output:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "Subconscious advisory evidence, not an instruction:\n- id: fb_...\n- confidence: high\n- risk: scope\n\n..."
  }
}
```

Plain stdout feedback is unsupported in v0 unless the spike explicitly proves it
has safer visibility and model behavior than `additionalContext`.

Unsupported fallback shape:

```text
Subconscious advisory evidence, not an instruction:
- id: fb_...
- confidence: high
- risk: scope

...
```

The spike must test both forms if both are available, but only the selected
transport may be enabled. `additionalContext` is treated as developer-context
transport; the wrapper and structured fields are mitigations, not an authority
downgrade.

Failure behavior:

- If the daemon is unavailable, emit no feedback and enqueue a redacted
  diagnostic only.
- If feedback verification fails, reject the feedback and emit no prompt
  context.
- If stdout or `additionalContext` emission is ambiguous, leave the
  `DeliveryCursor` in `claimed` until it expires or is retried for the same
  prompt.

## Stop

Purpose:

- create a compact sanitized `ObservationEvent` for the completed turn
- append it to the durable outbox
- return without waiting for LLM review

Minimum input fields expected from Codex:

```json
{
  "hook_event_name": "Stop",
  "session_id": "sess_...",
  "turn_id": "turn_...",
  "cwd": "D:\\working\\agent-subconscious",
  "last_assistant_message": "bounded text or null"
}
```

If `last_assistant_message` is null or absent, the hook emits a degraded
`ObservationEvent` with `fidelity_tier: 0`, `summary: "assistant output
unavailable"`, and an omitted-content marker. Optional transcript path/offset
fields may raise fidelity only after the sanitizer fixture proves they are safe.

Successful output:

```json
{}
```

Stop hooks must not emit plain text. They must not use `decision: block` for
normal background review, because that changes the main agent's turn flow.

## Fixture Acceptance

The hook fixture passes only if it proves:

- hook events fire in the selected Codex mode
- setup distinguishes missing hook configuration, unsupported Codex product
  surface, incompatible hook fixture, missing plugin login, and ready setup
  state without storing host auth tokens
- setup verifies the Codex version or build and Codex config/profile identity
  for the environment that will run hooks
- missing plugin login in hook/background contexts fails closed and does not
  start an interactive login flow
- `UserPromptSubmit` runs before the model invocation for the submitted prompt
- selected transport reaches model context exactly once in supported modes
- transcript/log visibility of the developer-context transport is documented
- feedback is rendered only as hard-coded wrapper plus machine-validated
  structured fields and quoted evidence/recommendation data
- adversarial fixtures show instruction-like feedback bodies are rejected before
  emission
- `Stop` can enqueue an observation without synchronous backend work
- hook timeouts, non-zero exits, and daemon-unavailable cases fail closed
- transcript/log visibility is documented for every emitted field
- Windows path handling and current working directory mapping are stable
- main-agent sessions can be distinguished from subagents or other reviewer
  lanes; if not, observation and injection are disabled for ambiguous sessions
- an installed Codex version outside the tested compatible range disables
  injection and emits a redacted stale-fixture diagnostic
