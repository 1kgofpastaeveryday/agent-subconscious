# Agent Subconscious

> Not yet usable: this repository is a local design and hook-fixture spike.
> It is not an installable or reliable product yet.

Agent Subconscious is a local second-opinion sidecar for coding agents.

It runs beside the main agent, receives compact updates after the main agent
works, thinks in the background, and injects feedback into the main agent's next
prompt context.

The purpose is not user-facing memory management. The purpose is to catch drift,
missed constraints, stale assumptions, risky final answers, and user-intent
mismatch before the main agent continues.

## Boundary

This is a standalone local project.

It is separate from Knudg. Knudg may later consume or inspire parts of this
design, but this project does not implement Knudg public cards, team corpora,
central retrieval, consent workflows, billing, or tenant policy.

## MVP

Current status: design spike, not implementation-ready MVP.

The local feedback loop below is the intended MVP shape, but implementation is
blocked until the accepted spike decisions in
[Open Decisions](docs/open-decisions.md) are proven by fixtures and the
contracts in [Contracts](docs/contracts.md) are testable.

The blocking decisions are:

- accepted Codex event source and injection path
- passing Codex hook fixture for supported product surfaces
- Codex plugin/hook setup diagnostics and explicit backend credential model
- signed/verifiable feedback envelope
- durable observation, delivery, purge, and retry state
- mechanical sanitizer and read-only tool enforcement contracts

Intended MVP is a local auto-start feedback loop:

1. Auto-start a local daemon with health checks and orphan cleanup.
2. Send compact session updates after meaningful main-agent steps.
3. Let the Subconscious agent update its own Markdown/JSON memory.
4. Fetch new feedback when the next user prompt arrives.
5. Inject that feedback as dynamic prompt context, not as a repo document.
6. Allow read-only investigation and disposable/incognito browser interaction.
7. Forbid durable mutation of the user's real workspace or authenticated
   sessions.

First launch must also handle Codex-side setup diagnostics explicitly. The v0
path is Codex Plugin/hooks. If Codex reports that the Subconscious plugin
integration is not logged in, setup should emit a redacted message such as
`Subconscious is not logged in. To continue, ask Codex: "log in to
subconscious".` Codex owns the interactive login/setup flow. Hooks and
background workers must not start browser-based auth flows themselves.

MVP remains blocked. The first thin hook spike may still be implemented
without claiming MVP readiness. Fixture-ready hook/setup output means only the
local fixture path is working; it is not proof of Codex host-surface login,
main-agent attestation, or real prompt injection readiness.

## Non-Goals

- No Letta compatibility layer requirement.
- No long-term raw transcript memory by default.
- No external write destinations in v0.
- No mutation tools in v0.
- No live interrupt mode in v0; feedback arrives on the next prompt.
- No team or public knowledge product in this repository.
- No Knudg dependency in local v0.

## Design Docs

- [Design](docs/design.md)
- [Contracts](docs/contracts.md)
- [Codex Hooks V0 Contract](docs/codex-hooks-v0.md)
- [Open Decisions](docs/open-decisions.md)
- [Knudg Integration Context](docs/integrations/knudg.md)
- [Claude Subconscious Reference](docs/references/claude-subconscious.md)
