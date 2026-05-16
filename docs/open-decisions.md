# Open Decisions

## D-001 Event Source

Status: accepted for v0 spike, blocked for MVP until proven in a host fixture.

Decision: use Codex hooks as the v0 host adapter.

Options:

- accepted: Codex hooks for session start, prompt submit, and stop/turn-complete
- fallback for fixtures only: local JSONL adapter behind the host adapter
- deferred: app-server event stream
- rejected for v0: manual export as the product event source

Required proof:

- `SessionStart` can start or attach to the daemon without blocking the main
  agent.
- `UserPromptSubmit` can add verified advisory feedback as dynamic context.
- `Stop` can enqueue a compact observation without requiring synchronous LLM
  work.
- Hook payload fields are mapped into `ObservationEvent` and `DeliveryCursor`
  records in `docs/contracts.md`.
- `docs/codex-hooks-v0.md` passes against the target Codex product surface,
  config location, operating systems, and selected transport behavior.

## D-002 Host Setup And Credential Model

Status: accepted for v0 plugin/hook spike, blocked for live backend calls until
configured.

Decision: v0 is a Codex Plugin/hooks integration. Separate Codex host
setup/auth state from backend LLM credentials.

If Codex reports that the Subconscious plugin integration is not logged in,
setup and hook diagnostics must show a clear user-facing message without
prescribing a shell command:

```text
Subconscious is not logged in.
To continue, ask Codex: "log in to subconscious".
```

Codex owns any interactive OAuth or account setup flow for the Subconscious
plugin. Subconscious must not decide that the recovery path is a specific CLI
command.

This host setup state does not authorize prompt injection by itself, does not
prove main-agent identity, does not make the product ready, and does not permit
implicit reuse of host-agent credentials for backend LLM calls. Live backend
calls still require explicit experiment API credentials or a later accepted
backend-auth RFC.

Required proof:

- foreground setup detects unsupported Codex product surfaces, missing hook
  configuration, incompatible hook fixtures, and missing Codex-led
  Subconscious login separately
- unauthenticated Codex plugin state produces a safe message such as
  `Subconscious is not logged in. To continue, ask Codex: "log in to subconscious".`
- hooks, daemon workers, and background reviewers never start interactive login
  flows; they fail closed with redacted diagnostics
- host setup readiness is only `host_setup_ready`; final local v0 readiness
  also requires the Codex hook fixture and main-agent/subagent distinction proof
- host auth tokens are stored only by Codex's own auth store, never in
  Subconscious memory, observations, logs, diagnostics, prompts, or state files
- every workspace or session operation still requires daemon-issued local
  capabilities
- logout/revocation is handled by Codex's plugin/account setup UX
- backend credentials are supplied by environment variable or user-owned local
  config, not copied from the host agent session
- backend credential values are never stored in Subconscious memory,
  observations, logs, diagnostics, or review prompts
- backend calls have timeout, retry, rate-limit, and disabled-mode behavior
- backend calls are disabled by default per workspace until explicit workspace
  opt-in records provider, outbound field inventory, and local-only fallback

## D-003 Prompt Feedback Injection

Status: accepted for v0 spike, blocked for MVP until verifier contract is
implemented.

Decision: use Codex `UserPromptSubmit` as the only v0 dynamic injection path.

Codex adds `UserPromptSubmit` `additionalContext` as extra developer context.
Because this transport is stronger than normal user text, every
Subconscious feedback item must be verified, bounded, and rendered as
non-authoritative advisory evidence.

Plain stdout feedback and Markdown `feedback.md` are not v0 injection paths
unless a future Codex-specific proof accepts one of them.

Required proof:

- feedback uses the `FeedbackItem` envelope in `docs/contracts.md`
- unsigned, expired, malformed, replayed, wrong-workspace, or over-limit items
  are rejected
- injected text includes a wrapper that says feedback is untrusted advisory
  evidence and does not override instructions
- feedback signatures use the signing-key contract in `docs/contracts.md`

## D-004 Auto-Start Lifecycle

Status: accepted for v0 spike, blocked for MVP until lifecycle tests pass.

Decision: auto-start one workspace-scoped daemon in v0. Use health checks,
startup lock, pid identity,
heartbeat, session registration, idle timeout, protocol versioning, and
identity-based stale-process cleanup. Never kill by port alone.

Required proof:

- duplicate start race starts at most one daemon
- stale lock and stale pid cleanup are identity-based
- dead heartbeat causes safe replacement
- active sessions prevent idle exit
- cleanup refuses to kill by port alone

## D-005 Memory Ownership

Status: accepted for v0 spike, blocked for MVP until storage contract is
implemented.

Decision: Subconscious owns its own memory, but durable state is written only
through the daemon's single-writer storage API.

The main agent treats memory-derived feedback as advisory evidence. Memory text
is never injected directly; it must be transformed into a verified
`FeedbackItem`.

## D-006 Tool Boundary

Status: accepted for v0 spike, blocked for MVP until adapter enforcement exists.

Decision: allow only adapter-enforced read-only investigation and disposable
browser interaction. Reject durable mutation and authenticated real-session
interaction. Do not add an ambiguous ask-for-permission middle tier in v0.

Required proof:

- no generic shell executor is exposed to Subconscious in v0
- filesystem and git adapters are allowlisted read-only operations
- browser adapter always creates a fresh profile with no shared cookies,
  extensions, credential store, or existing tabs
- production domains and authenticated real sessions are denied by policy

## D-007 Interrupt Mode

Status: accepted out of scope for v0.

Decision: live interrupt mode is out of scope for v0. Feedback arrives on the
next prompt. Live interrupt mode needs a separate design.

## D-008 Knudg Integration

Status: accepted out of scope for local v0.

Decision: no Knudg integration in the local v0 design. Preserve the product
context in `docs/integrations/knudg.md`, then design any Knudg-enabled version
as a separate bridge RFC after local v0 proves useful.
