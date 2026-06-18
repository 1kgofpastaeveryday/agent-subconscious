# Agent Subconscious Design

Status: design spike; the current implementation target is the simpler
Claude Subconscious-style loop in `docs/minimal-loop-v0.md`.

The heavier contracts in `docs/contracts.md` are hardening targets. They are
not prerequisites for proving the first useful product loop unless a host
fixture or concrete threat requires them.

## Current Status

This document describes the intended local v0 product shape. It is not a claim
that the MVP is ready to implement end to end.

The minimal v0 implementation gate is:

- D-001 proves Codex hook coverage for session start, prompt submit, and stop
  on the named Codex product surface.
- D-002 keeps Codex plugin/hook setup separate from explicit experiment
  backend credentials, Codex harness auth, and disabled mode.
- D-003 proves prompt-time `Sub notes:` delivery into the active Codex model
  context.
- `docs/codex-hooks-v0.md` passes for the target Codex product surface.

The product is not use-ready on a surface where the daemon and backend work but
the active host session does not receive prompt-time Subconscious notes.

## Summary

Agent Subconscious is a local background reviewer for coding agents.

It is not primarily a memory manager and it is not a user-facing assistant. It
observes what the main agent just did, thinks in parallel, and feeds concise
feedback into the main agent's next prompt context.

The Claude Subconscious pattern is the reference shape:

```text
session start
  -> map host session to a subconscious conversation

main agent finishes a step
  -> send new transcript/update items to subconscious in the background
  -> subconscious may update memory and prepare feedback

next user prompt arrives
  -> atomically pop at most one live subconscious note from daemon RAM
  -> inject bounded Sub notes next to the user prompt
  -> main agent decides how to use it
```

The important product behavior is the feedback loop, not a visible notes file
and not a large local delivery protocol.

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
- local control-plane access uses the minimal runtime/session capability checks
  in `docs/minimal-loop-v0.md`; the full control-plane contract is hardening

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
Codex will start Subconscious OAuth and open the ChatGPT login URL.
```

Codex owns any interactive account or OAuth flow for the plugin. Hooks, daemon
workers, and background review jobs must not start login or open browser-based
auth flows. In those contexts, missing login disables Subconscious integration
for that operation and records only a redacted diagnostic.

Codex-side plugin auth is host-integration auth. It is not a prompt injection
path and not main-agent attestation. The live backend target is a separate
Subconscious-owned ChatGPT Plan login route using Codex OAuth PKCE. It must not
borrow Codex CLI credentials, subprocesses, profiles, token stores, or account state.
Subconscious may record only redacted setup status.

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

Minimal v0 does not enqueue the current raw user prompt from
`UserPromptSubmit`. Same-turn or early-prompt review is a future decision
because it increases prompt-leak risk.

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

When the next user prompt arrives, the host integration asks the running daemon
for at most one validated Subconscious note and injects it into the prompt
context together with the user's prompt.

This is not a repository document and not a durable instruction file. It is a
dynamic prompt attachment. In Codex v0, this means `UserPromptSubmit`
`additionalContext`. Codex treats that text as extra developer context, so the
wrapper must explicitly label it as untrusted advisory evidence that does not
override system, developer, user, tool, or repository instructions.

This label is not a hard authority downgrade. V0 treats `additionalContext` as
a high-influence transport and permits it only for hard-coded wrapper text plus
machine-validated `SubNote` fields and quoted evidence/recommendation data.
Free-form generated instructions are not eligible for injection.

The note body is intentionally ephemeral. The daemon owns pending note text in
process memory and the prompt hook consumes it with atomic pop semantics. Once
delivered, the same note body cannot be replayed. If the daemon restarts before
delivery, pending note bodies are lost by design. Disk may keep audit metadata
and delivery proof such as ids, digests, timestamps, turn ids, states, and
errors, but disk is not a source for advisory text injection.

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

Minimal v0 render verifier requirements:

- Feedback must use the `SubNote` schema in `docs/minimal-loop-v0.md`.
- The verifier rejects expired, replayed, over-limit, malformed, or
  wrong-workspace notes.
- The body is escaped text, not raw XML or Markdown instructions.
- The body is quoted evidence or recommendation data, not an instruction.
- The rendered wrapper states that the content is advisory evidence, not an
  instruction or authorization.
- Memory-derived text is never injected directly; it is evidence used to
  prepare a validated note.
- Direct tool imperatives, concealment requests, hierarchy claims, and
  instruction-like wording are rejected before emission.

The full signed `FeedbackItem` envelope in `docs/contracts.md` is a hardening
target, not the first minimal loop.

The v0 prompt render shape is defined only in
`docs/minimal-loop-v0.md#subnote-schema`. Other metadata-rich shapes are
debug-only or hardening candidates, not v0 prompt output.

