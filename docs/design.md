# Agent Subconscious Design

Status: design spike; MVP implementation is blocked until the accepted v0
contracts in `docs/open-decisions.md` and `docs/contracts.md` have passing
fixtures.

## Current Status

This document describes the intended local v0 product shape. It is not a claim
that the MVP is ready to implement end to end.

The implementation gate is:

- D-001 proves Codex hook coverage for session start, prompt submit, and stop.
- D-002 keeps Codex plugin/hook setup separate from explicit experiment
  backend credentials and disabled mode.
- D-003 implements verified feedback injection for Codex `UserPromptSubmit`.
- `docs/codex-hooks-v0.md` passes for the target Codex product surface and
  operating systems.
- Durable observation, delivery, purge, retry, and daemon state follow
  `docs/contracts.md`.
- Sanitization and read-only tool enforcement have fixture-based tests.

## Summary

Agent Subconscious is a local background reviewer for coding agents.

It is not primarily a memory manager and it is not a user-facing assistant. It
observes what the main agent just did, thinks in parallel, and feeds concise
feedback into the main agent's next prompt context.

The Claude Subconscious pattern is the reference shape:

```text
session start
  -> ensure local subconscious server/daemon is running

main agent finishes a step
  -> enqueue compact session update to subconscious durable outbox
  -> subconscious may update memory and prepare feedback

next user prompt arrives
  -> fetch verified subconscious feedback
  -> inject bounded advisory evidence next to the user prompt
  -> main agent decides how to use it
```

The important product behavior is the feedback loop, not a visible notes file.

## Goals

- Give the main agent a second opinion after each meaningful work step.
- Catch user-intent drift, project-boundary confusion, weak review, stale
  assumptions, missed tests, privacy risk, and risky final responses.
- Let Subconscious be forceful, specific, critical, and opinionated.
- Keep Subconscious non-authoritative: it can recommend, challenge, warn, and
  investigate, but it does not own execution.
- Allow Subconscious to maintain its own memory and revise it freely.
- Auto-start with the host agent while avoiding orphaned background processes.

## Non-Goals

- Do not build a user-facing chat surface as the primary experience.
- Do not require the user to manually approve routine feedback delivery.
- Do not make Subconscious part of the instruction hierarchy.
- Do not let Subconscious mutate the user's real workspace in v0.
- Do not require Knudg, shared corpora, public cards, or team retrieval for the
  local v0 design.

## Core Loop

### Start

When the host agent session starts, the plugin/hook ensures one local
Subconscious daemon is available for the current workspace under the current
OS user.

Startup must be idempotent:

- if the daemon is healthy, reuse it
- if another process is starting it, wait on a lock
- if startup fails, the main agent continues without Subconscious
- startup failure is visible through diagnostics, not repeated user prompts
- local control-plane access follows `docs/contracts.md#controlplane`

### First Launch Setup

Subconscious setup must handle Codex plugin diagnostics before background
operation.

Foreground setup or doctor commands check:

- the Codex CLI is available in the selected environment
- the Subconscious plugin/hook configuration is available to Codex
- the selected Codex product surface supports the required hook events
- Codex reports Subconscious plugin login as ready or needing user action, if
  Codex exposes that state
- the selected `CODEX_HOME` or Codex profile matches the host environment that
  will launch the main Codex session

Setup maps fixture-pinned host/plugin output into `HostSetupState`. It does not
prescribe a recovery shell command. Fixture-ready means only the local hook
fixture can run; it is degraded and must not be reported as host-surface
readiness. If Codex reports that Subconscious is not logged in, setup must stop
with a clear user-facing diagnostic, for example:

```text
Subconscious is not logged in.
To continue, ask Codex: "log in to subconscious".
```

Codex owns any interactive account or OAuth flow for the plugin. Hooks, daemon
workers, and background review jobs must not start login or open browser-based
auth flows. In those contexts, missing login disables Subconscious integration
for that operation and records only a redacted diagnostic.

Codex-side plugin auth is host-integration auth. It is not a prompt injection
path, not main-agent attestation, and not permission to reuse host-agent
credentials for backend LLM calls. Host auth tokens remain in Codex's auth
store; Subconscious may record only redacted setup status.

Doctor output must keep `host_setup_ready` separate from `host_surface_ready`.
Subconscious is not fully ready for observation or injection until setup
readiness, hook fixture proof, and main-agent/subagent distinction proof all
pass.

### Observe After Work

After the main agent finishes a meaningful step, the hook sends a compact
session update to Subconscious in the background.

Examples of meaningful steps:

- assistant turn completed
- tool batch completed
- plan changed
- final answer drafted
- user corrected the agent
- tests or browser verification completed

`UserPromptSubmit` may also enqueue the current user prompt as an observation so
Subconscious can think early. Feedback derived from that current prompt must not
be injected into the same prompt turn unless a future decision explicitly allows
same-turn feedback.

