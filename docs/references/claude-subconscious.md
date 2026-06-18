# Claude Subconscious Reference

Status: local implementation reference, refreshed from
`letta-ai/claude-subconscious`.

This note summarizes the observed Claude Subconscious plugin shape on the local
machine. It is a reference for the Codex design, not a dependency.

Codex should copy the timing pattern and state minimalism, but not the XML
wrapper, acknowledgement instruction, or direct memory-change injection.

## Hook Shape

The Claude plugin uses these hooks:

- `SessionStart`: ensure the local server is running, initialize the agent, and
  sync memory.
- `UserPromptSubmit`: fetch new Subconscious messages and memory changes, emit
  them to stdout, and send the user's prompt to Subconscious early.
- `Stop`: read the session transcript and send new messages to Subconscious in
  a detached background worker.
- `PreToolUse`: optional synchronization/checkpoint hooks.

The key behavior is `UserPromptSubmit` injection. Subconscious feedback is not
primarily written to a doc for Claude to discover. It is dynamically attached to
the next prompt.

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
events after `lastProcessedIndex`, and sends them to Letta through a detached
worker so the Claude session does not block. The index advances only after the
worker succeeds.

At `UserPromptSubmit`, the plugin also sends an early prompt notification so
Subconscious can start thinking while Claude processes the user's prompt.

For Codex v0, the copied timing pattern excludes that early prompt
notification. Codex v0 does not enqueue the raw current prompt from
`UserPromptSubmit`.

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
- Treat the host plugin as a small sync adapter, not as a full local delivery
  protocol.
- Track simple cursors first: last processed transcript item and last delivered
  Subconscious message.
- Auto-start is acceptable if startup is idempotent and non-blocking.
- Prompt-time fetch and stop-time background send are the core loop.
- Memory can be owned by Subconscious.
- Process lifecycle needs stronger orphan handling than a pid file and port
  health check alone.

Do not copy these parts into Codex runtime hooks:

- XML or Markdown control wrappers as trusted structure.
- acknowledgement instructions that tell the main agent what to say.
- direct memory-change injection into prompt context.
- dependency installation from hooks.
- stdout transport unless a Codex fixture proves it is safer than
  `additionalContext`.
- port-only health or daemon trust.
