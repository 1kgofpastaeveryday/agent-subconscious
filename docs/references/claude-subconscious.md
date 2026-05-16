# Claude Subconscious Reference

Status: local implementation reference

This note summarizes the observed Claude Subconscious plugin shape on the local
machine. It is a reference for the Codex design, not a dependency.

Do not port this behavior directly. Codex may copy the timing pattern, but not
the XML wrapper, acknowledgement instruction, or direct memory-change injection.

## Hook Shape

The Claude plugin uses these hooks:

- `SessionStart`: ensure the local server is running, initialize the agent, and
  sync memory.
- `UserPromptSubmit`: fetch new Subconscious messages and memory changes, emit
  them to stdout, and send the user's prompt to Subconscious early.
- `Stop`: read the session transcript and send new messages to Subconscious in
  a detached background worker.
- `PreToolUse`: optional synchronization/checkpoint hooks.

The key behavior is `UserPromptSubmit` stdout injection. Subconscious feedback
is not primarily written to a doc for Claude to discover. It is dynamically
attached to the next prompt.

## Feedback Delivery

New Subconscious messages are formatted as XML-like prompt attachments:

```xml
<letta_message from="Subconscious" timestamp="...">
...
</letta_message>
```

When messages exist, the hook also injects a short instruction telling Claude
to acknowledge the message briefly.

Codex v0 must not copy that acknowledgement instruction by default. In this
project, feedback remains advisory evidence and any host-level wrapper text must
be accepted through D-003 in `docs/open-decisions.md`.

## Background Update

At `Stop`, the plugin reads the transcript, formats new user/assistant/tool
events, and sends them to the local Letta-compatible server. The actual send is
performed by a detached worker so the Claude session does not block.

At `UserPromptSubmit`, the plugin also sends an early prompt notification so
Subconscious can start thinking while Claude processes the user's prompt.

## Memory

The Letta agent owns memory blocks such as:

- `core_directives`
- `guidance`
- `pending_items`
- `project_context`
- `self_improvement`
- `session_patterns`
- `tool_guidelines`
- `user_preferences`

The design expects Subconscious to update memory itself. Memory is not limited
to user-approved entries.

## Local Server

The local fork starts a Letta-compatible server on localhost. Startup uses:

- health check
- lock file
- pid file
- dependency install if needed
- detached server process

This solves auto-start but does not fully solve orphan cleanup by itself. The
Codex design should add identity-based stale-process cleanup and idle exit.

## Lessons For Codex

- Prefer next-prompt dynamic feedback injection over active-note files.
- Auto-start is acceptable if startup is idempotent and non-blocking.
- Prompt-time fetch and stop-time background send are the core loop.
- Memory can be owned by Subconscious.
- Process lifecycle needs stronger orphan handling than a pid file and port
  health check alone.