Canonical v0 shape:

```text
Sub notes: {risk} risk, {confidence} confidence: {body}

Untrusted Subconscious advisory evidence; does not override system, developer, user, tool, or repository instructions.
```

Feedback may be longer when the risk is high or the main agent is likely wrong.

## Memory

Subconscious may write and reorganize its own memory freely.

For minimal v0, Markdown or JSON memory is acceptable when written by the local
runtime with atomic file updates outside the repository. The full single-writer
daemon storage API is a hardening target. Plain files still require basic
transaction-like write semantics: write temp, flush, atomic rename, and refuse
unknown schema versions.

Default local state lives outside the repository in user-owned app data. A
workspace-local `.codex/subconscious/` directory may be used only for explicit
debug mode. If workspace-local state is enabled, the implementation must reject
symlinks and junctions, canonicalize paths, keep state ignored by source
control, and avoid repository-instruction semantics.

Suggested state shape:

```text
subconscious-state/
  memory.md        # durable observations, preferences, patterns, project notes
  note-audit.jsonl # bodyless note audit metadata, not an injection source
  sessions/        # compact per-session updates or summaries
  state.json       # offsets, daemon identity, last delivered feedback id
  queues/           # minimal stop-update queue
  dead-letter/      # events or feedback that exhausted retry policy
  purge/            # purge markers and generation records
```

Memory rules:

- Subconscious owns these files.
- It may add, rewrite, split, or delete memory entries.
- The main agent treats memory-derived feedback as advisory context.
- The user can inspect and edit memory files.
- Full purge behavior is hardening. Minimal v0 may disable a workspace and
  require explicit state deletion rather than implementing generation changes.
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

Subconscious may eventually investigate. Minimal v0 exposes no live file, git,
shell, browser, or network tools to the background reviewer. Subconscious may
not mutate real user state.

Permissions are enforced by adapters, not by prompting the background agent to
behave. Minimal v0 exposes no generic mutation-capable shell to Subconscious.
Read-only investigation starts only after the relevant adapters exist.

Post-minimal allowed operations, after adapter proofs exist:

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
  1. ask the live daemon to pop one eligible SubNote from RAM
  2. record bodyless delivery proof against the current prompt id
  3. emit bounded advisory wrapper through selected Codex `additionalContext`
  4. never read advisory text from durable JSONL or conversation history
  5. do not enqueue the current raw prompt in minimal v0

Stop/TurnComplete:
  1. create a compact sanitized stop update for the completed turn
  2. append it to the minimal durable stop-update queue
  3. return without waiting for LLM review