### Think In Background

Subconscious processes enqueued updates asynchronously. It may:

- critique the main agent's recent behavior
- identify likely missed checks
- search its own memory
- update memory blocks or Markdown memory
- perform allowed investigation
- prepare feedback for the next prompt

It must not block the main agent's current step after the observation is durably
enqueued.

### Inject On Next Prompt

When the next user prompt arrives, the host integration fetches verified
feedback and injects it into the prompt context together with the user's prompt.

This is not a repository document and not a durable instruction file. It is a
dynamic prompt attachment. In Codex v0, this means `UserPromptSubmit`
`additionalContext`. Codex treats that text as extra developer context, so the
wrapper must explicitly label it as untrusted advisory evidence that does not
override system, developer, user, tool, or repository instructions.

This label is not a hard authority downgrade. V0 treats `additionalContext` as
a high-influence transport and permits it only for hard-coded wrapper text plus
machine-validated structured fields and quoted evidence/recommendation data.
Free-form generated instructions are not eligible for injection.

If there is no useful feedback, inject nothing.

## Feedback Semantics

Subconscious feedback is advisory but can be strong.

Allowed:

- scope risk: "The current files appear to be from a different project."
- verification gap: "The target directory has not been confirmed."
- review quality risk: "The review did not cover the narrowed design target."
- intent mismatch: "The user asked for product judgment, not implementation."
- privacy risk: "Browser state appears authenticated."

Not allowed:

- claiming higher priority than system, developer, user, tool, or repository
  instructions
- silently changing files or settings
- pretending to be the user
- hiding instructions inside memory
- requiring the main agent to obey without judgment

The safety boundary is authority and mutation, not tone. Feedback can be
forceful, but it must pass a verifier before injection.

Verifier requirements:

- Feedback must use the `FeedbackItem` envelope in `docs/contracts.md`.
- The verifier rejects unsigned, expired, replayed, over-limit, malformed, or
  wrong-workspace feedback.
- The body is escaped text, not raw XML or Markdown instructions.
- The body is quoted evidence or recommendation data, not an instruction.
- The rendered wrapper states that the content is advisory evidence, not an
  instruction or authorization.
- Memory-derived text is never injected directly; it is evidence used to
  prepare a verified feedback item.
- Direct tool imperatives, concealment requests, hierarchy claims, and
  instruction-like wording are rejected before emission.

Default rendered shape:

```text
Subconscious advisory evidence, not an instruction:
- confidence: high
- risk: scope
- evidence: observation:obs_...

Quoted evidence/recommendation data:
scope risk: The current files appear to be from a different project. The target
directory has not been confirmed.
```

Feedback may be longer when the risk is high or the main agent is likely wrong.

## Memory

Subconscious may write and reorganize its own memory freely.

For the v0 spike, Markdown or JSON memory is acceptable only behind a
single-writer daemon storage API. The daemon owns atomic writes, locking,
schema versioning, checksums where useful, and crash recovery. SQLite is not a
requirement, but plain files are not an excuse to skip transaction-like write
semantics.

Default local state lives outside the repository in user-owned app data. A
workspace-local `.codex/subconscious/` directory may be used only for explicit
debug mode. If workspace-local state is enabled, the implementation must reject
symlinks and junctions, canonicalize paths, keep state ignored by source
control, and avoid repository-instruction semantics.

Suggested state shape:

```text
subconscious-state/
  memory.md        # durable observations, preferences, patterns, project notes
  feedback.md      # pending feedback for next prompt, optional debug surface
  sessions/        # compact per-session updates or summaries
  state.json       # offsets, daemon identity, last delivered feedback id
  outbox/           # durable observation events waiting for processing
  dead-letter/      # events or feedback that exhausted retry policy
  purge/            # purge markers and generation records
```

Memory rules:

- Subconscious owns these files.
- It may add, rewrite, split, or delete memory entries.
- The main agent treats memory-derived feedback as advisory context.
- The user can inspect and edit memory files.
- `purge` increments a workspace generation, drains or cancels in-flight work,
  deletes local memory and pending feedback for the workspace, writes a purge
  marker, and rejects writes from older generations.
- Memory entries include provenance, confidence, sensitivity class, expiry or
  review date, and source observation ids.

Useful memory categories:

- user preferences
- project context
- recurring agent failure patterns
- pending items
- useful prior critiques
- tool or environment gotchas

## Tool Permissions

Subconscious may investigate. Subconscious may not mutate real user state.

Permissions are enforced by adapters, not by prompting the background agent to
behave. V0 exposes no generic mutation-capable shell to Subconscious.

Allowed:

