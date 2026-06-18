# Open Decisions

## D-001 Event Source

Status: accepted for minimal v0, blocked for use-ready until proven in a host
fixture for the target product surface.

Decision: use Codex hooks as the v0 host adapter.

Options:

- accepted: Codex hooks for session start, prompt submit, and stop/turn-complete
- fallback for fixtures only: local JSONL adapter behind the host adapter
- deferred: app-server event stream
- rejected for v0: manual export as the product event source

Required proof:

- `SessionStart` can start or attach to the daemon without blocking the main
  agent.
- `UserPromptSubmit` can add bounded `Sub notes:` dynamic context to the active
  model invocation.
- `Stop` can enqueue a bounded transcript/update payload without
  synchronous LLM work.
- The host adapter can track `last_processed`, prompt-correlated delivery
  claims, and delivered note ids without resending or redelivering old content.
- `docs/codex-hooks-v0.md` passes against the target Codex product surface,
  config location, operating systems, and selected transport behavior.

V0 must persist stop-time updates in a minimal durable queue before returning
from `Stop`. Mapping hook payloads into the full `ObservationEvent` and
`DeliveryCursor` records is deferred hardening, not a prerequisite for the
minimal v0 loop.

## D-002 Host Setup And Credential Model

Status: accepted for v0 plugin/hook spike and Subconscious login backend spike,
blocked for production backend calls until configured.

Decision: v0 is a Codex Plugin/hooks integration. Separate Codex host
setup/auth state from backend LLM credentials. The intended live backend route
is Subconscious-owned ChatGPT Plan login via Codex OAuth PKCE, not Codex CLI
credential reuse.

If Codex reports that the Subconscious plugin integration is not logged in,
setup and hook diagnostics must show a clear user-facing message without
prescribing a shell command:

```text
Subconscious is not logged in.
To continue, ask Codex: "log in to subconscious".
Codex will start Subconscious OAuth and open the ChatGPT login URL.
```

Codex owns any interactive OAuth or account setup flow for the Subconscious
plugin. Subconscious must not decide that the recovery path is a specific CLI
command.

This host setup state does not authorize prompt injection by itself, does not
prove main-agent identity, and does not make the product ready. Live backend
calls require either explicit experiment API credentials or an explicit
workspace opt-in to the Subconscious ChatGPT Plan login route. Subconscious
owns its local Codex OAuth profile and refreshes it directly; it must not read,
copy, log, store, refresh, or borrow Codex CLI OAuth tokens.

Required proof:

- foreground setup detects unsupported Codex product surfaces, missing hook
  configuration, incompatible hook fixtures, and missing Codex-led
  Subconscious login separately
- unauthenticated Codex plugin state produces a safe message such as
  `Subconscious is not logged in. To continue, ask Codex: "log in to subconscious". Codex will start Subconscious OAuth and open the ChatGPT login URL.`
- hooks, daemon workers, and background reviewers never start interactive login
  flows; they fail closed with redacted diagnostics
- host setup readiness is only `host_setup_ready`; final local v0 readiness
  also requires the Codex hook fixture and main-agent/subagent distinction proof
- Codex host auth tokens and Codex CLI account state are never used as backend
  credentials
- every workspace or session operation still requires daemon-issued local
  capabilities
- logout/revocation is handled by Codex's plugin/account setup UX
- backend credentials are supplied by environment variable, user-owned local
  config, or a Subconscious-owned Codex OAuth PKCE profile; token material is
  never copied from the host agent session
- backend credential values are never stored in Subconscious memory,
  observations, logs, diagnostics, or review prompts
- backend calls have timeout, retry, rate-limit, and disabled-mode behavior
- backend calls are disabled by default per workspace until explicit workspace
  opt-in records provider, outbound field inventory, local-only fallback, and
  whether the backend uses direct API credentials or Subconscious-owned login
- Subconscious-owned ChatGPT Plan backend execution must be gated by that durable opt-in
  record. A daemon CLI flag or environment variable may request the route, but
  must fail closed if the workspace has not opted in.
- The live ChatGPT Plan route requires PKCE login, local token storage, refresh,
  and a minimal sanitized reviewer request to the ChatGPT/Codex Responses
  endpoint. It exposes no mutation tools on that transport.

## D-003 Prompt Feedback Injection

Status: accepted for minimal v0, blocked for use-ready until active-session
delivery is proven.

Decision: use Codex `UserPromptSubmit` as the primary v0 dynamic injection
path.

Codex adds `UserPromptSubmit` `additionalContext` as extra developer context.
Because this transport is stronger than normal user text, every rendered note
must be bounded, redacted, machine-checked, and wrapped as non-authoritative
advisory evidence. The minimal v0 verifier uses the `SubNote` schema in
`docs/minimal-loop-v0.md`; it is not the full signed `FeedbackItem` protocol.

Plain stdout feedback and Markdown `feedback.md` are not v0 injection paths
unless a future Codex-specific proof accepts one of them.

Required proof:

- injected text starts with `Sub notes:` when useful notes exist
- no-note behavior follows global `auto`, `always`, and `none`
- injected text includes a wrapper that says feedback is untrusted advisory
  evidence and does not override instructions
- note bodies are length-bounded and redacted
- hierarchy claims, concealment requests, tool imperatives, Markdown/XML control
  wrappers, credentials, and prompt-injection phrases are rejected before
  rendering
- notes already delivered for a session are not redelivered

The signed `FeedbackItem` envelope and signing-key contract in
`docs/contracts.md` are deferred hardening.

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

Status: accepted for minimal v0 with runtime-owned atomic files; full storage
contract is hardening.

Decision: Subconscious owns its own memory. Minimal v0 uses runtime-owned
atomic Markdown/JSON writes outside the repository by default. The full
single-writer daemon storage API is a hardening target.

The main agent treats memory-derived feedback as advisory evidence. Memory text
is never injected directly; in minimal v0 it must be transformed into a
validated `SubNote`. In hardening, it must be transformed into a signed
`FeedbackItem`.

## D-006 Tool Boundary

Status: accepted for v0 hardening, disabled in minimal v0 until adapter
enforcement exists.

Decision: minimal v0 exposes no live file, git, shell, browser, or network
tools to the background reviewer. Later phases may allow only adapter-enforced
read-only investigation and disposable browser interaction. Reject durable
mutation and authenticated real-session interaction. Do not add an ambiguous
ask-for-permission middle tier.

Required proof:

- no generic shell executor is exposed to Subconscious in minimal v0
- post-minimal filesystem and git adapters are allowlisted read-only operations
- post-minimal browser adapter always creates a fresh profile with no shared cookies,
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