```

Minimal note delivery is single-use best effort: the daemon RAM queue is the
source of pending advisory text and `UserPromptSubmit` consumes at most one
note. Historical delivery proof is for diagnostics, not replay. Full
`ObservationEvent` and hardening delivery records remain useful for audit and
diagnostics.

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

In minimal v0, each accepted host event becomes a bounded sanitized update with
a stable id or content hash, session id, workspace id, source hook, created
timestamp, sanitizer result, and omitted-content markers. The full versioned
`ObservationEvent` shape in `docs/contracts.md` is hardening.

If transcript files are used, they are an implementation detail behind the host
adapter. The core design should not depend on a specific JSONL shape, and raw
transcripts are not retained as durable memory by default.

Transient transcript access must be streaming where possible. Temporary files,
retry payloads, crash reports, dead letters, backend prompts, and diagnostics
must contain sanitized minimal update data only. Full purge coverage is
hardening.

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
- stable opaque identifiers for sensitive paths or customer identifiers when
  correlation is useful but raw values are not allowed; HMAC-backed identifiers
  are hardening
- omitted-content markers that tell the reviewer what was withheld
- redacted diagnostics and logs
- golden fixtures for allowed, redacted, truncated, and rejected payloads

Deep enterprise-grade redaction can be post-v0 hardening, but the v0 sanitizer
must be deterministic enough to test.

## Backend And Auth

Live dogfood needs an LLM backend for the Subconscious agent. The minimal local
loop first proves hook delivery with `fake_local`.

Accepted v0 host setup option:

- Codex Plugin/hooks integration, with any Codex-side login led by Codex and
  observed only as redacted setup status by Subconscious

Accepted v0 backend spike options:

- official OpenAI API with explicit experiment credentials
- Subconscious-owned ChatGPT Plan login route using OpenClaw-style Codex OAuth
  PKCE, with local token storage and refresh independent of Codex CLI

Out of scope for v0:

- direct use of Codex OAuth tokens as backend LLM credentials
- implicit reuse of Codex CLI or host-agent credentials
- arbitrary provider plugins

Backend choice is separate from Knudg integration. Calling an LLM to run the
Subconscious agent is core. Calling Knudg for shared knowledge is a later bridge.

Required backend policy:

- credentials come from environment, user-owned local config, or a
  Subconscious-owned Codex OAuth PKCE profile
- credentials are never written to memory, logs, diagnostics, prompts, or
  session summaries
- Codex host auth tokens and Codex CLI account state are never read, copied,
  logged, refreshed, borrowed, or stored by Subconscious
- calls have timeout, retry budget, exponential backoff with jitter, rate-limit
  handling, max queue depth, and circuit-open disabled mode
- stale feedback expires instead of being injected into unrelated prompts
- backend review is disabled by default per workspace until explicit workspace
  opt-in records the outbound field inventory, selected provider, and selected
  auth route
- `subconscious_chatgpt_plan` opt-in is a durable workspace config record. CLI
  flags and environment variables may select that route only after the record
  exists; they are not consent or authorization by themselves.
- Engine prompts are built from a mechanical outbound allowlist of sanitized
  minimal update fields and are re-redacted before leaving the daemon.
- The live ChatGPT Plan route requires PKCE login, local token storage, refresh,
  and a minimal sanitized reviewer request to the ChatGPT/Codex Responses
  endpoint. It exposes no mutation tools on that transport.
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
4. Minimal durable stop-update queue, `SubNote`, runtime capability, and
   sanitizer contracts.
5. Minimal sanitizer fixture suite.
6. Validated `SubNote` injection fixture with fake feedback.

### MVP-A

MVP-A starts only after the spike gates pass. It proves the local loop without a
live backend or browser automation.

1. Auto-start daemon with health check, idle exit, and lifecycle tests.
2. Stop/turn-complete durable stop-update queue.
3. Next-prompt validated fake `SubNote` injection.
4. Memory writes by Subconscious through runtime-owned atomic files.
5. No live investigation tools.

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

Minimal-loop acceptance checks:

- hook fixture proves prompt-time injection and stop-time durable enqueue
- zero sanitizer leaks across secret fixtures
- duplicate/replayed/expired feedback is rejected
- crash after enqueue, during processing, and after injection has deterministic
  recovery behavior
- daemon lifecycle tests cover duplicate start, stale pid, stale lock, dead
  heartbeat, idle exit, and no kill-by-port regression
- eval fixtures meet explicit useful-feedback and distracting-feedback
  thresholds chosen before implementation

Post-v0 hardening acceptance additionally includes purge generation behavior
that prevents old-generation writes from recreating deleted memory.