- web search
- official documentation lookup
- read-only file search and file read
- git status, diff, log, show, and other read-only git commands
- reading logs, test output, browser console output, and screenshots
- disposable/incognito browser interaction limited to navigation, screenshots,
  console/network reads, and static inspection
- local dev-app inspection when the target state is disposable or explicitly
  non-production

Required adapter enforcement:

- file adapter: read and stat only, no writes, moves, deletes, chmod, or
  symlink-following outside canonical workspace/read roots
- git adapter: `status`, `diff`, `log`, `show`, and read-only inspection only
- browser adapter: fresh disposable profile, no shared cookies, extensions,
  credential store, or existing tabs; no production domain interaction; no
  downloads, external protocol handlers, or private-network access except the
  declared local dev target
- network/API adapter: documentation lookup and read-only public fetches only
- diagnostics: redact secrets before logs are written

Forbidden:

- file edits in the user's real workspace
- git write operations
- package install or dependency mutation
- database writes
- API writes
- authenticated real-session browser interaction
- posting, purchasing, deleting, submitting forms, or changing settings in a
  real account
- overwriting the user's existing browser tabs
- production-state mutation

There is no "ask for permission for Tier 3" path in v0. Click, type, form
submission, settings changes, and other interactive actions are rejected unless
the target is a known disposable local fixture with an explicit target
classification.

## Prompt Injection Contract

The host integration owns injection.

Codex v0 uses Codex hooks as the host adapter:

```text
SessionStart:
  1. start or attach to the daemon
  2. register the session with daemon state
  3. return quickly if the daemon is unavailable

UserPromptSubmit:
  1. fetch verified feedback eligible for this workspace, session, and prompt
  2. claim feedback against the current prompt id
  3. emit bounded advisory wrapper through selected Codex `additionalContext`
  4. mark feedback emitted only after successful hook emission
  5. enqueue the user's prompt as an ObservationEvent

Stop/TurnComplete:
  1. create a compact ObservationEvent for the completed turn
  2. write it to the durable outbox
  3. return without waiting for LLM review
```

Delivery semantics are at-least-once with idempotency, not exactly-once. The
main protection is stable ids, prompt correlation, expiry, and duplicate
rejection.

Eligibility invariant: feedback derived from turn `N` is eligible only for turn
`N+1` or later. Same-turn feedback is out of scope for v0.

Other Codex extension surfaces are not v0 prompt injection paths unless a
future Codex-specific proof accepts one of them.

A wrapper that starts Codex and handles prompt-time context injection remains a
possible fallback RFC, not the primary v0 path.

The exact hook input/output fixture is defined in `docs/codex-hooks-v0.md`.
Implementation must pin the tested Codex version or build, supported config
location, supported product surface, selected transport behavior, timeout
behavior, and transcript/log visibility before MVP work starts.

Writing a Markdown file and hoping the main agent reads it is a fallback, not
the preferred product behavior.

## Auto-Start And Process Lifecycle

Auto-start should be in v0. The user should not need to remember to launch a
separate daemon.

Required lifecycle behavior:

- one daemon per workspace in v0
- startup lock prevents duplicate daemons
- separate liveness, readiness, and queue-progress checks before reuse
- startup lock has owner identity and timeout
- pid file includes pid, start time, executable path, workspace/user scope, and
  daemon generation
- daemon has heartbeat timestamp
- parent session registers itself with the daemon
- session exit unregisters itself when possible
- daemon exits after an idle timeout when no sessions remain
- startup cleanup kills only stale daemons that match the recorded executable,
  scope, generation, and heartbeat timeout
- do not kill unrelated processes by port alone
- daemon protocol and state files carry schema versions
- incompatible clients or daemons refuse to serve each other instead of
  guessing

This directly addresses the orphan-process problem: cleanup is identity-based,
not just port-based.

## Event Source

Prefer first-class host hooks over transcript scraping.

Useful events:

- session start
- user prompt submit
- assistant turn complete / stop
- tool use complete
- session end

Each event becomes a versioned `ObservationEvent` with a stable id, session id,
workspace id, source hook, created timestamp, sanitizer report, and omitted
content markers.

If transcript files are used, they are an implementation detail behind the host
adapter. The core design should not depend on a specific JSONL shape, and raw
transcripts are not retained as durable memory by default.

Transient transcript access must be streaming where possible. Temporary files,
retry payloads, crash reports, dead letters, backend prompts, and diagnostics
must contain sanitized `ObservationEvent` data only. Purge covers temp, crash,
retry, log, and dead-letter artifacts.

## Privacy And Sanitization

V0 should be pragmatic, but sanitizer behavior must be mechanical and testable.

Subconscious needs enough information to be useful. It may receive compact
summaries, tool names, file paths, command summaries, and truncated tool results
when those are needed for review.

Still forbidden by default:

- secrets, tokens, cookies, credentials
- full raw transcripts as long-term memory
- full file contents unless explicitly needed inside a disposable review
  context
