# Agent Subconscious

Agent Subconscious is a local second-opinion sidecar for Codex Desktop.

It observes the active coding agent, asks a configured reviewer engine for a
short advisory note, and injects that note into the next Codex prompt as
dynamic context. The note is advisory evidence only; it does not override
system, developer, user, tool, or repository instructions.

## Boundary

This is a standalone local experiment. It is separate from Knudg and does not
implement public cards, team corpora, central retrieval, consent workflows,
billing, tenant policy, or shared knowledge management.

## Current Dogfood Path

The minimally dogfoodable path for this Codex Desktop environment is:

1. Codex hooks observe `SessionStart`, `UserPromptSubmit`, and `Stop` when the
   host dispatches those events.
2. A local auditable log is written at `live_event_log.jsonl` under the
   Subconscious workspace state directory. Each record includes `session_id`,
   `turn_id`, `event_type`, `observed_at`, `observation_id`, and
   `source_file_path` when a transcript or rollout file is available.
3. The daemon reviews completed turn observations with the selected LLM engine.
   A useful note body is held only in the running daemon's RAM queue. Disk gets
   bodyless audit metadata, or an abstain/error in `llm_activity.jsonl` and
   `observation_events.jsonl`.
4. The next `UserPromptSubmit` hook asks the live daemon to atomically pop at
   most one current note. A popped note is consumed before injection and cannot
   replay unless the daemon generates a fresh note. If the daemon restarts,
   pending undelivered note bodies are lost by design. `Sub notes: none` is only
   an empty diagnostic in `always` mode.
5. If `UserPromptSubmit` or `Stop` hooks do not fire, the fallback rollout
   watcher can observe Codex session JSONL logs. Injection still requires a
   prompt-time hook until another Codex Desktop injection surface exists.

## Setup

Install the local hook plugin for Windows:

```powershell
python -m agent_subconscious.codex_plugin_setup install --platform windows
python -m agent_subconscious.codex_plugin_setup doctor --platform windows
```

For a fresh dogfood session where you want visible injection even before the
reviewer has a note, set always mode:

```powershell
python -m agent_subconscious.cli sub-notes always
```

Use the live ChatGPT Plan backend when available:

```powershell
python -m agent_subconscious.cli login
python -m agent_subconscious.cli status
```

The login path creates an explicit workspace opt-in, opens an OpenAI OAuth URL,
and stores the Subconscious-owned OAuth profile in local Subconscious state.
The offline fallback reviewer remains `fake_local`.

## Verification Commands

Run the unit tests:

```powershell
python -m unittest discover -s tests
```

Run the daemon once against the current workspace state:

```powershell
python -m agent_subconscious.cli run --once --enable-rollout-watcher --cwd D:\working\agent-subconscious
```

Run the rollout watcher once if hook coverage is incomplete:

```powershell
python -m agent_subconscious.cli watch-rollout --once --cwd D:\working\agent-subconscious
```

Show the live dogfood diagnostic:

```powershell
python -m agent_subconscious.cli diagnose --cwd D:\working\agent-subconscious
```

The diagnostic reports:

- active Codex binary path and `codex --version` output, when available
- latest matching Codex rollout file
- whether `SessionStart`, `UserPromptSubmit`, and `Stop` have appeared in the
  auditable live log
- whether rollout watching is active
- daemon status
- whether the latest observed turn produced a note, abstained, failed, or is
  pending
- current live delivery state from the daemon RAM queue
- historical non-empty delivery proof from bodyless audit metadata
- whether the latest visible Sub notes proof is stale
- readiness as `READY` only when the latest proof is a real user-prompt
  non-empty injection with an emitted delivery record

## State Files

Subconscious-owned state is outside the repository under the workspace-specific
hook state directory. Important files:

- `observation_events.jsonl`: append-only sanitized observation state
- `live_event_log.jsonl`: dogfood audit log for hook and rollout observations
- `llm_activity.jsonl`: reviewer start, completion, abstain, and error records
- `sub_notes.jsonl`: bodyless note audit metadata only; it is not an injection
  source
- `sub_note_deliveries.jsonl`: prompt-time note delivery proof without note
  bodies
- `rollout_watcher_status.json`: fallback watcher status
- `transcript_refs.jsonl`: transcript path references without raw transcript
  body storage

## Current Limitations

- Prompt context injection is hook-dependent. The rollout watcher can observe
  missing `Stop` or prompt events from Codex JSONL logs, but it cannot inject
  context unless `UserPromptSubmit` or an equivalent Codex Desktop surface runs.
- The fallback watcher reads sanitized signals from rollout JSONL and records
  file paths, but it does not store raw prompts or raw assistant transcripts as
  durable memory.
- The live ChatGPT Plan backend requires a local Subconscious OAuth login and
  explicit workspace opt-in. Without that, `fake_local` can still produce or
  abstain from simple diagnostic notes.
- This repository does not enforce a read-only browser or external investigation
  sandbox yet; those remain product hardening work.

## Troubleshooting

If `diagnose` says hooks are not firing, reinstall and re-run doctor:

```powershell
python -m agent_subconscious.codex_plugin_setup install --platform windows
python -m agent_subconscious.codex_plugin_setup doctor --platform windows
```

If `Stop` is missing but Codex rollout logs exist, start the fallback watcher:

```powershell
python -m agent_subconscious.cli watch-rollout --once --cwd D:\working\agent-subconscious
```

If no note is produced, inspect `llm_activity.jsonl`. `outcome: abstain` means
the reviewer saw no useful alternate angle. `review_failed` records the local
error type.

If injection is missing while observations and note audit metadata exist, verify
the daemon is live and `UserPromptSubmit` is firing. Historical `sub_notes.jsonl`
rows do not contain reusable note bodies and are not enough for injection. In
`always` mode, a healthy prompt-time hook should inject `Sub notes: none` even
with no live pending note.

`diagnose` intentionally reports `NOT_READY` when the latest delivery is only
`Sub notes: none`, when it came from a synthetic diagnostic prompt, when the
latest note is expired/stale, when the rollout watcher is not following the
current rollout file, or when no emitted non-empty delivery proof exists.

## Design Docs

- [Design](docs/design.md)
- [Contracts](docs/contracts.md)
- [Codex Hooks V0 Contract](docs/codex-hooks-v0.md)
- [Open Decisions](docs/open-decisions.md)
- [Knudg Integration Context](docs/integrations/knudg.md)
- [Claude Subconscious Reference](docs/references/claude-subconscious.md)