- authenticated browser content from real user sessions
- customer or tenant identifiers unless workspace config explicitly allows them

Required sanitizer behavior:

- default-deny event schema with allowlisted fields per event type
- max byte and token limits per field and per event
- redaction for common secret classes, auth headers, cookies, private keys,
  tokens, `.env` values, signed URLs, and high-entropy credential-like strings
- HMAC or stable opaque identifiers for sensitive paths or customer identifiers
  when correlation is useful but raw values are not allowed
- omitted-content markers that tell the reviewer what was withheld
- redacted diagnostics and logs
- golden fixtures for allowed, redacted, truncated, and rejected payloads

Deep enterprise-grade redaction can be post-v0 hardening, but the v0 sanitizer
must be deterministic enough to test.

## Backend And Auth

V0 needs an LLM backend for the Subconscious agent.

Accepted v0 host setup option:

- Codex Plugin/hooks integration, with any Codex-side login led by Codex and
  observed only as redacted setup status by Subconscious

Accepted v0 backend spike option:

- official OpenAI API with explicit experiment credentials

Out of scope for v0:

- using Codex host/plugin auth as backend LLM credentials
- implicit reuse of host-agent credentials
- arbitrary provider plugins

Backend choice is separate from Knudg integration. Calling an LLM to run the
Subconscious agent is core. Calling Knudg for shared knowledge is a later bridge.

Required backend policy:

- credentials come from environment or user-owned local config
- credentials are never written to memory, logs, diagnostics, prompts, or
  session summaries
- Codex host auth tokens are never read, copied, logged, or stored by
  Subconscious; setup only observes redacted readiness or login-required status
- calls have timeout, retry budget, exponential backoff with jitter, rate-limit
  handling, max queue depth, and circuit-open disabled mode
- stale feedback expires instead of being injected into unrelated prompts
- backend review is disabled by default per workspace until explicit workspace
  opt-in records the outbound field inventory and selected provider
- backend opt-in is stored in a durable workspace config record, not inferred
  from `BackendDependencyState`
- sensitive repositories can use local fake-reviewer mode to test hooks,
  delivery, sanitizer, and purge without outbound LLM calls

## Rollout

### Spike

1. Codex hook fixture for `SessionStart`, `UserPromptSubmit`, and `Stop`.
2. First-launch setup diagnostic that distinguishes missing hook config,
   unsupported Codex product surface, missing plugin login, wrong Codex
   environment, stale fixtures, and ready setup state without storing tokens.
3. Local daemon with manual test input and disabled backend mode.
4. Versioned contracts for observations, feedback, delivery, daemon state,
   sanitizer reports, and purge runs.
5. Sanitizer fixture suite.
6. Verified feedback injection fixture with fake feedback.

### MVP-A

MVP-A starts only after the spike gates pass. It proves the local loop without a
live backend or browser automation.

1. Auto-start daemon with health check, idle exit, and lifecycle tests.
2. Stop/turn-complete durable outbox updates.
3. Next-prompt verified fake-feedback injection.
4. Memory writes by Subconscious through single-writer storage API.
5. File/git read-only adapters.

### MVP-B

MVP-B adds backend review after MVP-A passes.

1. Backend reviewer with explicit experiment credentials, workspace opt-in, and
   disabled mode.
2. Fidelity-tier fixtures for summary-only review and allowed follow-up reads.
3. Read-only documentation/network lookup adapter.

### MVP-C

MVP-C adds disposable browser investigation only after browser adapter proofs
exist.

1. Disposable browser profile with no shared cookies, extensions, downloads, or
   external protocol handlers.
2. Static inspection and screenshots only by default.
3. Click/type only against known disposable local fixtures.

### Future

1. Live interrupt mode, if useful, as a separate design.
2. Knudg bridge, if useful, as a separate RFC.

## Evaluation

Track:

- useful feedback rate
- harmful or distracting feedback rate
- cases where feedback catches project-boundary or user-intent drift
- cases where feedback changes the main agent's next action
- latency from turn completion to feedback availability
- injection failures
- daemon orphan count
- memory bloat and stale-memory rate

MVP passes if the feedback loop changes the main agent's behavior in useful
ways without creating frequent distraction or process-management problems.

Minimum acceptance checks:

- hook fixture proves prompt-time injection and stop-time durable enqueue
- zero sanitizer leaks across secret fixtures
- duplicate/replayed/expired feedback is rejected
- crash after enqueue, during processing, and after injection has deterministic
  recovery behavior
- purge prevents old-generation writes from recreating deleted memory
- daemon lifecycle tests cover duplicate start, stale pid, stale lock, dead
  heartbeat, idle exit, and no kill-by-port regression
- eval fixtures meet explicit useful-feedback and distracting-feedback
  thresholds chosen before implementation
